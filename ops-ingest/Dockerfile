# ops-ingest/Dockerfile
# =============================================================================
# Python 3.11-slim image for the ops-ingest ingestion gateway service.
# See plan.md §7.2 for image version rationale.
#
# WARNING: SISTEMA ADVISORY ONLY — NÃO SUBSTITUI SISTEMAS DE SEGURANÇA
#          INSTRUMENTADA (RN-06). Autenticação desativada por padrão (LAC-02).
# =============================================================================

FROM python:3.11-slim

LABEL service="ops-ingest" \
      description="OilOps ingestion gateway — normalises telemetry to canonical schema" \
      advisory="This system is ADVISORY ONLY and does NOT replace safety-instrumented systems"

WORKDIR /app

# Install system dependencies (curl for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy only the service requirements first to leverage Docker layer caching
COPY ops-ingest/requirements.txt /tmp/requirements-ops-ingest.txt
RUN pip install --no-cache-dir -r /tmp/requirements-ops-ingest.txt

# Copy shared module (required by all services)
COPY shared/ /app/shared/

# Copy ops-ingest service code.
# 1. Copy root-level ops_ingest/ package (contains __init__.py + contracts/)
#    first so that the underscore package namespace is established.
# 2. Copy ops-ingest/ (hyphen) service directory to ops_ingest/ — the app/
#    and other subdirs merge into the same destination.
COPY ops_ingest/ /app/ops_ingest/
COPY ops-ingest/ /app/ops_ingest/

# Expose PYTHONPATH so that `import ops_ingest.app.main` and
# `import shared.logging_config` resolve correctly at runtime
ENV PYTHONPATH=/app

EXPOSE 8001

# Default command — can be overridden in docker-compose.override.yml
CMD ["uvicorn", "ops_ingest.app.main:app", \
     "--host", "0.0.0.0", "--port", "8001", "--workers", "1"]
