# ops-ui/Dockerfile
# =============================================================================
# PLACEHOLDER — será substituído pelo Dockerfile multi-stage completo em
# TASK-034 (build Angular 17 + serve nginx).
#
# Este placeholder usa nginx:alpine com um index.html de status para que o
# serviço ops-ui suba corretamente no docker compose up enquanto TASK-034
# não é implementada. O healthcheck retorna 200 via nginx.
#
# TASK-034 substituirá este arquivo com:
#   - Stage 1: node:18-alpine — npm ci && ng build --configuration production
#   - Stage 2: nginx:alpine — copia dist/ e aplica nginx.conf com proxy /api/
#
# WARNING: SISTEMA ADVISORY ONLY — NÃO SUBSTITUI SISTEMAS DE SEGURANÇA
#          INSTRUMENTADA (RN-06). Autenticação desativada por padrão (LAC-02).
# =============================================================================

FROM nginx:alpine

LABEL service="ops-ui" \
      description="OilOps Angular 17 UI — placeholder (TASK-034 pending)" \
      advisory="This system is ADVISORY ONLY and does NOT replace safety-instrumented systems"

# Copy nginx configuration (proxy /api/ -> ops-api:8000)
COPY ops-ui/nginx.conf /etc/nginx/conf.d/default.conf

# Create a simple placeholder index.html so the healthcheck passes
RUN echo '<!DOCTYPE html><html><head><title>OilOps UI — Coming Soon</title></head><body>' \
    '<h1>OilOps Predictive Core</h1>' \
    '<p>UI placeholder — TASK-034 (Angular 17 build) pending.</p>' \
    '<p><a href="/api/health">API Health</a></p>' \
    '</body></html>' > /usr/share/nginx/html/index.html

EXPOSE 80
