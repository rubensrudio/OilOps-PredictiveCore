"""
ops-cli/main.py
===============
OilOps-PredictiveCore command-line interface built with Typer.

Commands
--------
inspect  — Query the latest prediction for an asset (implemented).
deploy   — Deploy a new model version          [stub — Fase 2].
train    — Trigger model training              [stub — Fase 2].
evaluate — Evaluate model performance          [stub — Fase 2].
replay   — Replay ingestion events             [stub — Fase 2].
export   — Export predictions                  [stub — Fase 2].

Design notes
------------
* The ``inspect`` command calls ``GET /predictions/{asset_id}`` on the
  ops-api service (plan.md § 5.2, INIT-US-02).  The base URL is resolved
  from the ``OPS_API_URL`` environment variable (default:
  ``http://localhost:8000``).

* Output is rendered via Rich: a one-row Table with the fields defined in
  the PredictionResult schema (TASK-017): ``anomaly_score``,
  ``confidence_score``, ``alert``, ``severity``, ``explain_status``.

* HTTP 404 → prints a human-readable "not found" message and exits 0
  (informational, not an error).

* Any other HTTP error or network failure → prints the error and exits 1.

* Stub commands all call ``raise typer.Exit(1)`` after printing the
  "Not implemented — Fase 2" notice so callers can detect the non-zero
  exit code programmatically.

References
----------
- tasks.md TASK-032
- spec.md INIT-US-02
- plan.md § 5.2, DA-06
"""

from __future__ import annotations

import os

import httpx
import typer
from rich.console import Console
from rich.table import Table

app = typer.Typer(help="OilOps-PredictiveCore CLI — advisory only")
console = Console()

_OPS_API_URL: str = os.environ.get("OPS_API_URL", "http://localhost:8000")


# ---------------------------------------------------------------------------
# inspect
# ---------------------------------------------------------------------------


@app.command()
def inspect(
    asset_id: str = typer.Argument(..., help="Asset ID to inspect"),
) -> None:
    """Query latest prediction for an asset.

    Calls ``GET /predictions/{asset_id}`` on the ops-api service and
    displays the result in a formatted Rich table.  When no prediction
    exists for the given asset the message is printed and the command
    exits with code 0.  Network errors and unexpected HTTP responses
    cause exit code 1.
    """
    url = f"{_OPS_API_URL}/predictions/{asset_id}"

    try:
        response = httpx.get(url, timeout=10.0)
    except httpx.RequestError as exc:
        console.print(f"[red]Error: could not reach ops-api — {exc}[/red]")
        raise typer.Exit(1) from exc

    if response.status_code == 404:
        console.print(
            f"[yellow]No prediction found for asset '{asset_id}'.[/yellow]"
        )
        return

    if response.status_code != 200:
        console.print(
            f"[red]Error: ops-api returned HTTP {response.status_code}[/red]"
        )
        raise typer.Exit(1)

    data: dict = response.json()

    table = Table(
        title=f"Latest prediction — {asset_id}",
        show_header=True,
        header_style="bold cyan",
    )
    table.add_column("Field", style="bold")
    table.add_column("Value")

    table.add_row("prediction_id", str(data.get("prediction_id", "—")))
    table.add_row("asset_class", str(data.get("asset_class", "—")))
    table.add_row("anomaly_score", str(data.get("anomaly_score", "—")))
    table.add_row("confidence_score", str(data.get("confidence_score", "—")))

    alert_value = data.get("alert")
    alert_display = (
        "[red]YES[/red]"
        if alert_value
        else "[green]NO[/green]"
        if alert_value is not None
        else "—"
    )
    table.add_row("alert", alert_display)

    severity = data.get("severity") or "—"
    if severity not in ("—", None):
        colour = {"low": "yellow", "medium": "orange3", "high": "red"}.get(
            severity, "white"
        )
        severity_display = f"[{colour}]{severity}[/{colour}]"
    else:
        severity_display = severity
    table.add_row("severity", severity_display)

    table.add_row("explain_status", str(data.get("explain_status", "—")))
    table.add_row("predicted_at", str(data.get("predicted_at", "—")))
    table.add_row("model_version", str(data.get("model_version", "—")))

    console.print(table)
    console.print(
        "[italic dim]ADVISORY ONLY — this system does not replace "
        "safety-instrumented systems.[/italic dim]"
    )


# ---------------------------------------------------------------------------
# Stubs — Fase 2
# ---------------------------------------------------------------------------


@app.command()
def deploy() -> None:
    """Deploy a new model version. [Not implemented — Fase 2]"""
    console.print("[yellow]Not implemented — Fase 2[/yellow]")
    raise typer.Exit(1)


@app.command()
def train() -> None:
    """Trigger model training. [Not implemented — Fase 2]"""
    console.print("[yellow]Not implemented — Fase 2[/yellow]")
    raise typer.Exit(1)


@app.command()
def evaluate() -> None:
    """Evaluate model performance. [Not implemented — Fase 2]"""
    console.print("[yellow]Not implemented — Fase 2[/yellow]")
    raise typer.Exit(1)


@app.command()
def replay() -> None:
    """Replay ingestion events. [Not implemented — Fase 2]"""
    console.print("[yellow]Not implemented — Fase 2[/yellow]")
    raise typer.Exit(1)


@app.command()
def export() -> None:
    """Export predictions. [Not implemented — Fase 2]"""
    console.print("[yellow]Not implemented — Fase 2[/yellow]")
    raise typer.Exit(1)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()
