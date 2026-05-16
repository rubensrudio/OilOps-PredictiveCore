# OilOps-PredictiveCore

**Product Requirements Document (PRD)**
**Version:** 0.1 — Initial Design
**Status:** Phase 1 — Early Prototype / Active Development
**Author:** Rubens Rudio
**Last updated:** May 2026

---

## 1. Executive Summary

**OilOps-PredictiveCore** is a predictive operational intelligence engine for the U.S. energy industry. It ingests time-series telemetry from industrial assets (rotating equipment, compressors, pumps, pipelines, refining process units), applies machine-learning models for anomaly detection and failure prediction, and exposes the results through standards-compliant APIs that integrate into existing operator workflows.

The engine is designed as a **portable, vendor-neutral predictive layer** that small and medium-sized U.S. energy operators can deploy on their own infrastructure or on commodity cloud, without committing to proprietary SCADA-vendor lock-in. It directly targets the documented operational pain points of the U.S. energy sector: unplanned downtime, reactive maintenance practices, and the structural gap between large integrated majors (who build bespoke predictive tooling) and smaller operators (who do not).

**Why it matters:** ABB's 2025 industry survey documents unplanned downtime costs of **US $125,000 per hour** in process industries. McKinsey, the U.S. Department of Energy, and academic literature converge on the conclusion that AI-driven predictive maintenance is one of the highest-ROI digital interventions available to the sector — but adoption is concentrated among operators with internal data-science teams. OilOps-PredictiveCore targets the underserved tier of operators.

**Status disclosure:** this is an early-stage prototype under active development as part of the petitioner's professional plan (Phase 2 of the implementation timeline, Months 12–36). The repository at the time of publication contains the core architecture, the ingestion contract, the model interface, and an initial reference implementation. The PRD describes the full v1 product vision; the repository README documents the current implementation state honestly.

---

## 2. Problem Statement

The U.S. energy sector — upstream oil and gas, midstream pipelines, downstream refining, and increasingly the electric grid — depends on industrial assets whose unplanned failures impose disproportionate costs:

- **Direct downtime cost.** ABB's 2025 survey reports US $125,000/hour in lost production for process industries.
- **Cascading consequences.** Energy disruptions propagate to consumers; the U.S. Census Bureau reports approximately 33.9 million U.S. households experienced at least one complete power outage in a recent twelve-month period, with roughly 70% of those outages lasting six hours or more.
- **Reactive maintenance dominance.** Many operators still rely on time-based or reactive maintenance schedules rather than condition-based maintenance. Industry analysis consistently identifies this as a low-maturity practice with measurable cost penalties.
- **Tooling inequality.** Integrated majors build bespoke predictive systems using proprietary historian data and dedicated data-science teams. Small and medium-sized operators cannot. A World Bank Group study found 46% of SMEs cite lack of financial resources and 43% cite lack of skilled staff as primary barriers to analytics adoption.

The result is a structural gap: the operators most exposed to downtime cost are the least able to deploy the technology that would reduce it.

**OilOps-PredictiveCore targets that gap** with a portable, vendor-neutral, self-hostable predictive maintenance engine that accepts standard telemetry inputs, ships with reference models for common asset classes, and produces actionable predictions through APIs designed for integration into existing operator workflows.

---

## 3. Goals and Non-Goals

### 3.1 Goals (v1)

1. **Telemetry-agnostic ingestion.** Accept time-series telemetry from any source that can publish to a documented streaming or batch contract (MQTT, Kafka, REST batch, OPC UA gateway).
2. **Reference model library.** Ship trained or training-ready models for at least three common asset classes (rotating equipment vibration, centrifugal pump cavitation, pipeline pressure anomaly).
3. **Explainable predictions.** Every prediction emitted by the engine carries a confidence score and the dominant features contributing to that score.
4. **Standards-aligned outputs.** Predictions are emitted in formats consumable by existing maintenance and reliability systems (CMMS integration via standard payloads).
5. **Self-hostable.** Operators can deploy the entire stack on their own infrastructure without external dependency on a vendor cloud.
6. **Observable.** Built-in metrics, structured logging, and audit trails for every prediction.

### 3.2 Non-Goals (v1)

- **Replacing a SCADA / historian.** OilOps-PredictiveCore consumes telemetry; it does not collect it from physical sensors directly.
- **Replacing a CMMS.** Predictions are emitted for consumption by existing maintenance systems; the engine does not schedule or dispatch work orders.
- **Universal asset coverage.** v1 ships with three reference asset-class models. Additional asset classes are roadmapped, not promised.
- **Real-time millisecond-latency control loops.** Predictions are operational-decision latency (seconds to minutes), not control-system latency (milliseconds).
- **Safety-rated certification.** v1 is an advisory system, not a safety-instrumented function. SIL certification is out of scope.

---

## 4. Target Users

| User segment | Use case | Why this user wins |
|---|---|---|
| **Small and medium U.S. energy operators** | Deploy predictive maintenance without hiring a data-science team | Reference models work out of the box; no in-house ML team required |
| **Plant reliability engineers** | Get explainable anomaly signals on existing asset fleets | Confidence scores and feature attributions support engineering judgment |
| **Independent operators on legacy SCADA** | Add a modern predictive layer without ripping out existing systems | Vendor-neutral ingestion; runs alongside any historian |
| **Cloud-native operations teams** | Container-native predictive workloads in their existing cloud footprint | Ships as containerized microservices |
| **Academic and applied-research groups** | Reproducible baseline for industrial predictive maintenance research | Open reference models, open datasets, documented training pipelines |

---

## 5. Architecture Overview

OilOps-PredictiveCore is a containerized, microservices-based stack. Each service has a single responsibility and communicates via well-defined contracts.

```
oilops-predictive-core/
├── ops-ingest/          Telemetry ingestion gateway (Kafka, MQTT, REST batch)
├── ops-store/           Time-series persistence layer (pluggable backend)
├── ops-feature/         Feature engineering and windowing
├── ops-models/          Model serving layer (TensorFlow Serving / ONNX Runtime)
├── ops-explain/         Explainability layer (feature attribution)
├── ops-api/             REST + WebSocket API for predictions and admin
├── ops-cli/             CLI for operators (deploy, train, evaluate, replay)
├── ops-ui/              Operator dashboard (read-only operational view)
└── ops-reference/       Reference models, datasets, training pipelines
```

### 5.1 Ingestion gateway (`ops-ingest`)

Pluggable telemetry intake. Reference adapters: MQTT, Apache Kafka, REST batch upload, OPC UA gateway. Adapters normalize incoming telemetry to a canonical internal schema and forward to the storage layer.

### 5.2 Storage (`ops-store`)

Time-series persistence with a pluggable backend interface. Reference implementation uses an embedded time-series store for self-hosted deployment; alternative backends (InfluxDB, TimescaleDB) supported via the storage abstraction.

### 5.3 Feature engineering (`ops-feature`)

Windowed feature extraction: statistical features (mean, variance, RMS), frequency-domain features (FFT bins for vibration analysis), and asset-specific features. Features are computed in-stream and stored alongside raw telemetry.

### 5.4 Model serving (`ops-models`)

Inference layer running pre-trained or operator-trained models. Reference models for v1:

- **Rotating equipment vibration anomaly** — autoencoder on vibration FFT features
- **Centrifugal pump cavitation classifier** — gradient-boosted classifier on flow/pressure/vibration features
- **Pipeline pressure anomaly** — sequence model on pressure and flow timeseries

The serving layer is backed by TensorFlow Serving or ONNX Runtime depending on model artifact format. Models are versioned; rollback is supported.

### 5.5 Explainability (`ops-explain`)

Every prediction emitted carries: (i) a confidence score; (ii) the top contributing features ranked by attribution; (iii) the historical baseline against which the anomaly was detected. v1 uses SHAP or feature-permutation methods depending on model class.

### 5.6 API surface (`ops-api`)

- `POST /telemetry` — push telemetry batch
- `GET /predictions/{asset_id}` — pull current predictions
- `WS /predictions/stream` — subscribe to prediction stream
- `GET /explain/{prediction_id}` — fetch explainability detail
- `POST /models/deploy` — deploy a new model version
- `GET /health`, `GET /metrics`, `GET /audit`

### 5.7 CLI (`ops-cli`)

Operator-facing CLI: `deploy`, `train`, `evaluate`, `replay`, `inspect`, `export`.

### 5.8 UI (`ops-ui`)

Read-only operational dashboard. Asset list, current predictions, prediction history, explainability views. Designed for reliability engineers, not as a competing CMMS surface.

### 5.9 Reference content (`ops-reference`)

The differentiator. Ships with:

- Pre-trained reference models for the three asset classes
- Open reference datasets (public industrial datasets where licensable; synthetic datasets otherwise)
- Documented training pipelines so operators can re-train on their own data

---

## 6. Key Technical Decisions

### 6.1 Self-hostable by default

Many target operators run regulated infrastructure; sending telemetry to an external SaaS is a non-starter. v1 ships as containers deployable to the operator's own environment (Docker Compose for evaluation, Kubernetes for production).

### 6.2 Vendor-neutral telemetry contracts

Ingestion accepts standard protocols rather than proprietary historian SDKs. This deliberately avoids re-creating the lock-in problem the framework is intended to solve.

### 6.3 Explainability as a first-class concern

Predictive maintenance only changes operator behavior when reliability engineers trust the predictions. Trust requires explainability. Confidence scores and feature attributions are non-optional outputs.

### 6.4 Reference models as the adoption wedge

The hardest part of predictive maintenance for an under-resourced operator is not the platform — it's the model. Shipping working reference models for three common asset classes removes the cold-start barrier.

### 6.5 Standards-aligned outputs over custom UI

The engine emits predictions to existing CMMS and operator systems rather than asking operators to adopt a new dashboard as their primary surface. The included UI is for inspection and demo, not as a workflow replacement.

---

## 7. Success Metrics

| Metric | Target | Measurement method |
|---|---|---|
| Reference model performance | Documented precision/recall on reference datasets | Published in `ops-reference/` for each model |
| Ingestion throughput | Sustainable rates suitable for typical asset fleets | Load testing documented in repository |
| Prediction latency | Operational decision latency (seconds, not milliseconds) | Documented benchmarks |
| Deployment time | Operator can stand up evaluation environment from documented quickstart | Quickstart guide validated by external reviewer |
| Explainability coverage | 100% of predictions carry confidence and top-feature attribution | API contract enforcement |
| Adoption signals | Forks, external contributors, deployment case studies | GitHub metrics and case-study collection |

---

## 8. Release Phases

### Phase 1 — Current (Months 0–12 of professional plan)

- Repository scaffolded; core architecture, ingestion contract, model interface defined
- Initial reference implementation for rotating equipment vibration anomaly model
- Operational documentation and contributor guide
- **State openly disclosed:** "early prototype under active development"

### Phase 2 — Months 12–24

- Second and third reference models (centrifugal pump, pipeline pressure)
- Pilot deployment with a U.S.-based operator
- Hardened deployment templates (Kubernetes)
- Public benchmark report on reference datasets
- **v1 release**

### Phase 3 — Months 24–36

- Additional asset-class models contributed by community or commissioned
- Integration adapters for major historian platforms (read-only)
- Cloud-native managed deployment templates (AWS, Azure)
- Case-study publication

---

## 9. Risks and Mitigations

| Risk | Mitigation |
|---|---|
| Reference models perform poorly on operator-specific data distributions | Ship documented re-training pipelines; emphasize that reference models are starting points, not turnkey solutions |
| Operators reject self-hosted deployment in favor of vendor SaaS | Self-hosting is the differentiator for regulated operators; SaaS is not a target use case for v1 |
| Explainability methods (SHAP, permutation) impose latency overhead | Explainability runs asynchronously to prediction emission; predictions emit first, attributions follow |
| Telemetry standards drift (OPC UA evolution, MQTT v5 adoption) | Adapter interface insulates the core from protocol-version changes |
| Safety-critical use creep | README, license, and API responses all explicitly state v1 is advisory; safety-rated certification is out of scope |

---

## 10. Out-of-Scope

- Sensor data acquisition (consumed from existing historian / SCADA)
- CMMS work-order scheduling and dispatch
- Safety-instrumented function (SIF) replacement
- Real-time control-loop integration
- Universal asset coverage beyond the v1 reference set
- Closed-source enterprise edition (v1 is open by design)

---

## 11. References

- ABB 2025 unplanned downtime cost survey (US $125,000/hour figure)
- U.S. Department of Energy, "AI for Energy" program documentation
- McKinsey & Company, "Why oil and gas companies must act on analytics"
- Fusion Data Hub, "6 Approaches to Maintenance and Reliability in Oil & Gas"
- World Bank Group, SME digitalization study (46%/43% adoption barriers)
- U.S. Census Bureau, household power outage statistics

---

*This PRD describes the v1 product vision for OilOps-PredictiveCore. The named project is currently in early-prototype development as part of Phase 1 of the petitioner's professional plan, with full v1 delivery targeted for Phase 2 (Months 12–24). The repository README documents the current implementation state honestly; this PRD describes the target product.*
