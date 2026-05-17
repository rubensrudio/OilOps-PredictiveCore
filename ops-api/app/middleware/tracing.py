"""
ops-api/app/middleware/tracing.py
===================================
TracingMiddleware — generates and propagates a ``trace_id`` UUID for every
incoming HTTP request.

Behaviour
---------
1. If the incoming request already carries an ``X-Trace-Id`` header, its
   value is reused as-is (pass-through for upstream propagation).
2. If the header is absent, a new UUIDv4 is generated.
3. The resolved ``trace_id`` is:
   a. Written into the shared ``contextvars.ContextVar`` via
      :func:`shared.logging_config.set_trace_id` — making it available to all
      log statements emitted within that request's async context.
   b. Injected as ``X-Trace-Id`` in the response headers so clients and
      downstream services can correlate logs.
4. After the request completes the ContextVar is cleared via the public
   :func:`shared.logging_config.clear_trace_id` function, restoring the
   ``"n/a"`` sentinel and ensuring clean state for the next request even in
   shared-process scenarios.

The middleware depends only on ``shared/logging_config.py`` (already a
project-level dependency from TASK-001) and the standard library ``uuid``
module; no additional packages are required.
"""

from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

_REQUEST_HEADER: str = "X-Trace-Id"
_RESPONSE_HEADER: str = "X-Trace-Id"


class TracingMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that generates and propagates a trace_id UUID.

    Usage::

        app.add_middleware(TracingMiddleware)

    The middleware reads ``X-Trace-Id`` from the request headers.  If present,
    the value is reused; otherwise a new UUIDv4 is generated.  The resolved
    ``trace_id`` is stored in the shared ContextVar (making it visible to the
    JSON logger) and echoed back in ``X-Trace-Id`` on the response.
    """

    async def dispatch(self, request: Request, call_next: object) -> Response:  # type: ignore[override]
        # Resolve trace_id — prefer inbound header over generated UUID.
        inbound = request.headers.get(_REQUEST_HEADER)
        trace_id: str = inbound if inbound else str(uuid.uuid4())

        # Push trace_id into the shared ContextVar.  Import is deferred to
        # runtime so that the module can be loaded even when ``shared/`` is not
        # on sys.path (e.g. isolated unit tests that mock the import).
        from shared.logging_config import set_trace_id  # noqa: PLC0415

        set_trace_id(trace_id)

        try:
            response: Response = await call_next(request)  # type: ignore[arg-type]
        finally:
            # Reset the ContextVar to the "n/a" sentinel via the public API.
            from shared.logging_config import clear_trace_id  # noqa: PLC0415

            clear_trace_id()

        response.headers[_RESPONSE_HEADER] = trace_id
        return response
