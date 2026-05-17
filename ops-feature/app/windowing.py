"""
ops-feature/app/windowing.py
==============================
WindowingPipeline — sliding-window feature engineering pipeline for
OilOps-PredictiveCore (TASK-014 / INIT-US-06 / CAT-13).

Responsibilities
----------------
1. Read ``raw_readings`` for a given ``asset_id`` from a
   :class:`~ops_store.app.storage_interface.StorageInterface` implementation.
2. Partition the readings into non-overlapping windows of configurable size
   (``window_size``, default: ``Settings.feature_window_size`` = 64 samples).
3. For each window that has enough samples, call
   :class:`~ops_feature.app.extractors.vibration.VibrationFeatureExtractor`
   to compute the feature vector.
4. Persist each computed feature record via
   :meth:`~ops_store.app.storage_interface.StorageInterface.write_feature_record`
   using INSERT OR IGNORE semantics (idempotency guaranteed by the unique
   index ``(asset_id, window_start, window_end, feature_version)`` in DuckDB —
   CAT-13).
5. Guarantee asset-level isolation (INIT-US-06-AC4): an exception raised while
   processing one asset must not propagate to callers processing other assets.

Usage
-----
::

    from ops_store.app.db.duckdb_store import DuckDBStore
    from ops_feature.app.windowing import WindowingPipeline

    store = DuckDBStore(db_path=":memory:")
    pipeline = WindowingPipeline(store=store)
    stats = pipeline.run(asset_id="PUMP-001")
    print(stats)
    # {"asset_id": "PUMP-001", "windows_processed": 2, "records_written": 2,
    #  "records_skipped": 0, "windows_too_small": 0}

Idempotency
-----------
Running the pipeline twice for the same asset and time range produces exactly
the same number of feature records as running it once.  The second run returns
``records_written=0`` for all duplicate windows (INSERT OR IGNORE swallows the
conflict silently).

Design decisions
----------------
- The pipeline accepts ``StorageInterface`` by constructor injection, making
  it fully testable with an in-memory DuckDB instance and easily replaceable
  with any future backend (InfluxDB, TimescaleDB — DA-02).
- Windows are formed as **contiguous, non-overlapping** slices of the raw
  readings sorted by ``timestamp``.  The final incomplete window (< window_size
  samples) is silently discarded — this is consistent with INIT-US-06-AC2
  (``VibrationFeatureExtractor`` returns ``None`` for short windows).
- The ``from_ts`` / ``to_ts`` query window defaults to the epoch and the
  far future so that **all** available readings are processed when the caller
  does not supply explicit bounds.
- ``feature_version`` is a class-level constant so that all records produced
  by this version of the pipeline share the same version string and the unique
  index on ``(asset_id, window_start, window_end, feature_version)`` correctly
  distinguishes records produced by different pipeline releases.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import numpy as np

from shared.config import Settings
from shared.logging_config import get_logger
from ops_feature.app.extractors.vibration import VibrationFeatureExtractor
from ops_feature.app.schemas import FeatureRecord
from ops_store.app.storage_interface import StorageInterface

_logger = get_logger("ops_feature.app.windowing")

# Version string embedded in every feature record produced by this pipeline.
# Bumping this value causes new feature records to be written for all
# previously-processed windows (old records remain untouched — RN-02).
FEATURE_VERSION: str = "1.0.0"

# Sentinel datetimes used when the caller does not supply explicit query bounds.
_EPOCH_UTC: datetime = datetime(1970, 1, 1, tzinfo=timezone.utc)
_FAR_FUTURE_UTC: datetime = datetime(9999, 12, 31, 23, 59, 59, tzinfo=timezone.utc)


class WindowingPipeline:
    """Sliding-window feature engineering pipeline for vibration signals.

    Parameters
    ----------
    store:
        Any concrete implementation of
        :class:`~ops_store.app.storage_interface.StorageInterface`.
        Injected at construction time for testability and backend portability.
    window_size:
        Number of raw readings per feature window.  Defaults to
        ``Settings().feature_window_size`` (64 samples from the environment,
        mapping to ``OILOPS_FEATURE_WINDOW_SIZE``).
    fft_bins:
        Number of FFT frequency bins in the feature vector.  Defaults to
        ``Settings().fft_bins`` (64 bins from the environment, mapping to
        ``OILOPS_FFT_BINS``).
    feature_version:
        Version label embedded in every written feature record.  Used by the
        DuckDB unique index to distinguish records from different pipeline
        releases (defaults to module-level :data:`FEATURE_VERSION`).
    """

    def __init__(
        self,
        store: StorageInterface,
        window_size: int | None = None,
        fft_bins: int | None = None,
        feature_version: str = FEATURE_VERSION,
    ) -> None:
        settings = Settings()
        self._store = store
        self._window_size: int = window_size if window_size is not None else settings.feature_window_size  # noqa: E501
        self._fft_bins: int = fft_bins if fft_bins is not None else settings.fft_bins
        self._feature_version = feature_version
        self._extractor = VibrationFeatureExtractor(
            fft_bins=self._fft_bins,
            min_window_size=self._window_size,
        )

        _logger.info(
            "WindowingPipeline initialised",
            extra={
                "window_size": self._window_size,
                "fft_bins": self._fft_bins,
                "feature_version": self._feature_version,
            },
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(
        self,
        asset_id: str,
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
    ) -> dict[str, Any]:
        """Execute the windowing pipeline for a single asset.

        Reads all raw readings for *asset_id* in the time range
        ``[from_ts, to_ts)``, partitions them into non-overlapping windows
        of ``window_size`` samples, computes features for each window that
        has enough samples, and persists the resulting feature records.

        Implements asset-level isolation (INIT-US-06-AC4): any exception
        raised during processing is caught, logged with ``ERROR`` severity,
        and a result dict with ``error`` key is returned — the exception does
        **not** propagate to the caller.

        Parameters
        ----------
        asset_id:
            Canonical identifier of the asset to process.
        from_ts:
            Inclusive start of the raw-readings query window (UTC-aware).
            Defaults to the Unix epoch when ``None``.
        to_ts:
            Exclusive end of the raw-readings query window (UTC-aware).
            Defaults to far-future sentinel when ``None``.

        Returns
        -------
        dict[str, Any]
            Statistics dict with keys:

            * ``asset_id`` — str, the asset that was processed.
            * ``windows_processed`` — int, number of windows attempted.
            * ``records_written`` — int, new feature records inserted.
            * ``records_skipped`` — int, duplicate windows (idempotent).
            * ``windows_too_small`` — int, windows skipped (<window_size).
            * ``error`` — str (only present when the pipeline raised an
              unrecoverable exception; all other counters will be 0).
        """
        _from_ts = from_ts if from_ts is not None else _EPOCH_UTC
        _to_ts = to_ts if to_ts is not None else _FAR_FUTURE_UTC

        _logger.info(
            "WindowingPipeline.run started",
            extra={"asset_id": asset_id, "from_ts": _from_ts.isoformat(), "to_ts": _to_ts.isoformat()},
        )

        try:
            return self._process_asset(asset_id, _from_ts, _to_ts)
        except Exception as exc:  # noqa: BLE001 — asset isolation: catch-all
            _logger.error(
                "WindowingPipeline.run failed — asset isolated",
                extra={"asset_id": asset_id, "error": str(exc)},
                exc_info=True,
            )
            return {
                "asset_id": asset_id,
                "windows_processed": 0,
                "records_written": 0,
                "records_skipped": 0,
                "windows_too_small": 0,
                "error": str(exc),
            }

    def run_many(
        self,
        asset_ids: list[str],
        from_ts: datetime | None = None,
        to_ts: datetime | None = None,
    ) -> list[dict[str, Any]]:
        """Execute the pipeline for multiple assets sequentially.

        Asset isolation (INIT-US-06-AC4) is enforced: a failure for one
        asset does not prevent the remaining assets from being processed.

        Parameters
        ----------
        asset_ids:
            List of canonical asset identifiers to process.
        from_ts:
            Shared inclusive start of the raw-readings query window.
        to_ts:
            Shared exclusive end of the raw-readings query window.

        Returns
        -------
        list[dict[str, Any]]
            One result dict per asset (same structure as :meth:`run`).
        """
        results: list[dict[str, Any]] = []
        for asset_id in asset_ids:
            result = self.run(asset_id, from_ts=from_ts, to_ts=to_ts)
            results.append(result)
            if "error" in result:
                _logger.warning(
                    "WindowingPipeline.run_many: asset skipped due to error",
                    extra={"asset_id": asset_id, "error": result["error"]},
                )
        return results

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _process_asset(
        self,
        asset_id: str,
        from_ts: datetime,
        to_ts: datetime,
    ) -> dict[str, Any]:
        """Core processing logic for a single asset (no exception handling).

        Called by :meth:`run`; exceptions propagate upward and are caught
        there for asset isolation.
        """
        # 1. Fetch raw readings -------------------------------------------
        raw_readings: list[dict[str, Any]] = self._store.get_raw_readings_by_asset(
            asset_id=asset_id,
            from_ts=from_ts,
            to_ts=to_ts,
        )

        n_readings = len(raw_readings)
        _logger.debug(
            "WindowingPipeline: readings fetched",
            extra={"asset_id": asset_id, "n_readings": n_readings},
        )

        if n_readings == 0:
            return {
                "asset_id": asset_id,
                "windows_processed": 0,
                "records_written": 0,
                "records_skipped": 0,
                "windows_too_small": 0,
            }

        # 2. Partition into windows ----------------------------------------
        windows = self._partition_windows(raw_readings)

        # 3. Process each window -------------------------------------------
        windows_processed = 0
        records_written = 0
        records_skipped = 0
        windows_too_small = 0

        for window_readings in windows:
            windows_processed += 1
            written, skipped, too_small = self._process_window(asset_id, window_readings)
            records_written += written
            records_skipped += skipped
            windows_too_small += too_small

        stats = {
            "asset_id": asset_id,
            "windows_processed": windows_processed,
            "records_written": records_written,
            "records_skipped": records_skipped,
            "windows_too_small": windows_too_small,
        }
        _logger.info("WindowingPipeline.run completed", extra=stats)
        return stats

    def _partition_windows(
        self,
        raw_readings: list[dict[str, Any]],
    ) -> list[list[dict[str, Any]]]:
        """Partition a sorted list of readings into non-overlapping windows.

        The readings are assumed to arrive already sorted by ``timestamp``
        (DuckDB ``ORDER BY timestamp ASC`` in the query).

        Only complete windows of exactly ``window_size`` samples are
        returned.  The trailing incomplete window is silently discarded,
        consistent with INIT-US-06-AC2 (the extractor would return ``None``
        anyway for a window below ``min_window_size``).

        Parameters
        ----------
        raw_readings:
            Ordered list of raw reading dicts for a single asset.

        Returns
        -------
        list[list[dict[str, Any]]]
            List of windows; each window is a list of exactly ``window_size``
            reading dicts.
        """
        size = self._window_size
        n = len(raw_readings)
        # Integer division — trailing remainder is discarded
        n_complete_windows = n // size
        windows: list[list[dict[str, Any]]] = [
            raw_readings[i * size: (i + 1) * size]
            for i in range(n_complete_windows)
        ]
        return windows

    def _process_window(
        self,
        asset_id: str,
        window_readings: list[dict[str, Any]],
    ) -> tuple[int, int, int]:
        """Compute features for a single window and persist the result.

        Returns
        -------
        tuple[int, int, int]
            ``(records_written, records_skipped, windows_too_small)``
            Each value is 0 or 1 for a single window.
        """
        n_samples = len(window_readings)

        # Extract signal values as numpy array
        values = np.array(
            [float(r["value"]) for r in window_readings],
            dtype=np.float64,
        )

        # Call VibrationFeatureExtractor (handles too-small guard internally)
        features = self._extractor.extract(values)

        if features is None:
            # Window is too small (extractor already logged WARNING)
            _logger.debug(
                "WindowingPipeline: window skipped (too small)",
                extra={"asset_id": asset_id, "n_samples": n_samples},
            )
            return 0, 0, 1

        # Build FeatureRecord -------------------------------------------
        window_start = self._parse_timestamp(window_readings[0]["timestamp"])
        window_end = self._parse_timestamp(window_readings[-1]["timestamp"])
        raw_record_ids = [str(r["id"]) for r in window_readings]

        record = FeatureRecord(
            id=str(uuid.uuid4()),
            asset_id=asset_id,
            window_start=window_start,
            window_end=window_end,
            raw_record_ids=raw_record_ids,
            feature_version=self._feature_version,
            rms=features["rms"],
            variance=features["variance"],
            kurtosis=features["kurtosis"],
            skewness=features["skewness"],
            fft_bins=features["fft_bins"],
        )

        # Persist via StorageInterface (INSERT OR IGNORE — CAT-13) --------
        inserted: bool = self._store.write_feature_record(record.model_dump())

        if inserted:
            _logger.debug(
                "WindowingPipeline: feature record written",
                extra={
                    "asset_id": asset_id,
                    "window_start": window_start.isoformat(),
                    "window_end": window_end.isoformat(),
                },
            )
            return 1, 0, 0
        else:
            _logger.debug(
                "WindowingPipeline: feature record already exists (idempotent)",
                extra={
                    "asset_id": asset_id,
                    "window_start": window_start.isoformat(),
                    "window_end": window_end.isoformat(),
                },
            )
            return 0, 1, 0

    @staticmethod
    def _parse_timestamp(value: Any) -> datetime:
        """Coerce a raw timestamp value to a UTC-aware ``datetime``.

        DuckDB returns ``timestamp`` columns as Python ``datetime`` objects
        when fetched via the Python API.  However, for in-memory test
        databases seeded via plain ``dict`` payloads the value may already
        be a ``datetime``.  This helper normalises both cases.

        Parameters
        ----------
        value:
            A ``datetime`` object (possibly naive) or an ISO 8601 string.

        Returns
        -------
        datetime
            UTC-aware ``datetime``.
        """
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=timezone.utc)
            return value
        # Fallback: ISO 8601 string
        dt = datetime.fromisoformat(str(value))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
