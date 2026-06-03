# Architecture

OilOps-PredictiveCore is a containerized, microservices stack. Each service
has a single responsibility and communicates over well-defined contracts. The
full product vision lives in the [PRD](PRD_OilOps-PredictiveCore.md); this
document is the visual + structural reference.

## Service topology

```mermaid
flowchart LR
    subgraph Edge["Operator edge / historian"]
        T[Telemetry sources<br/>MQTT · Kafka · REST batch · OPC UA]
    end

    subgraph Core["OilOps-PredictiveCore (self-hosted)"]
        ING[ops-ingest<br/>ingestion gateway]
        STORE[(ops-store<br/>time-series store)]
        FEAT[ops-feature<br/>windowing + FFT features]
        MODELS[ops-models<br/>ONNX Runtime serving]
        EXPLAIN[ops-explain<br/>feature attribution]
        API[ops-api<br/>REST + WebSocket gateway]
        REF[ops-reference<br/>models · datasets · training]
    end

    subgraph Clients["Consumers"]
        UI[ops-ui<br/>read-only dashboard]
        CLI[ops-cli<br/>operator CLI]
        CMMS[External CMMS / reliability systems]
    end

    T --> ING --> STORE
    STORE --> FEAT --> MODELS --> EXPLAIN
    MODELS --> API
    EXPLAIN --> API
    REF -. trained model .-> MODELS
    API --> UI
    API --> CMMS
    CLI --> API

    classDef impl fill:#27ae60,stroke:#1e7d46,color:#fff;
    classDef partial fill:#e67e22,stroke:#ad5b16,color:#fff;
    class ING,STORE,FEAT,MODELS,EXPLAIN,API,UI,CLI,REF impl;
```

## Prediction data flow

```mermaid
sequenceDiagram
    participant Src as Telemetry source
    participant API as ops-api
    participant Store as ops-store
    participant Feat as ops-feature
    participant Model as ops-models
    participant Exp as ops-explain

    Src->>API: POST /telemetry (batch)
    API->>Store: persist raw window
    Store->>Feat: window ready
    Feat->>Feat: rms/mean/std + 64 FFT bins (dim=68)
    Feat->>Model: feature vector
    Model->>Model: ONNX inference → anomaly_score
    Model->>API: prediction (score, confidence, alert)
    API-->>Src: 202 + X-Advisory-Only: true
    par async attribution
        Model->>Exp: prediction_id
        Exp->>API: top contributing features
    end
    Note over API,Exp: GET /explain/{id} returns attribution once ready
```

## Services

| Service | Responsibility | Stack |
|---|---|---|
| `ops-ingest` | Telemetry intake; normalize to canonical schema | Python / FastAPI |
| `ops-store` | Time-series persistence (pluggable backend) | Python / FastAPI |
| `ops-feature` | Windowed statistical + frequency-domain features | Python |
| `ops-models` | Model serving over the fixed ONNX contract | Python / ONNX Runtime |
| `ops-explain` | Feature attribution (async to prediction) | Python |
| `ops-api` | Public REST + WebSocket gateway, advisory middleware | Python / FastAPI |
| `ops-cli` | Operator CLI (deploy / train / evaluate / replay) | Python |
| `ops-ui` | Read-only operational dashboard | Angular 17 |
| `ops-reference` | Reference models, datasets, training pipelines | Python / scikit-learn / ONNX |
| `shared` | Cross-service Pydantic schemas | Python |

## API surface (`ops-api`)

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/telemetry` | Push a telemetry batch |
| `GET` | `/predictions/{asset_id}` | Current prediction for an asset |
| `WS` | `/predictions/stream` | Subscribe to the prediction stream |
| `GET` | `/explain/{prediction_id}` | Feature-attribution detail |
| `POST` | `/models/deploy` | Deploy a new model version |
| `GET` | `/health` | Health fan-out across services |
| `GET` | `/metrics` | Prometheus text exposition |
| `GET` | `/audit` | Paginated prediction audit log |

Every response carries `X-Advisory-Only: true` (RN-06).

## Cross-cutting concerns

- **Advisory middleware** — injects `X-Advisory-Only: true` on every response.
- **Tracing middleware** — generates / propagates a `trace_id` via `ContextVar`.
- **Observability** — Prometheus `/metrics`, structured logging, `/audit` trail.
- **Model versioning** — models are versioned in `ops-reference`; rollback supported.
