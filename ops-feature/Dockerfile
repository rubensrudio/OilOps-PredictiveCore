# ops-feature/Dockerfile
# =============================================================================
# Python 3.11-slim image for the ops-feature feature-engineering service.
# Computes RMS, variance, kurtosis, skewness and FFT bins from raw vibration
# signals using NumPy/SciPy.
# See plan.md §7.2 for image version rationale.
#
# WARNING: SISTEMA ADVISORY ONLY — NÃO SUBSTITUI SISTEMAS DE SEGURANÇA
#          INSTRUMENTADA (RN-06). Autenticação desativada por padrão (LAC-02).
# =============================================================================

FROM python:3.11-slim

LABEL service="ops-feature" \
      description="OilOps feature-engineering service — sliding-window FFT + statistical features" \
      advisory="This system is ADVISORY ONLY and does NOT replace safety-instrumented systems"

WORKDIR /app

# Install system dependencies (curl for healthcheck; build-essential for SciPy
# native extensions on slim image)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy only the service requirements first to leverage Docker layer caching
COPY ops-feature/requirements.txt /tmp/requirements-ops-feature.txt
RUN pip install --no-cache-dir -r /tmp/requirements-ops-feature.txt

# Copy shared module (required by all services)
COPY shared/ /app/shared/

# Copy ops-feature service code.
# The source directory is named "ops-feature" (hyphen) which is not a valid
# Python identifier; we copy it to "ops_feature" (underscore) so that uvicorn
# can resolve the import path ops_feature.app.main:app.
COPY ops-feature/ /app/ops_feature/

ENV PYTHONPATH=/app

# Run as non-root user (security hardening — OWASP Docker best practice)
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser
USER appuser

EXPOSE 8003

CMD ["uvicorn", "ops_feature.app.main:app", "--host", "0.0.0.0", "--port", "8003", "--workers", "1"]
