# ops-models/Dockerfile
# =============================================================================
# Python 3.11-slim image for the ops-models model-serving service.
# Uses ONNX Runtime for inference (TensorFlow only in ops-reference/training).
# See plan.md §7.2 (DA-05) for runtime image rationale.
#
# WARNING: SISTEMA ADVISORY ONLY — NÃO SUBSTITUI SISTEMAS DE SEGURANÇA
#          INSTRUMENTADA (RN-06). Autenticação desativada por padrão (LAC-02).
# =============================================================================

FROM python:3.11-slim

LABEL service="ops-models" \
      description="OilOps model-serving service — ONNX Runtime inference + model registry" \
      advisory="This system is ADVISORY ONLY and does NOT replace safety-instrumented systems"

WORKDIR /app

# Install system dependencies (curl for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy only the service requirements first to leverage Docker layer caching
COPY ops-models/requirements.txt /tmp/requirements-ops-models.txt
RUN pip install --no-cache-dir -r /tmp/requirements-ops-models.txt

# Copy shared module (required by all services)
COPY shared/ /app/shared/

# Copy ops-models service code.
# Copied to ops_models/ (underscore) so that ops_models.app.main:app resolves.
COPY ops-models/ /app/ops_models/

ENV PYTHONPATH=/app

# Volume mount points
VOLUME ["/data", "/data/models", "/ops-reference/models"]

EXPOSE 8004

# Default entrypoint: runs the init script (copy ONNX + register model) then
# starts uvicorn. Can be overridden in docker-compose.override.yml for dev.
COPY ops-models/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

ENTRYPOINT ["/bin/sh", "/entrypoint.sh"]
