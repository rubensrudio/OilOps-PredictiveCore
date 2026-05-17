"""
ops-api/app/websocket_manager.py
==================================
WebSocketManager — maintains the active WebSocket connections and provides
a ``broadcast()`` method that fans out prediction payloads to all connected
clients, with optional per-connection ``filter_asset_id`` filtering.

Design
------
* Each connection is stored as a ``(WebSocket, filter_asset_id | None)``
  tuple.  ``filter_asset_id = None`` means "no filter — receive everything".
* An ``asyncio.Lock`` guards the connection list so that concurrent
  connect/disconnect/broadcast operations do not race.
* ``stream_sequence`` is a per-manager counter, incremented for every
  message sent so clients can detect gaps.
* On broadcast, a failed send to one client (e.g. already-closed socket)
  is logged and the connection is removed; other clients are not affected
  (INIT-US-04-AC3).
* Clients that send an invalid payload during the stream session receive
  WebSocket close code 1008 (Policy Violation) per the spec edge case.

References
----------
- spec.md INIT-US-04, edge-case "payload inválido"
- tasks.md TASK-024
- plan.md § 3.1, § 5.4
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import WebSocket
from starlette.websockets import WebSocketState

from shared.logging_config import get_logger

_logger = get_logger("ops_api.websocket_manager")

# WebSocket close codes (RFC 6455)
_WS_CLOSE_POLICY_VIOLATION: int = 1008


class WebSocketManager:
    """Manage active WebSocket connections and broadcast predictions.

    Thread-safety
    -------------
    All state mutations are guarded by ``self._lock`` (``asyncio.Lock``), so
    this class is safe for concurrent use within a single event-loop.

    Attributes
    ----------
    stream_sequence:
        Monotonically increasing counter included in every outbound message.
        Incremented once per successful :meth:`broadcast` call.
    """

    def __init__(self) -> None:
        # List of (websocket, filter_asset_id) pairs.
        self._connections: list[tuple[WebSocket, str | None]] = []
        self._lock: asyncio.Lock = asyncio.Lock()
        self.stream_sequence: int = 0

    # ------------------------------------------------------------------
    # Lifecycle: connect / disconnect
    # ------------------------------------------------------------------

    async def connect(
        self,
        ws: WebSocket,
        filter_asset_id: str | None = None,
    ) -> None:
        """Accept *ws* and register it with an optional asset filter.

        Parameters
        ----------
        ws:
            Incoming WebSocket connection (not yet accepted).
        filter_asset_id:
            When set, the client will only receive broadcasts whose
            ``asset_id`` matches this value.  ``None`` means receive all.
        """
        await ws.accept()
        async with self._lock:
            self._connections.append((ws, filter_asset_id))

        _logger.info(
            "WebSocket client connected",
            extra={
                "event": "ws_connect",
                "filter_asset_id": filter_asset_id,
                "total_connections": len(self._connections),
            },
        )

    async def disconnect(self, ws: WebSocket) -> None:
        """Remove *ws* from the active connections list.

        Parameters
        ----------
        ws:
            WebSocket connection to deregister.

        Notes
        -----
        Removal is performed in a locked section to avoid a torn read of
        ``_connections`` while a concurrent broadcast is iterating it.
        The caller is responsible for calling ``ws.close()`` if the socket
        is still open — this method only removes the internal record.
        """
        async with self._lock:
            before = len(self._connections)
            self._connections = [
                (w, f) for w, f in self._connections if w is not ws
            ]
            removed = before - len(self._connections)

        _logger.info(
            "WebSocket client disconnected",
            extra={
                "event": "ws_disconnect",
                "connections_removed": removed,
                "total_connections": len(self._connections),
            },
        )

    # ------------------------------------------------------------------
    # Broadcast
    # ------------------------------------------------------------------

    async def broadcast(self, prediction: dict[str, Any]) -> None:
        """Broadcast *prediction* to all eligible connections.

        A connection is eligible when its ``filter_asset_id`` is ``None``
        (receives everything) **or** equals ``prediction["asset_id"]``
        (INIT-US-04-AC4).

        The ``stream_sequence`` counter is incremented before the fan-out
        so all recipients in a single broadcast call receive the same
        sequence number.  On partial failure (one client's send raises),
        that client is removed and the broadcast continues to the
        remaining clients.

        Parameters
        ----------
        prediction:
            Dict representation of a prediction result.  MUST contain an
            ``asset_id`` key; if absent, the broadcast is skipped with a
            warning log.
        """
        asset_id: str | None = prediction.get("asset_id")
        if asset_id is None:
            _logger.warning(
                "broadcast() called with prediction missing 'asset_id' — skipping",
                extra={"event": "ws_broadcast_skip", "prediction": prediction},
            )
            return

        # Increment sequence before fan-out so all clients get the same seq.
        self.stream_sequence += 1
        payload = {**prediction, "stream_sequence": self.stream_sequence}

        # Snapshot the connection list under the lock, then release before
        # the async send loop (avoids holding the lock during I/O).
        async with self._lock:
            snapshot = list(self._connections)

        failed: list[WebSocket] = []
        sent_count = 0

        for ws, filter_asset_id in snapshot:
            # Skip clients filtering on a different asset.
            if filter_asset_id is not None and filter_asset_id != asset_id:
                continue

            try:
                await ws.send_json(payload)
                sent_count += 1
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    "Failed to send to WebSocket client — removing connection",
                    extra={
                        "event": "ws_send_error",
                        "asset_id": asset_id,
                        "error": str(exc),
                    },
                )
                failed.append(ws)

        # Prune dead connections.
        if failed:
            async with self._lock:
                self._connections = [
                    (w, f) for w, f in self._connections if w not in failed
                ]

        _logger.info(
            "Broadcast complete",
            extra={
                "event": "ws_broadcast",
                "asset_id": asset_id,
                "stream_sequence": self.stream_sequence,
                "sent_to": sent_count,
                "failed": len(failed),
            },
        )

    # ------------------------------------------------------------------
    # Payload validation helper (used by the stream router)
    # ------------------------------------------------------------------

    @staticmethod
    async def close_with_policy_violation(ws: WebSocket, reason: str) -> None:
        """Close *ws* with code 1008 (Policy Violation) and log the reason.

        Parameters
        ----------
        ws:
            WebSocket connection to close.
        reason:
            Human-readable reason for the closure — logged in structured
            JSON for audit purposes.
        """
        _logger.warning(
            "Closing WebSocket with policy violation (1008)",
            extra={
                "event": "ws_policy_violation",
                "reason": reason,
                "close_code": _WS_CLOSE_POLICY_VIOLATION,
            },
        )
        if ws.client_state != WebSocketState.DISCONNECTED:
            await ws.close(code=_WS_CLOSE_POLICY_VIOLATION, reason=reason)


# ---------------------------------------------------------------------------
# Module-level singleton — shared across all router instances in one process
# ---------------------------------------------------------------------------

manager: WebSocketManager = WebSocketManager()
