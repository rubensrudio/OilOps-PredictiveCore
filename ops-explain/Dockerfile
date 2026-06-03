# ops-explain/Dockerfile
# =============================================================================
# Python 3.11-slim image for the ops-explain explainability service.
# Runs SHAP attribution calculations asynchronously using background tasks.
# See plan.md §7.2 (DA-03) for SHAP async design rationale.
#
# WARNING: SISTEMA ADVISORY ONLY — NÃO SUBSTITUI SISTEMAS DE SEGURANÇA
#          INSTRUMENTADA (RN-06). Autenticação desativada por padrão (LAC-02).
# =============================================================================

FROM python:3.11-slim

LABEL service="ops-explain" \
      description="OilOps explainability service — async SHAP attribution computation" \
      advisory="This system is ADVISORY ONLY and does NOT replace safety-instrumented systems"

WORKDIR /app

# Install system dependencies (curl for healthcheck; build-essential for SHAP
# native extensions)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy only the service requirements first to leverage Docker layer caching
COPY ops-explain/requirements.txt /tmp/requirements-ops-explain.txt
RUN pip install --no-cache-dir -r /tmp/requirements-ops-explain.txt

# Copy shared module (required by all services)
COPY shared/ /app/shared/

# Copy ops-explain service code.
# Copied to ops_explain/ (underscore) for importability.
COPY ops-explain/ /app/ops_explain/

ENV PYTHONPATH=/app

# Run as non-root user (security hardening — OWASP Docker best practice)
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser
USER appuser

EXPOSE 8005

CMD ["uvicorn", "ops_explain.app.main:app", \
     "--host", "0.0.0.0", "--port", "8005", "--workers", "1"]
