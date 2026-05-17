#!/usr/bin/env sh
# ops-models/entrypoint.sh
# =============================================================================
# Entrypoint de inicialização do serviço ops-models (TASK-030 / INIT-15).
#
# Responsabilidades (executadas ANTES de iniciar o uvicorn):
#   1. Copiar o artefato ONNX de referência do bind mount de ops-reference para
#      o volume persistente oilops-models (montado em /data/models/).
#   2. Registrar o modelo no SQLite local de model_registry via script Python
#      usando ModelRegistry, tornando-o is_active=1 para que o serviço sirva
#      predições imediatamente no boot — sem etapas manuais (INIT-US-05-AC3).
#   3. Iniciar o uvicorn via exec (substituindo o processo shell pelo servidor).
#
# Variáveis de ambiente usadas:
#   OPS_MODELS_DIR         - diretório do volume de artefatos ONNX (default: /models)
#   OILOPS_DATA_DIR        - diretório raiz de dados para o SQLite registry (default: /data)
#   OPS_REFERENCE_MODELS   - diretório do bind mount com o .onnx de referência
#                            (default: /ops-reference/models)
#
# WARNING: SISTEMA ADVISORY ONLY — NÃO SUBSTITUI SISTEMAS DE SEGURANÇA
#          INSTRUMENTADA (RN-06). Autenticação desativada por padrão (LAC-02).
# =============================================================================

set -e

DATA_DIR="${OILOPS_DATA_DIR:-/data}"
MODELS_DIR="${OPS_MODELS_DIR:-/models}"
REFERENCE_MODELS_DIR="${OPS_REFERENCE_MODELS:-/ops-reference/models}"
ONNX_SRC="${REFERENCE_MODELS_DIR}/vibration_autoencoder_v1.onnx"
ONNX_DST="${MODELS_DIR}/vibration_autoencoder_v1.onnx"

echo "[ops-models:entrypoint] Iniciando sequência de boot..."
echo "[ops-models:entrypoint] DATA_DIR=${DATA_DIR}"
echo "[ops-models:entrypoint] MODELS_DIR=${MODELS_DIR}"
echo "[ops-models:entrypoint] REFERENCE_MODELS_DIR=${REFERENCE_MODELS_DIR}"

# ---------------------------------------------------------------------------
# 1. Garantir que o diretório de modelos existe no volume
# ---------------------------------------------------------------------------
mkdir -p "${MODELS_DIR}"

# ---------------------------------------------------------------------------
# 2. Copiar artefato ONNX de referência para o volume persistente
# ---------------------------------------------------------------------------
if [ -f "${ONNX_SRC}" ]; then
    if [ ! -f "${ONNX_DST}" ]; then
        echo "[ops-models:entrypoint] Copiando ${ONNX_SRC} -> ${ONNX_DST}"
        cp "${ONNX_SRC}" "${ONNX_DST}"
    else
        echo "[ops-models:entrypoint] Artefato já presente em ${ONNX_DST} — pulando cópia."
    fi
else
    echo "[ops-models:entrypoint] AVISO: artefato de referência não encontrado em ${ONNX_SRC}."
    echo "[ops-models:entrypoint] O serviço iniciará sem modelo pré-carregado."
    echo "[ops-models:entrypoint] Use POST /internal/models/deploy para registrar um modelo."
fi

# ---------------------------------------------------------------------------
# 3. Registrar o modelo de referência no SQLite local de model_registry
#    usando o ModelRegistry do serviço — apenas se o .onnx foi copiado e
#    ainda não há nenhum modelo registrado como ativo.
# ---------------------------------------------------------------------------
if [ -f "${ONNX_DST}" ]; then
    echo "[ops-models:entrypoint] Registrando modelo de referência no registry..."
    python - <<'PYEOF'
import os
import sys
import sqlite3
from pathlib import Path

# Adicionar raiz do projeto ao PYTHONPATH para importar ModelRegistry
project_root = os.environ.get("PYTHONPATH", "/app").split(":")[0]
sys.path.insert(0, project_root)

data_dir = os.environ.get("OILOPS_DATA_DIR", "/data")
models_dir = os.environ.get("OPS_MODELS_DIR", "/models")
db_path = os.environ.get("OILOPS_REGISTRY_DB", os.path.join(data_dir, "model_registry.db"))
onnx_path = os.path.join(models_dir, "vibration_autoencoder_v1.onnx")

try:
    from ops_models.app.serving.model_registry import ModelRegistry

    registry = ModelRegistry(db_path=db_path)

    # Verificar se já existe um modelo ativo para rotating_equipment
    existing = registry.get_active_model("rotating_equipment")
    if existing is not None:
        print(f"[ops-models:entrypoint] Modelo ativo já registrado: {existing.get('model_id')} — pulando registro.")
        registry.close()
        sys.exit(0)

    # Registrar o modelo de referência
    import json
    model_id = registry.register_model(
        asset_class="rotating_equipment",
        version="1.0.0",
        artifact_path=onnx_path,
        artifact_format="onnx",
        anomaly_threshold=0.5,
        severity_thresholds=json.dumps({"low": 0.5, "medium": 0.75, "high": 0.9}),
    )
    print(f"[ops-models:entrypoint] Modelo registrado com model_id={model_id}")

    # Ativar a versão recém-registrada
    registry.activate_version(model_id)
    print(f"[ops-models:entrypoint] Modelo {model_id} ativado (is_active=1).")
    registry.close()

except ImportError as exc:
    print(f"[ops-models:entrypoint] AVISO: importação falhou ({exc}). Pulando auto-registro.")
    print("[ops-models:entrypoint] Use POST /internal/models/deploy após o boot.")
except Exception as exc:
    print(f"[ops-models:entrypoint] ERRO no registro do modelo: {exc}")
    print("[ops-models:entrypoint] Serviço continuará sem modelo pré-carregado.")
PYEOF
fi

# ---------------------------------------------------------------------------
# 4. Iniciar o uvicorn (substituição do processo shell — sem overhead extra)
# ---------------------------------------------------------------------------
echo "[ops-models:entrypoint] Iniciando uvicorn em 0.0.0.0:8004..."
exec uvicorn ops_models.app.main:app \
    --host 0.0.0.0 \
    --port 8004 \
    --workers 1 \
    --log-config /dev/null
