"""
ops-store/app/db/sqlite_store.py
==================================
Concrete SQLite implementation of ``StorageInterface`` for the metadata
tables managed by the ``ops-store`` service:

    * ``assets``           — equipment asset registry (auto-registration, INIT-03)
    * ``ingestion_batches``— one row per POST /telemetry call
    * ``predictions``      — every prediction emitted by ops-models
    * ``explain_results``  — SHAP attributions when ready (RN-04)
    * ``model_versions``   — artefact registry with rollback support (INIT-US-07-AC2)
    * ``audit_log``        — immutable event trail (INIT-US-08-AC2/AC3)

CRITICAL — Transactional guarantee (RN-03 / INIT-US-08-AC3)
------------------------------------------------------------
``write_prediction`` inserts into both ``predictions`` AND ``audit_log``
inside a **single transaction**.  If the audit_event INSERT fails for any
reason (constraint violation, type error, etc.) the entire transaction is
rolled back: the prediction row is never persisted.  No orphaned prediction
or audit gap can result from a partial failure.

Design notes
------------
* Uses the ``sqlite3`` standard library only — no SQLAlchemy (per task spec).
* ``PRAGMA foreign_keys = ON`` and ``PRAGMA journal_mode = WAL`` are applied
  to every connection via ``_connect()``.
* Schema is applied from ``001_sqlite_init.sql`` on first construction.
* ``write_raw_readings``, ``get_raw_readings_by_asset``, ``write_feature_record``,
  ``get_feature_records_by_asset`` all raise ``NotImplementedError`` — those
  tables live in DuckDB (TASK-006 / DA-02).
* ``get_latest_prediction`` returns ``None`` (never raises) when the asset has
  no predictions (INIT-US-02-AC2).
* Connection strategy: for in-memory databases (``":memory:"`` or
  ``"file::memory:?cache=shared"``), a single persistent connection is kept for
  the lifetime of the ``SQLiteStore`` instance, because each new
  ``sqlite3.connect(":memory:")`` call opens an *independent* empty database.
  For file-based databases a new connection is opened per operation (and
  closed immediately) to allow concurrent access via WAL mode.
* ``_connect()`` is exposed as a public helper so tests can inspect the
  in-memory database directly.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Generator

from ops_store.app.storage_interface import StorageInterface

# Path to the DDL migration relative to this file.
_MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_SQLITE_DDL = _MIGRATIONS_DIR / "001_sqlite_init.sql"


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    """Enable foreign-key enforcement and WAL journal mode on *conn*."""
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")


class SQLiteStore(StorageInterface):
    """SQLite-backed storage for metadata tables in ``ops-store``.

    Parameters
    ----------
    db_path:
        Filesystem path to the SQLite database file.  Pass ``":memory:"`` for
        an in-process, zero-persistence instance (used in all unit tests).
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self._db_path = db_path
        self._is_memory = db_path == ":memory:"

        # For in-memory databases we maintain one persistent connection so that
        # all operations share the same ephemeral database.  For file databases
        # we open a new connection per operation via _connect().
        if self._is_memory:
            self._mem_conn: sqlite3.Connection | None = sqlite3.connect(
                ":memory:", check_same_thread=False
            )
            self._mem_conn.row_factory = sqlite3.Row
            _apply_pragmas(self._mem_conn)
        else:
            self._mem_conn = None

        self._apply_schema()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        """Yield a database connection with PRAGMAs applied.

        For in-memory databases the singleton ``_mem_conn`` is yielded
        directly (caller must not close it).  For file databases a new
        connection is opened, yielded, and closed when the context exits.

        Usage::

            with self._connect() as conn:
                conn.execute(...)
                conn.commit()
        """
        if self._is_memory:
            # Yield the persistent in-memory connection.  The caller manages
            # commit/rollback explicitly; we do NOT close it here.
            assert self._mem_conn is not None
            yield self._mem_conn
        else:
            conn = sqlite3.connect(self._db_path, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            _apply_pragmas(conn)
            try:
                yield conn
            finally:
                conn.close()

    def _apply_schema(self) -> None:
        """Run the DDL migration script to create tables if they do not exist.

        The migration uses ``CREATE TABLE IF NOT EXISTS`` so this is safe to
        call on every construction (idempotent).

        ``executescript`` is used for multi-statement DDL; it commits any
        pending transaction before running, which is acceptable here because
        schema creation always precedes any data operations.
        """
        ddl = _SQLITE_DDL.read_text(encoding="utf-8")
        with self._connect() as conn:
            conn.executescript(ddl)

    @staticmethod
    def _resolve_prediction_id(prediction: dict[str, Any]) -> str:
        """Return the canonical prediction id from ``"id"`` or ``"prediction_id"`` key.

        The upstream dict may use either key depending on which service
        produced it (some use Pydantic field alias ``prediction_id``).  We
        accept both and always store under the ``id`` column.

        Raises
        ------
        KeyError
            When neither ``"id"`` nor ``"prediction_id"`` is present.
        """
        if "id" in prediction:
            return str(prediction["id"])
        if "prediction_id" in prediction:
            return str(prediction["prediction_id"])
        raise KeyError(
            "prediction dict must contain either 'id' or 'prediction_id'"
        )

    # ------------------------------------------------------------------
    # Public helper methods (used by application layer / tests for setup)
    # ------------------------------------------------------------------

    def register_asset(
        self,
        asset_id: str,
        asset_class: str,
        registered_at: str,
        metadata: str | None,
    ) -> None:
        """Upsert an asset row (auto-registration, INIT-03).

        Uses INSERT OR IGNORE so that re-registering an existing asset is a
        safe no-op.  The application normalizer calls this before writing
        raw_readings so that the FK from ``predictions.asset_id`` is always
        satisfied.
        """
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO assets
                    (id, asset_class, registered_at, metadata)
                VALUES (?, ?, ?, ?)
                """,
                (asset_id, asset_class, registered_at, metadata),
            )
            conn.commit()

    def register_model_version(
        self,
        model_id: str,
        version: str,
        asset_class: str,
        artifact_path: str,
        artifact_format: str,
        deployed_at: str,
        anomaly_threshold: float,
        severity_thresholds: str,
        deployed_by: str | None = None,
        is_active: int = 1,
    ) -> None:
        """Insert a model_versions row (INIT-US-07-AC1).

        Uses INSERT OR IGNORE so re-deploying the same model_id is safe.
        """
        with self._connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO model_versions
                    (id, version, asset_class, artifact_path, artifact_format,
                     deployed_at, is_active, deployed_by, anomaly_threshold,
                     severity_thresholds)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    model_id, version, asset_class, artifact_path,
                    artifact_format, deployed_at, is_active, deployed_by,
                    anomaly_threshold, severity_thresholds,
                ),
            )
            conn.commit()

    # ------------------------------------------------------------------
    # StorageInterface — DuckDB-owned methods (raise NotImplementedError)
    # ------------------------------------------------------------------

    def write_raw_readings(self, readings: list[dict[str, Any]]) -> int:
        """Not implemented on SQLiteStore — handled by DuckDBStore (TASK-006)."""
        raise NotImplementedError(
            "write_raw_readings is the responsibility of DuckDBStore. "
            "SQLiteStore manages only metadata tables (assets, predictions, "
            "audit_log, model_versions, ingestion_batches, explain_results)."
        )

    def get_raw_readings_by_asset(
        self,
        asset_id: str,
        from_ts: datetime,
        to_ts: datetime,
    ) -> list[dict[str, Any]]:
        """Not implemented on SQLiteStore — handled by DuckDBStore (TASK-006)."""
        raise NotImplementedError(
            "get_raw_readings_by_asset is the responsibility of DuckDBStore."
        )

    def write_feature_record(self, feature_record: dict[str, Any]) -> bool:
        """Not implemented on SQLiteStore — handled by DuckDBStore (TASK-006)."""
        raise NotImplementedError(
            "write_feature_record is the responsibility of DuckDBStore."
        )

    def get_feature_records_by_asset(
        self,
        asset_id: str,
        from_ts: datetime,
        to_ts: datetime,
    ) -> list[dict[str, Any]]:
        """Not implemented on SQLiteStore — handled by DuckDBStore (TASK-006)."""
        raise NotImplementedError(
            "get_feature_records_by_asset is the responsibility of DuckDBStore."
        )

    # ------------------------------------------------------------------
    # StorageInterface — Predictions (SQLite, atomic with audit_log, RN-03)
    # ------------------------------------------------------------------

    def write_prediction(
        self,
        prediction: dict[str, Any],
        audit_event: dict[str, Any],
    ) -> str:
        """Persist *prediction* and *audit_event* in a single transaction.

        CRITICAL (RN-03): both INSERTs share one ``sqlite3`` connection
        transaction.  If the audit_event INSERT raises **any** exception the
        connection is explicitly rolled back via ``conn.rollback()``,
        guaranteeing that the prediction row is also absent after the failure.

        Parameters
        ----------
        prediction:
            Dict with prediction fields.  Accepts either ``"id"`` or
            ``"prediction_id"`` as the primary-key key.
        audit_event:
            Dict with audit_log fields.  Must include ``"id"`` and
            ``"event_type"``.

        Returns
        -------
        str
            The prediction id (UUID string).

        Raises
        ------
        Exception
            Propagates any exception from either INSERT after rolling back the
            entire transaction so no partial state is left in the database.
        """
        pred_id = self._resolve_prediction_id(prediction)

        with self._connect() as conn:
            try:
                # Disable autocommit-style implicit transaction management so
                # we control the transaction boundaries explicitly.
                conn.execute("BEGIN")

                # ---- Insert prediction row --------------------------------
                conn.execute(
                    """
                    INSERT INTO predictions
                        (id, asset_id, asset_class, anomaly_score,
                         confidence_score, alert, severity, model_id,
                         model_version, feature_record_id, predicted_at,
                         explain_status)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        pred_id,
                        prediction.get("asset_id"),
                        prediction.get("asset_class"),
                        prediction.get("anomaly_score"),
                        prediction.get("confidence_score"),
                        prediction.get("alert"),
                        prediction.get("severity"),
                        prediction.get("model_id"),
                        prediction.get("model_version"),
                        prediction.get("feature_record_id"),
                        prediction.get("predicted_at"),
                        prediction.get("explain_status", "pending"),
                    ),
                )

                # ---- Insert audit_log row (MUST succeed for commit) -------
                # event_type has a NOT NULL constraint; passing None here will
                # raise sqlite3.IntegrityError which triggers the rollback
                # in the except clause below.
                conn.execute(
                    """
                    INSERT INTO audit_log
                        (id, event_type, prediction_id, asset_id,
                         model_version, triggered_at, confidence_score,
                         trace_id, details)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        audit_event.get("id"),
                        audit_event.get("event_type"),
                        audit_event.get("prediction_id"),
                        audit_event.get("asset_id"),
                        audit_event.get("model_version"),
                        audit_event.get("triggered_at"),
                        audit_event.get("confidence_score"),
                        audit_event.get("trace_id"),
                        audit_event.get("details"),
                    ),
                )

                conn.execute("COMMIT")

            except Exception:
                conn.execute("ROLLBACK")
                raise

        return pred_id

    def get_latest_prediction(self, asset_id: str) -> dict[str, Any] | None:
        """Return the most recent prediction for *asset_id*, or ``None``.

        Never raises for a missing asset — callers rely on the ``None``
        sentinel to issue HTTP 404 without error propagation (INIT-US-02-AC2).

        Returns
        -------
        dict[str, Any] | None
            Row as a dict (column-name keys) or ``None`` if no rows exist.
        """
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT id, asset_id, asset_class, anomaly_score,
                       confidence_score, alert, severity, model_id,
                       model_version, feature_record_id, predicted_at,
                       explain_status
                FROM predictions
                WHERE asset_id = ?
                ORDER BY predicted_at DESC
                LIMIT 1
                """,
                (asset_id,),
            ).fetchone()

        if row is None:
            return None

        return dict(row)

    # ------------------------------------------------------------------
    # StorageInterface — Audit log (SQLite, immutable append-only)
    # ------------------------------------------------------------------

    def write_audit_event(self, audit_event: dict[str, Any]) -> str:
        """Append a single audit event outside a prediction transaction.

        Use for non-prediction events such as ``model_deployed`` or
        ``ingestion_received``.  For prediction events prefer
        :meth:`write_prediction` which guarantees atomicity.

        Returns
        -------
        str
            The ``id`` (UUID string) of the newly inserted row.
        """
        event_id = str(audit_event["id"])

        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO audit_log
                    (id, event_type, prediction_id, asset_id,
                     model_version, triggered_at, confidence_score,
                     trace_id, details)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    audit_event.get("event_type"),
                    audit_event.get("prediction_id"),
                    audit_event.get("asset_id"),
                    audit_event.get("model_version"),
                    audit_event.get("triggered_at"),
                    audit_event.get("confidence_score"),
                    audit_event.get("trace_id"),
                    audit_event.get("details"),
                ),
            )
            conn.commit()

        return event_id

    def get_audit_log(
        self,
        *,
        asset_id: str | None = None,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        """Return a paginated slice of the audit log.

        All filters are optional; missing filters include all rows.

        Parameters
        ----------
        asset_id:
            Filter by asset; ``None`` includes all assets.
        from_ts:
            Inclusive lower bound on ``triggered_at``; ``None`` means no
            lower bound.
        to_ts:
            Exclusive upper bound on ``triggered_at``; ``None`` means no
            upper bound.
        page:
            1-based page number.
        page_size:
            Number of rows per page.

        Returns
        -------
        dict with keys ``events``, ``total``, ``page``, ``page_size``.
        """
        conditions: list[str] = []
        params: list[Any] = []

        if asset_id is not None:
            conditions.append("asset_id = ?")
            params.append(asset_id)

        if from_ts is not None:
            conditions.append("triggered_at >= ?")
            params.append(from_ts.isoformat())

        if to_ts is not None:
            conditions.append("triggered_at < ?")
            params.append(to_ts.isoformat())

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        offset = (page - 1) * page_size

        with self._connect() as conn:
            total_row = conn.execute(
                f"SELECT COUNT(*) AS cnt FROM audit_log {where_clause}",
                params,
            ).fetchone()
            total: int = total_row["cnt"] if total_row else 0

            rows = conn.execute(
                f"""
                SELECT id, event_type, prediction_id, asset_id,
                       model_version, triggered_at, confidence_score,
                       trace_id, details
                FROM audit_log
                {where_clause}
                ORDER BY triggered_at DESC
                LIMIT ? OFFSET ?
                """,
                params + [page_size, offset],
            ).fetchall()

        events = [dict(row) for row in rows]

        return {
            "events": events,
            "total": total,
            "page": page,
            "page_size": page_size,
        }
