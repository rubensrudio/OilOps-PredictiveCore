"""
ops-api/app/middleware/advisory.py
===================================
AdvisoryMiddleware — injects the mandatory ``X-Advisory-Only: true`` header
into every HTTP response emitted by the ops-api service (RN-06).

Design
------
- Implemented as a pure ASGI middleware (not ``BaseHTTPMiddleware``) to ensure
  the advisory header is injected even on 500-level error responses.
  ``BaseHTTPMiddleware`` in Starlette 0.37+ bypasses ``dispatch()`` for
  unhandled server errors, so a raw ASGI wrapper is the only approach that
  guarantees the header on *every* response including HTTP 500.
- The header value is always the string ``"true"`` (lower-case) to match
  the canonical form specified in plan.md §5 and the RN-06 requirement.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

_HEADER_NAME: bytes = b"x-advisory-only"
_HEADER_VALUE: bytes = b"true"


class AdvisoryMiddleware:
    """Pure ASGI middleware that stamps every response with the advisory header.

    This is a raw ASGI middleware (not ``BaseHTTPMiddleware``) so that it
    intercepts the ``http.response.start`` ASGI event and appends the header
    before any bytes are sent to the client — including error responses that
    Starlette generates internally for unhandled exceptions.

    Usage::

        app.add_middleware(AdvisoryMiddleware)

    """

    def __init__(self, app: Callable[..., Any]) -> None:
        self._app = app

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Coroutine[Any, Any, Any]],
        send: Callable[[dict[str, Any]], Coroutine[Any, Any, None]],
    ) -> None:
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return

        async def _send_with_header(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.start":
                headers: list[tuple[bytes, bytes]] = list(message.get("headers", []))
                headers.append((_HEADER_NAME, _HEADER_VALUE))
                message = {**message, "headers": headers}
            await send(message)

        await self._app(scope, receive, _send_with_header)
