"""ops-models FastAPI application — TASK-018.

Exposes four internal endpoints:

- ``POST /internal/predict``          — Run inference via OnnxRunner, calculate
                                        alert/severity from model thresholds,
                                        and fire-and-forget the prediction to
                                        ops-store via BackgroundTask.
- ``POST /internal/models/deploy``    — Accept a multipart/form-data upload of
                                        an ``.onnx`` artefact plus a JSON
                                        metadata payload; validate, save, and
                                        register with ModelRegistry.
- ``GET  /internal/models/{model_id}``— Return the stored model metadata dict.
- ``GET  /health``                    — Liveness probe.

Design notes
------------
- ``ModelRegistry`` and ``OnnxRunner`` are created once per request via
  FastAPI *generator Depends* (``yield`` + ``finally: close()``).
  ``OnnxRunner`` is only instantiated when we have an active model path, so
  its construction is deferred to the request path rather than startup.
- ``OPS_STORE_URL`` is read from the ``OPS_STORE_URL`` environment variable
  (default: ``http://ops-store:8002``).  Persistence to ops-store is
  fire-and-forget via ``BackgroundTasks``.
- The ``features`` field in ``PredictionRequest`` is an optional extension
  for Phase 1 (TASK-022 will wire the real feature_records flow).  When
  absent the endpoint returns 422 because inference cannot proceed without
  an input vector.
- Only ``.onnx`` files are accepted.  Any other extension causes HTTP 422
  with a human-readable error (INIT-US-07-AC3, plan.md § 5.5).

Severity mapping
----------------
Given ``severity_thresholds`` (JSON stored in the registry, e.g.
``{"low": 0.5, "medium": 0.75, "high": 0.9}``), the highest threshold that
the ``anomaly_score`` exceeds determines severity:

    high   → score >= thresholds["high"]
    medium → score >= thresholds["medium"]
    low    → score >= thresholds["low"]
    None   → alert is False (score < anomaly_threshold)
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

import httpx
from fastapi import BackgroundTasks, Depends, FastAPI, File, Form, HTTPException, UploadFile
from pydantic import Field

from ops_models.app.schemas import (
    ModelDeployRequest,
    ModelDeployResponse,
    PredictionRequest,
    PredictionResult,
)
from ops_models.app.serving.model_registry import ModelRegistry
from ops_models.app.serving.onnx_runner import OnnxRunner
from shared.logging_config import get_logger


# ---------------------------------------------------------------------------
# Phase-1 request extension
# ---------------------------------------------------------------------------


class Phase1PredictionRequest(PredictionRequest):
    """PredictionRequest extended with an inline ``features`` vector.

    Phase 1 simplification: the caller supplies the feature vector directly
    in the request body.  TASK-022 will replace this with the real
    feature_records lookup from ops-store (plan.md § 3.2).
    """

    features: list[float] = Field(
        ...,
        min_length=1,
        description=(
            "Phase-1 inline feature vector for inference. "
            "TASK-022 will wire this to the feature_records store."
        ),
    )

# ---------------------------------------------------------------------------
# Module-level setup
# ---------------------------------------------------------------------------

_logger = get_logger("ops-models")

_OPS_STORE_URL: str = os.environ.get("OPS_STORE_URL", "http://ops-store:8002")

# Directory where artefacts are persisted (configurable via env var).
_DATA_DIR: Path = Path(os.environ.get("OILOPS_DATA_DIR", "/data"))
_MODEL_DATA_DIR: Path = _DATA_DIR / "models"

app = FastAPI(
    title="ops-models",
    description=(
        "Internal model-serving service for OilOps-PredictiveCore. "
        "WARNING: This system is ADVISORY ONLY. "
        "It does NOT replace safety-instrumented systems (RN-06)."
    ),
    version="0.1.0",
)


# ---------------------------------------------------------------------------
# Dependency providers
# ---------------------------------------------------------------------------

# Shared in-process registry — one instance per process lifetime.
# In production this would point at a persistent DB path; for testing the
# caller can override this via dependency override.
_REGISTRY_DB_PATH: str = os.environ.get(
    "OILOPS_REGISTRY_DB", str(_DATA_DIR / "model_registry.db")
)


def get_registry() -> Generator[ModelRegistry, None, None]:
    """FastAPI Depends generator that yields a ModelRegistry and closes it."""
    registry = ModelRegistry(db_path=_REGISTRY_DB_PATH)
    try:
        yield registry
    finally:
        registry.close()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _calculate_severity(
    anomaly_score: float,
    severity_thresholds: dict[str, Any],
) -> str | None:
    """Return severity string based on anomaly_score and configured thresholds.

    Parameters
    ----------
    anomaly_score:
        Score produced by the ONNX runner (0–1).
    severity_thresholds:
        Dict with keys ``"low"``, ``"medium"``, ``"high"`` mapping to float
        threshold values.  If a key is absent it is treated as unreachable.

    Returns
    -------
    ``"high"``, ``"medium"``, ``"low"``, or ``None`` (score below all thresholds).
    """
    high = severity_thresholds.get("high")
    medium = severity_thresholds.get("medium")
    low = severity_thresholds.get("low")

    if high is not None and anomaly_score >= float(high):
        return "high"
    if medium is not None and anomaly_score >= float(medium):
        return "medium"
    if low is not None and anomaly_score >= float(low):
        return "low"
    return None


def _build_persist_payload(result: PredictionResult) -> dict[str, Any]:
    """Build the ``WritePredictionRequest`` envelope expected by ops-store.

    ops-store ``POST /internal/predictions`` requires the envelope::

        {
            "prediction": { ...all PredictionResult fields... },
            "audit_event": {
                "id": "<uuid4>",
                "event_type": "prediction",
                "prediction_id": "...",
                "asset_id": "...",
                "model_version": "...",
                "triggered_at": "<ISO 8601 UTC>",
                "confidence_score": 0.8,
                "trace_id": null,
                "details": {"anomaly_score": 0.7, "alert": true, "severity": "medium"}
            }
        }

    Parameters
    ----------
    result:
        The ``PredictionResult`` returned by inference.

    Returns
    -------
    dict
        Ready-to-serialise payload matching ``WritePredictionRequest``.
    """
    prediction_dict = result.model_dump(mode="json")

    audit_event: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "event_type": "prediction",
        "prediction_id": result.prediction_id,
        "asset_id": result.asset_id,
        "model_version": result.model_version,
        "triggered_at": datetime.now(timezone.utc).isoformat(),
        "confidence_score": result.confidence_score,
        "trace_id": None,
        "details": {
            "anomaly_score": result.anomaly_score,
            "alert": result.alert,
            "severity": result.severity,
        },
    }

    return {"prediction": prediction_dict, "audit_event": audit_event}


def _persist_prediction_background(result: PredictionResult) -> None:
    """Fire-and-forget: POST prediction envelope to ops-store.

    Builds the ``WritePredictionRequest`` envelope (``prediction`` + ``audit_event``)
    required by ``POST /internal/predictions`` in ops-store and sends it.
    Failures are logged but do NOT surface to the caller (BackgroundTask).

    Parameters
    ----------
    result:
        The ``PredictionResult`` from inference.  The envelope is constructed
        here (not in the request handler) so that ``audit_event.triggered_at``
        reflects the actual persistence timestamp.
    """
    try:
        payload = _build_persist_payload(result)
        with httpx.Client(timeout=5.0) as client:
            resp = client.post(
                f"{_OPS_STORE_URL}/internal/predictions",
                json=payload,
            )
            if resp.status_code not in (200, 201):
                _logger.warning(
                    "ops-store /internal/predictions returned non-2xx",
                    extra={"status_code": resp.status_code, "body": resp.text[:200]},
                )
    except Exception as exc:  # noqa: BLE001
        _logger.error(
            "Failed to persist prediction to ops-store",
            extra={"error": str(exc)},
        )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.get("/health")
def health() -> dict[str, str]:
    """Liveness probe — always returns 200 when the process is running."""
    return {"status": "ok", "service": "ops-models"}


@app.post("/internal/predict", response_model=PredictionResult)
def predict(
    request: Phase1PredictionRequest,
    background_tasks: BackgroundTasks,
    registry: ModelRegistry = Depends(get_registry),
) -> PredictionResult:
    """Execute inference and return a PredictionResult.

    Steps
    -----
    1. Look up the active model for the requested ``asset_class``.
       Return HTTP 503 if none found.
    2. Import OnnxRunner lazily (avoids import-time onnxruntime dependency
       when registry has no model path yet).
    3. Run inference using the ``features`` vector supplied in the request
       body (Phase 1 simplification — TASK-022 adds the feature_records flow).
    4. Compute ``alert`` and ``severity`` from model thresholds.
    5. Build a ``PredictionResult`` with ``explain_status="pending"``.
    6. Schedule fire-and-forget persistence to ops-store.
    """
    # Step 1: resolve active model.
    model_meta = registry.get_active_model(request.asset_class)
    if model_meta is None:
        raise HTTPException(
            status_code=503,
            detail=f"No active model for {request.asset_class}",
        )

    # Step 2 & 3: run inference.
    features: list[float] = request.features
    runner = OnnxRunner(model_meta["artifact_path"])
    scores = runner.run(features)
    anomaly_score: float = scores["anomaly_score"]
    confidence_score: float = scores["confidence_score"]

    # Step 4: compute alert & severity.
    anomaly_threshold: float = float(model_meta.get("anomaly_threshold", 0.5))
    alert: bool = anomaly_score >= anomaly_threshold

    severity: str | None = None
    if alert:
        raw_thresholds = model_meta.get("severity_thresholds", "{}")
        if isinstance(raw_thresholds, str):
            try:
                severity_thresholds: dict[str, Any] = json.loads(raw_thresholds)
            except json.JSONDecodeError:
                severity_thresholds = {}
        else:
            severity_thresholds = raw_thresholds
        severity = _calculate_severity(anomaly_score, severity_thresholds)

    # Step 5: build result.
    prediction_id = str(uuid.uuid4())
    result = PredictionResult(
        prediction_id=prediction_id,
        asset_id=request.asset_id,
        asset_class=request.asset_class,
        anomaly_score=anomaly_score,
        confidence_score=confidence_score,
        alert=alert,
        severity=severity,
        model_version=model_meta.get("version", "unknown"),
        explain_status="pending",
    )

    # Step 6: fire-and-forget persistence.
    # Pass the full PredictionResult object so _persist_prediction_background
    # can build the WritePredictionRequest envelope (prediction + audit_event).
    background_tasks.add_task(
        _persist_prediction_background,
        result,
    )

    _logger.info(
        "Prediction completed",
        extra={
            "prediction_id": prediction_id,
            "asset_id": request.asset_id,
            "asset_class": request.asset_class,
            "anomaly_score": anomaly_score,
            "alert": alert,
        },
    )

    return result


@app.post("/internal/models/deploy", response_model=ModelDeployResponse)
async def deploy_model(
    artifact: UploadFile = File(...),
    metadata: str = Form(...),
    registry: ModelRegistry = Depends(get_registry),
) -> ModelDeployResponse:
    """Deploy a new ONNX model artefact.

    Steps
    -----
    1. Validate that the uploaded file has a ``.onnx`` extension (HTTP 422
       for any other format — plan.md § 5.5, INIT-US-07-AC3).
    2. Parse the ``metadata`` JSON field into ``ModelDeployRequest``.
    3. Save the artefact to ``_MODEL_DATA_DIR/{model_id}.onnx``.
    4. Register the model in the registry (``is_active=0`` by default).
    5. If ``metadata.is_active`` is ``True`` (or not specified, defaulting to
       ``True`` as per the task description), activate the registered version.
    6. Return ``ModelDeployResponse``.
    """
    # Step 1: validate file extension.
    filename: str = artifact.filename or ""
    if not filename.lower().endswith(".onnx"):
        ext = Path(filename).suffix or "(no extension)"
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unsupported artifact format '{ext}'. "
                "Only '.onnx' files are accepted."
            ),
        )

    # Step 2: parse metadata.
    try:
        meta_dict = json.loads(metadata)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid metadata JSON: {exc}",
        ) from exc

    # ``is_active`` is not part of ModelDeployRequest's required fields, but
    # the task specifies auto-activation when true.  Extract it before
    # constructing the Pydantic model.
    is_active_flag: bool = bool(meta_dict.pop("is_active", True))

    try:
        deploy_req = ModelDeployRequest(**meta_dict)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=422,
            detail=f"Invalid metadata payload: {exc}",
        ) from exc

    # Step 3: persist artefact.
    _MODEL_DATA_DIR.mkdir(parents=True, exist_ok=True)
    # Construct a deterministic model_id from asset_class + version.
    model_id = f"{deploy_req.asset_class}-v{deploy_req.version}"
    dest_path = _MODEL_DATA_DIR / f"{model_id}.onnx"
    content = await artifact.read()
    dest_path.write_bytes(content)

    _logger.info(
        "Model artefact saved",
        extra={"model_id": model_id, "path": str(dest_path), "bytes": len(content)},
    )

    # Step 4: register.
    registered_id = registry.register_model(
        asset_class=deploy_req.asset_class,
        version=deploy_req.version,
        artifact_path=str(dest_path),
        artifact_format="onnx",
        anomaly_threshold=deploy_req.anomaly_threshold,
        severity_thresholds=json.dumps(deploy_req.severity_thresholds),
    )

    # Step 5: activate if requested.
    if is_active_flag:
        registry.activate_version(registered_id)

    _logger.info(
        "Model registered",
        extra={
            "model_id": registered_id,
            "asset_class": deploy_req.asset_class,
            "version": deploy_req.version,
            "is_active": is_active_flag,
        },
    )

    return ModelDeployResponse(
        model_id=registered_id,
        version=deploy_req.version,
        asset_class=deploy_req.asset_class,
        deployed_at=datetime.now(tz=timezone.utc),
        is_active=is_active_flag,
    )


@app.get("/internal/models/{model_id}")
def get_model(
    model_id: str,
    registry: ModelRegistry = Depends(get_registry),
) -> dict[str, Any]:
    """Return metadata for a specific model version.

    Returns HTTP 404 if the model_id is not found in the registry.
    """
    models = registry.list_models()
    for m in models:
        if m.get("model_id") == model_id:
            return m
    raise HTTPException(
        status_code=404,
        detail=f"Model '{model_id}' not found in the registry.",
    )
