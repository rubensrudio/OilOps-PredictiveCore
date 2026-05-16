# Tarefas — Motor Preditivo Inicial: Scaffolding e Contratos de Núcleo

## Resumo

- Total de tarefas: 32
- Tarefas paralelizáveis: 20
- Caminho crítico estimado: TASK-001 → TASK-002 → TASK-003 → TASK-006 → TASK-007 → TASK-010 → TASK-013 → TASK-016 → TASK-019 → TASK-022 → TASK-028 → TASK-030

### Distribuição por Risco
- Crítico: 0
- Alto: 14
- Médio: 14
- Baixo: 4

### Distribuição por QA
- full: 14
- wave: 14
- smoke: 3
- auto: 1

### Distribuição por Perfil
- frontend: 3
- backend: 26
- infra: 3
- misto: 0

---

## Legenda

- `[P]` = Paralelizável com outras `[P]` que não compartilham arquivos
- Esforço: S / M / L
- Tipo: lógica-negócio | crud-padrão | ui-puro | integração-externa | migration | config | refactor | infra | teste
- Risco: crítico | alto | médio | baixo
- QA: full | wave | smoke | auto
- Perfil: frontend | backend | infra | misto

---

## Tarefas

### TASK-001 — Scaffolding do módulo shared (logging, config, schema canônico)

- **Esforço**: M
- **Paralelizável**: Não
- **Depende de**: —
- **Tipo**: lógica-negócio
- **Risco**: alto
- **QA**: full
- **Perfil**: backend
- **Arquivos**:
  - `shared/logging_config.py`
  - `shared/config.py`
- **Descrição**: Criar o módulo `shared/` com configuração centralizada de logging JSON estruturado (python-json-logger) com campos obrigatórios `timestamp`, `service`, `level`, `message`, `trace_id`. Implementar `config.py` com pydantic-settings lendo as variáveis de ambiente: `OILOPS_API_KEY`, `OILOPS_DATA_DIR`, `OILOPS_MAX_BACKFILL_DAYS`, `OILOPS_FEATURE_WINDOW_SIZE`, `OILOPS_FFT_BINS`, `OILOPS_EXPLAIN_TOP_N`.
- **Critério de verificação**: `python -c "from shared.logging_config import get_logger; from shared.config import Settings; s = Settings(); assert s.fft_bins == 64"` passa sem erro.

---

### TASK-002 [P] — Schema canônico compartilhado (CanonicalReading Pydantic)

- **Esforço**: S
- **Paralelizável**: Sim
- **Depende de**: TASK-001
- **Tipo**: lógica-negócio
- **Risco**: alto
- **QA**: full
- **Perfil**: backend
- **Arquivos**:
  - `shared/schemas/canonical.py`
  - `shared/schemas/__init__.py`
- **Descrição**: Definir o modelo Pydantic `CanonicalReading` com todos os campos do schema canônico (RN-01): `id` (UUID), `asset_id`, `timestamp` (UTC datetime), `metric_name`, `value` (float), `unit`, `source_protocol`, `ingested_at`, `ingestion_id` (UUID), `is_backfill` (bool, default False). Este schema é o contrato central partilhado por todos os serviços.
- **Critério de verificação**: Teste unitário instancia `CanonicalReading` com payload válido e com payload inválido (campo ausente) — validação Pydantic levanta `ValidationError` no segundo caso.

---

### TASK-003 — Migrations de schema: DDL DuckDB e SQLite (ops-store)

- **Esforço**: M
- **Paralelizável**: Não
- **Depende de**: TASK-001
- **Tipo**: migration
- **Risco**: alto
- **QA**: full
- **Perfil**: backend
- **Arquivos**:
  - `ops-store/app/db/migrations/001_duckdb_init.sql`
  - `ops-store/app/db/migrations/001_sqlite_init.sql`
- **Descrição**: Criar os scripts DDL iniciais para as tabelas: `raw_readings` e `feature_records` em DuckDB; `assets`, `ingestion_batches`, `predictions`, `explain_results`, `model_versions`, `audit_log` em SQLite. Incluir índice único em `feature_records(asset_id, window_start, window_end, feature_version)` para idempotência (CAT-13). Incluir constraints NOT NULL, defaults e FKs conforme seção 4 do plan.
- **Critério de verificação**: Scripts executados contra bancos em branco sem erro; `SELECT * FROM raw_readings LIMIT 0` retorna schema correto no DuckDB; idem para tabelas SQLite.

---

### TASK-004 [P] — Contratos de eventos de domínio (ops-ingest/contracts)

- **Esforço**: S
- **Paralelizável**: Sim
- **Depende de**: TASK-002
- **Tipo**: lógica-negócio
- **Risco**: médio
- **QA**: wave
- **Perfil**: backend
- **Arquivos**:
  - `ops-ingest/contracts/events.py`
  - `ops-ingest/contracts/__init__.py`
- **Descrição**: Definir as classes de eventos de domínio como dataclasses ou Pydantic models: `IngestionCompletedEvent`, `FeaturesComputedEvent`, `PredictionEmittedEvent`. Incluir `trace_id` em todos os eventos (preparação para migração a Kafka na Fase 2 — DA-04).
- **Critério de verificação**: `python -c "from ops_ingest.contracts.events import IngestionCompletedEvent"` não levanta ImportError; campos obrigatórios presentes.

---

### TASK-005 [P] — Abstrações de storage (ops-store/storage_interface)

- **Esforço**: S
- **Paralelizável**: Sim
- **Depende de**: TASK-002
- **Tipo**: lógica-negócio
- **Risco**: alto
- **QA**: full
- **Perfil**: backend
- **Arquivos**:
  - `ops-store/app/storage_interface.py`
  - `ops-store/app/__init__.py`
- **Descrição**: Definir a interface abstrata de storage (`ABC`) com os métodos: `write_raw_readings`, `get_raw_readings_by_asset`, `write_feature_record`, `get_feature_records_by_asset`, `write_prediction`, `get_latest_prediction`, `write_audit_event`, `get_audit_log`. Esta abstração permite plugar InfluxDB/TimescaleDB sem alterar o core (DA-02).
- **Critério de verificação**: Classe abstrata importável; instanciar diretamente levanta `TypeError` por métodos abstratos não implementados.

---

### TASK-006 — Implementação DuckDB store (raw_readings e feature_records)

- **Esforço**: M
- **Paralelizável**: Não
- **Depende de**: TASK-003, TASK-005
- **Tipo**: lógica-negócio
- **Risco**: alto
- **QA**: full
- **Perfil**: backend
- **Arquivos**:
  - `ops-store/app/db/duckdb_store.py`
  - `ops-store/app/db/__init__.py`
- **Descrição**: Implementar `DuckDBStore` concretizando a interface de storage para `raw_readings` e `feature_records`. Incluir: escrita idempotente de feature records via INSERT OR IGNORE usando o índice único; leitura de raw readings por `asset_id` e janela temporal; conexão gerenciada com WAL para suporte a concorrência (risco 3 do plan).
- **Critério de verificação**: Teste unitário com DuckDB em memória: escreve 2 raw_readings, lê de volta — retorna exatamente 2. Escreve feature_record duplicado (mesmo asset+window) — retorna 1 registro (idempotência).

---

### TASK-007 — Implementação SQLite store (assets, predictions, models, audit)

- **Esforço**: M
- **Paralelizável**: Não
- **Depende de**: TASK-003, TASK-005
- **Tipo**: lógica-negócio
- **Risco**: alto
- **QA**: full
- **Perfil**: backend
- **Arquivos**:
  - `ops-store/app/db/sqlite_store.py`
  - `ops-store/app/db/sqlite_store.py`
- **Descrição**: Implementar `SQLiteStore` concretizando a interface de storage para tabelas SQLite: `assets` (com auto-registration — INIT-03), `ingestion_batches`, `predictions` (com escrita síncrona em transação com `audit_log` — RN-03/INIT-US-08-AC3), `explain_results`, `model_versions`, `audit_log`. Garantir que `write_prediction` e `write_audit_event` ocorram na mesma transação.
- **Critério de verificação**: Teste unitário com SQLite em memória: inserir prediction e audit_event em transação — ambos presentes após commit. Simular falha na escrita de audit_log — transação sofre rollback e prediction não é persistida.

---

### TASK-008 [P] — FastAPI app do ops-store (rotas internas de escrita/leitura)

- **Esforço**: S
- **Paralelizável**: Sim
- **Depende de**: TASK-006, TASK-007
- **Tipo**: crud-padrão
- **Risco**: médio
- **QA**: wave
- **Perfil**: backend
- **Arquivos**:
  - `ops-store/app/main.py`
  - `ops-store/requirements.txt`
- **Descrição**: Criar a FastAPI app de `ops-store` com as rotas internas: `POST /internal/readings` (persiste raw_readings), `GET /internal/readings/{asset_id}` (lê por ativo e janela), `POST /internal/features` (persiste feature_record), `GET /internal/features/{asset_id}` (lê por ativo), `POST /internal/predictions`, `GET /internal/predictions/{asset_id}/latest`. Injetar instâncias de `DuckDBStore` e `SQLiteStore` via FastAPI Depends. Incluir `requirements.txt` com dependências.
- **Critério de verificação**: `GET /internal/predictions/UNKNOWN/latest` retorna HTTP 404; `POST /internal/readings` com payload válido retorna HTTP 201.

---

### TASK-009 [P] — Schemas Pydantic de ingestão (ops-ingest/schemas)

- **Esforço**: S
- **Paralelizável**: Sim
- **Depende de**: TASK-002
- **Tipo**: lógica-negócio
- **Risco**: médio
- **QA**: wave
- **Perfil**: backend
- **Arquivos**:
  - `ops-ingest/app/schemas.py`
  - `ops-ingest/app/__init__.py`
- **Descrição**: Definir os schemas Pydantic de ingestão: `IngestReading` (campos do payload externo: `asset_id`, `timestamp`, `metric_name`, `value`, `unit`, `source_protocol`), `IngestRequest` (lista de `IngestReading`), `IngestionResponse` (`ingestion_id`, `records_received`, `records_accepted`, `records_rejected`, `rejection_details`). Validação por campo com mensagens descritivas (INIT-04).
- **Critério de verificação**: Teste unitário: `IngestReading` com `value="string"` levanta `ValidationError`; `IngestRequest` com lista vazia passa validação.

---

### TASK-010 — Normalizer e auto-registration (ops-ingest/normalizer)

- **Esforço**: M
- **Paralelizável**: Não
- **Depende de**: TASK-002, TASK-009
- **Tipo**: lógica-negócio
- **Risco**: alto
- **QA**: full
- **Perfil**: backend
- **Arquivos**:
  - `ops-ingest/app/normalizer.py`
  - `ops-ingest/app/adapters/rest_batch.py`
- **Descrição**: Implementar `Normalizer` com: (i) transformação de `IngestReading` para `CanonicalReading` com geração de UUID e `ingested_at`; (ii) reordenação de lista por `timestamp` antes de persistir (INIT-02); (iii) sinalização de `is_backfill=True` quando `timestamp` > `max_backfill_days` (edge case spec); (iv) lógica de auto-registration de `asset_id` desconhecido via chamada ao `ops-store` (INIT-03). Implementar `RestBatchAdapter` que chama o `Normalizer` e retorna `IngestionResponse`.
- **Critério de verificação**: Teste unitário com lista de leituras fora de ordem — retorna lista ordenada por timestamp. Leitura com timestamp 35 dias atrás (> default 30) — `is_backfill=True`. Asset desconhecido — chamada a `ops-store` para registro.

---

### TASK-011 [P] — Stubs MQTT e Kafka (ops-ingest/adapters)

- **Esforço**: S
- **Paralelizável**: Sim
- **Depende de**: TASK-009
- **Tipo**: lógica-negócio
- **Risco**: baixo
- **QA**: smoke
- **Perfil**: backend
- **Arquivos**:
  - `ops-ingest/app/adapters/mqtt_stub.py`
  - `ops-ingest/app/adapters/kafka_stub.py`
- **Descrição**: Criar stubs documentados para adaptadores MQTT e Kafka. Cada stub deve: (i) expor a mesma interface que o `RestBatchAdapter`; (ii) levantar `NotImplementedError` com mensagem clara indicando que a implementação completa é Fase 2; (iii) incluir docstring explicando o contrato esperado para implementação futura.
- **Critério de verificação**: Instanciar e chamar método principal de cada stub levanta `NotImplementedError` com mensagem não vazia.

---

### TASK-012 — FastAPI app do ops-ingest (roteador POST /telemetry interno)

- **Esforço**: S
- **Paralelizável**: Não
- **Depende de**: TASK-010, TASK-011
- **Tipo**: lógica-negócio
- **Risco**: alto
- **QA**: full
- **Perfil**: backend
- **Arquivos**:
  - `ops-ingest/app/main.py`
  - `ops-ingest/requirements.txt`
- **Descrição**: Criar FastAPI app de `ops-ingest` com rota interna `POST /internal/ingest`. App deve: injetar `RestBatchAdapter` via Depends; chamar normalizer; delegar persistência para `ops-store` via HTTP (httpx); retornar `IngestionResponse`. Integrar logging JSON estruturado com `trace_id` propagado do header. Incluir `requirements.txt`.
- **Critério de verificação**: `POST /internal/ingest` com payload válido retorna HTTP 202 com campos `ingestion_id`, `records_received`, `records_accepted`, `records_rejected`. Payload malformado retorna HTTP 400 com lista de erros por campo.

---

### TASK-013 — Feature extractor de vibração (RMS, variância, kurtosis, skewness, FFT)

- **Esforço**: M
- **Paralelizável**: Não
- **Depende de**: TASK-001
- **Tipo**: lógica-negócio
- **Risco**: alto
- **QA**: full
- **Perfil**: backend
- **Arquivos**:
  - `ops-feature/app/extractors/vibration.py`
  - `ops-feature/app/schemas.py`
- **Descrição**: Implementar `VibrationFeatureExtractor` usando NumPy/SciPy: cálculo de RMS, variância, kurtosis, skewness e FFT com `fft_bins` configuráveis (default 64) sobre um array de valores de vibração. Definir schemas Pydantic `FeatureRecord` e `FeatureRequest`. Extrator deve logar aviso e retornar `None` quando janela tem menos de `min_window_size` amostras (INIT-US-06-AC2).
- **Critério de verificação**: Teste unitário `test_vibration.py` com sinal sintético (senoide): verifica que RMS, variância, kurtosis, skewness retornam float; verifica que `fft_bins` retorna lista de 64 floats. Janela com 0 amostras retorna `None` e loga aviso (CAT-09).

---

### TASK-014 [P] — Janela deslizante e idempotência (ops-feature/windowing)

- **Esforço**: M
- **Paralelizável**: Sim
- **Depende de**: TASK-006, TASK-013
- **Tipo**: lógica-negócio
- **Risco**: alto
- **QA**: full
- **Perfil**: backend
- **Arquivos**:
  - `ops-feature/app/windowing.py`
  - `ops-feature/app/__init__.py`
- **Descrição**: Implementar `WindowingPipeline`: lê raw_readings de `ops-store` para um `asset_id`; particiona em janelas de tamanho configurável; para cada janela chama `VibrationFeatureExtractor`; persiste feature_record via `ops-store` com referência a `raw_record_ids`, `window_start`, `window_end`, `feature_version`. Garantir isolamento por ativo (INIT-US-06-AC4): exceção em um ativo não afeta outros. Idempotência garantida pelo índice único do DuckDB.
- **Critério de verificação**: Teste de integração com DuckDB em memória: ingerir 128 amostras para ativo A e 10 para ativo B; executar pipeline; ativo A gera feature_records, ativo B (< min_window_size) não gera. Executar pipeline novamente para ativo A — nenhum feature_record duplicado (CAT-13).

---

### TASK-015 — FastAPI app do ops-feature (rota de trigger de cálculo)

- **Esforço**: S
- **Paralelizável**: Não
- **Depende de**: TASK-014
- **Tipo**: crud-padrão
- **Risco**: médio
- **QA**: wave
- **Perfil**: backend
- **Arquivos**:
  - `ops-feature/app/main.py`
  - `ops-feature/requirements.txt`
- **Descrição**: Criar FastAPI app de `ops-feature` com rota interna `POST /internal/compute/{asset_id}` que dispara `WindowingPipeline` para o ativo informado. Incluir polling periódico (30s default, configurável) que verifica raw_readings sem feature_records e dispara cálculo. Logging JSON estruturado com `trace_id`. Incluir `requirements.txt`.
- **Critério de verificação**: `POST /internal/compute/ASSET-001` com dados de raw_readings disponíveis retorna HTTP 202. Chamada para ativo sem raw_readings retorna HTTP 200 com `{"computed": 0}`.

---

### TASK-016 — ONNX runner e model registry (ops-models/serving)

- **Esforço**: L
- **Paralelizável**: Não
- **Depende de**: TASK-007
- **Tipo**: lógica-negócio
- **Risco**: alto
- **QA**: full
- **Perfil**: backend
- **Arquivos**:
  - `ops-models/app/serving/onnx_runner.py`
  - `ops-models/app/serving/model_registry.py`
- **Descrição**: Implementar `OnnxRunner`: carrega artefato ONNX via `onnxruntime.InferenceSession`; executa inferência sobre vetor de features; retorna `anomaly_score` e `confidence_score`. Implementar `ModelRegistry`: gerencia versões em SQLite via `ops-store`; método `get_active_model(asset_class)` retorna a versão `is_active=True`; método `activate_version` desativa a anterior e ativa a nova (rollback possível — INIT-US-07-AC2); carregamento automático no boot da versão ativa (INIT-15).
- **Critério de verificação**: Teste com modelo ONNX dummy (gerado via `onnx.helper`): `OnnxRunner.run(features)` retorna float em [0,1]. `ModelRegistry.activate_version(v2)` marca v1 `is_active=False` e v2 `is_active=True`; `get_active_model` retorna v2.

---

### TASK-017 [P] — Schemas Pydantic de modelos e inferência (ops-models/schemas)

- **Esforço**: S
- **Paralelizável**: Sim
- **Depende de**: TASK-002
- **Tipo**: crud-padrão
- **Risco**: médio
- **QA**: wave
- **Perfil**: backend
- **Arquivos**:
  - `ops-models/app/schemas.py`
  - `ops-models/app/__init__.py`
- **Descrição**: Definir schemas Pydantic: `PredictionRequest` (feature_record_id, asset_id, asset_class), `PredictionResult` (prediction_id, asset_id, asset_class, anomaly_score, confidence_score, alert, severity, predicted_at, model_version, explain_status), `ModelDeployRequest` (asset_class, version, anomaly_threshold, severity_thresholds), `ModelDeployResponse` (model_id, version, asset_class, deployed_at, is_active).
- **Critério de verificação**: Schemas importáveis e instanciáveis com dados válidos; campo `severity` aceita apenas `low | medium | high | None`; `anomaly_score` e `confidence_score` são float em [0.0, 1.0] (validators).

---

### TASK-018 — FastAPI app do ops-models (inferência + deploy de modelos)

- **Esforço**: M
- **Paralelizável**: Não
- **Depende de**: TASK-016, TASK-017
- **Tipo**: lógica-negócio
- **Risco**: alto
- **QA**: full
- **Perfil**: backend
- **Arquivos**:
  - `ops-models/app/main.py`
  - `ops-models/requirements.txt`
- **Descrição**: Criar FastAPI app de `ops-models` com rotas internas: `POST /internal/predict` (executa inferência via `OnnxRunner`; calcula `alert` e `severity` a partir dos limiares do modelo; persiste predição via `ops-store`; dispara `ops-explain` assíncrono via `BackgroundTask`); `POST /internal/models/deploy` (valida formato ONNX ou TF SavedModel — retorna 422 para formato inválido, INIT-US-07-AC3; registra via `ModelRegistry`); `GET /internal/models/{model_id}`. Incluir `requirements.txt`.
- **Critério de verificação**: `POST /internal/predict` com feature_record válido retorna `PredictionResult` com `explain_status: pending`. `POST /internal/models/deploy` com arquivo `.pkl` retorna HTTP 422. Deploy de ONNX válido seguido de novo predict usa nova versão (CAT-10).

---

### TASK-019 — SHAP explainer e background tasks (ops-explain)

- **Esforço**: L
- **Paralelizável**: Não
- **Depende de**: TASK-007, TASK-016
- **Tipo**: lógica-negócio
- **Risco**: alto
- **QA**: full
- **Perfil**: backend
- **Arquivos**:
  - `ops-explain/app/shap_explainer.py`
  - `ops-explain/app/background.py`
- **Descrição**: Implementar `SHAPExplainer`: usa `shap.DeepExplainer` (DA-03, mitiga latência) sobre o autoencoder ONNX; calcula top-N features por magnitude de atribuição (default top-5 — INIT-US-03-AC4); computa `baseline_window` com estatísticas (mean, std, p5, p95) do período histórico de referência. Implementar `BackgroundTaskManager`: enfileira tarefas de explicabilidade; ao reiniciar o serviço, consulta predições com `explain_status=pending` e recoloca na fila (DA-03 mitigação de perda de tarefa). Atualiza `explain_status=ready` ou `failed` via `ops-store`.
- **Critério de verificação**: Teste com modelo ONNX dummy e features sintéticas: `SHAPExplainer.explain` retorna lista de `feature_attributions` com no mínimo 5 itens ordenados por `rank`. `BackgroundTaskManager.requeue_pending` chamado com 3 predições pending — todas enfileiradas.

---

### TASK-020 — FastAPI app do ops-explain (rota GET /explain interno)

- **Esforço**: S
- **Paralelizável**: Não
- **Depende de**: TASK-019
- **Tipo**: crud-padrão
- **Risco**: médio
- **QA**: wave
- **Perfil**: backend
- **Arquivos**:
  - `ops-explain/app/main.py`
  - `ops-explain/requirements.txt`
- **Descrição**: Criar FastAPI app de `ops-explain` com: `GET /internal/explain/{prediction_id}` — retorna resultado SHAP se `explain_status=ready`, HTTP 202 com `retry_after` se `pending`, HTTP 404 se inexistente. `POST /internal/explain/trigger/{prediction_id}` — enfileira cálculo SHAP para a predição informada (chamado por `ops-models` após inferência). Logging JSON estruturado. Incluir `requirements.txt`.
- **Critério de verificação**: `GET /internal/explain/{id}` com `explain_status=pending` retorna HTTP 202 e campo `retry_after > 0`. `GET /internal/explain/{id}` com `explain_status=ready` retorna HTTP 200 com `feature_attributions`.

---

### TASK-021 — Middlewares X-Advisory-Only e trace_id (ops-api/middleware)

- **Esforço**: S
- **Paralelizável**: Não
- **Depende de**: TASK-001
- **Tipo**: lógica-negócio
- **Risco**: alto
- **QA**: full
- **Perfil**: backend
- **Arquivos**:
  - `ops-api/app/middleware/advisory.py`
  - `ops-api/app/middleware/tracing.py`
- **Descrição**: Implementar middleware `AdvisoryMiddleware` que injeta header `X-Advisory-Only: true` em todas as respostas (RN-06). Implementar `TracingMiddleware` que: gera `trace_id` UUID se não presente no request; injeta `trace_id` no header de response; propaga via `contextvars` para que o logger inclua em todos os logs do request. Ambos registrados no app antes dos routers.
- **Critério de verificação**: Teste de integração: qualquer response da app tem header `X-Advisory-Only: true`. Request sem `X-Trace-Id` — response contém header com UUID gerado. Request com `X-Trace-Id: abc` — response repropaga `abc`.

---

### TASK-022 — Router POST /telemetry e GET /predictions/{asset_id} (ops-api)

- **Esforço**: M
- **Paralelizável**: Não
- **Depende de**: TASK-021, TASK-012, TASK-018
- **Tipo**: lógica-negócio
- **Risco**: alto
- **QA**: full
- **Perfil**: backend
- **Arquivos**:
  - `ops-api/app/routers/telemetry.py`
  - `ops-api/app/routers/predictions.py`
- **Descrição**: Implementar `POST /telemetry`: delega para `ops-ingest` via httpx; retorna HTTP 202 com `IngestionResponse` (INIT-US-01). Implementar `GET /predictions/{asset_id}`: consulta `ops-models`/`ops-store` para predição mais recente; retorna HTTP 200 com `PredictionResult`; HTTP 404 com mensagem descritiva quando sem predições (INIT-US-02). Ambos com header `X-Advisory-Only: true` (via middleware).
- **Critério de verificação**: Teste de contrato: `POST /telemetry` com payload válido retorna HTTP 202 com `ingestion_id`; payload malformado retorna HTTP 400. `GET /predictions/UNKNOWN` retorna HTTP 404. Response de predictions contém header `X-Advisory-Only: true` (CAT-04).

---

### TASK-023 [P] — Router GET /explain/{prediction_id} (ops-api)

- **Esforço**: S
- **Paralelizável**: Sim
- **Depende de**: TASK-021, TASK-020
- **Tipo**: crud-padrão
- **Risco**: médio
- **QA**: wave
- **Perfil**: backend
- **Arquivos**:
  - `ops-api/app/routers/explain.py`
  - `ops-api/app/routers/__init__.py`
- **Descrição**: Implementar `GET /explain/{prediction_id}`: delega para `ops-explain` via httpx; repropaga HTTP 200, 202 (com `retry_after`) ou 404 conforme status da explicabilidade (INIT-US-03). Propagar `trace_id` no header da chamada interna ao `ops-explain`.
- **Critério de verificação**: `GET /explain/{id}` com `explain_status=ready` retorna HTTP 200 com `feature_attributions` contendo ≥ 5 itens. `GET /explain/{id}` com `explain_status=pending` retorna HTTP 202 com `retry_after`.

---

### TASK-024 [P] — WebSocket manager e router WS /predictions/stream (ops-api)

- **Esforço**: M
- **Paralelizável**: Sim
- **Depende de**: TASK-021, TASK-018
- **Tipo**: lógica-negócio
- **Risco**: alto
- **QA**: full
- **Perfil**: backend
- **Arquivos**:
  - `ops-api/app/websocket_manager.py`
  - `ops-api/app/routers/stream.py`
- **Descrição**: Implementar `WebSocketManager`: mantém lista de conexões ativas; método `broadcast(prediction)` envia para todos os conectados filtrando por `filter_asset_id` se fornecido no handshake (INIT-US-04-AC4); desconexão registrada em log estruturado sem impacto nos demais clientes (INIT-US-04-AC3); `stream_sequence` incrementado por conexão. Tratar payload inválido no stream com código WS 1008 e log (edge case spec). Rota `WS /predictions/stream` registrada no router.
- **Critério de verificação**: Teste assíncrono com `pytest-asyncio`: dois clientes conectados, um com filtro `PUMP-001`, outro sem filtro; broadcast de predição para `PUMP-001` — primeiro cliente recebe, segundo também recebe. Broadcast para `PUMP-002` — apenas o cliente sem filtro recebe.

---

### TASK-025 [P] — Router POST /models/deploy (ops-api)

- **Esforço**: S
- **Paralelizável**: Sim
- **Depende de**: TASK-021, TASK-018
- **Tipo**: crud-padrão
- **Risco**: médio
- **QA**: wave
- **Perfil**: backend
- **Arquivos**:
  - `ops-api/app/routers/models.py`
  - `ops-api/app/routers/models.py`
- **Descrição**: Implementar `POST /models/deploy` que recebe `multipart/form-data` com `artifact` (arquivo) e `metadata` JSON; delega para `ops-models` via httpx; retorna `ModelDeployResponse` HTTP 200 ou HTTP 422 para formato inválido (INIT-US-07).
- **Critério de verificação**: `POST /models/deploy` com arquivo `.pkl` retorna HTTP 422 com mensagem de erro descritiva. Com arquivo ONNX válido retorna HTTP 200 com `model_id` e `is_active: true`.

---

### TASK-026 [P] — Router GET /health (ops-api)

- **Esforço**: S
- **Paralelizável**: Sim
- **Depende de**: TASK-021
- **Tipo**: lógica-negócio
- **Risco**: médio
- **QA**: wave
- **Perfil**: backend
- **Arquivos**:
  - `ops-api/app/routers/health.py`
  - `ops-api/app/routers/health.py`
- **Descrição**: Implementar `GET /health`: faz health check paralelo (asyncio.gather) em `ops-ingest`, `ops-store`, `ops-feature`, `ops-models`, `ops-explain` via `GET /health` de cada serviço; retorna HTTP 200 com `{"status": "healthy", "services": {...}}` quando todos healthy; HTTP 503 com `{"status": "starting"}` quando algum serviço ainda está subindo (edge case spec). Incluir `checked_at` timestamp.
- **Critério de verificação**: Mock de `ops-models` retornando 503 — `GET /health` retorna HTTP 503 com `ops-models: starting`. Todos os serviços mockados como healthy — retorna HTTP 200 com `status: healthy`.

---

### TASK-027 [P] — Routers GET /metrics e GET /audit (ops-api)

- **Esforço**: M
- **Paralelizável**: Sim
- **Depende de**: TASK-021, TASK-007
- **Tipo**: crud-padrão
- **Risco**: médio
- **QA**: wave
- **Perfil**: backend
- **Arquivos**:
  - `ops-api/app/routers/metrics.py`
  - `ops-api/app/routers/audit.py`
- **Descrição**: Implementar `GET /metrics`: usa `prometheus-client` para expor `predictions_total` (counter por asset_class), `predictions_latency_seconds` (histogram), `ingestion_records_total` (counter por status), `model_inference_latency_seconds` (histogram por model) — formato Prometheus text/plain (INIT-US-08-AC1). Implementar `GET /audit`: consulta `audit_log` via `ops-store`; suporta query params `asset_id`, `from`, `to`, `page`, `page_size` (INIT-US-08-AC2).
- **Critério de verificação**: `GET /metrics` retorna Content-Type `text/plain` com as 4 métricas obrigatórias no formato Prometheus (CAT-12). `GET /audit?asset_id=PUMP-001&page=1&page_size=10` retorna JSON com campos `events`, `total`, `page`, `page_size`.

---

### TASK-028 — FastAPI app principal do ops-api (main.py, registro de routers e middlewares)

- **Esforço**: M
- **Paralelizável**: Não
- **Depende de**: TASK-022, TASK-023, TASK-024, TASK-025, TASK-026, TASK-027
- **Tipo**: lógica-negócio
- **Risco**: alto
- **QA**: full
- **Perfil**: backend
- **Arquivos**:
  - `ops-api/app/main.py`
  - `ops-api/requirements.txt`
- **Descrição**: Criar app FastAPI principal de `ops-api`: registrar middlewares `AdvisoryMiddleware` e `TracingMiddleware` (TASK-021); incluir todos os routers (telemetry, predictions, explain, stream, models, health, metrics, audit); configurar lifespan para inicializar `WebSocketManager` e `prometheus-client`. Incluir `requirements.txt` com todas as dependências do serviço. Aviso explícito de sistema advisory no endpoint `/` (RN-06).
- **Critério de verificação**: App inicializa sem erro com `uvicorn ops_api.app.main:app`; `GET /` retorna payload com aviso de sistema advisory; todos os routers acessíveis (lista de rotas via `GET /openapi.json`).

---

### TASK-029 [P] — ops-reference: pipeline de treinamento do autoencoder de vibração

- **Esforço**: L
- **Paralelizável**: Sim
- **Depende de**: TASK-013
- **Tipo**: lógica-negócio
- **Risco**: alto
- **QA**: full
- **Perfil**: backend
- **Arquivos**:
  - `ops-reference/training/train_vibration_autoencoder.py`
  - `ops-reference/models/vibration_autoencoder_v1_metrics.json`
- **Descrição**: Implementar pipeline reproduzível de treinamento do autoencoder (TF/Keras) sobre o CWRU Bearing Dataset: carrega subset `ops-reference/datasets/cwru_sample/`; aplica `VibrationFeatureExtractor` (TASK-013) para gerar features; treina autoencoder; exporta para ONNX via `tf2onnx`; salva artefato em `ops-reference/models/vibration_autoencoder_v1.onnx`; salva métricas de precision/recall em `vibration_autoencoder_v1_metrics.json`. Dataset CWRU sample deve ser commitado em `ops-reference/datasets/cwru_sample/` (< 10MB, verificar antes). Artefato ONNX via Git LFS (risco 5 do plan).
- **Critério de verificação**: Script executa end-to-end sem erro; `vibration_autoencoder_v1.onnx` criado e carregável via `onnxruntime`; `vibration_autoencoder_v1_metrics.json` contém campos `precision`, `recall`, `f1` com valores numéricos (CAT-06).

---

### TASK-030 — Docker Compose: orquestração completa com healthchecks e init automático de modelo

- **Esforço**: M
- **Paralelizável**: Não
- **Depende de**: TASK-008, TASK-012, TASK-015, TASK-018, TASK-020, TASK-028, TASK-029
- **Tipo**: infra
- **Risco**: alto
- **QA**: full
- **Perfil**: infra
- **Arquivos**:
  - `docker-compose.yml`
  - `docker-compose.override.yml`
- **Descrição**: Criar `docker-compose.yml` com todos os 7 serviços (ops-ingest:8001, ops-store:8002, ops-feature:8003, ops-models:8004, ops-explain:8005, ops-api:8000, ops-ui:3000); volumes `oilops-data` e `oilops-models`; healthchecks para cada serviço; `depends_on: condition: service_healthy` (risco 4 do plan); comentário explícito de aviso sobre autenticação desativada por padrão (risco 6). Init automático do modelo de referência no boot de `ops-models` via script de entrypoint que copia ONNX para volume e registra no DB (INIT-15). Criar `docker-compose.override.yml` com overrides de dev (volume mounts de código, portas extras).
- **Critério de verificação**: `docker compose up --wait` completa sem erro; `docker compose ps` mostra todos os serviços com status `healthy`; `curl http://localhost:8000/health` retorna HTTP 200 (CAT-01, CAT-02).

---

### TASK-031 [P] — Makefile e scaffolding raiz (targets up, down, test, lint, quickstart)

- **Esforço**: S
- **Paralelizável**: Sim
- **Depende de**: TASK-030
- **Tipo**: infra
- **Risco**: baixo
- **QA**: smoke
- **Perfil**: infra
- **Arquivos**:
  - `Makefile`
  - `.gitattributes`
- **Descrição**: Criar `Makefile` com targets: `up` (`docker compose up -d --wait`), `down`, `test` (executa pytest em todos os módulos), `lint` (ruff/flake8), `format` (black/isort), `train` (executa pipeline de treinamento do ops-reference), `quickstart` (up + ingestão de exemplo + chamada a /predictions). Criar `.gitattributes` configurando Git LFS para `*.onnx` (risco 5 do plan).
- **Critério de verificação**: `make up` executa sem erro de sintaxe Make; `make down` para os serviços; `.gitattributes` tem linha `*.onnx filter=lfs diff=lfs merge=lfs -text`.

---

### TASK-032 [P] — ops-cli: scaffolding Typer com comando inspect implementado

- **Esforço**: M
- **Paralelizável**: Sim
- **Depende de**: TASK-022
- **Tipo**: crud-padrão
- **Risco**: baixo
- **QA**: auto
- **Perfil**: backend
- **Arquivos**:
  - `ops-cli/main.py`
  - `ops-cli/requirements.txt`
- **Descrição**: Criar CLI Typer com os comandos: `inspect` (implementado — chama `GET /predictions/{asset_id}` e exibe resultado formatado); `deploy`, `train`, `evaluate`, `replay`, `export` (stubs que levantam `typer.Exit` com mensagem "Not implemented — Fase 2"). Incluir `requirements.txt` com `typer`, `httpx`, `rich`.
- **Critério de verificação**: `python ops-cli/main.py inspect --help` exibe documentação sem erro. `python ops-cli/main.py deploy` exibe mensagem de "Not implemented — Fase 2" e retorna exit code não-zero.

---

### TASK-033 [P] — ops-ui: scaffolding Angular 17 com tela de inspeção de predições

- **Esforço**: L
- **Paralelizável**: Sim
- **Depende de**: TASK-022
- **Tipo**: ui-puro
- **Risco**: médio
- **QA**: wave
- **Perfil**: frontend
- **Arquivos**:
  - `ops-ui/src/app/app.module.ts`
  - `ops-ui/src/app/pages/inspection/inspection.component.ts`
- **Descrição**: Gerar projeto Angular 17 em `ops-ui/` com Angular CLI; criar módulo e componente `InspectionComponent`: campo de input para `asset_id`, botão de consulta, chamada a `GET /predictions/{asset_id}` via HttpClient, exibição dos campos `anomaly_score`, `confidence_score`, `alert`, `severity`, `explain_status`. Estado de loading e mensagem de erro para HTTP 404. Nginx config para servir build estático no container ops-ui.
- **Critério de verificação**: `npm run build` completa sem erros; componente renderiza formulário e tabela de resultado; `ng test` passa para o componente de inspeção.

---

### TASK-034 [P] — ops-ui: Dockerfile multi-stage (build Angular + serve nginx)

- **Esforço**: S
- **Paralelizável**: Sim
- **Depende de**: TASK-033
- **Tipo**: infra
- **Risco**: baixo
- **QA**: smoke
- **Perfil**: infra
- **Arquivos**:
  - `ops-ui/Dockerfile`
  - `ops-ui/nginx.conf`
- **Descrição**: Criar Dockerfile multi-stage: stage `build` usa `node:18-alpine` para `npm ci && npm run build`; stage final usa `nginx:alpine` copiando o `dist/` para `/usr/share/nginx/html`. Criar `nginx.conf` configurando proxy reverso para `ops-api:8000` no path `/api/`. Imagem resultante deve ser referenciada no `docker-compose.yml` (TASK-030).
- **Critério de verificação**: `docker build -t ops-ui ops-ui/` completa sem erro; container sobe e `curl http://localhost:3000` retorna HTML do app Angular.

---

## Grupos de Paralelização Sugeridos

- **Onda 1** (pode começar imediatamente):
  - TASK-001 — shared logging e config (bloqueante; base de tudo)

- **Onda 2** (após TASK-001):
  - TASK-002 [P] — schema canônico
  - TASK-003 — migrations DDL (depende de TASK-001)
  - TASK-013 — feature extractor de vibração (depende de TASK-001)

- **Onda 3** (após TASK-002 e TASK-003):
  - TASK-004 [P] — contratos de eventos
  - TASK-005 [P] — storage interface
  - TASK-009 [P] — schemas de ingestão
  - TASK-017 [P] — schemas de modelos

- **Onda 4** (após TASK-003 + TASK-005):
  - TASK-006 — DuckDB store
  - TASK-007 — SQLite store
  - TASK-014 [P] — windowing (depende de TASK-006 + TASK-013)

- **Onda 5** (após TASK-006 + TASK-007):
  - TASK-008 [P] — FastAPI ops-store
  - TASK-010 — normalizer + rest_batch (depende de TASK-002 + TASK-009)
  - TASK-016 — ONNX runner + model registry (depende de TASK-007)
  - TASK-029 [P] — pipeline de treinamento (depende de TASK-013)

- **Onda 6** (após TASK-010 + TASK-014 + TASK-015-deps):
  - TASK-011 [P] — stubs MQTT/Kafka
  - TASK-015 — FastAPI ops-feature (depende de TASK-014)
  - TASK-017 [P] — schemas de modelos (se não concluído na onda 3)

- **Onda 7** (após TASK-008 + TASK-010 + TASK-011):
  - TASK-012 — FastAPI ops-ingest
  - TASK-016 — ONNX runner + model registry (se não concluído)

- **Onda 8** (após TASK-016 + TASK-017):
  - TASK-018 — FastAPI ops-models
  - TASK-019 — SHAP explainer + background tasks (depende de TASK-007 + TASK-016)

- **Onda 9** (após TASK-019):
  - TASK-020 — FastAPI ops-explain
  - TASK-021 — middlewares advisory + tracing (pode iniciar após TASK-001)

- **Onda 10** (após TASK-021 + TASK-012 + TASK-018 + TASK-020):
  - TASK-022 — routers POST /telemetry + GET /predictions
  - TASK-023 [P] — router GET /explain
  - TASK-024 [P] — WebSocket manager + router stream
  - TASK-025 [P] — router POST /models/deploy
  - TASK-026 [P] — router GET /health
  - TASK-027 [P] — routers GET /metrics + GET /audit

- **Onda 11** (após todos os routers):
  - TASK-028 — FastAPI app principal ops-api
  - TASK-033 [P] — ops-ui scaffolding Angular (pode iniciar após TASK-022)
  - TASK-032 [P] — ops-cli scaffolding Typer (pode iniciar após TASK-022)

- **Onda 12** (após TASK-028 + todos os serviços):
  - TASK-030 — Docker Compose orquestração completa
  - TASK-034 [P] — Dockerfile multi-stage ops-ui (após TASK-033)

- **Onda 13** (após TASK-030):
  - TASK-031 [P] — Makefile e .gitattributes
