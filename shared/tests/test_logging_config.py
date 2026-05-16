"""
Tests for shared/logging_config.py

Verifies:
- get_logger returns a configured logger
- Logger emits JSON with all mandatory fields: timestamp, service, level, message, trace_id
- trace_id is injected when set in context
- trace_id falls back to 'n/a' when not set
"""

import json
import logging
import io



class TestGetLogger:
    def test_get_logger_returns_logger_instance(self):
        from shared.logging_config import get_logger

        logger = get_logger("test_service")
        assert isinstance(logger, logging.Logger)

    def test_get_logger_name_matches_service(self):
        from shared.logging_config import get_logger

        logger = get_logger("oilops.test")
        assert logger.name == "oilops.test"

    def test_logger_has_json_handler(self):
        from shared.logging_config import get_logger
        from pythonjsonlogger import jsonlogger

        logger = get_logger("oilops.json_check")
        formatter_types = [
            type(h.formatter) for h in logger.handlers if hasattr(h, "formatter")
        ]
        assert any(
            issubclass(ft, jsonlogger.JsonFormatter) for ft in formatter_types
        ), f"At least one handler must use JsonFormatter (subclass). Got: {formatter_types}"


class TestJsonFields:
    """Verifies that emitted log records contain all mandatory fields."""

    def _capture_log_output(self, service_name: str, message: str, level=logging.INFO) -> dict:
        """
        Helper: attach a temporary in-memory StreamHandler to the logger
        obtained via get_logger() and capture the first JSON line emitted.

        A fresh _OilOpsJsonFormatter instance is used for the capture handler
        so the test is independent of the handler already attached by get_logger.
        """
        from shared.logging_config import get_logger, _OilOpsJsonFormatter

        logger = get_logger(service_name)

        buffer = io.StringIO()
        capture_handler = logging.StreamHandler(buffer)
        # Use the same formatter class as production code.
        capture_handler.setFormatter(
            _OilOpsJsonFormatter(fmt="%(asctime)s %(levelname)s %(message)s")
        )
        logger.addHandler(capture_handler)
        try:
            logger.log(level, message)
        finally:
            logger.removeHandler(capture_handler)

        buffer.seek(0)
        raw = buffer.getvalue().strip()
        assert raw, "Logger produced no output"
        # The handler may emit multiple lines (e.g. one per handler).
        # We parse the last non-empty line as that's the capture handler's.
        lines = [ln for ln in raw.splitlines() if ln.strip()]
        return json.loads(lines[-1])

    def test_mandatory_fields_present(self):
        record = self._capture_log_output("svc.mandatory", "test mandatory fields")
        for field in ("timestamp", "service", "level", "message", "trace_id"):
            assert field in record, f"Mandatory field '{field}' missing from log record"

    def test_message_field_matches_input(self):
        record = self._capture_log_output("svc.msg", "hello oilops")
        assert record["message"] == "hello oilops"

    def test_service_field_matches_logger_name(self):
        record = self._capture_log_output("svc.named", "service name check")
        assert record["service"] == "svc.named"

    def test_level_field_is_info(self):
        record = self._capture_log_output("svc.level", "level check", logging.INFO)
        assert record["level"].upper() == "INFO"

    def test_level_field_is_warning(self):
        record = self._capture_log_output("svc.warn", "warning check", logging.WARNING)
        assert record["level"].upper() in ("WARNING", "WARN")

    def test_timestamp_field_is_present_and_non_empty(self):
        record = self._capture_log_output("svc.ts", "timestamp check")
        assert record["timestamp"], "timestamp field must not be empty"

    def test_trace_id_fallback_when_not_set(self):
        from shared.logging_config import clear_trace_id

        clear_trace_id()
        record = self._capture_log_output("svc.notrace", "no trace")
        assert record["trace_id"] == "n/a", (
            f"Expected trace_id='n/a' when not set, got '{record['trace_id']}'"
        )

    def test_trace_id_injected_from_context(self):
        from shared.logging_config import set_trace_id, clear_trace_id

        set_trace_id("abc-123-xyz")
        try:
            record = self._capture_log_output("svc.withtrace", "with trace")
            assert record["trace_id"] == "abc-123-xyz"
        finally:
            clear_trace_id()


class TestTraceIdContextVar:
    def test_set_and_get_trace_id(self):
        from shared.logging_config import set_trace_id, get_trace_id, clear_trace_id

        set_trace_id("trace-001")
        assert get_trace_id() == "trace-001"
        clear_trace_id()

    def test_clear_trace_id_returns_na(self):
        from shared.logging_config import set_trace_id, get_trace_id, clear_trace_id

        set_trace_id("trace-002")
        clear_trace_id()
        assert get_trace_id() == "n/a"
