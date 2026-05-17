"""Tests for BackgroundTaskManager (TASK-019).

Criteria from tasks.md (TASK-019):
    - BackgroundTaskManager.requeue_pending called with 3 pending predictions
      — all 3 are enqueued.
    - Worker processes tasks: explain_fn called, explain_status updated to
      "ready" on success.
    - Worker updates explain_status to "failed" when features are not found.
    - Worker updates explain_status to "failed" when explain_fn raises.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from ops_explain.app.background import BackgroundTaskManager


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _StubStore:
    """Minimal in-memory stub for SQLiteStore dependencies."""

    def __init__(self, pending_rows: list[dict[str, Any]] | None = None) -> None:
        self._pending = pending_rows or []
        self.status_updates: dict[str, str] = {}

    def get_predictions_by_status(self, status: str, limit: int = 100) -> list[dict[str, Any]]:
        return [r for r in self._pending if r.get("explain_status") == status][:limit]

    def update_explain_status(self, prediction_id: str, status: str) -> bool:
        self.status_updates[prediction_id] = status
        return True


def _make_explain_fn(result: dict[str, Any] | None = None, raise_exc: Exception | None = None):
    """Return a stub explain_fn."""
    def explain_fn(prediction_id: str, features: list[float]) -> dict[str, Any]:
        if raise_exc is not None:
            raise raise_exc
        return result or {
            "method": "shap_kernel",
            "feature_attributions": [
                {"feature_name": f"f{i}", "attribution_value": float(i), "rank": i + 1}
                for i in range(5)
            ],
            "baseline_window": {"stats_per_feature": {}},
        }
    return explain_fn


def _make_get_features_fn(features: list[float] | None = None):
    """Return a stub get_features_fn that always returns *features*."""
    def get_features_fn(feature_record_id: str) -> list[float] | None:
        return features
    return get_features_fn


# ---------------------------------------------------------------------------
# Tests — requeue_pending
# ---------------------------------------------------------------------------


class TestRequeuePending:
    """Verify that requeue_pending enqueues all pending predictions."""

    def test_requeues_three_pending_predictions(self) -> None:
        """tasks.md criterion: 3 pending predictions → all 3 enqueued."""
        pending = [
            {"id": f"pred-00{i}", "explain_status": "pending", "feature_record_id": f"fr-00{i}"}
            for i in range(1, 4)
        ]
        store = _StubStore(pending_rows=pending)
        mgr = BackgroundTaskManager(
            store=store,
            explain_fn=_make_explain_fn(),
            get_features_fn=_make_get_features_fn([0.1, 0.2, 0.3]),
        )

        count = mgr.requeue_pending()

        assert count == 3
        assert mgr._queue.qsize() == 3

    def test_requeues_zero_when_no_pending(self) -> None:
        store = _StubStore(pending_rows=[])
        mgr = BackgroundTaskManager(
            store=store,
            explain_fn=_make_explain_fn(),
            get_features_fn=_make_get_features_fn([0.5]),
        )

        count = mgr.requeue_pending()

        assert count == 0
        assert mgr._queue.qsize() == 0

    def test_requeues_only_pending_not_ready(self) -> None:
        rows = [
            {"id": "pred-001", "explain_status": "pending", "feature_record_id": "fr-001"},
            {"id": "pred-002", "explain_status": "ready", "feature_record_id": "fr-002"},
            {"id": "pred-003", "explain_status": "failed", "feature_record_id": "fr-003"},
        ]
        store = _StubStore(pending_rows=rows)
        mgr = BackgroundTaskManager(
            store=store,
            explain_fn=_make_explain_fn(),
            get_features_fn=_make_get_features_fn([0.5]),
        )

        count = mgr.requeue_pending()

        assert count == 1
        assert mgr._queue.qsize() == 1

    def test_skips_rows_with_missing_id(self) -> None:
        rows = [
            {"explain_status": "pending", "feature_record_id": "fr-001"},  # no 'id'
            {"id": "pred-002", "explain_status": "pending", "feature_record_id": "fr-002"},
        ]
        store = _StubStore(pending_rows=rows)
        mgr = BackgroundTaskManager(
            store=store,
            explain_fn=_make_explain_fn(),
            get_features_fn=_make_get_features_fn([0.5]),
        )

        count = mgr.requeue_pending()

        # Row without 'id' is skipped; only pred-002 is enqueued.
        assert count == 1

    def test_limit_is_respected(self) -> None:
        pending = [
            {"id": f"pred-{i:03d}", "explain_status": "pending", "feature_record_id": f"fr-{i:03d}"}
            for i in range(50)
        ]
        store = _StubStore(pending_rows=pending)
        mgr = BackgroundTaskManager(
            store=store,
            explain_fn=_make_explain_fn(),
            get_features_fn=_make_get_features_fn([0.1]),
        )

        count = mgr.requeue_pending(limit=10)

        assert count == 10
        assert mgr._queue.qsize() == 10


# ---------------------------------------------------------------------------
# Tests — enqueue
# ---------------------------------------------------------------------------


class TestEnqueue:
    """Verify synchronous enqueue behaviour."""

    def test_enqueue_adds_to_queue(self) -> None:
        store = _StubStore()
        mgr = BackgroundTaskManager(
            store=store,
            explain_fn=_make_explain_fn(),
            get_features_fn=_make_get_features_fn([0.5]),
        )

        mgr.enqueue("pred-001", "fr-001")
        mgr.enqueue("pred-002", "fr-002")

        assert mgr._queue.qsize() == 2


# ---------------------------------------------------------------------------
# Tests — worker processing (async)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestWorkerProcessing:
    """Verify the async worker processes tasks correctly."""

    async def test_worker_marks_task_ready_on_success(self) -> None:
        store = _StubStore()
        mgr = BackgroundTaskManager(
            store=store,
            explain_fn=_make_explain_fn(),
            get_features_fn=_make_get_features_fn([0.1, 0.2, 0.3]),
        )

        await mgr.start_worker()
        mgr.enqueue("pred-001", "fr-001")

        # Allow worker to process.
        await asyncio.sleep(0.5)
        await mgr.stop_worker()

        assert store.status_updates.get("pred-001") == "ready"

    async def test_worker_marks_task_failed_when_features_missing(self) -> None:
        store = _StubStore()
        mgr = BackgroundTaskManager(
            store=store,
            explain_fn=_make_explain_fn(),
            get_features_fn=_make_get_features_fn(None),  # returns None → failed
        )

        await mgr.start_worker()
        mgr.enqueue("pred-missing", "fr-missing")

        await asyncio.sleep(0.5)
        await mgr.stop_worker()

        assert store.status_updates.get("pred-missing") == "failed"

    async def test_worker_marks_task_failed_on_explain_exception(self) -> None:
        store = _StubStore()
        mgr = BackgroundTaskManager(
            store=store,
            explain_fn=_make_explain_fn(raise_exc=RuntimeError("shap error")),
            get_features_fn=_make_get_features_fn([0.5, 0.5]),
        )

        await mgr.start_worker()
        mgr.enqueue("pred-err", "fr-001")

        await asyncio.sleep(0.5)
        await mgr.stop_worker()

        assert store.status_updates.get("pred-err") == "failed"

    async def test_worker_processes_multiple_tasks_sequentially(self) -> None:
        store = _StubStore()
        mgr = BackgroundTaskManager(
            store=store,
            explain_fn=_make_explain_fn(),
            get_features_fn=_make_get_features_fn([0.1, 0.2]),
        )

        await mgr.start_worker()
        for i in range(5):
            mgr.enqueue(f"pred-{i:03d}", f"fr-{i:03d}")

        # Give worker time to drain.
        await asyncio.sleep(2.0)
        await mgr.stop_worker()

        for i in range(5):
            assert store.status_updates.get(f"pred-{i:03d}") == "ready", (
                f"pred-{i:03d} was not marked ready"
            )

    async def test_start_worker_idempotent(self) -> None:
        """Calling start_worker twice should not start a second coroutine."""
        store = _StubStore()
        mgr = BackgroundTaskManager(
            store=store,
            explain_fn=_make_explain_fn(),
            get_features_fn=_make_get_features_fn([0.5]),
        )

        await mgr.start_worker()
        task_id_before = id(mgr._worker_task)
        await mgr.start_worker()  # second call should be a no-op
        task_id_after = id(mgr._worker_task)

        assert task_id_before == task_id_after

        await mgr.stop_worker()

    async def test_result_store_called_on_success(self) -> None:
        """If result_store is provided, write_explain_result is called."""
        store = _StubStore()
        result_store = MagicMock()
        result_store.write_explain_result = MagicMock()

        mgr = BackgroundTaskManager(
            store=store,
            explain_fn=_make_explain_fn(),
            get_features_fn=_make_get_features_fn([0.1]),
            result_store=result_store,
        )

        await mgr.start_worker()
        mgr.enqueue("pred-rs", "fr-rs")

        await asyncio.sleep(0.5)
        await mgr.stop_worker()

        result_store.write_explain_result.assert_called_once()
        assert store.status_updates.get("pred-rs") == "ready"

    async def test_failed_result_store_write_marks_failed(self) -> None:
        """If write_explain_result raises, prediction is marked failed."""
        store = _StubStore()
        result_store = MagicMock()
        result_store.write_explain_result.side_effect = RuntimeError("db error")

        mgr = BackgroundTaskManager(
            store=store,
            explain_fn=_make_explain_fn(),
            get_features_fn=_make_get_features_fn([0.1]),
            result_store=result_store,
        )

        await mgr.start_worker()
        mgr.enqueue("pred-db-err", "fr-db-err")

        await asyncio.sleep(0.5)
        await mgr.stop_worker()

        assert store.status_updates.get("pred-db-err") == "failed"
