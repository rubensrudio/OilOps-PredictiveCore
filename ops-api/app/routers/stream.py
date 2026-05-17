"""
ops-api/app/routers/stream.py
==============================
WebSocket router for ``WS /predictions/stream``.

Behaviour
---------
1. Client connects (optionally passing ``?asset_id=PUMP-001``).
2. The connection is registered in :data:`~ops_api.app.websocket_manager.manager`
   with the supplied ``filter_asset_id`` (``None`` when omitted).
3. The endpoint enters a receive loop:
   - Only ``{"type": "ping"}`` messages are processed — the server echoes
     ``{"type": "pong"}`` back (heartbeat / keepalive).
   - Any message that cannot be decoded as JSON is treated as an invalid
     payload: the connection is closed with code 1008 (Policy Violation) and
     the violation is logged in structured JSON (spec edge case).
   - Any other valid-JSON message that does not match the recognised types is
     silently ignored (forward-compatibility).
4. On disconnect (``WebSocketDisconnect`` or any connection error), the
   connection is removed from the manager and the disconnect is logged
   without affecting other connected clients (INIT-US-04-AC3).

Broadcast
---------
Prediction payloads are pushed to all eligible clients via
:meth:`~ops_api.app.websocket_manager.WebSocketManager.broadcast`, which is
called externally (e.g. from ops-models router or a background task) when a
new prediction is persisted.

Trace propagation
-----------------
WebSocket connections are long-lived; ``TracingMiddleware`` sets a ``trace_id``
on HTTP upgrade but the ContextVar may not survive across subsequent async
iterations of the receive loop.  For this reason, the stream endpoint uses a
connection-scoped ``connection_id`` UUID logged at connect/disconnect time
instead of relying on per-message trace propagation.  This is intentional and
documented in the TASK-024 technical note.

References
----------
- spec.md INIT-US-04, edge case "payload inválido → 1008"
- tasks.md TASK-024
- plan.md § 5.4
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ops_api.app.websocket_manager import manager
from shared.logging_config import get_logger

router = APIRouter(tags=["stream"])

_logger = get_logger("ops_api.routers.stream")

# Message types handled by the receive loop.
_MSG_PING = "ping"
_MSG_PONG = "pong"


@router.websocket("/predictions/stream")
async def predictions_stream(
    websocket: WebSocket,
    asset_id: str | None = None,
) -> None:
    """WebSocket endpoint for real-time prediction streaming.

    Query Parameters
    ----------------
    asset_id : str, optional
        When provided, the client will only receive predictions whose
        ``asset_id`` matches this value (INIT-US-04-AC4).  Omitting the
        parameter results in receiving all predictions.

    Protocol
    --------
    After connecting, the client may send:

    * ``{"type": "ping"}`` — server responds with ``{"type": "pong"}``.
    * Any other valid-JSON message — silently ignored.
    * Any non-JSON message — connection closed with code 1008.

    Outbound messages are pushed by the
    :class:`~ops_api.app.websocket_manager.WebSocketManager` whenever
    :meth:`~ops_api.app.websocket_manager.WebSocketManager.broadcast` is
    called externally.
    """
    connection_id = str(uuid.uuid4())

    _logger.info(
        "WebSocket stream connection initiating",
        extra={
            "event": "ws_stream_connect_init",
            "connection_id": connection_id,
            "filter_asset_id": asset_id,
        },
    )

    await manager.connect(websocket, filter_asset_id=asset_id)

    _logger.info(
        "WebSocket stream connection accepted",
        extra={
            "event": "ws_stream_connected",
            "connection_id": connection_id,
            "filter_asset_id": asset_id,
        },
    )

    try:
        while True:
            # Block until the client sends a message or the connection closes.
            raw = await websocket.receive_text()

            # Attempt JSON decode — any failure is a policy violation.
            try:
                message = _parse_json(raw)
            except ValueError:
                _logger.warning(
                    "Invalid JSON payload received on WebSocket stream",
                    extra={
                        "event": "ws_invalid_payload",
                        "connection_id": connection_id,
                        "raw_length": len(raw),
                    },
                )
                await manager.close_with_policy_violation(
                    websocket,
                    reason="Invalid JSON payload",
                )
                return

            msg_type = message.get("type") if isinstance(message, dict) else None

            if msg_type == _MSG_PING:
                # Heartbeat: echo pong back.
                await websocket.send_json({"type": _MSG_PONG})
            # All other valid-JSON messages are silently ignored.

    except WebSocketDisconnect:
        # Normal client-initiated disconnect — log and clean up.
        _logger.info(
            "WebSocket client disconnected normally",
            extra={
                "event": "ws_stream_disconnect",
                "connection_id": connection_id,
                "filter_asset_id": asset_id,
            },
        )
    except Exception as exc:  # noqa: BLE001
        # Unexpected error — log with context but do not re-raise so other
        # clients are unaffected (INIT-US-04-AC3).
        _logger.error(
            "Unexpected error in WebSocket stream handler",
            extra={
                "event": "ws_stream_error",
                "connection_id": connection_id,
                "filter_asset_id": asset_id,
                "error": str(exc),
            },
        )
    finally:
        await manager.disconnect(websocket)
        _logger.info(
            "WebSocket connection removed from manager",
            extra={
                "event": "ws_stream_cleanup",
                "connection_id": connection_id,
                "filter_asset_id": asset_id,
            },
        )


def _parse_json(raw: str) -> object:
    """Decode *raw* as JSON, raising ``ValueError`` on any decode error.

    Separated from the route handler to allow easy unit-testing of the
    error path without needing a live WebSocket.
    """
    import json  # noqa: PLC0415 — local import intentional (rarely used path)

    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"JSON decode failed: {exc}") from exc
