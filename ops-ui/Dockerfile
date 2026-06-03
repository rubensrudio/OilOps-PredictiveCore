# ops-ui/Dockerfile
# =============================================================================
# Multi-stage build: Angular 17 production build + nginx:alpine static serving.
# Build context: repo root (.)  — all COPY paths are relative to repo root.
#
# Stage 1 (build): node:18-alpine — installs deps and compiles Angular app.
# Stage 2 (final): nginx:alpine  — serves the static dist over port 80.
#
# Angular 17 (@angular-devkit/build-angular:application) outputs to:
#   dist/ops-ui/browser/
#
# WARNING: SISTEMA ADVISORY ONLY — NÃO SUBSTITUI SISTEMAS DE SEGURANÇA
#          INSTRUMENTADA (RN-06). Autenticação desativada por padrão (LAC-02).
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1: build
# ---------------------------------------------------------------------------
FROM node:18-alpine AS build

WORKDIR /app

# Copy only package manifests first to leverage Docker layer cache.
COPY ops-ui/package*.json ./

# Install exact versions from lock file (no network-mutable installs).
RUN npm ci

# Copy application source and build configuration.
COPY ops-ui/src ./src
COPY ops-ui/angular.json .
COPY ops-ui/tsconfig*.json ./

# Compile Angular application for production.
RUN npm run build -- --configuration production

# ---------------------------------------------------------------------------
# Stage 2: final
# ---------------------------------------------------------------------------
FROM nginx:alpine AS final

LABEL service="ops-ui" \
      description="OilOps Angular 17 UI — multi-stage nginx static server" \
      advisory="This system is ADVISORY ONLY and does NOT replace safety-instrumented systems"

# Copy compiled Angular browser bundle from the build stage.
# Angular 17 (application builder) outputs to dist/ops-ui/browser/.
COPY --from=build /app/dist/ops-ui/browser /usr/share/nginx/html

# Apply nginx reverse-proxy configuration (proxy /api/ -> ops-api:8000).
# Path is relative to repo-root build context.
COPY ops-ui/nginx.conf /etc/nginx/conf.d/default.conf

EXPOSE 80
