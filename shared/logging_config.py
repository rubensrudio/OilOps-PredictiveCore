"""
shared/logging_config.py
========================
Centralised JSON-structured logging for all OilOps-PredictiveCore services.

Every log record emitted by a logger obtained through :func:`get_logger` will
contain the following mandatory fields:

  - ``timestamp``  — ISO-8601 UTC timestamp of the log event
  - ``service``    — name passed to :func:`get_logger` (mirrors the logger name)
  - ``level``      — log level (INFO, WARNING, ERROR, …)
  - ``message``    — human-readable log message
  - ``trace_id``   — distributed tracing identifier; ``"n/a"`` when not set

The ``trace_id`` is stored in a :mod:`contextvars` ``ContextVar`` so that
FastAPI middleware can set it once per request and every log call within that
request (across awaits) will automatically include the correct value.

Public API
----------
- :func:`get_logger`    — obtain a named, JSON-formatted logger
- :func:`set_trace_id`  — set the current request's trace_id
- :func:`get_trace_id`  — read the current trace_id (returns "n/a" if unset)
- :func:`clear_trace_id`— reset trace_id to the "n/a" sentinel
"""

from __future__ import annotations

import datetime
import logging
import sys
from contextvars import ContextVar, Token
from typing import Any, MutableMapping

from pythonjsonlogger import jsonlogger

# ---------------------------------------------------------------------------
# Trace-id context variable
# ---------------------------------------------------------------------------

_TRACE_ID_SENTINEL: str = "n/a"
_trace_id_var: ContextVar[str] = ContextVar("trace_id", default=_TRACE_ID_SENTINEL)


def set_trace_id(trace_id: str) -> Token[str]:
    """Set the trace_id for the current async context.

    Returns the :class:`contextvars.Token` produced by
    :meth:`~contextvars.ContextVar.set`.  Callers that need to restore the
    previous value (e.g. middleware that wraps a single request) should keep
    the token and call ``_trace_id_var.reset(token)`` when done.  For most
    application code, the token can be safely discarded.
    """
    return _trace_id_var.set(trace_id)


def get_trace_id() -> str:
    """Return the current trace_id, or ``"n/a"`` when none has been set."""
    return _trace_id_var.get(_TRACE_ID_SENTINEL)


def clear_trace_id() -> None:
    """Reset the trace_id to the ``"n/a"`` sentinel value."""
    _trace_id_var.set(_TRACE_ID_SENTINEL)


# ---------------------------------------------------------------------------
# Custom JSON formatter
# ---------------------------------------------------------------------------

class _OilOpsJsonFormatter(jsonlogger.JsonFormatter):
    """
    Extends :class:`pythonjsonlogger.jsonlogger.JsonFormatter` to:

    1. Rename ``asctime`` → ``timestamp`` (mandatory field name).
    2. Rename ``levelname`` → ``level``.
    3. Always inject ``service`` (= logger name) and ``trace_id``.

    The ``fmt`` string passed to the parent controls which *additional*
    standard fields are included.  We explicitly list only the fields we want
    to avoid leaking noisy internal attributes.
    """

    def add_fields(
        self,
        log_record: MutableMapping[str, Any],
        record: logging.LogRecord,
        message_dict: MutableMapping[str, Any],
    ) -> None:
        super().add_fields(log_record, record, message_dict)

        # --- mandatory field: timestamp (ISO-8601 UTC) ----------------------
        # jsonlogger already sets 'asctime' when the fmt contains %(asctime)s.
        # We rename it so callers always see 'timestamp'.
        if "asctime" in log_record:
            log_record["timestamp"] = log_record.pop("asctime")
        elif "timestamp" not in log_record:
            # Fallback: compute from the log record's created epoch value.
            # datetime.datetime.fromtimestamp(..., tz=utc) is used instead of
            # the deprecated utcfromtimestamp() which produces a naive datetime
            # and raises DeprecationWarning on Python 3.12+.
            log_record["timestamp"] = (
                datetime.datetime.fromtimestamp(
                    record.created, tz=datetime.timezone.utc
                ).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
                + "Z"
            )

        # --- mandatory field: level ----------------------------------------
        if "levelname" in log_record:
            log_record["level"] = log_record.pop("levelname")
        elif "level" not in log_record:
            log_record["level"] = record.levelname

        # --- mandatory field: service (= logger name) ----------------------
        log_record["service"] = record.name

        # --- mandatory field: trace_id ------------------------------------
        log_record["trace_id"] = get_trace_id()

        # Ensure 'message' is always present (jsonlogger sets it as the
        # formatted message; we keep that but guarantee the key exists).
        if "message" not in log_record:
            log_record["message"] = record.getMessage()


# ---------------------------------------------------------------------------
# Logger factory
# ---------------------------------------------------------------------------

# Keep track of loggers we have already configured so that calling
# get_logger("same.name") twice does not add duplicate handlers.
_configured_loggers: set[str] = set()


def get_logger(service_name: str, level: int = logging.INFO) -> logging.Logger:
    """
    Return a :class:`logging.Logger` configured to emit JSON records.

    Parameters
    ----------
    service_name:
        Identifier injected into every record's ``service`` field.  Follows
        the standard Python logger hierarchy (dots separate levels).
    level:
        Minimum log level.  Defaults to :data:`logging.INFO`.

    Returns
    -------
    logging.Logger
        A logger instance with exactly one :class:`logging.StreamHandler`
        writing to *stdout* using :class:`_OilOpsJsonFormatter`.

    .. note::
        **Idempotency / level-change limitation** — once a logger has been
        configured by a first call to ``get_logger(name)``, subsequent calls
        with the *same* ``service_name`` but a *different* ``level`` return the
        existing logger **without** updating its level.  This is intentional to
        avoid duplicate handlers, but it means the level set on the first call
        wins for the lifetime of the process.  If you need to change the level
        at runtime, retrieve the logger via ``logging.getLogger(name)`` and call
        ``.setLevel()`` directly.
    """
    logger = logging.getLogger(service_name)

    if service_name in _configured_loggers:
        return logger

    logger.setLevel(level)
    logger.propagate = False  # prevent double-logging via root logger

    formatter = _OilOpsJsonFormatter(
        # Include asctime so the formatter has a value to rename to 'timestamp'.
        # NOTE: datefmt intentionally omitted so that formatTime() uses its
        # default ISO-style representation.  The add_fields() method overrides
        # the key name from 'asctime' to 'timestamp' regardless of the format.
        fmt="%(asctime)s %(levelname)s %(message)s",
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)
    logger.addHandler(handler)

    _configured_loggers.add(service_name)
    return logger
