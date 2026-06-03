#!/usr/bin/env bash
# =============================================================================
# OilOps-PredictiveCore — end-to-end demo (Bash)
# =============================================================================
# Brings the stack up, pushes a small vibration telemetry batch for one asset,
# then reads back the prediction, explanation, metrics and audit trail.
#
# Advisory (RN-06): this system is ADVISORY ONLY. Not safety-rated.
#
# Usage:
#   ./scripts/demo.sh                  # uses http://localhost:8000
#   API_URL=http://host:port ./scripts/demo.sh
#   OILOPS_API_KEY=secret ./scripts/demo.sh   # if the gateway requires a key
# =============================================================================
set -euo pipefail

API_URL="${API_URL:-http://localhost:8000}"
ASSET_ID="${ASSET_ID:-PUMP-001}"
AUTH=()
if [[ -n "${OILOPS_API_KEY:-}" ]]; then
  AUTH=(-H "X-API-Key: ${OILOPS_API_KEY}")
fi
TS="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

echo "==> 1/5  Bringing the stack up (docker compose up -d --wait)"
docker compose up -d --wait

echo "==> 2/5  Health check: GET ${API_URL}/health"
curl -sf "${AUTH[@]}" "${API_URL}/health" | python -m json.tool

echo "==> 3/5  Pushing telemetry batch for asset ${ASSET_ID}: POST ${API_URL}/telemetry"
curl -sf "${AUTH[@]}" -X POST "${API_URL}/telemetry" \
  -H "Content-Type: application/json" \
  -d "{
        \"readings\": [
          {\"asset_id\": \"${ASSET_ID}\", \"timestamp\": \"${TS}\", \"metric_name\": \"vibration_x\", \"value\": 0.0231, \"unit\": \"m/s2\", \"source_protocol\": \"rest_batch\"},
          {\"asset_id\": \"${ASSET_ID}\", \"timestamp\": \"${TS}\", \"metric_name\": \"vibration_x\", \"value\": 0.0198, \"unit\": \"m/s2\", \"source_protocol\": \"rest_batch\"},
          {\"asset_id\": \"${ASSET_ID}\", \"timestamp\": \"${TS}\", \"metric_name\": \"vibration_x\", \"value\": 0.0265, \"unit\": \"m/s2\", \"source_protocol\": \"rest_batch\"}
        ]
      }" | python -m json.tool

echo "==> 4/5  Reading current prediction: GET ${API_URL}/predictions/${ASSET_ID}"
curl -s "${AUTH[@]}" -D - "${API_URL}/predictions/${ASSET_ID}" | sed -n '1,40p'

echo "==> 5/5  Observability: /metrics (head) and /audit (first page)"
curl -sf "${AUTH[@]}" "${API_URL}/metrics" | head -n 15 || true
echo "---"
curl -sf "${AUTH[@]}" "${API_URL}/audit?limit=5" | python -m json.tool || true

echo ""
echo "==> Demo complete. Note the X-Advisory-Only: true header on every response."
echo "    Tear down with:  make down"
