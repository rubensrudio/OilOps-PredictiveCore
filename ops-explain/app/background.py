"""
ops-explain/app/background.py
================================
Background task manager for SHAP attribution computation (TASK-019).

Responsibilities
----------------
1. Enqueue SHAP attribution tasks for individual prediction IDs.
2. On service start, recover in-flight tasks by querying ``ops-store``
   for predictions with ``explain_status = "pending"`` and re-adding
   them to the queue (DA-03 mitigation — prevents task loss across
   restarts).
3. Execute queued tasks asynchronously: call :class:`SHAPExplainer`,
   persist the result dict, and flip ``explain_status`` to ``"ready"``
   or ``"failed"`` via the ``ops-store`` SQLite store.

Architecture
------------
The manager uses :class:`asyncio.Queue` for in-process task serialisation.
A single worker coroutine drains the queue one task at a time.  This keeps
the SHAP computation out of the HTTP request path (RN-04) while avoiding
concurrent SHAP sessions that would saturate the CPU.

The worker coroutine is started by calling :meth:`start_worker` in the
FastAPI ``lifespan`` event.  It must be stopped (via :meth:`stop_worker`)
on shutdown to avoid "Task was destroyed but it is pending" warnings.

Storage contract
----------------
``BackgroundTaskManager`` depends on a *store object* that provides three
methods (duck-typed, not formally bound to ``SQLiteStore`` so that unit
tests can pass a mock):

    store.get_predictions_by_status(status: str, limit: int) -> list[dict]
    store.update_explain_status(prediction_id: str, status: str) -> bool
    store.write_explain_result(result: dict) -> None   # optional — see note

Because ``ops-store`` SQLite does not yet expose a ``write_explain_result``
endpoint, Phase 1 persists the raw explain result dict via an optional
``result_store`` argument.  If ``result_store`` is ``None`` the attributions
are logged at DEBUG level only (sufficient for Phase 1 wiring).

Explainer contract
------------------
``BackgroundTaskManager`` accepts a callable ``explain_fn`` with signature::

    explain_fn(features: list[float]) -> dict

This allows the unit tests to inject a stub without instantiating a full
:class:`SHAPExplainer`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

logger = logging.getLogger(__name__)


class BackgroundTaskManager:
    """Manage asynchronous SHAP explanation tasks.

    Parameters
    ----------
    store:
        Object that exposes ``get_predictions_by_status`` and
        ``update_explain_status`` (duck-typed against ``SQLiteStore``).
    explain_fn:
        Callable with signature
        ``explain_fn(prediction_id: str, features: list[float]) -> dict``.
        Expected to return the dict produced by :class:`SHAPExplainer`.
        Receives the raw feature vector and returns attribution results.
    get_features_fn:
        Callable with signature
        ``get_features_fn(feature_record_id: str) -> list[float] | None``.
        Used to retrieve feature vectors from ``ops-store`` by
        ``feature_record_id``.  If ``None`` is returned for a given ID the
        task is marked as ``"failed"`` immediately.
    result_store:
        Optional object that exposes a ``write_explain_result(result: dict)``
        method.  If ``None`` the attribution dict is only logged (Phase 1
        acceptable behaviour while ``ops-explain`` FastAPI app is wired up).
    """

    def __init__(
        self,
        store: Any,
        explain_fn: Callable[[str, list[float]], dict[str, Any]],
        get_features_fn: Callable[[str], list[float] | None],
        result_store: Any | None = None,
    ) -> None:
        self._store = store
        self._explain_fn = explain_fn
        self._get_features_fn = get_features_fn
        self._result_store = result_store

        # (prediction_id, feature_record_id) tuples
        self._queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
        self._worker_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Queue management
    # ------------------------------------------------------------------

    def enqueue(self, prediction_id: str, feature_record_id: str) -> None:
        """Add a SHAP task for *prediction_id* to the queue.

        This method is synchronous and safe to call from any context
        (FastAPI background task, request handler, etc.).  It does not
        start the worker — call :meth:`start_worker` once at startup.

        Parameters
        ----------
        prediction_id:
            UUID string of the prediction that requires SHAP attribution.
        feature_record_id:
            UUID string of the feature_record to fetch from ``ops-store``.
        """
        self._queue.put_nowait((prediction_id, feature_record_id))
        logger.debug(
            "Enqueued SHAP task",
            extra={
                "prediction_id": prediction_id,
                "feature_record_id": feature_record_id,
                "queue_size": self._queue.qsize(),
            },
        )

    def requeue_pending(self, limit: int = 100) -> int:
        """Re-add all ``explain_status=pending`` predictions to the queue.

        Should be called once during service startup (lifespan event) to
        recover tasks that were lost when the process was last stopped
        (DA-03 mitigation).

        Parameters
        ----------
        limit:
            Maximum number of pending predictions to recover.  Default: 100.

        Returns
        -------
        int
            Number of predictions added to the queue.
        """
        pending = self._store.get_predictions_by_status("pending", limit=limit)
        count = 0
        for row in pending:
            prediction_id = str(row.get("id") or row.get("prediction_id", ""))
            feature_record_id = str(row.get("feature_record_id", ""))
            if not prediction_id:
                logger.warning(
                    "Skipping pending prediction with missing id: %r", row
                )
                continue
            self.enqueue(prediction_id, feature_record_id)
            count += 1

        if count:
            logger.info(
                "Requeued %d pending SHAP tasks on startup.",
                count,
                extra={"requeued_count": count},
            )
        return count

    # ------------------------------------------------------------------
    # Worker lifecycle
    # ------------------------------------------------------------------

    async def start_worker(self) -> None:
        """Start the background worker coroutine.

        Safe to call multiple times — will not start a second worker if
        one is already running.
        """
        if self._worker_task is not None and not self._worker_task.done():
            logger.debug("BackgroundTaskManager worker already running.")
            return

        self._worker_task = asyncio.create_task(
            self._worker_loop(), name="shap-worker"
        )
        logger.info("BackgroundTaskManager worker started.")

    async def stop_worker(self) -> None:
        """Signal the worker to stop and await its completion.

        Sends a sentinel ``None`` value to the queue so the loop exits
        cleanly after draining any in-flight item.
        """
        if self._worker_task is None or self._worker_task.done():
            return

        # Sentinel to unblock the queue.get() call inside _worker_loop.
        self._queue.put_nowait(None)  # type: ignore[arg-type]
        try:
            await asyncio.wait_for(self._worker_task, timeout=30.0)
        except asyncio.TimeoutError:
            logger.warning(
                "BackgroundTaskManager worker did not stop within 30 s; "
                "cancelling."
            )
            self._worker_task.cancel()

    # ------------------------------------------------------------------
    # Internal worker
    # ------------------------------------------------------------------

    async def _worker_loop(self) -> None:
        """Drain the task queue, executing SHAP attributions one at a time.

        The loop runs until a sentinel ``None`` is dequeued (sent by
        :meth:`stop_worker`).  Any exception raised during a single task is
        caught so the worker remains alive for subsequent tasks.
        """
        logger.debug("BackgroundTaskManager worker loop started.")
        while True:
            item = await self._queue.get()

            # Sentinel: time to exit.
            if item is None:
                self._queue.task_done()
                logger.debug("BackgroundTaskManager worker loop stopping.")
                break

            prediction_id, feature_record_id = item
            await self._process_task(prediction_id, feature_record_id)
            self._queue.task_done()

    async def _process_task(
        self, prediction_id: str, feature_record_id: str
    ) -> None:
        """Execute a single SHAP attribution task.

        1. Fetch feature vector via ``get_features_fn``.
        2. Call ``explain_fn`` to get attribution dict.
        3. Optionally persist the result via ``result_store``.
        4. Update ``explain_status`` to ``"ready"`` or ``"failed"``.

        All exceptions are caught; on failure ``explain_status`` is set to
        ``"failed"`` and the error is logged at ERROR level.
        """
        logger.debug(
            "Processing SHAP task",
            extra={
                "prediction_id": prediction_id,
                "feature_record_id": feature_record_id,
            },
        )

        try:
            # Run CPU-bound work in a thread pool to avoid blocking the event
            # loop.  asyncio.to_thread requires Python 3.9+.
            result = await asyncio.to_thread(
                self._run_shap_sync, prediction_id, feature_record_id
            )

            if result is None:
                # Feature vector not found — already logged inside helper.
                self._store.update_explain_status(prediction_id, "failed")
                return

            # Persist the result if a result_store is available.
            if self._result_store is not None:
                try:
                    self._result_store.write_explain_result(result)
                except Exception as persist_exc:  # noqa: BLE001
                    logger.error(
                        "Failed to persist explain result for %s: %s",
                        prediction_id,
                        persist_exc,
                        extra={"prediction_id": prediction_id},
                    )
                    self._store.update_explain_status(prediction_id, "failed")
                    return
            else:
                logger.debug(
                    "No result_store configured; explain result for %s: %r",
                    prediction_id,
                    result,
                )

            self._store.update_explain_status(prediction_id, "ready")
            logger.info(
                "SHAP attribution complete for prediction %s.",
                prediction_id,
                extra={"prediction_id": prediction_id},
            )

        except Exception as exc:  # noqa: BLE001
            logger.error(
                "SHAP task failed for prediction %s: %s",
                prediction_id,
                exc,
                exc_info=True,
                extra={"prediction_id": prediction_id},
            )
            try:
                self._store.update_explain_status(prediction_id, "failed")
            except Exception as update_exc:  # noqa: BLE001
                logger.error(
                    "Failed to mark prediction %s as failed: %s",
                    prediction_id,
                    update_exc,
                )

    def _run_shap_sync(
        self,
        prediction_id: str,
        feature_record_id: str,
    ) -> dict[str, Any] | None:
        """Synchronous portion of SHAP computation (runs in thread pool).

        Returns
        -------
        dict | None
            Attribution dict from ``explain_fn``, or ``None`` if the
            feature vector could not be retrieved.
        """
        features = self._get_features_fn(feature_record_id)
        if features is None:
            logger.warning(
                "Feature vector not found for feature_record_id=%s "
                "(prediction_id=%s); marking as failed.",
                feature_record_id,
                prediction_id,
            )
            return None

        attribution_result = self._explain_fn(prediction_id, features)
        attribution_result["prediction_id"] = prediction_id
        return attribution_result
