# SPEC: Motor Preditivo Inicial — Scaffolding e Contratos de Núcleo

**Feature ID:** INITIAL
**PRD:** [PRD_OilOps-PredictiveCore.md](../../../docs/PRD_OilOps-PredictiveCore.md)
**Data:** 2026-05-16
**Status:** Draft
**Escopo:** Grande (scaffolding de toda a arquitetura, contratos de ingestão, interface de modelos, implementação de referência para vibração de equipamentos rotativos)

---

## Problem Statement

O repositório OilOps-PredictiveCore existe apenas com o PRD — não há código, estrutura de serviços, contratos de API, pipelines de feature engineering, nem modelos treinados. Sem o scaffolding inicial, nenhum outro desenvolvimento pode avançar: não há contrato de ingestão para receber telemetria, não há interface de modelo para servir predições, e não há estrutura de projeto para os colaboradores trabalharem.

A Fase 1 (Meses 0–12) exige: arquitetura scaffolded, contrato de ingestão documentado, interface de modelo definida, e implementação de referência do primeiro modelo (anomalia de vibração em equipamentos rotativos).

---

## Goals

- [ ] Scaffolding de todos os módulos da arquitetura (`ops-ingest`, `ops-store`, `ops-feature`, `ops-models`, `ops-explain`, `ops-api`, `ops-cli`, `ops-ui`, `ops-reference`)
- [ ] Contrato canônico de ingestão de telemetria documentado e implementado (schema de normalização interna)
- [ ] Interface de modelo definida (contrato de entrada/saída para todos os modelos do motor)
- [ ] Implementação de referência para modelo de anomalia de vibração em equipamentos rotativos (autoencoder sobre features FFT)
- [ ] Stack implantável localmente via Docker Compose para avaliação
- [ ] Documentação operacional: README, quickstart, guia do contribuidor
- [ ] Logging estruturado e rastreabilidade de predições desde o primeiro endpoint

---

## Out of Scope

| Item | Razão |
|------|-------|
| Modelo de cavitação em bombas centrífugas | Fase 2 (Meses 12–24) |
| Modelo de anomalia de pressão em dutos | Fase 2 (Meses 12–24) |
| Integração com CMMS (despacho de ordens de serviço) | Out of scope v1 |
| Coleta direta de sensores físicos (substitui SCADA/historian) | Out of scope v1 — consome telemetria, não a coleta |
| Dashboard operacional (`ops-ui`) com funcionalidades completas | Fase 1 entrega estrutura e tela de inspeção; dashboard completo é Fase 2 |
| Implantação Kubernetes para produção | Fase 2 — Fase 1 usa Docker Compose |
| Certificação SIL / safety-instrumented function | Out of scope v1 — sistema é advisory |
| Cloud-native managed templates (AWS, Azure) | Fase 3 |
| Integração com adapters de historians proprietários | Fase 3 |

---

## Atores

| Ator | Descrição |
|------|-----------|
| **Engenheiro de confiabilidade** | Usuário primário — consulta predições e explicabilidade via dashboard ou API |
| **Operador de pequeno/médio porte** | Implanta a stack, configura adaptadores de ingestão, avalia predições |
| **Time de dados / pesquisador** | Re-treina modelos de referência com dados proprietários |
| **Sistema externo (SCADA/historian)** | Publica telemetria via MQTT, Kafka, REST batch ou gateway OPC UA |
| **Sistema CMMS** | Consome predições da API para criar ordens de manutenção |

---

## Fluxo Principal

```
[SCADA/Historian] --telemetria--> [ops-ingest]
                                       |
                              normaliza para schema canônico
                                       |
                                  [ops-store]
                                       |
                              extrai features por janela
                                       |
                                 [ops-feature]
                                       |
                              executa inferência
                                       |
                                  [ops-models]
                                       |
                              gera atribuição de features
                                       |
                                 [ops-explain]
                                       |
                        emite predição com score + atribuições
                                       |
                                   [ops-api]
                                  /         \
                    [Engenheiro via UI]   [CMMS via REST/WS]
```

---

## Regras de Negócio

### RN-01: Schema canônico de telemetria
Toda telemetria que entra no sistema, independente do protocolo de origem (MQTT, Kafka, REST batch, OPC UA), DEVE ser normalizada para o schema canônico interno antes de ser persistida. O schema canônico inclui: `asset_id`, `timestamp` (UTC ISO 8601), `metric_name`, `value` (float), `unit`, `source_protocol`, `ingested_at`.

### RN-02: Imutabilidade dos dados brutos
Dados brutos normalizados em `ops-store` são imutáveis após gravação. Features derivadas são armazenadas em tabela separada e vinculadas ao dado bruto por referência.

### RN-03: Toda predição carrega explicabilidade
Nenhuma predição pode ser emitida pela API sem: (i) `confidence_score` (float 0–1), (ii) lista das top-N features contribuintes ranqueadas por atribuição, (iii) referência ao baseline histórico usado para detecção. Predições sem esses campos são rejeitadas na camada `ops-explain`.

### RN-04: Explicabilidade assíncrona à predição
A predição é emitida ao cliente imediatamente após inferência. A atribuição de features (SHAP ou permutação) é computada de forma assíncrona e disponibilizada via `GET /explain/{prediction_id}`. A predição inicial inclui `explain_status: pending | ready`.

### RN-05: Versionamento de modelos
Cada modelo em `ops-models` tem versão explícita (`model_id`, `version`, `asset_class`, `deployed_at`). Toda predição registra qual versão do modelo a gerou. Rollback deve ser possível sem redeployment da stack.

### RN-06: Sistema é advisory — não safety-rated
Todo response da API DEVE incluir header `X-Advisory-Only: true`. README, documentação e payloads de API DEVEM conter aviso explícito de que o sistema é auxiliar, não substitui sistemas de segurança instrumentada.

### RN-07: Telemetria-agnóstica
O core (`ops-store`, `ops-feature`, `ops-models`) não conhece o protocolo de origem. Adaptadores de ingestão em `ops-ingest` são responsáveis por isolar essa complexidade e entregar apenas o schema canônico.

### RN-08: Autossuficiência de deployment
A stack completa (ingestão → armazenamento → features → modelos → API) deve ser iniciável com um único comando (`docker compose up`) sem dependências de serviços externos. Backends plugáveis (InfluxDB, TimescaleDB) são opcionais; a implementação embarcada funciona out-of-the-box.

---

## User Stories

### INIT-US-01: Ingestão via REST batch (P1 — MVP)

**User Story:** Como operador, quero enviar telemetria histórica em lote via HTTP POST, para que eu possa alimentar o motor com dados existentes do meu historian sem configurar streaming.

**Acceptance Criteria:**

1. WHEN `POST /telemetry` recebe payload com lista de leituras no schema de ingestão THEN sistema SHALL normalizar para schema canônico e persistir em `ops-store` com status HTTP 202
2. WHEN payload contém leituras com `timestamp` fora de ordem THEN sistema SHALL reordenar por `timestamp` antes de persistir
3. WHEN payload contém `asset_id` não cadastrado THEN sistema SHALL registrar o ativo automaticamente e aceitar as leituras (auto-registration)
4. WHEN payload está malformado (campo obrigatório ausente, tipo inválido) THEN sistema SHALL retornar HTTP 400 com lista de erros de validação por campo
5. WHEN `POST /telemetry` é chamado THEN response DEVE incluir `ingestion_id`, `records_received`, `records_accepted`, `records_rejected`

---

### INIT-US-02: Consulta de predição por ativo (P1 — MVP)

**User Story:** Como engenheiro de confiabilidade, quero consultar a predição atual de um ativo pelo seu ID, para verificar se há anomalia detectada e qual é o nível de confiança.

**Acceptance Criteria:**

1. WHEN `GET /predictions/{asset_id}` é chamado E existem predições para o ativo THEN sistema SHALL retornar a predição mais recente com `prediction_id`, `asset_id`, `asset_class`, `anomaly_score`, `confidence_score`, `predicted_at`, `model_version`, `explain_status`
2. WHEN `GET /predictions/{asset_id}` é chamado E não existem predições THEN sistema SHALL retornar HTTP 404 com mensagem descritiva
3. WHEN `anomaly_score` >= limiar configurado do modelo THEN response SHALL incluir `alert: true` e `severity` (low | medium | high)
4. WHEN response é retornado THEN header `X-Advisory-Only: true` DEVE estar presente

---

### INIT-US-03: Consulta de explicabilidade de predição (P1 — MVP)

**User Story:** Como engenheiro de confiabilidade, quero ver quais features mais contribuíram para uma predição de anomalia, para avaliar se o sinal é tecnicamente plausível antes de escalar para manutenção.

**Acceptance Criteria:**

1. WHEN `GET /explain/{prediction_id}` é chamado E `explain_status = ready` THEN sistema SHALL retornar `prediction_id`, `method` (shap | permutation), lista de features com `feature_name`, `attribution_value`, `rank`, e `baseline_window` com estatísticas do período de referência
2. WHEN `GET /explain/{prediction_id}` é chamado E `explain_status = pending` THEN sistema SHALL retornar HTTP 202 com campo `retry_after` em segundos
3. WHEN `GET /explain/{prediction_id}` é chamado E `prediction_id` não existe THEN sistema SHALL retornar HTTP 404
4. WHEN explicabilidade é computada THEN DEVE cobrir no mínimo as top-5 features por magnitude de atribuição

---

### INIT-US-04: Stream de predições em tempo real (P1 — MVP)

**User Story:** Como sistema CMMS, quero me inscrever num stream WebSocket de predições para receber alertas automaticamente sem polling, para criar ordens de manutenção preventiva em tempo real.

**Acceptance Criteria:**

1. WHEN cliente conecta em `WS /predictions/stream` THEN sistema SHALL aceitar conexão e iniciar envio de predições novas conforme geradas
2. WHEN predição com `alert: true` é gerada THEN mensagem no stream SHALL incluir todos os campos de `GET /predictions/{asset_id}` mais `stream_sequence`
3. WHEN conexão WebSocket cai THEN sistema SHALL registrar desconexão em log estruturado sem impactar outros clientes conectados
4. WHEN cliente envia filtro de `asset_id` no handshake THEN stream SHALL filtrar apenas predições do ativo solicitado

---

### INIT-US-05: Implantação local via Docker Compose (P1 — MVP)

**User Story:** Como operador, quero iniciar toda a stack com um único comando, para avaliar o sistema no meu próprio ambiente sem dependências externas.

**Acceptance Criteria:**

1. WHEN `docker compose up` é executado na raiz do repositório THEN todos os serviços (`ops-ingest`, `ops-store`, `ops-feature`, `ops-models`, `ops-explain`, `ops-api`) SHALL iniciar com sucesso
2. WHEN stack está no ar THEN `GET /health` SHALL retornar HTTP 200 com status de cada serviço dependente
3. WHEN stack é iniciada pela primeira vez THEN modelo de referência de vibração de equipamentos rotativos SHALL ser carregado automaticamente sem etapas manuais adicionais
4. WHEN operador executa o quickstart documentado THEN SHALL conseguir ingerir telemetria de exemplo e receber predição em menos de 10 minutos

---

### INIT-US-06: Feature engineering para vibração (P1 — MVP)

**User Story:** Como o sistema, preciso extrair features de domínio de frequência e tempo de sinais de vibração, para alimentar o autoencoder de detecção de anomalias com inputs representativos.

**Acceptance Criteria:**

1. WHEN série temporal de vibração de um ativo é recebida em `ops-feature` THEN sistema SHALL calcular features de janela deslizante: RMS, variância, kurtosis, skewness, e bins FFT configuráveis (default: 64 bins) para cada janela
2. WHEN janela não tem dados suficientes (< `min_window_size` configurado) THEN sistema SHALL logar aviso e não gerar feature record para aquela janela
3. WHEN features são computadas THEN cada feature record DEVE referenciar o `asset_id`, `window_start`, `window_end`, e `raw_record_ids` incluídos na janela
4. WHEN feature computation falha para um ativo THEN outros ativos NÃO DEVEM ser afetados (isolamento por ativo)

---

### INIT-US-07: Deploy de nova versão de modelo (P2)

**User Story:** Como operador com time de dados, quero fazer upload de um novo artefato de modelo treinado com meus dados, para substituir o modelo de referência sem redeployment da stack.

**Acceptance Criteria:**

1. WHEN `POST /models/deploy` recebe artefato de modelo THEN sistema SHALL validar formato (TensorFlow SavedModel ou ONNX), registrar versão, e responder com `model_id`, `version`, `deployed_at`
2. WHEN novo modelo é deployado THEN versão anterior NÃO é apagada — deve ser possível reativar via rollback
3. WHEN `POST /models/deploy` recebe artefato com formato não suportado THEN sistema SHALL retornar HTTP 422 com detalhe do erro
4. WHEN modelo é deployado THEN predições seguintes para o `asset_class` do modelo passam a usar a nova versão automaticamente

---

### INIT-US-08: Observabilidade e auditoria (P1 — MVP)

**User Story:** Como operador, quero acessar métricas, logs estruturados e trilha de auditoria do sistema, para monitorar saúde operacional e rastrear cada predição emitida.

**Acceptance Criteria:**

1. WHEN `GET /metrics` é chamado THEN sistema SHALL retornar métricas no formato Prometheus: `predictions_total`, `predictions_latency_seconds`, `ingestion_records_total`, `model_inference_latency_seconds`
2. WHEN `GET /audit` é chamado THEN sistema SHALL retornar log de eventos de predição com `prediction_id`, `asset_id`, `model_version`, `triggered_at`, `confidence_score`
3. WHEN qualquer predição é emitida THEN evento DEVE ser gravado no audit log de forma síncrona antes do response ser enviado ao cliente
4. WHEN erro interno ocorre em qualquer serviço THEN log estruturado JSON DEVE incluir: `timestamp`, `service`, `level`, `message`, `trace_id`, `asset_id` (quando aplicável)

---

## Edge Cases

- WHEN `ops-models` está indisponível e telemetria continua chegando THEN `ops-ingest` e `ops-store` DEVEM continuar funcionando normalmente — inferência é recuperada quando `ops-models` volta
- WHEN dois processos tentam computar features para o mesmo ativo e janela simultaneamente THEN sistema DEVE garantir idempotência — feature record não duplicado
- WHEN artefato de modelo corrompido é deployado via `POST /models/deploy` THEN sistema DEVE rejeitar e manter o modelo anterior ativo sem interrupção do serviço
- WHEN telemetria chega com `timestamp` muito antigo (> `max_backfill_window` configurado, default 30 dias) THEN sistema DEVE aceitar mas sinalizar `is_backfill: true` no registro
- WHEN `GET /health` é chamado durante inicialização dos contêineres THEN DEVE retornar HTTP 503 com `status: starting` até que todos os serviços dependentes estejam prontos
- WHEN cliente WebSocket envia payload inválido no stream THEN conexão DEVE ser encerrada com código 1008 (policy violation) e motivo logado

---

## Contratos de API (Resumo)

| Método | Endpoint | Serviço | Descrição |
|--------|----------|---------|-----------|
| POST | `/telemetry` | ops-api → ops-ingest | Ingestão de lote de leituras |
| GET | `/predictions/{asset_id}` | ops-api → ops-models | Predição mais recente do ativo |
| WS | `/predictions/stream` | ops-api | Stream de predições em tempo real |
| GET | `/explain/{prediction_id}` | ops-api → ops-explain | Explicabilidade de predição |
| POST | `/models/deploy` | ops-api → ops-models | Deploy de novo artefato de modelo |
| GET | `/health` | ops-api | Health check agregado de todos os serviços |
| GET | `/metrics` | ops-api | Métricas Prometheus |
| GET | `/audit` | ops-api | Trilha de auditoria de predições |

---

## Premissas

- A stack é implantada em ambiente com Docker e Docker Compose disponíveis
- O modelo de referência de vibração (autoencoder) é treinado sobre dataset público de referência (`ops-reference/`) e embarcado no repositório como artefato pré-treinado
- Protocolo primário de ingestão para Fase 1 é REST batch; adaptadores MQTT e Kafka fazem parte do scaffolding mas podem ser stubs documentados na Fase 1
- [LACUNA: linguagem/runtime primário do backend não está definida no PRD — Python/FastAPI é o padrão do portfólio do autor, mas não foi declarado explicitamente para este projeto]
- [LACUNA: formato de autenticação da API (JWT, API Key, mTLS) não está especificado no PRD v0.1 — sistema pode ser aberto para ambiente de avaliação local, mas produção exige decisão]
- [LACUNA: SLA de latência de predição não está quantificado — PRD diz "segundos a minutos"; o plan-architect deve propor limiar mensurável para os benchmarks]

---

## Requirement Traceability

| Req ID | Story | Descrição resumida | Prioridade |
|--------|-------|--------------------|-----------|
| INIT-01 | INIT-US-01 | `POST /telemetry` aceita lote e normaliza para schema canônico | P1 |
| INIT-02 | INIT-US-01 | Reordenação de leituras fora de ordem | P1 |
| INIT-03 | INIT-US-01 | Auto-registration de ativo desconhecido | P1 |
| INIT-04 | INIT-US-01 | Validação de payload com erros por campo | P1 |
| INIT-05 | INIT-US-01 | Response de ingestão com contadores | P1 |
| INIT-06 | INIT-US-02 | `GET /predictions/{asset_id}` retorna predição mais recente | P1 |
| INIT-07 | INIT-US-02 | 404 para ativo sem predições | P1 |
| INIT-08 | INIT-US-02 | Campo `alert` e `severity` quando anomaly_score >= limiar | P1 |
| INIT-09 | INIT-US-02 | Header `X-Advisory-Only: true` em toda resposta de predição | P1 |
| INIT-10 | INIT-US-03 | `GET /explain/{prediction_id}` retorna top-5 features com atribuição | P1 |
| INIT-11 | INIT-US-03 | 202 com `retry_after` quando `explain_status = pending` | P1 |
| INIT-12 | INIT-US-04 | WebSocket `/predictions/stream` envia predições novas | P1 |
| INIT-13 | INIT-US-04 | Filtro de `asset_id` no handshake do stream | P1 |
| INIT-14 | INIT-US-05 | `docker compose up` inicia todos os serviços | P1 |
| INIT-15 | INIT-US-05 | Modelo de referência carregado automaticamente no boot | P1 |
| INIT-16 | INIT-US-05 | Quickstart concluído em < 10 minutos | P1 |
| INIT-17 | INIT-US-06 | Feature engineering: RMS, variância, kurtosis, skewness, FFT bins | P1 |
| INIT-18 | INIT-US-06 | Isolamento de falha de feature por ativo | P1 |
| INIT-19 | INIT-US-07 | `POST /models/deploy` valida e registra artefato TF ou ONNX | P2 |
| INIT-20 | INIT-US-07 | Rollback de modelo sem apagar versão anterior | P2 |
| INIT-21 | INIT-US-08 | `GET /metrics` retorna métricas Prometheus | P1 |
| INIT-22 | INIT-US-08 | `GET /audit` retorna trilha de predições | P1 |
| INIT-23 | INIT-US-08 | Audit log gravado de forma síncrona antes do response | P1 |
| INIT-24 | RN-06 | Header `X-Advisory-Only: true` e avisos em docs/payloads | P1 |

**Total:** 24 requisitos | 0 mapeados a tasks | 24 pendentes

---

## Success Criteria

- [ ] `docker compose up` na raiz sobe toda a stack sem erros
- [ ] `GET /health` retorna HTTP 200 com todos os serviços reportando healthy
- [ ] Operador consegue ingerir telemetria de exemplo via `POST /telemetry` e receber predição via `GET /predictions/{asset_id}` em uma sessão de quickstart
- [ ] Toda predição retornada contém `confidence_score` e `explain_status`
- [ ] `GET /explain/{prediction_id}` retorna top-5 features quando `explain_status = ready`
- [ ] Modelo de referência de vibração de equipamentos rotativos está documentado com precision/recall no dataset de referência em `ops-reference/`
- [ ] Logs estruturados em JSON emitidos por todos os serviços com `trace_id` propagado
- [ ] README e quickstart validados por revisor externo
