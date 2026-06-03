"""
ops-cli/tests/test_cli.py
=========================
Unit tests for the OilOps-PredictiveCore CLI (TASK-032).

All network calls are intercepted via ``unittest.mock.patch`` so the tests
run without a live ops-api service.

Test matrix
-----------
- test_inspect_help              — ``--help`` flag exits 0 and mentions "asset".
- test_inspect_no_help           — bare ``inspect`` (no argument) exits non-zero
                                   (Typer enforces the required argument).
- test_deploy_returns_exit_1     — stub exits 1 with "Fase 2" in output.
- test_train_returns_exit_1      — stub exits 1 with "Fase 2" in output.
- test_evaluate_returns_exit_1   — stub exits 1 with "Fase 2" in output.
- test_replay_returns_exit_1     — stub exits 1 with "Fase 2" in output.
- test_export_returns_exit_1     — stub exits 1 with "Fase 2" in output.
- test_inspect_404_shows_message — HTTP 404 → "not found" message, exit 0.
- test_inspect_success           — HTTP 200 with prediction payload → output
                                   contains anomaly_score value.
- test_inspect_http_error        — non-200/non-404 response → exit 1 with
                                   error message.
- test_inspect_network_error     — RequestError (unreachable) → exit 1 with
                                   error message.

References
----------
- tasks.md TASK-032
- plan.md DA-06
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
from typer.testing import CliRunner

from main import app  # type: ignore[import]  # resolved via conftest.py sys.path injection

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

runner = CliRunner()

_SAMPLE_PREDICTION: dict = {
    "prediction_id": "550e8400-e29b-41d4-a716-446655440000",
    "asset_id": "PUMP-001",
    "asset_class": "rotating_equipment",
    "anomaly_score": 0.87,
    "confidence_score": 0.92,
    "alert": True,
    "severity": "high",
    "predicted_at": "2026-05-17T10:01:30Z",
    "model_version": "vibration-autoencoder-v1",
    "explain_status": "pending",
}


def _make_response(status_code: int, json_body: dict | None = None) -> MagicMock:
    """Build a fake httpx.Response-like MagicMock."""
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.status_code = status_code
    mock_resp.json.return_value = json_body or {}
    return mock_resp


# ---------------------------------------------------------------------------
# --help
# ---------------------------------------------------------------------------


def test_inspect_help() -> None:
    """inspect --help exits 0 and documents the asset_id argument."""
    result = runner.invoke(app, ["inspect", "--help"])
    assert result.exit_code == 0, result.output
    assert "asset" in result.output.lower()


# ---------------------------------------------------------------------------
# Stubs — Fase 2
# ---------------------------------------------------------------------------


def test_deploy_returns_exit_1() -> None:
    """deploy stub exits 1 and prints the Fase 2 notice."""
    result = runner.invoke(app, ["deploy"])
    assert result.exit_code != 0
    assert "Fase 2" in result.output


def test_train_returns_exit_1() -> None:
    """train stub exits 1 and prints the Fase 2 notice."""
    result = runner.invoke(app, ["train"])
    assert result.exit_code != 0
    assert "Fase 2" in result.output


def test_evaluate_returns_exit_1() -> None:
    """evaluate stub exits 1 and prints the Fase 2 notice."""
    result = runner.invoke(app, ["evaluate"])
    assert result.exit_code != 0
    assert "Fase 2" in result.output


def test_replay_returns_exit_1() -> None:
    """replay stub exits 1 and prints the Fase 2 notice."""
    result = runner.invoke(app, ["replay"])
    assert result.exit_code != 0
    assert "Fase 2" in result.output


def test_export_returns_exit_1() -> None:
    """export stub exits 1 and prints the Fase 2 notice."""
    result = runner.invoke(app, ["export"])
    assert result.exit_code != 0
    assert "Fase 2" in result.output


# ---------------------------------------------------------------------------
# inspect — HTTP 404
# ---------------------------------------------------------------------------


def test_inspect_404_shows_message() -> None:
    """HTTP 404 from ops-api prints a 'not found' message and exits 0."""
    mock_resp = _make_response(
        404,
        {"detail": "No predictions found for asset_id 'PUMP-404'"},
    )

    with patch("main.httpx.get", return_value=mock_resp):
        result = runner.invoke(app, ["inspect", "PUMP-404"])

    assert result.exit_code == 0, result.output
    output_lower = result.output.lower()
    assert "not" in output_lower or "found" in output_lower or "no prediction" in output_lower


# ---------------------------------------------------------------------------
# inspect — HTTP 200 (success)
# ---------------------------------------------------------------------------


def test_inspect_success() -> None:
    """HTTP 200 with a prediction payload renders anomaly_score in the output."""
    mock_resp = _make_response(200, _SAMPLE_PREDICTION)

    with patch("main.httpx.get", return_value=mock_resp):
        result = runner.invoke(app, ["inspect", "PUMP-001"])

    assert result.exit_code == 0, result.output
    assert "0.87" in result.output  # anomaly_score value


def test_inspect_success_renders_confidence_score() -> None:
    """HTTP 200 response also shows confidence_score in the table."""
    mock_resp = _make_response(200, _SAMPLE_PREDICTION)

    with patch("main.httpx.get", return_value=mock_resp):
        result = runner.invoke(app, ["inspect", "PUMP-001"])

    assert result.exit_code == 0, result.output
    assert "0.92" in result.output  # confidence_score value


def test_inspect_success_renders_explain_status() -> None:
    """HTTP 200 response shows explain_status in the table."""
    mock_resp = _make_response(200, _SAMPLE_PREDICTION)

    with patch("main.httpx.get", return_value=mock_resp):
        result = runner.invoke(app, ["inspect", "PUMP-001"])

    assert result.exit_code == 0, result.output
    assert "pending" in result.output  # explain_status value


# ---------------------------------------------------------------------------
# inspect — HTTP error (non-200, non-404)
# ---------------------------------------------------------------------------


def test_inspect_http_error() -> None:
    """Non-200/non-404 HTTP response exits 1 with an error message."""
    mock_resp = _make_response(502, {"detail": "ops-store unreachable"})

    with patch("main.httpx.get", return_value=mock_resp):
        result = runner.invoke(app, ["inspect", "PUMP-001"])

    assert result.exit_code != 0
    assert "502" in result.output or "error" in result.output.lower()


# ---------------------------------------------------------------------------
# inspect — network / connection error
# ---------------------------------------------------------------------------


def test_inspect_network_error() -> None:
    """RequestError (unreachable host) exits 1 with an error message."""
    with patch(
        "main.httpx.get",
        side_effect=httpx.ConnectError("Connection refused"),
    ):
        result = runner.invoke(app, ["inspect", "PUMP-001"])

    assert result.exit_code != 0
    output_lower = result.output.lower()
    assert "error" in output_lower or "could not reach" in output_lower
