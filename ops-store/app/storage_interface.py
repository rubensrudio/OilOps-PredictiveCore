"""
ops-store/app/storage_interface.py
====================================
Abstract base class (ABC) that defines the **storage contract** for the
OilOps-PredictiveCore ``ops-store`` service.

Every concrete storage backend — the embedded DuckDB + SQLite implementation
(TASK-006, TASK-007) and any future backends such as InfluxDB or TimescaleDB
(DA-02) — **must** implement all methods declared here without altering the
consuming code in ``ops-feature``, ``ops-models``, ``ops-explain``, or
``ops-api``.

Design rationale
----------------
* Using ``abc.ABC`` + ``@abstractmethod`` makes missing implementations a hard
  error at *class definition* time rather than at call time, so integration
  tests catch gaps early (criterion: instantiating ``StorageInterface``
  directly raises ``TypeError``).
* Method signatures use plain Python built-in types and ``dict`` /``list``
  payloads rather than Pydantic models to keep ``ops-store`` decoupled from
  the shared schemas package.  Concrete implementations remain free to accept
  richer types as their internal Pydantic models, provided they respect the
  declared return types.
* All datetime parameters are expected as **UTC-aware** ``datetime`` objects.
  The application layer is responsible for enforcing this; the interface does
  not validate — it declares intent.

Usage
-----
Subclassing::

    from ops_store.app.storage_interface import StorageInterface

    class MyStore(StorageInterface):
        def write_raw_readings(self, readings): ...
        # ... implement all abstract methods ...

Attempting to instantiate the base class directly raises ``TypeError``::

    StorageInterface()  # raises TypeError: Can't instantiate abstract class ...
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any


class StorageInterface(ABC):
    """Abstract storage interface for all OilOps-PredictiveCore persistence.

    All methods are abstract; no concrete behaviour is provided here.
    Concrete backends (DuckDB store, SQLite store, and future pluggable
    adapters) must implement every method listed below.

    Parameters carried by each method are documented as plain Python types so
    that consumers are not forced to depend on a specific serialisation library.
    Implementations are expected to accept ``dict``-based payloads produced by
    calling ``.model_dump()`` on the relevant Pydantic schema (e.g.
    ``CanonicalReading``, ``FeatureRecord``, ``PredictionResult``).

    Separation of concerns
    ----------------------
    * ``write_raw_readings`` / ``get_raw_readings_by_asset`` — DuckDB backend
      (TASK-006), raw telemetry time-series (RN-02: immutable after write).
    * ``write_feature_record`` / ``get_feature_records_by_asset`` — DuckDB
      backend (TASK-006), computed feature vectors with idempotency guarantee
      (CAT-13).
    * ``write_prediction`` / ``get_latest_prediction`` — SQLite backend
      (TASK-007), prediction rows written atomically with audit_log (RN-03).
    * ``write_audit_event`` / ``get_audit_log`` — SQLite backend (TASK-007),
      immutable audit trail (INIT-US-08-AC2/AC3).
    """

    # ------------------------------------------------------------------
    # Raw readings (DuckDB -- time-series, immutable after write, RN-02)
    # ------------------------------------------------------------------

    @abstractmethod
    def write_raw_readings(self, readings: list[dict[str, Any]]) -> int:
        """Persist a batch of raw telemetry readings.

        Readings are **immutable** after being written (RN-02).  Each element
        of *readings* must be a ``dict`` that represents a ``CanonicalReading``
        (see ``shared/schemas/canonical.py``).

        Parameters
        ----------
        readings:
            List of canonical reading dicts.  An empty list is a no-op and
            returns ``0``.

        Returns
        -------
        int
            Number of rows actually inserted.

        Raises
        ------
        StorageWriteError
            When the underlying engine reports a write failure.
        """

    @abstractmethod
    def get_raw_readings_by_asset(
        self,
        asset_id: str,
        from_ts: datetime,
        to_ts: datetime,
    ) -> list[dict[str, Any]]:
        """Retrieve raw readings for a given asset within a time window.

        Parameters
        ----------
        asset_id:
            Canonical asset identifier (e.g. ``"PUMP-001"``).
        from_ts:
            Inclusive start of the query window (UTC-aware ``datetime``).
        to_ts:
            Exclusive end of the query window (UTC-aware ``datetime``).

        Returns
        -------
        list[dict[str, Any]]
            List of raw reading dicts ordered by ``timestamp`` ascending.
            Returns an empty list when no rows match.

        Raises
        ------
        StorageReadError
            When the underlying engine reports a read failure.
        """

    # ------------------------------------------------------------------
    # Feature records (DuckDB -- idempotent write via unique index, CAT-13)
    # ------------------------------------------------------------------

    @abstractmethod
    def write_feature_record(self, feature_record: dict[str, Any]) -> bool:
        """Persist a computed feature record for an asset time-window.

        Writes are **idempotent**: if a record with the same
        ``(asset_id, window_start, window_end, feature_version)`` already
        exists the duplicate is silently ignored (INSERT OR IGNORE) and
        ``False`` is returned (CAT-13 -- concurrent writes safe).

        Parameters
        ----------
        feature_record:
            Dict representation of a ``FeatureRecord`` Pydantic model.
            Must contain ``asset_id``, ``window_start``, ``window_end``,
            ``feature_version``, and the computed feature columns.

        Returns
        -------
        bool
            ``True`` when the record was inserted; ``False`` when a duplicate
            was detected and ignored.

        Raises
        ------
        StorageWriteError
            When the underlying engine reports a write failure for a reason
            other than a duplicate key.
        """

    @abstractmethod
    def get_feature_records_by_asset(
        self,
        asset_id: str,
        from_ts: datetime,
        to_ts: datetime,
    ) -> list[dict[str, Any]]:
        """Retrieve feature records for an asset within a time window.

        Parameters
        ----------
        asset_id:
            Canonical asset identifier.
        from_ts:
            Inclusive start of the query window (UTC-aware ``datetime``).
        to_ts:
            Exclusive end of the query window (UTC-aware ``datetime``).

        Returns
        -------
        list[dict[str, Any]]
            List of feature record dicts ordered by ``window_start``
            ascending.  Returns an empty list when no rows match.

        Raises
        ------
        StorageReadError
            When the underlying engine reports a read failure.
        """

    # ------------------------------------------------------------------
    # Predictions (SQLite -- atomic write with audit_log, RN-03)
    # ------------------------------------------------------------------

    @abstractmethod
    def write_prediction(
        self,
        prediction: dict[str, Any],
        audit_event: dict[str, Any],
    ) -> str:
        """Persist a prediction and its audit event in a single transaction.

        Both *prediction* and *audit_event* must be written atomically (RN-03
        / INIT-US-08-AC3): if either write fails the entire transaction is
        rolled back so no orphaned prediction or audit gap is produced.

        Parameters
        ----------
        prediction:
            Dict representation of the ``predictions`` row.  Must contain at
            minimum: ``id`` (UUID str), ``asset_id``, ``asset_class``,
            ``anomaly_score``, ``confidence_score``, ``alert``, ``model_id``,
            ``model_version``, ``feature_record_id``, ``predicted_at``,
            ``explain_status`` (default ``"pending"``).
        audit_event:
            Dict representation of the ``audit_log`` row to be written in the
            same transaction.  Must contain at minimum: ``id`` (UUID str),
            ``event_type`` (``"prediction_emitted"``), ``triggered_at``,
            ``prediction_id`` (same as ``prediction["id"]``).

        Returns
        -------
        str
            The ``prediction_id`` (UUID string) of the newly persisted row.

        Raises
        ------
        StorageWriteError
            When the transaction fails; both rows are rolled back.
        """

    @abstractmethod
    def get_latest_prediction(self, asset_id: str) -> dict[str, Any] | None:
        """Return the most recent prediction for a given asset.

        Parameters
        ----------
        asset_id:
            Canonical asset identifier.

        Returns
        -------
        dict[str, Any] | None
            The prediction dict with the highest ``predicted_at`` timestamp,
            or ``None`` when no predictions exist for *asset_id*.

        Raises
        ------
        StorageReadError
            When the underlying engine reports a read failure.
        """

    # ------------------------------------------------------------------
    # Audit log (SQLite -- immutable append-only, INIT-US-08-AC2)
    # ------------------------------------------------------------------

    @abstractmethod
    def write_audit_event(self, audit_event: dict[str, Any]) -> str:
        """Append a single audit event outside of a prediction transaction.

        Use this method for non-prediction events (e.g. ``model_deployed``,
        ``ingestion_received``).  For prediction events, prefer
        :meth:`write_prediction` which writes both rows atomically.

        Parameters
        ----------
        audit_event:
            Dict representation of the ``audit_log`` row.  Must contain at
            minimum: ``id`` (UUID str), ``event_type``, ``triggered_at``.

        Returns
        -------
        str
            The ``id`` (UUID string) of the newly inserted audit event.

        Raises
        ------
        StorageWriteError
            When the underlying engine reports a write failure.
        """

    @abstractmethod
    def get_audit_log(
        self,
        *,
        asset_id: str | None = None,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        """Retrieve a paginated slice of the audit log.

        All filter parameters are optional; omitting them returns the full
        audit log (subject to pagination).

        Parameters
        ----------
        asset_id:
            When provided, only events with a matching ``asset_id`` are
            returned.
        from_ts:
            Inclusive lower bound on ``triggered_at`` (UTC-aware
            ``datetime``).  ``None`` means no lower bound.
        to_ts:
            Exclusive upper bound on ``triggered_at`` (UTC-aware
            ``datetime``).  ``None`` means no upper bound.
        page:
            1-based page number.  Defaults to ``1``.
        page_size:
            Maximum number of events per page.  Defaults to ``50``.

        Returns
        -------
        dict[str, Any]
            A dict with the following keys:

            * ``events`` -- ``list[dict]`` of audit event rows.
            * ``total`` -- ``int`` total number of rows matching the filters.
            * ``page`` -- ``int`` current page number.
            * ``page_size`` -- ``int`` page size used.

        Raises
        ------
        StorageReadError
            When the underlying engine reports a read failure.
        """
