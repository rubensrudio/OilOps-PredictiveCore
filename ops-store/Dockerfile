# ops-store/Dockerfile
# =============================================================================
# Python 3.11-slim image for the ops-store persistence service.
# Backs both DuckDB (time-series raw readings + feature records) and SQLite
# (assets, predictions, audit log, model versions).
# See plan.md §7.2 for image version rationale.
#
# WARNING: SISTEMA ADVISORY ONLY — NÃO SUBSTITUI SISTEMAS DE SEGURANÇA
#          INSTRUMENTADA (RN-06). Autenticação desativada por padrão (LAC-02).
# =============================================================================

FROM python:3.11-slim

LABEL service="ops-store" \
      description="OilOps persistence service — DuckDB time-series + SQLite metadata" \
      advisory="This system is ADVISORY ONLY and does NOT replace safety-instrumented systems"

WORKDIR /app

# Install system dependencies (curl for healthcheck)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy only the service requirements first to leverage Docker layer caching
COPY ops-store/requirements.txt /tmp/requirements-ops-store.txt
RUN pip install --no-cache-dir -r /tmp/requirements-ops-store.txt

# Copy shared module (required by all services)
COPY shared/ /app/shared/

# Copy ops-store service code and importable package.
# 1. Copy root-level ops_store/ package (storage_interface.py) first.
# 2. Merge ops-store/ (hyphen) service directory into ops_store/ so that
#    ops_store.app.main:app resolves correctly at runtime.
COPY ops_store/ /app/ops_store/
COPY ops-store/ /app/ops_store/

ENV PYTHONPATH=/app

# Volume mount point for persistent data (DuckDB + SQLite files)
VOLUME ["/data"]

# Run as non-root user (security hardening — OWASP Docker best practice)
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser
RUN mkdir -p /data && chown -R appuser:appgroup /data
USER appuser

EXPOSE 8002

CMD ["uvicorn", "ops_store.app.main:app", \
     "--host", "0.0.0.0", "--port", "8002", "--workers", "1"]
