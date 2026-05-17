"""
ops-ingest/app/adapters/rest_batch.py
=======================================
REST batch ingestion adapter — the primary Phase 1 implementation (TASK-010).

This is the **concrete** adapter that MQTT and Kafka stubs model their
interface after.  Any caller that holds a reference typed as
``RestBatchAdapter`` (or its conceptual interface) can be swapped to a
different adapter at runtime without changing call sites — strategy pattern.

Responsibilities
----------------
- Accept an :class:`~app.schemas.IngestRequest` carrying a batch of
  :class:`~app.schemas.IngestReading` objects.
- Delegate transformation to :class:`~app.normalizer.Normalizer` (which
  generates UUIDs, stamps ``ingested_at``, flags ``is_backfill``, and
  auto-registers unknown assets).
- Return an :class:`~app.schemas.IngestionResponse` with:
  - ``ingestion_id``  — fresh UUID for this batch
  - ``records_received``   — total readings in the payload
  - ``records_accepted``   — readings successfully normalised
  - ``records_rejected``   — readings that could not be normalised (0 in
    Phase 1, since validation already occurred at the Pydantic boundary)
  - ``rejection_details``  — per-record error list (empty in Phase 1)

Phase 1 scope
-------------
Persistence to ``ops-store`` is NOT performed here.  ``RestBatchAdapter``
returns the :class:`IngestionResponse` and exposes the list of
:class:`~shared.schemas.canonical.CanonicalReading` objects via the
``last_canonical_readings`` attribute so that the FastAPI route handler
(TASK-012) can forward them to ``ops-store``.

References
----------
- spec.md INIT-US-01, INIT-05, RN-01, RN-07
- plan.md § 3.2 (synchronous ingest path), § 5.1 (POST /telemetry contract)
- tasks.md TASK-010 (this task), TASK-012 (FastAPI app that calls this adapter)
"""

from __future__ import annotations

import uuid
from typing import Any

from shared.config import Settings
from shared.schemas.canonical import CanonicalReading

from app.normalizer import AbstractStoreClient, Normalizer, StubStoreClient
from app.schemas import IngestRequest, IngestionResponse


class RestBatchAdapter:
    """Concrete REST batch ingestion adapter.

    Accepts a validated :class:`~app.schemas.IngestRequest`, normalises all
    readings via :class:`~app.normalizer.Normalizer`, and returns an
    :class:`~app.schemas.IngestionResponse` summarising the batch outcome.

    Parameters
    ----------
    store_client:
        Injected :class:`~app.normalizer.AbstractStoreClient` used for
        asset auto-registration (INIT-03).  Defaults to
        :class:`~app.normalizer.StubStoreClient` for Phase 1.  Pass a mock
        implementation in unit tests to verify auto-registration calls.
    settings:
        Application settings instance controlling ``max_backfill_days`` and
        other pipeline parameters.  Defaults to a fresh :class:`~shared.config.Settings`
        (reads from environment variables).

    Attributes
    ----------
    last_canonical_readings:
        The :class:`~shared.schemas.canonical.CanonicalReading` list produced
        by the most recent :meth:`ingest` call.  ``None`` before any call.
        The FastAPI route handler (TASK-012) reads this to persist the data
        to ``ops-store``.
    """

    def __init__(
        self,
        store_client: AbstractStoreClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        self._normalizer = Normalizer(
            store_client=store_client or StubStoreClient(),
            settings=settings or Settings(),
        )
        self.last_canonical_readings: list[CanonicalReading] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ingest(self, request: IngestRequest) -> IngestionResponse:
        """Process a batch ingestion request.

        Steps:
        1. Generate a fresh ``ingestion_id`` UUID for this batch.
        2. Delegate normalisation + sorting + backfill flagging to
           :class:`~app.normalizer.Normalizer`.
        3. Store the canonical readings in ``last_canonical_readings`` for
           the FastAPI layer to persist (TASK-012).
        4. Return :class:`~app.schemas.IngestionResponse` with counters.

        Parameters
        ----------
        request:
            Validated :class:`~app.schemas.IngestRequest` from the HTTP
            layer.  An empty ``readings`` list is valid — returns a response
            with all counters at 0 and an empty ``rejection_details``.

        Returns
        -------
        IngestionResponse
            Summary of the ingestion batch outcome.

        Notes
        -----
        ``records_rejected`` is always 0 in Phase 1 because Pydantic
        validation at the API boundary rejects malformed payloads before
        they reach this adapter.  Rejection tracking at this level will be
        introduced if/when partial-batch semantics are implemented.
        """
        ingestion_id: uuid.UUID = uuid.uuid4()
        records_received: int = len(request.readings)

        canonical_readings: list[CanonicalReading] = self._normalizer.normalize(
            readings=request.readings,
            ingestion_id=ingestion_id,
        )
        records_accepted: int = len(canonical_readings)
        records_rejected: int = records_received - records_accepted
        rejection_details: list[dict[str, Any]] = []

        # Expose for FastAPI route handler (TASK-012) to persist.
        self.last_canonical_readings = canonical_readings

        return IngestionResponse(
            ingestion_id=ingestion_id,
            records_received=records_received,
            records_accepted=records_accepted,
            records_rejected=records_rejected,
            rejection_details=rejection_details,
        )
