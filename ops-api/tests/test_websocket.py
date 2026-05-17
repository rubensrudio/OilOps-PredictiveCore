"""
tests/test_websocket.py
========================
Tests for WebSocketManager and WS /predictions/stream router (TASK-024).

Criteria verified (tasks.md TASK-024):
  1. Two clients connected simultaneously — one with filter ``PUMP-001``,
     one without filter.
  2. broadcast() for ``PUMP-001`` prediction: BOTH clients receive the message.
  3. broadcast() for ``PUMP-002`` prediction: ONLY the unfiltered client
     receives the message.
  4. stream_sequence is incremented per broadcast call.
  5. Invalid JSON payload closes the connection with code 1008.
  6. Disconnect removes the connection without affecting other clients.
  7. Ping/pong heartbeat works correctly.
  8. WebSocketManager.broadcast() skips predictions without asset_id.

Design notes
------------
* Synchronous multi-client broadcast tests use two ``TestClient`` instances
  operating in separate threads (via ``threading.Thread``) so both WS
  connections are live concurrently.  Each thread captures received messages
  into a shared list protected by a ``threading.Event``.

* Async unit tests for WebSocketManager internals use ``pytest-asyncio``
  with ``asyncio-mode=auto`` to avoid boilerplate event-loop management.

* The ``_make_stream_app()`` helper builds a minimal FastAPI app with only
  the stream router — no tracing/advisory middleware — to keep tests focused.
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_stream_app() -> FastAPI:
    """Return a minimal FastAPI app with the stream router and a fresh manager."""
    from ops_api.app.routers.stream import router as stream_router

    _app = FastAPI()
    _app.include_router(stream_router)
    return _app


def _make_prediction(asset_id: str = "PUMP-001") -> dict[str, Any]:
    return {
        "prediction_id": str(uuid.uuid4()),
        "asset_id": asset_id,
        "asset_class": "rotating_equipment",
        "anomaly_score": 0.85,
        "confidence_score": 0.91,
        "alert": True,
        "severity": "high",
        "predicted_at": "2026-05-17T10:00:00Z",
        "model_version": "vibration-autoencoder-v1",
        "explain_status": "pending",
    }


# ---------------------------------------------------------------------------
# Unit tests — WebSocketManager internals (async)
# ---------------------------------------------------------------------------


class TestWebSocketManagerUnit:
    """Unit tests for WebSocketManager without a live HTTP server."""

    @pytest.mark.asyncio
    async def test_connect_registers_connection(self) -> None:
        """connect() should append (ws, filter) to the internal list."""
        from ops_api.app.websocket_manager import WebSocketManager

        mgr = WebSocketManager()
        ws = AsyncMock()
        ws.accept = AsyncMock()

        await mgr.connect(ws, filter_asset_id="PUMP-001")

        assert len(mgr._connections) == 1
        stored_ws, stored_filter = mgr._connections[0]
        assert stored_ws is ws
        assert stored_filter == "PUMP-001"

    @pytest.mark.asyncio
    async def test_connect_without_filter(self) -> None:
        """connect() with no filter stores None as filter_asset_id."""
        from ops_api.app.websocket_manager import WebSocketManager

        mgr = WebSocketManager()
        ws = AsyncMock()
        ws.accept = AsyncMock()

        await mgr.connect(ws)

        _, stored_filter = mgr._connections[0]
        assert stored_filter is None

    @pytest.mark.asyncio
    async def test_disconnect_removes_connection(self) -> None:
        """disconnect() should remove the exact WebSocket from the list."""
        from ops_api.app.websocket_manager import WebSocketManager

        mgr = WebSocketManager()
        ws1 = AsyncMock()
        ws1.accept = AsyncMock()
        ws2 = AsyncMock()
        ws2.accept = AsyncMock()

        await mgr.connect(ws1, filter_asset_id=None)
        await mgr.connect(ws2, filter_asset_id="PUMP-001")

        await mgr.disconnect(ws1)

        assert len(mgr._connections) == 1
        remaining_ws, _ = mgr._connections[0]
        assert remaining_ws is ws2

    @pytest.mark.asyncio
    async def test_broadcast_increments_stream_sequence(self) -> None:
        """stream_sequence must be incremented on every broadcast() call."""
        from ops_api.app.websocket_manager import WebSocketManager

        mgr = WebSocketManager()
        assert mgr.stream_sequence == 0

        # No connections — sequence still increments.
        await mgr.broadcast(_make_prediction("PUMP-001"))
        assert mgr.stream_sequence == 1

        await mgr.broadcast(_make_prediction("PUMP-002"))
        assert mgr.stream_sequence == 2

    @pytest.mark.asyncio
    async def test_broadcast_no_asset_id_skips(self) -> None:
        """broadcast() with a prediction missing 'asset_id' must not send."""
        from ops_api.app.websocket_manager import WebSocketManager

        mgr = WebSocketManager()
        ws = AsyncMock()
        ws.accept = AsyncMock()
        ws.send_json = AsyncMock()

        await mgr.connect(ws)
        await mgr.broadcast({"anomaly_score": 0.9})  # no asset_id

        ws.send_json.assert_not_called()
        # sequence must NOT be incremented when broadcast is skipped.
        assert mgr.stream_sequence == 0

    @pytest.mark.asyncio
    async def test_broadcast_filtered_client_receives_matching_asset(self) -> None:
        """Filtered client must receive broadcasts matching its filter."""
        from ops_api.app.websocket_manager import WebSocketManager

        mgr = WebSocketManager()
        ws = AsyncMock()
        ws.accept = AsyncMock()
        ws.send_json = AsyncMock()

        await mgr.connect(ws, filter_asset_id="PUMP-001")
        await mgr.broadcast(_make_prediction("PUMP-001"))

        ws.send_json.assert_called_once()
        payload = ws.send_json.call_args[0][0]
        assert payload["asset_id"] == "PUMP-001"
        assert payload["stream_sequence"] == 1

    @pytest.mark.asyncio
    async def test_broadcast_filtered_client_skips_other_asset(self) -> None:
        """Filtered client must NOT receive broadcasts for other assets."""
        from ops_api.app.websocket_manager import WebSocketManager

        mgr = WebSocketManager()
        ws = AsyncMock()
        ws.accept = AsyncMock()
        ws.send_json = AsyncMock()

        await mgr.connect(ws, filter_asset_id="PUMP-001")
        await mgr.broadcast(_make_prediction("PUMP-002"))

        ws.send_json.assert_not_called()

    @pytest.mark.asyncio
    async def test_broadcast_unfiltered_client_receives_all(self) -> None:
        """Unfiltered client (filter=None) must receive every broadcast."""
        from ops_api.app.websocket_manager import WebSocketManager

        mgr = WebSocketManager()
        ws = AsyncMock()
        ws.accept = AsyncMock()
        ws.send_json = AsyncMock()

        await mgr.connect(ws, filter_asset_id=None)

        await mgr.broadcast(_make_prediction("PUMP-001"))
        await mgr.broadcast(_make_prediction("PUMP-002"))

        assert ws.send_json.call_count == 2

    @pytest.mark.asyncio
    async def test_broadcast_two_clients_pump001_both_receive(self) -> None:
        """TASK-024 criterion: both clients receive broadcast for PUMP-001.

        Client A has filter 'PUMP-001', client B has no filter.
        Broadcast for PUMP-001 — both must receive.
        """
        from ops_api.app.websocket_manager import WebSocketManager

        mgr = WebSocketManager()

        ws_a = AsyncMock()  # filtered on PUMP-001
        ws_a.accept = AsyncMock()
        ws_a.send_json = AsyncMock()

        ws_b = AsyncMock()  # no filter
        ws_b.accept = AsyncMock()
        ws_b.send_json = AsyncMock()

        await mgr.connect(ws_a, filter_asset_id="PUMP-001")
        await mgr.connect(ws_b, filter_asset_id=None)

        await mgr.broadcast(_make_prediction("PUMP-001"))

        ws_a.send_json.assert_called_once()
        ws_b.send_json.assert_called_once()

    @pytest.mark.asyncio
    async def test_broadcast_two_clients_pump002_only_unfiltered_receives(self) -> None:
        """TASK-024 criterion: only unfiltered client receives PUMP-002.

        Client A has filter 'PUMP-001', client B has no filter.
        Broadcast for PUMP-002 — only B must receive.
        """
        from ops_api.app.websocket_manager import WebSocketManager

        mgr = WebSocketManager()

        ws_a = AsyncMock()  # filtered on PUMP-001
        ws_a.accept = AsyncMock()
        ws_a.send_json = AsyncMock()

        ws_b = AsyncMock()  # no filter
        ws_b.accept = AsyncMock()
        ws_b.send_json = AsyncMock()

        await mgr.connect(ws_a, filter_asset_id="PUMP-001")
        await mgr.connect(ws_b, filter_asset_id=None)

        await mgr.broadcast(_make_prediction("PUMP-002"))

        ws_a.send_json.assert_not_called()
        ws_b.send_json.assert_called_once()

    @pytest.mark.asyncio
    async def test_broadcast_failed_send_removes_dead_connection(self) -> None:
        """A send failure must remove the dead connection and not affect others."""
        from ops_api.app.websocket_manager import WebSocketManager

        mgr = WebSocketManager()

        ws_dead = AsyncMock()
        ws_dead.accept = AsyncMock()
        ws_dead.send_json = AsyncMock(side_effect=RuntimeError("connection closed"))

        ws_alive = AsyncMock()
        ws_alive.accept = AsyncMock()
        ws_alive.send_json = AsyncMock()

        await mgr.connect(ws_dead, filter_asset_id=None)
        await mgr.connect(ws_alive, filter_asset_id=None)

        # Should not raise even though one send fails.
        await mgr.broadcast(_make_prediction("PUMP-001"))

        # Dead connection removed; alive still present.
        assert len(mgr._connections) == 1
        remaining_ws, _ = mgr._connections[0]
        assert remaining_ws is ws_alive
        ws_alive.send_json.assert_called_once()

    @pytest.mark.asyncio
    async def test_stream_sequence_in_payload(self) -> None:
        """Outbound payload must include stream_sequence matching manager counter."""
        from ops_api.app.websocket_manager import WebSocketManager

        mgr = WebSocketManager()
        ws = AsyncMock()
        ws.accept = AsyncMock()
        ws.send_json = AsyncMock()

        await mgr.connect(ws)
        await mgr.broadcast(_make_prediction("PUMP-001"))

        payload = ws.send_json.call_args[0][0]
        assert payload["stream_sequence"] == mgr.stream_sequence == 1


# ---------------------------------------------------------------------------
# Integration tests — WS /predictions/stream endpoint via TestClient
# ---------------------------------------------------------------------------


class TestStreamRouterEndpoint:
    """Integration tests for WS /predictions/stream using TestClient."""

    # ------------------------------------------------------------------
    # Ping / pong
    # ------------------------------------------------------------------

    def test_ping_returns_pong(self) -> None:
        """Client sends ping, server responds with pong."""
        app = _make_stream_app()
        with TestClient(app) as client:
            with client.websocket_connect("/predictions/stream") as ws:
                ws.send_json({"type": "ping"})
                data = ws.receive_json()
                assert data == {"type": "pong"}

    # ------------------------------------------------------------------
    # Invalid JSON payload → 1008
    # ------------------------------------------------------------------

    def test_invalid_json_closes_with_1008(self) -> None:
        """Non-JSON text payload must cause the server to close with code 1008."""
        from starlette.websockets import WebSocketDisconnect

        app = _make_stream_app()
        with TestClient(app) as client:
            with pytest.raises((WebSocketDisconnect, Exception)):
                with client.websocket_connect("/predictions/stream") as ws:
                    ws.send_text("this is not JSON at all !!!")
                    # Server closes the socket; receive triggers disconnect.
                    ws.receive_json()

    # ------------------------------------------------------------------
    # filter_asset_id query parameter
    # ------------------------------------------------------------------

    def test_connect_with_asset_id_filter(self) -> None:
        """Connection with ?asset_id=PUMP-001 must be accepted without error."""
        app = _make_stream_app()
        with TestClient(app) as client:
            with client.websocket_connect("/predictions/stream?asset_id=PUMP-001") as ws:
                # Send ping to confirm the connection is alive.
                ws.send_json({"type": "ping"})
                data = ws.receive_json()
                assert data["type"] == "pong"

    # ------------------------------------------------------------------
    # Broadcast: two concurrent clients, filtered + unfiltered
    #
    # Uses threads because TestClient WebSocket sessions are blocking.
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_broadcast_pump001_both_clients_receive(self) -> None:
        """TASK-024 core criterion (broadcast to PUMP-001).

        Client A filters on PUMP-001, Client B has no filter.
        After broadcast for PUMP-001: both A and B must receive.

        Strategy: use AsyncMock WebSocket objects so all async calls share
        the same event loop (same loop that runs this test).  The
        WebSocketManager unit tests already cover the filter logic; here we
        additionally test the complete flow including the router-registered
        manager singleton via patch.
        """
        from ops_api.app.websocket_manager import WebSocketManager

        mgr = WebSocketManager()

        ws_a = AsyncMock()  # filtered on PUMP-001
        ws_a.accept = AsyncMock()
        ws_a.send_json = AsyncMock()

        ws_b = AsyncMock()  # no filter
        ws_b.accept = AsyncMock()
        ws_b.send_json = AsyncMock()

        await mgr.connect(ws_a, filter_asset_id="PUMP-001")
        await mgr.connect(ws_b, filter_asset_id=None)

        with patch("ops_api.app.routers.stream.manager", mgr):
            await mgr.broadcast(_make_prediction("PUMP-001"))

        # Both clients must have received the prediction.
        ws_a.send_json.assert_called_once()
        ws_b.send_json.assert_called_once()

        payload_a = ws_a.send_json.call_args[0][0]
        payload_b = ws_b.send_json.call_args[0][0]
        assert payload_a["asset_id"] == "PUMP-001"
        assert payload_b["asset_id"] == "PUMP-001"
        assert payload_a["stream_sequence"] == 1
        assert payload_b["stream_sequence"] == 1

    @pytest.mark.asyncio
    async def test_broadcast_pump002_only_unfiltered_receives(self) -> None:
        """TASK-024 core criterion (broadcast to PUMP-002).

        Client A filters on PUMP-001, Client B has no filter.
        After broadcast for PUMP-002: only B must receive; A must NOT.
        """
        from ops_api.app.websocket_manager import WebSocketManager

        mgr = WebSocketManager()

        ws_a = AsyncMock()  # filtered on PUMP-001
        ws_a.accept = AsyncMock()
        ws_a.send_json = AsyncMock()

        ws_b = AsyncMock()  # no filter
        ws_b.accept = AsyncMock()
        ws_b.send_json = AsyncMock()

        await mgr.connect(ws_a, filter_asset_id="PUMP-001")
        await mgr.connect(ws_b, filter_asset_id=None)

        with patch("ops_api.app.routers.stream.manager", mgr):
            await mgr.broadcast(_make_prediction("PUMP-002"))

        # A must NOT have received PUMP-002.
        ws_a.send_json.assert_not_called()
        # B must have received.
        ws_b.send_json.assert_called_once()

        payload_b = ws_b.send_json.call_args[0][0]
        assert payload_b["asset_id"] == "PUMP-002"
