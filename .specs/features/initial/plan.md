# Plano Técnico — Motor Preditivo Inicial: Scaffolding e Contratos de Núcleo

## 1. Resumo Executivo

OilOps-PredictiveCore é um motor de inteligência preditiva para operadores de energia dos EUA. A feature INITIAL corresponde à Fase 1 do PRD (Meses 0–12): criação do scaffolding completo da arquitetura de microserviços, definição e implementação dos contratos canônicos de ingestão de telemetria e de interface de modelo, e entrega da implementação de referência do primeiro modelo — detecção de anomalia de vibração em equipamentos rotativos usando autoencoder sobre features FFT.

O resultado tangível desta feature é um repositório que passa de "apenas PRD" para uma stack funcional e implantável via `docker compose up`. Qualquer operador que seguir o quickstart poderá, em menos de 10 minutos, ingerir telemetria de exemplo e receber uma predição anotada com score de confiança e status de explicabilidade. Todos os outros módulos da Fase 1 (ingestão, armazenamento, features, modelos, explicabilidade, API) estarão scaffolded com contratos definidos, mesmo que algumas implementações internas sejam stubs documentados para evolução na Fase 2.

A stack adota Python 3.11+ / FastAPI como runtime primário dos serviços de backend, coerente com o padrão dominante do portfólio do autor (ver "AI - Credit Approval ML API" como referência canônica) e adequado às exigências de bibliotecas ML (TensorFlow, ONNX Runtime, SHAP, NumPy/SciPy). O frontend (`ops-ui`) será Angular (17), seguindo o padrão existente de dashboards no portfólio. O mecanismo de persistência embarcado out-of-the-box será SQLite + DuckDB para séries temporais leves, com abstração de storage permitindo migrar para InfluxDB/TimescaleDB sem alteração do core.

---

## 2. Premissas e Lacunas do Spec

### Lacunas explícitas (marcadas `[LACUNA]` no spec)

| ID | Lacuna | Decisão adotada neste plano | Justificativa |
|----|--------|-----------------------------|---------------|
| LAC-01 | Linguagem/runtime primário do backend não definida no PRD v0.1 | **Python 3.11+ / FastAPI** | Padrão dominante do portfólio (AI - Credit Approval ML API); ecossistema ML mais rico (TensorFlow, ONNX, SHAP, NumPy). Requer aprovação explícita do autor antes da implementação. |
| LAC-02 | Formato de autenticação da API (JWT, API Key, mTLS) não especificado | **Fase 1: sem autenticação obrigatória** (endpoint aberto para ambiente local de avaliação); **variável de ambiente `OILOPS_API_KEY` opcional** disponível como mecanismo básico de API Key para quem quiser ativar | PRD declara explicitamente que Fase 1 é "early prototype / evaluation". Produção exige decisão formal antes da Fase 2. |
| LAC-03 | SLA de latência de predição não quantificado ("segundos a minutos") | **Limiar proposto para benchmark: p95 < 2 segundos** para predição síncrona (inferência); **p95 < 30 segundos** para explicabilidade assíncrona | PRD descarta "millisecond-latency control loops" e posiciona como "operational decision latency". 2s é conservador e mensurável. Requer validação do autor. |

### Premissas adicionais adotadas

- O modelo de referência de vibração será treinado sobre o dataset público **CWRU Bearing Dataset** (Case Western Reserve University), amplamente referenciado na literatura de predictive maintenance industrial. Artefato pré-treinado embarcado em `ops-reference/models/`.
- Adaptadores MQTT e Kafka em `ops-ingest` serão scaffolded como stubs com interface documentada; implementação completa é Fase 2, conforme declarado no spec.
- O armazenamento embarcado out-of-the-box será **SQLite** para metadados (ativos, predições, audit log, registro de modelos) e **DuckDB** para séries temporais (leituras brutas e feature records), pois ambos rodam em-processo sem serviços externos, satisfazendo RN-08 (autossuficiência de deployment).
- `ops-ui` entrega estrutura Angular 17 com tela de inspeção de predições; dashboard operacional completo é Fase 2.
- `ops-cli` entrega scaffolding dos comandos (`deploy`, `train`, `evaluate`, `replay`, `inspect`, `export`) com implementação do `inspect` como referência; demais comandos retornam `NotImplementedError` documentado.
- Comunicação interna entre serviços na Fase 1 é via **HTTP REST in-process** (serviços como módulos Python dentro do mesmo contêiner `ops-api` ou via HTTP entre contêineres Docker Compose). Mensageria assíncrona (Kafka interno) é Fase 2.

---

## 3. Arquitetura Proposta

### 3.1 Visão de Componentes

```
┌─────────────────────────────────────────────────────────────────────────┐
│  Docker Compose Network: oilops-net                                     │
│                                                                         │
│  ┌──────────────┐    schema canônico    ┌──────────────┐               │
│  │  ops-ingest  │ ─────────────────────▶│  ops-store   │               │
│  │  (FastAPI)   │                       │  (FastAPI +  │               │
│  │  REST batch  │                       │  DuckDB +    │               │
│  │  MQTT stub   │                       │  SQLite)     │               │
│  │  Kafka stub  │                       └──────┬───────┘               │
│  └──────────────┘                              │ raw records           │
│                                                ▼                       │
│                                        ┌──────────────┐               │
│                                        │  ops-feature │               │
│                                        │  (FastAPI +  │               │
│                                        │  NumPy/SciPy │               │
│                                        │  /PyFFTW)    │               │
│                                        └──────┬───────┘               │
│                                               │ feature vectors        │
│                                               ▼                       │
│                                        ┌──────────────┐               │
│                                        │  ops-models  │               │
│                                        │  (FastAPI +  │               │
│                                        │  TF/ONNX RT) │               │
│                                        └──────┬───────┘               │
│                                               │ prediction + score     │
│                                               ▼                       │
│                                        ┌──────────────┐               │
│                                        │  ops-explain │               │
│                                        │  (FastAPI +  │               │
│                                        │  SHAP +      │               │
│                                        │  asyncio BG) │               │
│                                        └──────┬───────┘               │
│                                               │                       │
│  ┌──────────────┐    REST / WS         ┌──────▼───────┐               │
│  │  ops-ui      │ ◀────────────────── │  ops-api     │               │
│  │  (Angular 17)│                      │  (FastAPI +  │               │
│  └──────────────┘                      │  WebSocket)  │               │
│                                        └──────────────┘               │
│                                                                         │
│  ┌──────────────┐    ┌──────────────┐                                  │
│  │  ops-cli     │    │ ops-reference│                                  │
│  │  (Python CLI │    │ (artefatos + │                                  │
│  │  / Typer)    │    │  datasets +  │                                  │
│  └──────────────┘    │  pipelines)  │                                  │
│                      └──────────────┘                                  │
└─────────────────────────────────────────────────────────────────────────┘
```

**Serviços Docker Compose:**

| Serviço | Imagem base | Porta exposta | Responsabilidade |
|---------|-------------|---------------|------------------|
| `ops-ingest` | python:3.11-slim | 8001 (interno) | Gateway de ingestão; normaliza para schema canônico |
| `ops-store` | python:3.11-slim | 8002 (interno) | Persistência de séries temporais (DuckDB) e metadados (SQLite) |
| `ops-feature` | python:3.11-slim | 8003 (interno) | Feature engineering por janela deslizante |
| `ops-models` | python:3.11-slim | 8004 (interno) | Serving de modelos TF/ONNX; registro de versões |
| `ops-explain` | python:3.11-slim | 8005 (interno) | Cálculo assíncrono de atribuições SHAP |
| `ops-api` | python:3.11-slim | **8000 (exposta)** | API pública REST + WebSocket; agrega todos os serviços |
| `ops-ui` | node:18-alpine (build) + nginx:alpine | **3000 (exposta)** | Dashboard Angular servido por nginx |

Volumes Docker: `oilops-data` (DuckDB + SQLite), `oilops-models` (artefatos de modelos).

### 3.2 Fluxo Principal

**Caminho de ingestão (síncrono):**

1. Cliente envia `POST /telemetry` para `ops-api` com lista de leituras no schema de ingestão.
2. `ops-api` delega para `ops-ingest` via HTTP interno.
3. `ops-ingest` valida, reordena por timestamp, normaliza para schema canônico.
4. `ops-ingest` chama `ops-store` para persistir os registros brutos (imutáveis).
5. `ops-api` retorna HTTP 202 com `ingestion_id`, `records_received`, `records_accepted`, `records_rejected`.
6. Em background: `ops-store` publica evento interno; `ops-feature` consome e computa features por janela deslizante.
7. `ops-feature` persiste feature records vinculados aos raw records.
8. `ops-feature` publica evento; `ops-models` consome, executa inferência, persiste predição.
9. `ops-models` publica evento; `ops-explain` inicia cálculo SHAP assíncrono em background.
10. Predição fica disponível via `GET /predictions/{asset_id}` com `explain_status: pending`.
11. Quando SHAP conclui, `ops-explain` atualiza `explain_status: ready`; predição disponível via `GET /explain/{prediction_id}`.
12. Clientes WebSocket conectados em `WS /predictions/stream` recebem predição no momento em que ela é persistida (passo 8).

**Fluxo de comunicação interna Fase 1:**

Na Fase 1, a comunicação entre serviços é HTTP síncrono entre contêineres Docker Compose. O pipeline de feature → model → explain é disparado por polling periódico (configurável, default: 30s) sobre novos registros sem features, e por trigger imediato via chamada HTTP quando ingestão persiste registros. Isso evita a dependência de um broker de mensagens externo (satisfaz RN-08) mantendo a interface preparada para Kafka na Fase 2.

### 3.3 Decisões Arquiteturais (com trade-offs)

**DA-01: Python/FastAPI como runtime primário**

- Escolhido: alinhado ao padrão dominante do portfólio; ecossistema ML mais rico (TF, ONNX, SHAP, NumPy, SciPy).
- Trade-off: GIL Python limita paralelismo CPU-bound; para throughput alto de inferência, ONNX Runtime com workers separados mitiga isso.
- Alternativa considerada e rejeitada: Java/Spring WebFlux (presente no portfólio, mas ecossistema ML Java menos maduro; TensorFlow Java existe mas é menos mantido que o Python).

**DA-02: DuckDB para séries temporais embarcadas**

- Escolhido: DuckDB é analítico, in-process, sem servidor, suporta queries temporais eficientes, lida bem com janelas deslizantes em Python.
- Trade-off: não é otimizado para escritas de alta frequência em tempo real (é analítico). Para Fase 1 com ingestão batch, é adequado.
- Alternativa considerada e rejeitada: SQLite puro (insuficiente para queries temporais analíticas); InfluxDB (requer serviço separado, violaria autossuficiência embarcada).
- Evolução Fase 2: abstração de storage permite plugar InfluxDB/TimescaleDB sem mudar o core.

**DA-03: SHAP assíncrono via `asyncio.create_task` + BackgroundTask FastAPI**

- Escolhido: predição emitida imediatamente após inferência; SHAP roda em background (RN-04).
- Trade-off: estado de explain_status precisa ser persistido atomicamente; usando SQLite para esse registro de estado.
- Risco: se o processo morrer durante o cálculo SHAP, a tarefa se perde. Mitigação Fase 1: ao reiniciar, predições com `explain_status: pending` são reenfileiradas para cálculo.

**DA-04: Comunicação interna HTTP síncrona (Fase 1) com interface preparada para Kafka (Fase 2)**

- Escolhido: elimina dependência de broker para deployment simples (RN-08).
- Trade-off: sem back-pressure, sem replay de eventos. Aceitável para Fase 1 / avaliação local.
- Contratos de domínio de eventos definidos em `ops-ingest/contracts/events.py` desde o início para facilitar migração.

**DA-05: Autoencoder de vibração com TensorFlow/Keras + ONNX export**

- Escolhido: TensorFlow/Keras para treinamento (referência bem documentada para CWRU dataset); modelo exportado como ONNX para serving via ONNX Runtime (menor footprint em inferência).
- Trade-off: exige TF apenas no treinamento (ops-reference); ops-models usa apenas ONNX Runtime.

**DA-06: Typer para ops-cli**

- Escolhido: Typer é o padrão Python moderno para CLIs (usado amplamente com FastAPI); coerente com o runtime do projeto.
- Alternativa considerada: Click (base do Typer, mais verboso). Typer é escolha natural.

---

## 4. Modelos de Dados

### 4.1 Schema canônico de telemetria (RN-01)

**Tabela: `raw_readings` (DuckDB)**

| Coluna | Tipo | Restrição | Descrição |
|--------|------|-----------|-----------|
| `id` | UUID | PK | Identificador imutável do registro |
| `asset_id` | VARCHAR(64) | NOT NULL, FK→assets | Identificador do ativo |
| `timestamp` | TIMESTAMPTZ | NOT NULL | Timestamp da leitura (UTC) |
| `metric_name` | VARCHAR(128) | NOT NULL | Nome da métrica (ex: vibration_x) |
| `value` | DOUBLE | NOT NULL | Valor da leitura |
| `unit` | VARCHAR(32) | NOT NULL | Unidade de engenharia |
| `source_protocol` | VARCHAR(32) | NOT NULL | Protocolo de origem (rest_batch, mqtt, kafka, opcua) |
| `ingested_at` | TIMESTAMPTZ | NOT NULL | Timestamp de ingestão no sistema |
| `ingestion_id` | UUID | NOT NULL, FK→ingestion_batches | Lote de ingestão que originou o registro |
| `is_backfill` | BOOLEAN | DEFAULT FALSE | Sinaliza dados históricos > max_backfill_window |

**Tabela: `assets` (SQLite)**

| Coluna | Tipo | Restrição | Descrição |
|--------|------|-----------|-----------|
| `id` | VARCHAR(64) | PK | asset_id canônico |
| `asset_class` | VARCHAR(64) | NOT NULL | Classe do ativo (rotating_equipment, pump, pipeline) |
| `registered_at` | TIMESTAMPTZ | NOT NULL | Timestamp de auto-registration |
| `metadata` | JSON | NULLABLE | Metadados livres do ativo |

**Tabela: `ingestion_batches` (SQLite)**

| Coluna | Tipo | Restrição | Descrição |
|--------|------|-----------|-----------|
| `id` | UUID | PK | ingestion_id retornado ao cliente |
| `received_at` | TIMESTAMPTZ | NOT NULL | Momento do recebimento |
| `records_received` | INTEGER | NOT NULL | Total de registros no payload |
| `records_accepted` | INTEGER | NOT NULL | Registros aceitos e persistidos |
| `records_rejected` | INTEGER | NOT NULL | Registros rejeitados (validação) |
| `rejection_details` | JSON | NULLABLE | Lista de erros por campo dos rejeitados |
| `source_ip` | VARCHAR(64) | NULLABLE | IP do cliente |

### 4.2 Feature records

**Tabela: `feature_records` (DuckDB)**

| Coluna | Tipo | Restrição | Descrição |
|--------|------|-----------|-----------|
| `id` | UUID | PK | Identificador do feature record |
| `asset_id` | VARCHAR(64) | NOT NULL, FK→assets | Ativo |
| `window_start` | TIMESTAMPTZ | NOT NULL | Início da janela de cálculo |
| `window_end` | TIMESTAMPTZ | NOT NULL | Fim da janela de cálculo |
| `raw_record_ids` | JSON (array UUID) | NOT NULL | IDs dos raw_readings incluídos |
| `feature_version` | VARCHAR(32) | NOT NULL | Versão do pipeline de features |
| `rms` | DOUBLE | NULLABLE | Root Mean Square |
| `variance` | DOUBLE | NULLABLE | Variância |
| `kurtosis` | DOUBLE | NULLABLE | Kurtosis |
| `skewness` | DOUBLE | NULLABLE | Skewness |
| `fft_bins` | JSON (array double) | NULLABLE | Vetor de bins FFT (default 64 bins) |
| `computed_at` | TIMESTAMPTZ | NOT NULL | Timestamp do cálculo |

Índice único em `(asset_id, window_start, window_end, feature_version)` para garantir idempotência (edge case de computação simultânea).

### 4.3 Predições e explicabilidade

**Tabela: `predictions` (SQLite)**

| Coluna | Tipo | Restrição | Descrição |
|--------|------|-----------|-----------|
| `id` | UUID | PK | prediction_id |
| `asset_id` | VARCHAR(64) | NOT NULL, FK→assets | Ativo predito |
| `asset_class` | VARCHAR(64) | NOT NULL | Classe do ativo na época da predição |
| `anomaly_score` | DOUBLE | NOT NULL | Score de anomalia (0–1) |
| `confidence_score` | DOUBLE | NOT NULL | Confiança da predição (0–1) |
| `alert` | BOOLEAN | NOT NULL | anomaly_score >= limiar do modelo |
| `severity` | VARCHAR(8) | NULLABLE | low / medium / high (quando alert=true) |
| `model_id` | VARCHAR(64) | NOT NULL, FK→model_versions | Modelo que gerou a predição |
| `model_version` | VARCHAR(32) | NOT NULL | Versão do modelo |
| `feature_record_id` | UUID | NOT NULL, FK→feature_records | Features usadas |
| `predicted_at` | TIMESTAMPTZ | NOT NULL | Timestamp da predição |
| `explain_status` | VARCHAR(16) | NOT NULL DEFAULT 'pending' | pending / ready / failed |

**Tabela: `explain_results` (SQLite)**

| Coluna | Tipo | Restrição | Descrição |
|--------|------|-----------|-----------|
| `id` | UUID | PK | ID do resultado de explicabilidade |
| `prediction_id` | UUID | NOT NULL, UNIQUE, FK→predictions | Predição explicada |
| `method` | VARCHAR(32) | NOT NULL | shap / permutation |
| `feature_attributions` | JSON | NOT NULL | Array [{feature_name, attribution_value, rank}] |
| `baseline_window` | JSON | NOT NULL | {start, end, mean, std, p5, p95} por feature |
| `computed_at` | TIMESTAMPTZ | NOT NULL | Timestamp do cálculo SHAP |

### 4.4 Registro de modelos

**Tabela: `model_versions` (SQLite)**

| Coluna | Tipo | Restrição | Descrição |
|--------|------|-----------|-----------|
| `id` | VARCHAR(64) | PK | model_id (ex: vibration-autoencoder-v1) |
| `version` | VARCHAR(32) | NOT NULL | Versão semântica |
| `asset_class` | VARCHAR(64) | NOT NULL | Classe de ativo alvo |
| `artifact_path` | VARCHAR(512) | NOT NULL | Caminho relativo do artefato ONNX no volume |
| `artifact_format` | VARCHAR(16) | NOT NULL | onnx / tensorflow_savedmodel |
| `deployed_at` | TIMESTAMPTZ | NOT NULL | Timestamp do deploy |
| `is_active` | BOOLEAN | NOT NULL DEFAULT FALSE | Versão em uso para inferência |
| `deployed_by` | VARCHAR(128) | NULLABLE | Origem do deploy (auto / operador) |
| `anomaly_threshold` | DOUBLE | NOT NULL DEFAULT 0.5 | Limiar para alert=true |
| `severity_thresholds` | JSON | NOT NULL | {low, medium, high} — limites de severidade |

### 4.5 Audit log

**Tabela: `audit_log` (SQLite)**

| Coluna | Tipo | Restrição | Descrição |
|--------|------|-----------|-----------|
| `id` | UUID | PK | ID do evento |
| `event_type` | VARCHAR(64) | NOT NULL | prediction_emitted / model_deployed / ingestion_received |
| `prediction_id` | UUID | NULLABLE | FK→predictions (quando aplicável) |
| `asset_id` | VARCHAR(64) | NULLABLE | Ativo relacionado |
| `model_version` | VARCHAR(32) | NULLABLE | Versão do modelo (quando aplicável) |
| `triggered_at` | TIMESTAMPTZ | NOT NULL | Timestamp do evento |
| `confidence_score` | DOUBLE | NULLABLE | Score de confiança (quando aplicável) |
| `trace_id` | VARCHAR(64) | NULLABLE | ID de rastreabilidade distribuída |
| `details` | JSON | NULLABLE | Payload livre adicional |

**Nota sobre escrita síncrona (RN-03 / INIT-US-08 AC3):** o audit log é gravado na mesma transação SQLite da predição, antes do response ser enviado ao cliente.

---

## 5. Contratos de API

### Header obrigatório em todas as respostas de predição (RN-06)

```
X-Advisory-Only: true
```

### 5.1 POST /telemetry

**Request:**

```json
{
  "readings": [
    {
      "asset_id": "string",
      "timestamp": "2026-05-16T10:00:00Z",
      "metric_name": "vibration_x",
      "value": 0.0023,
      "unit": "m/s2",
      "source_protocol": "rest_batch"
    }
  ]
}
```

**Response 202:**

```json
{
  "ingestion_id": "uuid",
  "records_received": 100,
  "records_accepted": 98,
  "records_rejected": 2,
  "rejection_details": [
    {"index": 5, "field": "timestamp", "error": "invalid ISO 8601 format"},
    {"index": 42, "field": "value", "error": "expected float, got string"}
  ]
}
```

**Response 400:** payload malformado (campo obrigatório ausente, tipo inválido).

```json
{
  "detail": [
    {"loc": ["body", "readings", 0, "asset_id"], "msg": "field required", "type": "value_error.missing"}
  ]
}
```

### 5.2 GET /predictions/{asset_id}

**Response 200:**

```json
{
  "prediction_id": "uuid",
  "asset_id": "string",
  "asset_class": "rotating_equipment",
  "anomaly_score": 0.87,
  "confidence_score": 0.92,
  "alert": true,
  "severity": "high",
  "predicted_at": "2026-05-16T10:01:30Z",
  "model_version": "vibration-autoencoder-v1",
  "explain_status": "pending"
}
```

**Response 404:** ativo sem predições.

```json
{"detail": "No predictions found for asset_id 'PUMP-001'"}
```

### 5.3 GET /explain/{prediction_id}

**Response 200 (explain_status = ready):**

```json
{
  "prediction_id": "uuid",
  "method": "shap",
  "feature_attributions": [
    {"feature_name": "fft_bin_12", "attribution_value": 0.43, "rank": 1},
    {"feature_name": "rms", "attribution_value": 0.31, "rank": 2},
    {"feature_name": "kurtosis", "attribution_value": 0.18, "rank": 3},
    {"feature_name": "fft_bin_28", "attribution_value": 0.14, "rank": 4},
    {"feature_name": "variance", "attribution_value": 0.09, "rank": 5}
  ],
  "baseline_window": {
    "start": "2026-05-09T10:00:00Z",
    "end": "2026-05-16T10:00:00Z",
    "stats_per_feature": {
      "rms": {"mean": 0.0012, "std": 0.0003, "p5": 0.0008, "p95": 0.0018}
    }
  }
}
```

**Response 202 (explain_status = pending):**

```json
{"explain_status": "pending", "retry_after": 15}
```

**Response 404:** prediction_id inexistente.

### 5.4 WS /predictions/stream

**Handshake (query param ou mensagem inicial):**

```json
{"filter_asset_id": "PUMP-001"}
```

**Mensagem de stream (para cada predição nova):**

```json
{
  "stream_sequence": 42,
  "prediction_id": "uuid",
  "asset_id": "PUMP-001",
  "asset_class": "rotating_equipment",
  "anomaly_score": 0.87,
  "confidence_score": 0.92,
  "alert": true,
  "severity": "high",
  "predicted_at": "2026-05-16T10:01:30Z",
  "model_version": "vibration-autoencoder-v1",
  "explain_status": "pending"
}
```

**Desconexão inválida:** código WS 1008, motivo logado em JSON estruturado.

### 5.5 POST /models/deploy

**Request:** `multipart/form-data` com campo `artifact` (arquivo ONNX ou TF SavedModel zip) e `metadata` JSON:

```json
{
  "asset_class": "rotating_equipment",
  "version": "2.0.0",
  "anomaly_threshold": 0.6,
  "severity_thresholds": {"low": 0.6, "medium": 0.75, "high": 0.9}
}
```

**Response 200:**

```json
{
  "model_id": "vibration-autoencoder-v2.0.0",
  "version": "2.0.0",
  "asset_class": "rotating_equipment",
  "deployed_at": "2026-05-16T10:05:00Z",
  "is_active": true
}
```

**Response 422:** formato não suportado.

```json
{"detail": "Unsupported artifact format. Expected: onnx or tensorflow_savedmodel. Got: .pkl"}
```

### 5.6 GET /health

**Response 200:**

```json
{
  "status": "healthy",
  "services": {
    "ops-ingest": "healthy",
    "ops-store": "healthy",
    "ops-feature": "healthy",
    "ops-models": "healthy",
    "ops-explain": "healthy"
  },
  "checked_at": "2026-05-16T10:00:00Z"
}
```

**Response 503 (durante inicialização):**

```json
{"status": "starting", "services": {"ops-models": "starting"}}
```

### 5.7 GET /metrics

Formato Prometheus text/plain. Métricas mínimas obrigatórias:

```
# HELP predictions_total Total predictions emitted
# TYPE predictions_total counter
predictions_total{asset_class="rotating_equipment"} 1234

# HELP predictions_latency_seconds Prediction end-to-end latency
# TYPE predictions_latency_seconds histogram
predictions_latency_seconds_bucket{le="0.5"} 890
predictions_latency_seconds_bucket{le="1.0"} 1100
predictions_latency_seconds_bucket{le="2.0"} 1234

# HELP ingestion_records_total Total telemetry records ingested
# TYPE ingestion_records_total counter
ingestion_records_total{status="accepted"} 50000
ingestion_records_total{status="rejected"} 42

# HELP model_inference_latency_seconds Model inference latency
# TYPE model_inference_latency_seconds histogram
model_inference_latency_seconds_bucket{model="vibration-autoencoder-v1",le="0.1"} 500
```

### 5.8 GET /audit

**Response 200:**

```json
{
  "events": [
    {
      "prediction_id": "uuid",
      "asset_id": "PUMP-001",
      "model_version": "vibration-autoencoder-v1",
      "triggered_at": "2026-05-16T10:01:30Z",
      "confidence_score": 0.92,
      "trace_id": "abc123"
    }
  ],
  "total": 1,
  "page": 1,
  "page_size": 50
}
```

Query params: `asset_id` (filtro), `from` / `to` (período), `page`, `page_size`.

---

## 6. Componentes Afetados

O repositório está em estado zero (sem código). Todos os componentes abaixo serão **criados** (nenhum arquivo existente é tocado ou quebrado).

| Módulo / Arquivo | Tipo | Impacto |
|-----------------|------|---------|
| `ops-ingest/` | Novo serviço | Gateway de ingestão; adaptadores REST batch (implementado), MQTT/Kafka (stubs) |
| `ops-ingest/app/main.py` | Novo | FastAPI app; roteador `/telemetry` interno |
| `ops-ingest/app/adapters/rest_batch.py` | Novo | Implementação do adaptador REST batch |
| `ops-ingest/app/adapters/mqtt_stub.py` | Novo | Stub MQTT documentado |
| `ops-ingest/app/adapters/kafka_stub.py` | Novo | Stub Kafka documentado |
| `ops-ingest/app/normalizer.py` | Novo | Normalização para schema canônico; auto-registration |
| `ops-ingest/app/schemas.py` | Novo | Pydantic: IngestRequest, CanonicalReading, IngestionResponse |
| `ops-ingest/contracts/events.py` | Novo | Definições de eventos de domínio (preparação para Kafka Fase 2) |
| `ops-store/` | Novo serviço | Persistência DuckDB (séries temporais) + SQLite (metadados) |
| `ops-store/app/main.py` | Novo | FastAPI app; rotas internas de leitura/escrita |
| `ops-store/app/db/duckdb_store.py` | Novo | Abstração DuckDB para raw_readings e feature_records |
| `ops-store/app/db/sqlite_store.py` | Novo | Abstração SQLite para assets, predictions, models, audit |
| `ops-store/app/db/migrations/` | Novo | Scripts de criação de schema (DuckDB DDL + SQLite DDL) |
| `ops-store/app/storage_interface.py` | Novo | Interface abstrata de storage (preparação para InfluxDB/Timescale) |
| `ops-feature/` | Novo serviço | Pipeline de feature engineering |
| `ops-feature/app/main.py` | Novo | FastAPI app; rota de trigger de cálculo |
| `ops-feature/app/windowing.py` | Novo | Janela deslizante configurável; idempotência por índice único |
| `ops-feature/app/extractors/vibration.py` | Novo | RMS, variância, kurtosis, skewness, FFT bins (NumPy/SciPy) |
| `ops-feature/app/schemas.py` | Novo | Pydantic: FeatureRecord, FeatureRequest |
| `ops-models/` | Novo serviço | Serving de modelos ONNX/TF |
| `ops-models/app/main.py` | Novo | FastAPI app; rotas de inferência e deploy |
| `ops-models/app/serving/onnx_runner.py` | Novo | Wrapper ONNX Runtime para inferência |
| `ops-models/app/serving/model_registry.py` | Novo | Gerenciamento de versões; rollback; carregamento automático no boot |
| `ops-models/app/schemas.py` | Novo | Pydantic: PredictionRequest, PredictionResult, ModelDeployRequest |
| `ops-explain/` | Novo serviço | Cálculo assíncrono de atribuições SHAP |
| `ops-explain/app/main.py` | Novo | FastAPI app; rota GET /explain interno |
| `ops-explain/app/shap_explainer.py` | Novo | Wrapper SHAP; cálculo top-N features; atualização de explain_status |
| `ops-explain/app/background.py` | Novo | Gerenciamento de tasks de explicabilidade; requeue de pending ao reiniciar |
| `ops-api/` | Novo serviço | API pública; agrega todos os serviços |
| `ops-api/app/main.py` | Novo | FastAPI app principal; middleware X-Advisory-Only; middleware trace_id |
| `ops-api/app/routers/telemetry.py` | Novo | POST /telemetry → ops-ingest |
| `ops-api/app/routers/predictions.py` | Novo | GET /predictions/{asset_id} |
| `ops-api/app/routers/explain.py` | Novo | GET /explain/{prediction_id} |
| `ops-api/app/routers/stream.py` | Novo | WS /predictions/stream |
| `ops-api/app/routers/models.py` | Novo | POST /models/deploy |
| `ops-api/app/routers/health.py` | Novo | GET /health (agrega health dos serviços dependentes) |
| `ops-api/app/routers/metrics.py` | Novo | GET /metrics (Prometheus format) |
| `ops-api/app/routers/audit.py` | Novo | GET /audit |
| `ops-api/app/middleware/advisory.py` | Novo | Middleware que injeta X-Advisory-Only: true |
| `ops-api/app/middleware/tracing.py` | Novo | Middleware que gera/propaga trace_id |
| `ops-api/app/websocket_manager.py` | Novo | Gerenciamento de conexões WS; broadcast; filtro por asset_id |
| `ops-cli/` | Novo módulo | CLI Typer |
| `ops-cli/main.py` | Novo | Entry point Typer; comandos: inspect (implementado), demais stubs |
| `ops-ui/` | Novo serviço | Angular 17 frontend |
| `ops-ui/src/app/` | Novo | Estrutura Angular: módulo de inspeção de predições |
| `ops-reference/` | Novo diretório | Artefatos, datasets, pipelines de treinamento |
| `ops-reference/models/vibration_autoencoder_v1.onnx` | Novo | Artefato ONNX pré-treinado (CWRU dataset) |
| `ops-reference/datasets/cwru_sample/` | Novo | Subset do CWRU Bearing Dataset (licença permissiva) |
| `ops-reference/training/train_vibration_autoencoder.py` | Novo | Pipeline de treinamento reproduzível |
| `ops-reference/models/vibration_autoencoder_v1_metrics.json` | Novo | Precision/recall documentados no dataset de referência |
| `docker-compose.yml` | Novo | Orquestração completa; volumes; healthchecks; init automático do modelo |
| `docker-compose.override.yml` | Novo | Overrides opcionais para dev local (portas, volumes de código) |
| `Makefile` | Novo | Targets: `up`, `down`, `test`, `lint`, `format`, `train`, `quickstart` |
| `shared/` | Novo | Código compartilhado entre serviços Python (schemas canônicos, logging, config) |
| `shared/logging_config.py` | Novo | Configuração centralizada de logging JSON estruturado (python-json-logger) |
| `shared/config.py` | Novo | Configuração baseada em variáveis de ambiente (pydantic-settings) |
| `shared/schemas/canonical.py` | Novo | Pydantic: CanonicalReading (schema canônico compartilhado) |

---

## 7. Dependências Externas

### 7.1 Bibliotecas Python (por serviço)

| Biblioteca | Versão mínima | Serviço(s) | Justificativa |
|------------|--------------|------------|---------------|
| fastapi | 0.111+ | todos os serviços | Framework web padrão do portfólio |
| uvicorn[standard] | 0.29+ | todos os serviços | ASGI server; suporte WebSocket |
| pydantic | 2.x | todos os serviços | Validação de schemas; padrão do portfólio |
| pydantic-settings | 2.x | todos os serviços | Configuração via env vars |
| python-json-logger | 2.x | todos os serviços | Logging JSON estruturado; padrão do portfólio (AI Credit API) |
| httpx | 0.27+ | ops-api | Cliente HTTP async para chamadas internas |
| duckdb | 0.10+ | ops-store, ops-feature | Armazenamento de séries temporais analítico |
| numpy | 1.26+ | ops-feature, ops-explain | Feature engineering |
| scipy | 1.13+ | ops-feature | FFT, kurtosis, skewness |
| onnxruntime | 1.18+ | ops-models | Serving de modelos ONNX |
| shap | 0.45+ | ops-explain | Atribuições SHAP |
| tensorflow | 2.16+ | ops-reference (somente treinamento) | Treinamento do autoencoder |
| tf2onnx | 1.16+ | ops-reference (somente treinamento) | Export TF → ONNX |
| prometheus-client | 0.20+ | ops-api | Geração de métricas Prometheus |
| typer | 0.12+ | ops-cli | Framework CLI |
| pytest | 8.x | todos | Testes; padrão do portfólio |
| pytest-asyncio | 0.23+ | todos | Testes assíncronos FastAPI |
| pytest-cov | 5.x | todos | Coverage; padrão do portfólio |

### 7.2 Infraestrutura Docker

| Imagem | Versão | Uso |
|--------|--------|-----|
| python:3.11-slim | latest-stable | Base para todos os serviços Python |
| node:18-alpine | latest-stable | Build do ops-ui Angular |
| nginx:alpine | latest-stable | Serve build estático do ops-ui |

### 7.3 Dataset de referência

| Dataset | Licença | Fonte | Uso |
|---------|---------|-------|-----|
| CWRU Bearing Dataset | Domínio público / uso acadêmico | Case Western Reserve University | Treinamento e avaliação do modelo de vibração |

### 7.4 Credenciais e configuração

Nenhuma credencial de terceiros é necessária para Fase 1. Configurações via variáveis de ambiente (sem valores sensíveis obrigatórios para deployment local):

- `OILOPS_API_KEY` — opcional; ativa autenticação básica por API Key
- `OILOPS_DATA_DIR` — diretório de volumes de dados (default: `/data`)
- `OILOPS_MAX_BACKFILL_DAYS` — janela máxima de backfill (default: 30)
- `OILOPS_FEATURE_WINDOW_SIZE` — tamanho mínimo de janela para features (default: 64)
- `OILOPS_FFT_BINS` — número de bins FFT (default: 64)
- `OILOPS_EXPLAIN_TOP_N` — top-N features de atribuição (default: 5)

---

## 8. Áreas Sensíveis

- **Autenticação/autorização/sessão:** NÃO (Fase 1 opera sem autenticação obrigatória; variável `OILOPS_API_KEY` é opcional e de responsabilidade do operador configurar antes de expor à rede). A **ausência de autenticação padrão é em si uma área de atenção** — documentar explicitamente no README que o sistema NÃO deve ser exposto à internet sem ativar o mecanismo de autenticação.

- **Pagamento/faturamento/cálculo financeiro real:** NÃO

- **Dados pessoais/sensíveis (PII, saúde, financeiro):** NÃO (dados são telemetria industrial de equipamentos, não dados pessoais)

- **Migration de dados em tabela com produção:** NÃO (repositório está em estado zero; não há dados de produção existentes; scripts de criação de schema são DDL inicial, não migrations sobre tabelas populadas)

- **Lógica regulatória/fiscal/compliance:** NÃO diretamente. Observação: o sistema opera no setor de energia dos EUA, que tem regulação setorial (NERC, EPA). O sistema é declaradamente advisory (RN-06); os avisos obrigatórios em payloads e docs mitigam risco de interpretação equivocada como sistema safety-rated.

- **Endpoint público sem autenticação prévia:** SIM — todos os endpoints em Fase 1 estão abertos por padrão (LAC-02). Componentes afetados: `ops-api/app/main.py`, `ops-api/app/routers/` (todos os routers), `docker-compose.yml` (porta 8000 exposta). Mitigação: middleware de API Key opcional implementado desde o início; documentação explícita de que não se deve expor à internet sem ativar autenticação.

- **Criptografia/manuseio de chaves:** NÃO (em Fase 1; HTTPS é responsabilidade do operador via reverse proxy — documentar na guia de deployment)

- **Integração externa nova com terceiro:** NÃO (Fase 1 não integra com serviços externos; adapters MQTT/Kafka são stubs; o sistema é autossuficiente)

---

## 9. Riscos e Mitigações

| Prioridade | Risco | Probabilidade | Impacto | Mitigação |
|-----------|-------|--------------|---------|-----------|
| 1 | **Modelo de referência (autoencoder CWRU) com desempenho insuficiente no dataset de referência** — precision/recall baixos tornam o sistema inútil como demonstração | Média | Alto | Usar arquitetura autoencoder bem documentada (CWRU é benchmark conhecido); incluir métricas honest no `ops-reference/`; documentar limitações e enfatizar que é ponto de partida para re-treinamento |
| 2 | **Latência SHAP assíncrona ultrapassando limiar proposto de 30s** — SHAP kernel é computacionalmente intenso para modelos de autoencoder | Média | Médio | Usar SHAP `DeepExplainer` para autoencoders TF/ONNX (mais rápido que KernelExplainer); limitar background de features ao vetor de features (64+4 dims, não o raw signal); configurar timeout com fallback para permutation importance se SHAP exceder threshold |
| 3 | **DuckDB: degradação de desempenho sob escrita concorrente de múltiplos ativos** | Baixa | Médio | DuckDB suporta escritas concorrentes via WAL; para Fase 1 (ingestão batch, não streaming contínuo) o risco é baixo. Mitigação preventiva: fila interna de escrita serializada em `ops-store` |
| 4 | **Dependência circular de serviços na inicialização do Docker Compose** | Baixa | Alto | Implementar healthcheck em cada serviço; usar `depends_on: condition: service_healthy` no Compose; `ops-api` só fica healthy após todos os dependentes responderem |
| 5 | **Artefato ONNX pré-treinado embarcado com tamanho excessivo para repositório Git** | Média | Baixo | Usar Git LFS para `ops-reference/models/*.onnx`; modelo autoencoder para vibração (CWRU) é tipicamente < 10MB — verificar antes de commitar |
| 6 | **Authenticação ausente exposta inadvertidamente** — LAC-02 | Média | Alto | README com aviso proeminente; `docker-compose.yml` com comentário explícito; middleware de API Key ativável por variável de ambiente desde a Fase 1 |
| 7 | **Escopo do scaffolding subestimado** — 9 módulos simultâneos | Alta | Médio | Priorização clara: P1 primeiro (`ops-ingest`, `ops-store`, `ops-feature`, `ops-models`, `ops-api`); `ops-explain` e `ops-ui` como P1 reduzido; `ops-cli` e `ops-reference` como P1/P2 conforme velocity |

---

## 10. Critérios de Aceite Técnicos

| ID | Critério | Verificação |
|----|---------|-------------|
| CAT-01 | `docker compose up` na raiz sobe todos os serviços sem erros de inicialização | `docker compose ps` mostra todos os serviços com status `healthy` |
| CAT-02 | `GET /health` retorna HTTP 200 com todos os serviços reportando `healthy` após stack estabilizar | Chamada curl ao endpoint; verificar JSON de resposta |
| CAT-03 | Quickstart documentado executado do zero em < 10 minutos resulta em predição via `GET /predictions/{asset_id}` | Validação por revisor externo seguindo README |
| CAT-04 | Toda predição retornada contém `confidence_score`, `explain_status`, e header `X-Advisory-Only: true` | Teste automatizado de contrato de API (pytest + httpx) |
| CAT-05 | `GET /explain/{prediction_id}` retorna top-5 features com `attribution_value` quando `explain_status = ready` | Teste de integração end-to-end: ingerir → predição → aguardar explain → verificar |
| CAT-06 | Modelo de vibração tem precision/recall documentados no dataset CWRU em `ops-reference/models/vibration_autoencoder_v1_metrics.json` | Arquivo presente e com métricas numéricas verificáveis |
| CAT-07 | Logs de todos os serviços estão em JSON estruturado com campos `timestamp`, `service`, `level`, `message`, `trace_id` | Inspecionar `docker compose logs` após quickstart |
| CAT-08 | `trace_id` é propagado do request inicial de ingestão até o log da predição correspondente | Verificar nos logs que o mesmo `trace_id` aparece em `ops-ingest`, `ops-store`, `ops-feature`, `ops-models` |
| CAT-09 | Feature engineering produz RMS, variância, kurtosis, skewness, e 64 bins FFT para sinal de vibração de exemplo | Teste unitário em `ops-feature/tests/test_vibration.py` com sinal sintético |
| CAT-10 | `POST /models/deploy` com artefato ONNX válido registra nova versão e nova versão passa a ser usada nas inferências seguintes | Teste de integração: deploy v2 → ingerir → predição usa model_version v2 |
| CAT-11 | `POST /models/deploy` com formato inválido retorna HTTP 422 sem interromper o modelo ativo | Teste automatizado |
| CAT-12 | `GET /metrics` retorna formato Prometheus válido com as 4 métricas obrigatórias | Verificar com `promtool check metrics` ou validação manual do formato |
| CAT-13 | Feature records são idempotentes: duas requisições simultâneas de features para o mesmo ativo e janela resultam em apenas um registro | Teste de concorrência com `asyncio.gather` |
| CAT-14 | Ingestão continua funcionando quando `ops-models` está indisponível (edge case) | Teste de integração: parar `ops-models` → ingerir → verificar que `ops-ingest` e `ops-store` respondem normalmente |

---

*Próximo passo: agente `scrum-task-breaker` deve consumir este `plan.md` para decompor em tasks, usando a seção 8 (Áreas Sensíveis) para classificar o risco de cada task.*
