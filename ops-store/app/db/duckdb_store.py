"""
ops-store/app/db/duckdb_store.py
==================================
Concrete DuckDB implementation of ``StorageInterface`` for OilOps-PredictiveCore.

Responsibilities (TASK-006)
----------------------------
* ``write_raw_readings``      — batch-insert canonical telemetry rows into
                                ``raw_readings``; returns the count of inserted
                                rows.
* ``get_raw_readings_by_asset`` — query ``raw_readings`` by ``asset_id`` and
                                  UTC time window ``[from_ts, to_ts)``.
* ``write_feature_record``    — idempotent single-row upsert into
                                ``feature_records`` via INSERT OR IGNORE on the
                                unique index
                                ``(asset_id, window_start, window_end,
                                feature_version)``; returns ``True`` when a new
                                row is inserted, ``False`` when a duplicate is
                                silently dropped (CAT-13).
* ``get_feature_records_by_asset`` — query ``feature_records`` by ``asset_id``
                                     and UTC time window matched against
                                     ``window_start``.

SQLite-only methods (TASK-007)
-------------------------------
``write_prediction``, ``get_latest_prediction``, ``write_audit_event``, and
``get_audit_log`` all raise ``NotImplementedError``.  These are implemented
by ``SQLiteStore`` (TASK-007) and must never be called on this class.

Design notes
------------
* DuckDB is opened **in-process** (embedded) — no external server required
  (RN-08 autossuficiência de deployment).
* The DDL is applied from ``001_duckdb_init.sql`` on first use, using
  ``CREATE TABLE IF NOT EXISTS`` / ``CREATE INDEX IF NOT EXISTS`` so that
  repeated construction is idempotent (safe for tests using ``:memory:``).
* Thread safety: DuckDB supports multiple reader-threads on the same
  connection; for concurrent writers in Fase 1 (batch ingestion model) a
  single shared connection is sufficient.  If concurrent writers become a
  concern in Fase 2 the connection can be replaced by a connection-pool.
* All datetime parameters received from callers are expected to be
  **UTC-aware** ``datetime`` objects.  They are serialised to ISO 8601
  strings for the parameterised query (DuckDB TIMESTAMPTZ accepts ISO 8601).
* Return values are plain ``list[dict]`` built from ``duckdb.DuckDBPyRelation``
  using ``.fetchdf()`` + ``.to_dict(orient="records")`` to stay framework-
  agnostic and honour the ``StorageInterface`` contract.

Usage
-----
::

    from ops_store.app.db.duckdb_store import DuckDBStore

    store = DuckDBStore(db_path=":memory:")          # in-memory (tests)
    store = DuckDBStore(db_path="/data/oilops.ddb")  # file-backed (production)

    count = store.write_raw_readings(readings)       # int
    rows  = store.get_raw_readings_by_asset(...)     # list[dict]
    new   = store.write_feature_record(feature)      # bool
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

from ops_store.app.storage_interface import StorageInterface

logger = logging.getLogger(__name__)

# Path to the DDL migration that creates the DuckDB tables.
_MIGRATION_SQL_PATH = (
    Path(__file__).parent / "migrations" / "001_duckdb_init.sql"
)

# Ordered column list for raw_readings INSERT (matches DDL column order).
_RAW_READINGS_COLUMNS = (
    "id",
    "asset_id",
    "timestamp",
    "metric_name",
    "value",
    "unit",
    "source_protocol",
    "ingested_at",
    "ingestion_id",
    "is_backfill",
)

# Ordered column list for feature_records INSERT (matches DDL column order).
_FEATURE_RECORDS_COLUMNS = (
    "id",
    "asset_id",
    "window_start",
    "window_end",
    "raw_record_ids",
    "feature_version",
    "rms",
    "variance",
    "kurtosis",
    "skewness",
    "fft_bins",
    "computed_at",
)


class DuckDBStore(StorageInterface):
    """Concrete DuckDB storage backend for raw_readings and feature_records.

    Parameters
    ----------
    db_path:
        DuckDB database path.  Use ``":memory:"`` for in-process ephemeral
        storage (default for tests).  A filesystem path creates a persistent
        file-backed database.

    Raises
    ------
    duckdb.Error
        Propagated if DuckDB fails to open the connection or execute the
        migration DDL.
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self._db_path = db_path
        self._conn: duckdb.DuckDBPyConnection = duckdb.connect(db_path)
        self._apply_migrations()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _apply_migrations(self) -> None:
        """Execute the DuckDB DDL migration script against the open connection.

        Uses ``CREATE TABLE IF NOT EXISTS`` / ``CREATE INDEX IF NOT EXISTS`` so
        that re-running against an already-initialised database is safe.
        """
        sql = _MIGRATION_SQL_PATH.read_text(encoding="utf-8")
        self._conn.executemany if False else self._conn.execute(sql)
        logger.debug(
            "DuckDBStore: migration applied",
            extra={"db_path": self._db_path},
        )

    @staticmethod
    def _row_to_dict(row: tuple, columns: tuple[str, ...]) -> dict[str, Any]:
        """Convert a DuckDB result row tuple to a ``dict``."""
        return dict(zip(columns, row))

    # ------------------------------------------------------------------
    # Raw readings — RN-02 (immutable after write)
    # ------------------------------------------------------------------

    def write_raw_readings(self, readings: list[dict[str, Any]]) -> int:
        """Persist a batch of raw telemetry readings into ``raw_readings``.

        Parameters
        ----------
        readings:
            List of canonical reading dicts.  Each dict must contain all
            columns declared in ``_RAW_READINGS_COLUMNS``.  An empty list is a
            no-op.

        Returns
        -------
        int
            Number of rows actually inserted.

        Raises
        ------
        duckdb.Error
            Propagated on write failure (e.g. duplicate primary key).
        """
        if not readings:
            return 0

        placeholders = ", ".join(["?"] * len(_RAW_READINGS_COLUMNS))
        insert_sql = (
            f"INSERT INTO raw_readings "
            f"({', '.join(_RAW_READINGS_COLUMNS)}) "
            f"VALUES ({placeholders})"
        )

        params_batch = [
            tuple(_coerce_value(r.get(col)) for col in _RAW_READINGS_COLUMNS)
            for r in readings
        ]

        self._conn.executemany(insert_sql, params_batch)
        inserted = len(readings)
        logger.debug(
            "DuckDBStore: write_raw_readings",
            extra={"count": inserted},
        )
        return inserted

    def get_raw_readings_by_asset(
        self,
        asset_id: str,
        from_ts: datetime,
        to_ts: datetime,
    ) -> list[dict[str, Any]]:
        """Retrieve raw readings for a given asset within a UTC time window.

        The window is **inclusive** on ``from_ts`` and **exclusive** on
        ``to_ts`` (i.e. ``[from_ts, to_ts)``).  Results are ordered by
        ``timestamp`` ascending.

        Parameters
        ----------
        asset_id:
            Canonical asset identifier.
        from_ts:
            Inclusive start of the window (UTC-aware ``datetime``).
        to_ts:
            Exclusive end of the window (UTC-aware ``datetime``).

        Returns
        -------
        list[dict[str, Any]]
            List of raw reading dicts.  Empty list when no rows match.
        """
        query = """
            SELECT
                id, asset_id, timestamp, metric_name, value, unit,
                source_protocol, ingested_at, ingestion_id, is_backfill
            FROM raw_readings
            WHERE asset_id = ?
              AND timestamp >= ?::TIMESTAMPTZ
              AND timestamp <  ?::TIMESTAMPTZ
            ORDER BY timestamp ASC
        """
        result = self._conn.execute(
            query,
            [asset_id, _ts_str(from_ts), _ts_str(to_ts)],
        ).fetchall()

        columns = _RAW_READINGS_COLUMNS
        return [self._row_to_dict(row, columns) for row in result]

    # ------------------------------------------------------------------
    # Feature records — idempotent via unique index (CAT-13)
    # ------------------------------------------------------------------

    def write_feature_record(self, feature_record: dict[str, Any]) -> bool:
        """Persist a computed feature record with idempotency guarantee.

        Uses ``INSERT OR IGNORE`` backed by the unique index
        ``uq_feature_records_window`` on
        ``(asset_id, window_start, window_end, feature_version)``.

        Parameters
        ----------
        feature_record:
            Dict with all columns in ``_FEATURE_RECORDS_COLUMNS``.

        Returns
        -------
        bool
            ``True`` when the row was inserted; ``False`` when a duplicate was
            silently ignored.

        Raises
        ------
        duckdb.Error
            Propagated on write failure for reasons other than a duplicate key.
        """
        placeholders = ", ".join(["?"] * len(_FEATURE_RECORDS_COLUMNS))
        insert_sql = (
            f"INSERT OR IGNORE INTO feature_records "
            f"({', '.join(_FEATURE_RECORDS_COLUMNS)}) "
            f"VALUES ({placeholders})"
        )

        params = tuple(
            _coerce_value(feature_record.get(col))
            for col in _FEATURE_RECORDS_COLUMNS
        )

        # Count rows before to detect whether the INSERT OR IGNORE actually
        # inserted (DuckDB does not expose a reliable changes() equivalent).
        before: int = self._conn.execute(
            "SELECT COUNT(*) FROM feature_records "
            "WHERE asset_id = ? "
            "  AND window_start = ?::TIMESTAMPTZ "
            "  AND window_end   = ?::TIMESTAMPTZ "
            "  AND feature_version = ?",
            [
                feature_record.get("asset_id"),
                _coerce_value(feature_record.get("window_start")),
                _coerce_value(feature_record.get("window_end")),
                feature_record.get("feature_version"),
            ],
        ).fetchone()[0]  # type: ignore[index]

        self._conn.execute(insert_sql, params)

        after: int = self._conn.execute(
            "SELECT COUNT(*) FROM feature_records "
            "WHERE asset_id = ? "
            "  AND window_start = ?::TIMESTAMPTZ "
            "  AND window_end   = ?::TIMESTAMPTZ "
            "  AND feature_version = ?",
            [
                feature_record.get("asset_id"),
                _coerce_value(feature_record.get("window_start")),
                _coerce_value(feature_record.get("window_end")),
                feature_record.get("feature_version"),
            ],
        ).fetchone()[0]  # type: ignore[index]

        inserted = after > before
        logger.debug(
            "DuckDBStore: write_feature_record",
            extra={
                "asset_id": feature_record.get("asset_id"),
                "inserted": inserted,
            },
        )
        return inserted

    def get_feature_records_by_asset(
        self,
        asset_id: str,
        from_ts: datetime,
        to_ts: datetime,
    ) -> list[dict[str, Any]]:
        """Retrieve feature records for an asset within a UTC time window.

        The window is matched against ``window_start`` using the same
        ``[from_ts, to_ts)`` inclusive-exclusive semantics as
        :meth:`get_raw_readings_by_asset`.  Results are ordered by
        ``window_start`` ascending.

        Parameters
        ----------
        asset_id:
            Canonical asset identifier.
        from_ts:
            Inclusive start of the window (UTC-aware ``datetime``).
        to_ts:
            Exclusive end of the window (UTC-aware ``datetime``).

        Returns
        -------
        list[dict[str, Any]]
            List of feature record dicts.  Empty list when no rows match.
        """
        query = """
            SELECT
                id, asset_id, window_start, window_end, raw_record_ids,
                feature_version, rms, variance, kurtosis, skewness,
                fft_bins, computed_at
            FROM feature_records
            WHERE asset_id = ?
              AND window_start >= ?::TIMESTAMPTZ
              AND window_start <  ?::TIMESTAMPTZ
            ORDER BY window_start ASC
        """
        result = self._conn.execute(
            query,
            [asset_id, _ts_str(from_ts), _ts_str(to_ts)],
        ).fetchall()

        columns = _FEATURE_RECORDS_COLUMNS
        return [self._row_to_dict(row, columns) for row in result]

    # ------------------------------------------------------------------
    # SQLite-only methods — delegated to SQLiteStore (TASK-007)
    # ------------------------------------------------------------------

    def write_prediction(
        self,
        prediction: dict[str, Any],
        audit_event: dict[str, Any],
    ) -> str:
        """Not implemented — delegated to SQLiteStore (TASK-007)."""
        raise NotImplementedError(
            "write_prediction is implemented by SQLiteStore (TASK-007), "
            "not DuckDBStore.  Use SQLiteStore for prediction persistence."
        )

    def get_latest_prediction(self, asset_id: str) -> dict[str, Any] | None:
        """Not implemented — delegated to SQLiteStore (TASK-007)."""
        raise NotImplementedError(
            "get_latest_prediction is implemented by SQLiteStore (TASK-007), "
            "not DuckDBStore."
        )

    def write_audit_event(self, audit_event: dict[str, Any]) -> str:
        """Not implemented — delegated to SQLiteStore (TASK-007)."""
        raise NotImplementedError(
            "write_audit_event is implemented by SQLiteStore (TASK-007), "
            "not DuckDBStore."
        )

    def get_audit_log(
        self,
        *,
        asset_id: str | None = None,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> dict[str, Any]:
        """Not implemented — delegated to SQLiteStore (TASK-007)."""
        raise NotImplementedError(
            "get_audit_log is implemented by SQLiteStore (TASK-007), "
            "not DuckDBStore."
        )

    # ------------------------------------------------------------------
    # Resource management
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the underlying DuckDB connection."""
        self._conn.close()

    def __enter__(self) -> "DuckDBStore":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Module-level helpers (private)
# ---------------------------------------------------------------------------


def _ts_str(ts: datetime) -> str:
    """Serialise a ``datetime`` to an ISO 8601 string for DuckDB parameters.

    DuckDB TIMESTAMPTZ accepts ISO 8601 strings with timezone offset.
    """
    return ts.isoformat()


def _coerce_value(value: Any) -> Any:
    """Coerce a Python value to a DuckDB-compatible scalar.

    * ``datetime`` objects are serialised to ISO 8601 strings.
    * ``list`` and ``dict`` objects are serialised to JSON strings (stored as
      DuckDB JSON columns).
    * All other types are passed through unchanged.
    """
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    return value
