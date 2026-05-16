"""
ops-models/app/schemas.py
==========================
Pydantic schemas for the ops-models service (TASK-017).

Models
------
- :class:`PredictionRequest`   — Input payload to trigger inference for a
                                  pre-computed feature record.
- :class:`PredictionResult`    — Output payload returned after model inference.
                                  Enforces range validators on ``anomaly_score``
                                  and ``confidence_score`` (float in [0.0, 1.0])
                                  and a strict Literal for ``severity``.
- :class:`ModelDeployRequest`  — Input payload to deploy a new model version.
- :class:`ModelDeployResponse` — Response returned after a successful model
                                  deployment.

Design notes
------------
- ``severity`` is typed as ``Literal["low", "medium", "high"] | None`` so that
  Pydantic rejects any value outside this set at the boundary (TASK-017 criterion).
- ``anomaly_score`` and ``confidence_score`` use ``Annotated[float, Field(ge=0.0,
  le=1.0)]`` — Pydantic v2 enforces ``ge``/``le`` constraints natively and raises
  a ``ValidationError`` with a descriptive message for out-of-range values.
- All datetime fields carry timezone info (``AwareDatetime``) consistent with the
  UTC contract established in ``shared/schemas/canonical.py``.
- ``explain_status`` accepts the three states defined in the plan (plan.md § 4.3):
  ``pending``, ``ready``, ``failed``.  It defaults to ``"pending"`` because a
  prediction is always emitted before SHAP attribution is computed (RN-04).
- ``severity_thresholds`` in ``ModelDeployRequest`` is ``dict[str, float]`` rather
  than a nested model to stay flexible — the plan defines ``{low, medium, high}``
  keys (plan.md § 4.4) but does not mandate additional validation at schema level.

Usage
-----
>>> from ops_models.app.schemas import PredictionResult
>>> from datetime import datetime, timezone
>>> r = PredictionResult(
...     prediction_id="550e8400-e29b-41d4-a716-446655440000",
...     asset_id="PUMP-001",
...     asset_class="rotating_equipment",
...     anomaly_score=0.87,
...     confidence_score=0.92,
...     alert=True,
...     severity="high",
...     predicted_at=datetime.now(tz=timezone.utc),
...     model_version="vibration-autoencoder-v1",
... )
>>> r.explain_status
'pending'
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Dict, Literal, Optional

from pydantic import AwareDatetime, BaseModel, Field


# ---------------------------------------------------------------------------
# Type aliases
# ---------------------------------------------------------------------------

# Float clamped to [0.0, 1.0] — used for probability / score fields.
_ScoreFloat = Annotated[float, Field(ge=0.0, le=1.0)]

# Severity values admitted by the system (plan.md § 4.3, INIT-US-02-AC3).
_SeverityLiteral = Optional[Literal["low", "medium", "high"]]

# Explain status values (plan.md § 4.3, RN-04).
_ExplainStatus = Literal["pending", "ready", "failed"]


# ---------------------------------------------------------------------------
# PredictionRequest
# ---------------------------------------------------------------------------


class PredictionRequest(BaseModel):
    """Input payload to trigger inference for a pre-computed feature record.

    Sent by the ops-feature pipeline (or an HTTP trigger from ops-api) to
    ``POST /internal/predict`` in the ops-models service.

    Attributes
    ----------
    feature_record_id:
        UUID (as string) of the ``feature_records`` row to use as the input
        vector for inference.  Resolves to a row in DuckDB via ``ops-store``.
    asset_id:
        Canonical identifier of the asset being evaluated (e.g. ``"PUMP-001"``).
    asset_class:
        Asset class that determines which model version to use for inference
        (e.g. ``"rotating_equipment"``).
    """

    model_config = {"populate_by_name": True}

    feature_record_id: str = Field(
        ...,
        min_length=1,
        description=(
            "UUID of the feature_records row to use as inference input. "
            "Must be a non-empty string."
        ),
    )
    asset_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Canonical identifier of the asset being evaluated.",
    )
    asset_class: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description=(
            "Asset class used to select the active model version for inference."
        ),
    )


# ---------------------------------------------------------------------------
# PredictionResult
# ---------------------------------------------------------------------------


class PredictionResult(BaseModel):
    """Output schema returned by ``POST /internal/predict`` and exposed via
    ``GET /predictions/{asset_id}`` (INIT-US-02).

    Validators enforce:
    - ``anomaly_score`` in [0.0, 1.0] (``ge=0.0, le=1.0`` via ``_ScoreFloat``).
    - ``confidence_score`` in [0.0, 1.0] (same).
    - ``severity`` is one of ``"low"``, ``"medium"``, ``"high"``, or ``None``.

    Attributes
    ----------
    prediction_id:
        UUID (as string) that uniquely identifies this prediction.
        Stored in the ``predictions`` SQLite table.
    asset_id:
        Canonical identifier of the asset that was evaluated.
    asset_class:
        Class of the asset at the time of prediction.
    anomaly_score:
        Reconstruction error normalised to [0.0, 1.0].  Values closer to 1.0
        indicate higher likelihood of anomaly.
    confidence_score:
        Model confidence in the prediction, normalised to [0.0, 1.0].
    alert:
        ``True`` when ``anomaly_score`` >= the model's ``anomaly_threshold``
        (INIT-US-02-AC3).
    severity:
        ``"low"``, ``"medium"``, or ``"high"`` when ``alert=True``; ``None``
        when ``alert=False``.  Mapped from ``severity_thresholds`` of the
        active model version.
    predicted_at:
        UTC timestamp when inference was executed.
    model_version:
        Version string of the model that produced this prediction (RN-05).
    explain_status:
        One of ``"pending"``, ``"ready"``, ``"failed"``.  Defaults to
        ``"pending"`` because SHAP attribution is computed asynchronously
        after inference (RN-04).
    """

    # ``protected_namespaces = ()`` suppresses the Pydantic v2 warning for
    # the ``model_version`` field name, which conflicts with the default
    # "model_" namespace.  The field name is mandated by the spec contract
    # (plan.md § 4.3) and cannot be renamed.
    model_config = {"populate_by_name": True, "protected_namespaces": ()}

    prediction_id: str = Field(
        ...,
        min_length=1,
        description="UUID that uniquely identifies this prediction.",
    )
    asset_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Canonical identifier of the asset that was evaluated.",
    )
    asset_class: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Class of the asset at the time of prediction.",
    )
    anomaly_score: _ScoreFloat = Field(
        ...,
        description=(
            "Reconstruction error normalised to [0.0, 1.0]. "
            "Values closer to 1.0 indicate higher anomaly likelihood."
        ),
    )
    confidence_score: _ScoreFloat = Field(
        ...,
        description="Model confidence in the prediction, normalised to [0.0, 1.0].",
    )
    alert: bool = Field(
        ...,
        description=(
            "True when anomaly_score >= the model's anomaly_threshold "
            "(INIT-US-02-AC3)."
        ),
    )
    severity: _SeverityLiteral = Field(
        default=None,
        description=(
            "Severity level: 'low', 'medium', or 'high' when alert=True; "
            "None when alert=False."
        ),
    )
    predicted_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc),
        description="UTC timestamp when inference was executed.",
    )
    model_version: str = Field(
        ...,
        min_length=1,
        description=(
            "Version string of the model that produced this prediction (RN-05)."
        ),
    )
    explain_status: _ExplainStatus = Field(
        default="pending",
        description=(
            "Explain computation status: 'pending' (default), 'ready', or 'failed'. "
            "Defaults to 'pending' because SHAP runs asynchronously (RN-04)."
        ),
    )


# ---------------------------------------------------------------------------
# ModelDeployRequest
# ---------------------------------------------------------------------------


class ModelDeployRequest(BaseModel):
    """Input payload to deploy a new model version via ``POST /internal/models/deploy``.

    Received as the ``metadata`` JSON field in the ``multipart/form-data``
    request (plan.md § 5.5, INIT-US-07).

    Attributes
    ----------
    asset_class:
        Asset class this model targets (e.g. ``"rotating_equipment"``).
    version:
        Semantic version string of the new model (e.g. ``"2.0.0"``).
    anomaly_threshold:
        Score threshold above which ``alert=True`` is set.  Stored in
        ``model_versions.anomaly_threshold``.
    severity_thresholds:
        Mapping of severity labels to score thresholds.  Expected keys:
        ``"low"``, ``"medium"``, ``"high"`` (plan.md § 4.4).
    """

    model_config = {"populate_by_name": True}

    asset_class: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Asset class this model targets.",
    )
    version: str = Field(
        ...,
        min_length=1,
        max_length=32,
        description="Semantic version string of the new model (e.g. '2.0.0').",
    )
    anomaly_threshold: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description=(
            "Score threshold [0.0, 1.0] above which alert=True is set. "
            "Default: 0.5."
        ),
    )
    severity_thresholds: Dict[str, float] = Field(
        ...,
        description=(
            "Mapping of severity labels to score thresholds. "
            "Expected keys: 'low', 'medium', 'high'."
        ),
    )


# ---------------------------------------------------------------------------
# ModelDeployResponse
# ---------------------------------------------------------------------------


class ModelDeployResponse(BaseModel):
    """Response returned after a successful model deployment.

    Returned by ``POST /internal/models/deploy`` and by
    ``GET /internal/models/{model_id}`` (plan.md § 5.5).

    Attributes
    ----------
    model_id:
        Canonical model identifier (e.g. ``"vibration-autoencoder-v2.0.0"``).
        Constructed by the ``ModelRegistry`` as ``{asset_class}-v{version}``.
    version:
        Semantic version string of the deployed model.
    asset_class:
        Asset class this model targets.
    deployed_at:
        UTC timestamp when the model was registered (``AwareDatetime``).
    is_active:
        ``True`` if this version is currently active for inference.  Defaults
        to ``True`` because deployment activates the new version immediately
        (INIT-US-07-AC4 / plan.md § 4.4).
    """

    # ``protected_namespaces = ()`` suppresses the Pydantic v2 warning for
    # the ``model_id`` field name, which conflicts with the default
    # "model_" namespace.  The field name is mandated by the spec contract
    # (plan.md § 5.5) and cannot be renamed.
    model_config = {"populate_by_name": True, "protected_namespaces": ()}

    model_id: str = Field(
        ...,
        min_length=1,
        description=(
            "Canonical model identifier "
            "(e.g. 'vibration-autoencoder-v2.0.0')."
        ),
    )
    version: str = Field(
        ...,
        min_length=1,
        max_length=32,
        description="Semantic version string of the deployed model.",
    )
    asset_class: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Asset class this model targets.",
    )
    deployed_at: AwareDatetime = Field(
        default_factory=lambda: datetime.now(tz=timezone.utc),
        description="UTC timestamp when the model was registered.",
    )
    is_active: bool = Field(
        default=True,
        description=(
            "True if this version is currently active for inference. "
            "Deployment activates the new version immediately (INIT-US-07-AC4)."
        ),
    )
