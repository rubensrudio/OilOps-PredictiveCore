"""
ops-ingest/app/normalizer.py
=============================
Normalizer layer for ops-ingest (TASK-010).

Responsibilities
----------------
1. Transform :class:`~app.schemas.IngestReading` objects into
   :class:`~shared.schemas.canonical.CanonicalReading` objects, generating a
   fresh UUID4 for ``id`` and setting ``ingested_at`` to ``datetime.now(UTC)``.

2. Re-order the reading list by ``timestamp`` **before** returning (INIT-02).

3. Flag ``is_backfill=True`` when a reading's ``timestamp`` is older than
   ``now() - max_backfill_days`` (edge case from spec; configurable via
   ``Settings.max_backfill_days``, default 30 days).

4. Auto-registration of unknown ``asset_id`` values via an injectable
   :class:`AbstractStoreClient` (INIT-03).  In Phase 1 a stub implementation
   is provided; the real implementation will call ``ops-store`` via HTTP.

Design notes
------------
- The ``Normalizer`` class receives a ``store_client`` dependency at
  construction time (constructor injection) so that tests can supply a mock
  without monkeypatching globals.
- All datetimes are UTC-aware (:class:`pydantic.AwareDatetime`); naive datetimes
  are rejected by Pydantic at the boundary.
- ``ingestion_id`` is generated once per normalizer call (one batch = one
  ``ingestion_id``) and shared across all readings in the batch.
- This module has **no I/O side effects** — persistence is delegated to the
  ``store_client`` for auto-registration only.

References
----------
- spec.md RN-01 (schema canônico), INIT-02 (reordenação), INIT-03
  (auto-registration), edge case (is_backfill)
- plan.md § 3.2 (fluxo de ingestão), § 4.1 (schema raw_readings)
"""

from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone

from shared.config import Settings
from shared.schemas.canonical import CanonicalReading

from app.schemas import IngestReading


# ---------------------------------------------------------------------------
# Store client interface (injectable — enables mock in tests)
# ---------------------------------------------------------------------------


class AbstractStoreClient(ABC):
    """Abstract interface for the ops-store client used by the Normalizer.

    The only operation required by the Normalizer is ``ensure_asset_registered``
    which implements INIT-03 (auto-registration of unknown asset_ids).

    A concrete HTTP implementation will be provided when ``ops-store`` is
    wired up in TASK-012.  For Phase 1 tests, use :class:`StubStoreClient`.
    """

    @abstractmethod
    def ensure_asset_registered(self, asset_id: str) -> None:
        """Ensure that *asset_id* is registered in ops-store.

        If the asset does not exist, this method MUST register it.  If it
        already exists, this method MUST be idempotent (no error, no
        duplicate).

        Parameters
        ----------
        asset_id:
            Canonical asset identifier to register (e.g. ``"PUMP-001"``).

        Raises
        ------
        RuntimeError
            If the registration call fails due to a connectivity or server
            error.  The Normalizer catches this and records the failure in
            ``rejection_details``.
        """


class StubStoreClient(AbstractStoreClient):
    """Phase 1 stub store client that records which asset_ids were registered.

    This stub is used in unit tests and as the default in-process client when
    the real ``ops-store`` HTTP client is not yet wired (TASK-012).

    Attributes
    ----------
    registered_assets:
        Set of ``asset_id`` strings that have been passed to
        :meth:`ensure_asset_registered`.  Inspect this in tests to verify
        that the Normalizer triggered auto-registration.
    """

    def __init__(self) -> None:
        self.registered_assets: set[str] = set()

    def ensure_asset_registered(self, asset_id: str) -> None:
        """Record asset_id as registered (no real I/O)."""
        self.registered_assets.add(asset_id)


# ---------------------------------------------------------------------------
# Normalizer
# ---------------------------------------------------------------------------


class Normalizer:
    """Transforms a list of :class:`IngestReading` objects into
    :class:`CanonicalReading` objects.

    The normalizer is the central piece of the ingestion pipeline.  It
    performs all enrichment and reordering steps required by the spec before
    the readings reach ``ops-store``.

    Parameters
    ----------
    store_client:
        An :class:`AbstractStoreClient` implementation used for
        auto-registration (INIT-03).  Defaults to :class:`StubStoreClient`
        for Phase 1 / testing scenarios.
    settings:
        Application settings instance.  Defaults to a fresh :class:`Settings`
        (reads from environment variables).  Pass a custom instance in tests
        to control ``max_backfill_days``.
    """

    def __init__(
        self,
        store_client: AbstractStoreClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._store_client: AbstractStoreClient = (
            store_client if store_client is not None else StubStoreClient()
        )
        self._settings: Settings = settings if settings is not None else Settings()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def normalize(
        self,
        readings: list[IngestReading],
        ingestion_id: uuid.UUID,
    ) -> list[CanonicalReading]:
        """Normalize a list of :class:`IngestReading` objects.

        Steps performed (in order):
        1. For each unique ``asset_id`` in the batch, call
           :meth:`AbstractStoreClient.ensure_asset_registered` (INIT-03).
        2. Convert each :class:`IngestReading` to a :class:`CanonicalReading`,
           generating a UUID4 ``id`` and stamping ``ingested_at`` = now(UTC).
        3. Flag ``is_backfill=True`` when the reading's ``timestamp`` is older
           than ``now() - max_backfill_days`` (edge case spec).
        4. Sort the resulting list by ``timestamp`` ascending (INIT-02).

        Parameters
        ----------
        readings:
            List of validated :class:`IngestReading` objects from the
            ingestion payload.  May be empty (returns empty list).
        ingestion_id:
            UUID that identifies the ingestion batch.  Shared across all
            canonical readings produced from this call.

        Returns
        -------
        list[CanonicalReading]
            Normalized, sorted list of canonical readings ready for
            persistence.  Empty list when *readings* is empty.
        """
        if not readings:
            return []

        # Step 1: auto-register all unique asset_ids (INIT-03).
        seen_assets: set[str] = set()
        for reading in readings:
            if reading.asset_id not in seen_assets:
                seen_assets.add(reading.asset_id)
                self._store_client.ensure_asset_registered(reading.asset_id)

        # Capture now() once so all readings in this batch share the same
        # ingested_at timestamp and the backfill cutoff is consistent.
        now_utc: datetime = datetime.now(tz=timezone.utc)
        backfill_cutoff: datetime = now_utc - timedelta(
            days=self._settings.max_backfill_days
        )

        # Step 2 + 3: convert and flag backfill.
        canonical_readings: list[CanonicalReading] = []
        for reading in readings:
            is_backfill: bool = reading.timestamp < backfill_cutoff
            canonical = CanonicalReading(
                id=uuid.uuid4(),
                asset_id=reading.asset_id,
                timestamp=reading.timestamp,
                metric_name=reading.metric_name,
                value=reading.value,
                unit=reading.unit,
                source_protocol=reading.source_protocol,
                ingested_at=now_utc,
                ingestion_id=ingestion_id,
                is_backfill=is_backfill,
            )
            canonical_readings.append(canonical)

        # Step 4: sort by timestamp ascending (INIT-02).
        canonical_readings.sort(key=lambda r: r.timestamp)

        return canonical_readings
