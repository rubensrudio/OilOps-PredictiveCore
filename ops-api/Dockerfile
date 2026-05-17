# ops-api/Dockerfile
# =============================================================================
# Python 3.11-slim image for the ops-api public gateway service.
# Exposes the REST + WebSocket API and aggregates all internal services.
# This is the only service with ports exposed to external clients (8000).
# See plan.md §7.2 for image version rationale.
#
# WARNING: SISTEMA ADVISORY ONLY — NÃO SUBSTITUI SISTEMAS DE SEGURANÇA
#          INSTRUMENTADA (RN-06). Autenticação desativada por padrão (LAC-02).
# =============================================================================

FROM python:3.11-slim

LABEL service="ops-api" \
      description="OilOps public API gateway — REST + WebSocket, aggregates all services" \
      advisory="This system is ADVISORY ONLY and does NOT replace safety-instrumented systems"

WORKDIR /app

# Install system dependencies (curl for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy only the service requirements first to leverage Docker layer caching
COPY ops-api/requirements.txt /tmp/requirements-ops-api.txt
RUN pip install --no-cache-dir -r /tmp/requirements-ops-api.txt

# Copy shared module (required by all services)
COPY shared/ /app/shared/

# Copy ops-api service code.
# Copied to ops_api/ (underscore) for importability.
COPY ops-api/ /app/ops_api/

ENV PYTHONPATH=/app

EXPOSE 8000

CMD ["uvicorn", "ops_api.app.main:app", \
     "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
