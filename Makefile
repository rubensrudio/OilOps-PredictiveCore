SHELL := /bin/bash

# =============================================================================
# OilOps-PredictiveCore — Makefile
# =============================================================================
#
# AVISO (RN-06): Este sistema é ADVISORY ONLY — não substitui sistemas de
# segurança instrumentada (SIL / safety-rated).
# NÃO exponha a stack à internet sem ativar OILOPS_API_KEY.
# =============================================================================

COMPOSE  := docker compose
PYTEST   := python -m pytest
RUFF     := python -m ruff

# Python source modules (ops-ui and ops-cli are excluded — not Python pytest targets)
PY_MODULES := ops-store ops-ingest ops-feature ops-models ops-explain ops-api shared

# Health endpoint (ops-api public port)
API_URL := http://localhost:8000

.PHONY: help up down test lint format train quickstart

# Default target
help:
	@echo ""
	@echo "OilOps-PredictiveCore — available targets"
	@echo "-----------------------------------------"
	@echo "  up          Start all services in detached mode (waits for healthy)"
	@echo "  down        Stop and remove all service containers"
	@echo "  test        Run pytest across all Python service modules"
	@echo "  lint        Run ruff check on all Python modules (read-only)"
	@echo "  format      Run ruff format --fix on all Python modules"
	@echo "  train       Run the vibration autoencoder training pipeline"
	@echo "  quickstart  Bring stack up, wait, then hit /api/health as smoke test"
	@echo ""

up:
	@echo "==> Starting services (docker compose up -d --wait)..."
	$(COMPOSE) up -d --wait

down:
	@echo "==> Stopping services..."
	$(COMPOSE) down

test:
	@echo "==> Running pytest across all Python modules..."
	$(PYTEST) $(PY_MODULES) -v --tb=short

lint:
	@echo "==> Running ruff check (lint) on all Python modules..."
	$(RUFF) check $(PY_MODULES)

format:
	@echo "==> Running ruff format --fix on all Python modules..."
	$(RUFF) format --fix $(PY_MODULES)
	@echo "==> Running ruff check --fix (import sort + auto-fixable rules)..."
	$(RUFF) check --fix $(PY_MODULES)

train:
	@echo "==> Running vibration autoencoder training pipeline..."
	python ops-reference/training/train_vibration_autoencoder.py

quickstart: up
	@echo "==> Waiting 10 seconds for services to stabilise..."
	sleep 10
	@echo "==> Smoke test: GET $(API_URL)/health"
	curl -sf $(API_URL)/health | python -m json.tool || (echo "ERROR: /health did not return a valid response" && exit 1)
	@echo ""
	@echo "==> Quickstart complete. Stack is up and /health responded."
	@echo "    Ingest sample telemetry:  curl -X POST $(API_URL)/telemetry ..."
	@echo "    Query predictions:        curl $(API_URL)/predictions/<asset_id>"
