"""
ops-api/app/routers/models.py
==============================
Router for ``POST /models/deploy``.

This module implements the public-facing model deployment endpoint.
ops-api acts as an API gateway: it validates the artifact format and metadata
before delegating the actual deployment to ``ops-models`` via an async httpx
multipart/form-data call.

Route
-----
POST /models/deploy
    Accept a multipart/form-data request with:
    - ``artifact`` (UploadFile) — model artifact file
    - ``metadata`` (str/JSON) — JSON string of :class:`ModelDeployRequest`

    Returns HTTP 200 with :class:`ModelDeployResponse` on success.
    Returns HTTP 422 with descriptive error for unsupported artifact format
    or invalid metadata JSON (INIT-US-07).

Design notes
------------
* Format validation (gateway level):
  ops-api validates the artifact file extension BEFORE forwarding to ops-models.
  Only ``.onnx`` extensions are accepted (plan.md § 5.5, spec INIT-US-07-AC3).
  Files with .pkl, .h5, or any other extension are rejected immediately with
  HTTP 422 and a descriptive message — ops-models is never called.
  This satisfies the criterion without requiring a real ONNX binary.

* Metadata validation:
  The ``metadata`` form field must be valid JSON that deserialises into a
  :class:`~ops_models.app.schemas.ModelDeployRequest`.  Pydantic validation
  errors are caught and returned as HTTP 422.

* Upstream delegation:
  For accepted artifacts the router forwards the multipart request verbatim to
  ``ops-models POST /internal/models/deploy`` via httpx and propagates both 200
  and upstream 422 responses to the caller.

* Header propagation:
  The ``X-Trace-Id`` header from the incoming request is forwarded to ops-models
  so the trace spans both services (CAT-08).

* X-Advisory-Only: true is injected by AdvisoryMiddleware and requires no
  manual handling in this router (RN-06).

References
----------
- tasks.md TASK-025
- spec.md INIT-US-07, RN-06
- plan.md § 3.2, § 5.5
"""

from __future__ import annotations

import json
import os
from typing import AsyncGenerator

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from pydantic import ValidationError

from ops_models.app.schemas import ModelDeployRequest, ModelDeployResponse

router = APIRouter(tags=["models"])

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Supported artifact extensions (plan.md § 5.5, INIT-US-07-AC1).
_SUPPORTED_EXTENSIONS = frozenset({".onnx"})

_DEFAULT_OPS_MODELS_URL = "http://ops-models:8004"


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def _get_ops_models_url() -> str:
    """Return the base URL of the ops-models service from the environment."""
    return os.environ.get("OPS_MODELS_URL", _DEFAULT_OPS_MODELS_URL)


# ---------------------------------------------------------------------------
# HTTP client dependency — injected via Depends
# ---------------------------------------------------------------------------


async def get_models_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    """Yield a short-lived async ``httpx.AsyncClient`` for the ops-models call.

    This dependency is overridden in tests via ``app.dependency_overrides``
    to inject a mock that never makes real network calls.
    """
    async with httpx.AsyncClient(timeout=60.0) as client:
        yield client


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _validate_artifact_extension(filename: str | None) -> str:
    """Return the lowercased extension or raise HTTP 422 for unsupported formats.

    Parameters
    ----------
    filename:
        Original filename supplied by the client (e.g. ``"model.onnx"``).

    Returns
    -------
    str
        Lowercased file extension including the leading dot (e.g. ``".onnx"``).

    Raises
    ------
    HTTPException (422)
        When *filename* is ``None``, empty, or its extension is not in
        ``_SUPPORTED_EXTENSIONS``.
    """
    if not filename:
        raise HTTPException(
            status_code=422,
            detail=(
                "Artifact filename is missing. "
                "Supported formats: .onnx"
            ),
        )

    # Extract extension from the last component of the filename.
    # Using rsplit instead of os.path.splitext so Windows path separators
    # in filenames uploaded from other OSes do not cause issues.
    dot_idx = filename.rfind(".")
    if dot_idx == -1:
        ext = ""
    else:
        ext = filename[dot_idx:].lower()

    if ext not in _SUPPORTED_EXTENSIONS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Unsupported artifact format. "
                f"Expected: {', '.join(sorted(_SUPPORTED_EXTENSIONS))}. "
                f"Got: {ext if ext else '(no extension)'}"
            ),
        )

    return ext


def _parse_metadata(metadata_str: str) -> ModelDeployRequest:
    """Parse and validate the ``metadata`` form field.

    Parameters
    ----------
    metadata_str:
        Raw JSON string from the ``metadata`` form field.

    Returns
    -------
    ModelDeployRequest
        Validated metadata payload.

    Raises
    ------
    HTTPException (422)
        When *metadata_str* is not valid JSON or fails
        :class:`~ops_models.app.schemas.ModelDeployRequest` validation.
    """
    try:
        raw = json.loads(metadata_str)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"metadata is not valid JSON: {exc}",
        ) from exc

    try:
        return ModelDeployRequest(**raw)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422,
            detail=exc.errors(),
        ) from exc


# ---------------------------------------------------------------------------
# Endpoint: POST /models/deploy
# ---------------------------------------------------------------------------


@router.post(
    "/models/deploy",
    status_code=200,
    response_model=ModelDeployResponse,
    summary="Deploy a new model artifact to ops-models",
    responses={
        200: {
            "description": (
                "Model deployed successfully — returns model_id, version, "
                "asset_class, deployed_at, is_active."
            )
        },
        422: {
            "description": (
                "Unsupported artifact format (e.g. .pkl, .h5) or invalid "
                "metadata JSON / missing required fields."
            )
        },
    },
)
async def deploy_model(
    artifact: UploadFile = File(...),
    metadata: str = Form(...),
    request: Request = None,  # type: ignore[assignment]
    http_client: httpx.AsyncClient = Depends(get_models_client),
) -> ModelDeployResponse:
    """Accept a model artifact and deploy it via ``ops-models``.

    Processing steps:

    1. Validate artifact extension — reject immediately with HTTP 422 if the
       file is not ``.onnx`` (plan.md § 5.5, INIT-US-07-AC3).
    2. Parse and validate ``metadata`` JSON against
       :class:`~ops_models.app.schemas.ModelDeployRequest`.  Invalid JSON or
       missing required fields result in HTTP 422.
    3. Read artifact bytes and forward the multipart request to
       ``ops-models POST /internal/models/deploy`` via httpx.
    4. Propagate the ``X-Trace-Id`` header to ops-models (CAT-08).
    5. Return :class:`~ops_models.app.schemas.ModelDeployResponse` (HTTP 200)
       or propagate the upstream 422 response.

    Parameters
    ----------
    artifact:
        Uploaded model artifact file (``UploadFile``).  Must have a ``.onnx``
        extension.
    metadata:
        JSON string conforming to
        :class:`~ops_models.app.schemas.ModelDeployRequest`.
    request:
        Starlette :class:`~starlette.requests.Request` — used to read the
        ``X-Trace-Id`` header for propagation.
    http_client:
        Injected async ``httpx.AsyncClient``.

    Returns
    -------
    ModelDeployResponse
        Deployment confirmation with ``model_id`` and ``is_active=True``
        (HTTP 200).

    Raises
    ------
    HTTP 422
        Unsupported artifact format or invalid metadata.
    HTTP 502
        When ``ops-models`` is unreachable or returns an unexpected status.
    """
    # ------------------------------------------------------------------
    # Step 1: validate artifact extension at the gateway boundary.
    # ------------------------------------------------------------------
    _validate_artifact_extension(artifact.filename)

    # ------------------------------------------------------------------
    # Step 2: parse and validate metadata JSON.
    # ------------------------------------------------------------------
    _parse_metadata(metadata)

    # ------------------------------------------------------------------
    # Step 3: read artifact bytes and forward to ops-models.
    # ------------------------------------------------------------------
    artifact_bytes = await artifact.read()

    models_url = _get_ops_models_url()

    headers: dict[str, str] = {}
    if request is not None:
        trace_id = request.headers.get("X-Trace-Id")
        if trace_id:
            headers["X-Trace-Id"] = trace_id

    try:
        response = await http_client.post(
            f"{models_url}/internal/models/deploy",
            files={
                "artifact": (
                    artifact.filename or "model.onnx",
                    artifact_bytes,
                    artifact.content_type or "application/octet-stream",
                )
            },
            data={"metadata": metadata},
            headers=headers,
        )
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"ops-models unreachable: {exc}",
        ) from exc

    # ------------------------------------------------------------------
    # Step 4: propagate upstream 422 (e.g. ops-models rejected the artifact
    # after its own deeper validation) or return the success response.
    # ------------------------------------------------------------------
    if response.status_code == 422:
        raise HTTPException(status_code=422, detail=response.json())

    if response.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"ops-models returned unexpected status {response.status_code}",
        )

    return ModelDeployResponse(**response.json())
