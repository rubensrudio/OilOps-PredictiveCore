-- =============================================================================
-- Migration: 001_sqlite_init.sql
-- Database:  SQLite (ops-store)
-- Purpose:   Initial schema for metadata tables: assets, ingestion_batches,
--            predictions, explain_results, model_versions, audit_log.
--
-- Execution: run once against a blank SQLite database.
-- Idempotency: uses CREATE TABLE IF NOT EXISTS so re-running is safe.
--
-- FK enforcement: PRAGMA foreign_keys = ON must be set at connection time
--                 (SQLite disables FK enforcement by default).  The ops-store
--                 application layer enables it on every new connection.
-- =============================================================================

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;   -- enables concurrent readers with a single writer

-- ---------------------------------------------------------------------------
-- Table: assets
-- Master-data for every equipment asset known to the system.
-- New asset IDs are auto-registered by ops-ingest normalizer (INIT-03).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS assets (
    id            TEXT    NOT NULL,   -- asset_id canonical identifier (PK)
    asset_class   TEXT    NOT NULL,   -- rotating_equipment | pump | pipeline
    registered_at TEXT    NOT NULL,   -- ISO 8601 UTC timestamp
    metadata      TEXT,               -- JSON blob of free-form asset metadata

    PRIMARY KEY (id)
);

-- ---------------------------------------------------------------------------
-- Table: ingestion_batches
-- One row per POST /telemetry call; referenced by raw_readings in DuckDB.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ingestion_batches (
    id               TEXT    NOT NULL,   -- UUID v4 (PK); returned as ingestion_id
    received_at      TEXT    NOT NULL,   -- ISO 8601 UTC timestamp
    records_received INTEGER NOT NULL,
    records_accepted INTEGER NOT NULL,
    records_rejected INTEGER NOT NULL,
    rejection_details TEXT,             -- JSON array of {index, field, error} objects
    source_ip        TEXT,              -- originating client IP (nullable)

    PRIMARY KEY (id)
);

-- ---------------------------------------------------------------------------
-- Table: model_versions
-- Tracks every model artefact ever registered; supports rollback (INIT-US-07-AC2).
-- Only one version per asset_class should have is_active = 1 at any time;
-- the application layer enforces this within a single transaction.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS model_versions (
    id                  TEXT    NOT NULL,   -- model_id e.g. vibration-autoencoder-v1 (PK)
    version             TEXT    NOT NULL,   -- semantic version string e.g. 1.0.0
    asset_class         TEXT    NOT NULL,   -- target asset class
    artifact_path       TEXT    NOT NULL,   -- relative path to ONNX / SavedModel artefact
    artifact_format     TEXT    NOT NULL    CHECK (artifact_format IN ('onnx', 'tensorflow_savedmodel')),
    deployed_at         TEXT    NOT NULL,   -- ISO 8601 UTC
    is_active           INTEGER NOT NULL DEFAULT 0  CHECK (is_active IN (0, 1)),
    deployed_by         TEXT,              -- 'auto' or operator identifier
    anomaly_threshold   REAL    NOT NULL DEFAULT 0.5,
    severity_thresholds TEXT    NOT NULL,  -- JSON {low, medium, high}

    PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS idx_model_versions_asset_active
    ON model_versions (asset_class, is_active);

-- ---------------------------------------------------------------------------
-- Table: predictions
-- Every prediction emitted by ops-models is recorded here.
-- Each prediction row is written atomically with its audit_log entry (RN-03).
-- feature_record_id references feature_records in DuckDB; FK not enforced
-- at the SQLite level because the FK target lives in a different database.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS predictions (
    id               TEXT    NOT NULL,   -- UUID v4 prediction_id (PK)
    asset_id         TEXT    NOT NULL    REFERENCES assets(id),
    asset_class      TEXT    NOT NULL,
    anomaly_score    REAL    NOT NULL,
    confidence_score REAL    NOT NULL,
    alert            INTEGER NOT NULL    CHECK (alert IN (0, 1)),
    severity         TEXT                CHECK (severity IN ('low', 'medium', 'high') OR severity IS NULL),
    model_id         TEXT    NOT NULL    REFERENCES model_versions(id),
    model_version    TEXT    NOT NULL,
    feature_record_id TEXT   NOT NULL,  -- UUID; FK lives in DuckDB (cross-db)
    predicted_at     TEXT    NOT NULL,  -- ISO 8601 UTC
    explain_status   TEXT    NOT NULL DEFAULT 'pending'
                                        CHECK (explain_status IN ('pending', 'ready', 'failed')),

    PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS idx_predictions_asset_ts
    ON predictions (asset_id, predicted_at DESC);

-- ---------------------------------------------------------------------------
-- Table: explain_results
-- One row per prediction once SHAP completes (RN-04).
-- UNIQUE on prediction_id ensures at most one explain result per prediction.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS explain_results (
    id                   TEXT NOT NULL,   -- UUID v4 (PK)
    prediction_id        TEXT NOT NULL UNIQUE REFERENCES predictions(id),
    method               TEXT NOT NULL    CHECK (method IN ('shap', 'permutation')),
    feature_attributions TEXT NOT NULL,  -- JSON [{feature_name, attribution_value, rank}]
    baseline_window      TEXT NOT NULL,  -- JSON {start, end, stats_per_feature}
    computed_at          TEXT NOT NULL,  -- ISO 8601 UTC

    PRIMARY KEY (id)
);

-- ---------------------------------------------------------------------------
-- Table: audit_log
-- Immutable audit trail of system events (INIT-US-08-AC2/AC3).
-- Written in the SAME transaction as the prediction it accompanies (RN-03).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS audit_log (
    id               TEXT NOT NULL,   -- UUID v4 (PK)
    event_type       TEXT NOT NULL,   -- prediction_emitted | model_deployed | ingestion_received
    prediction_id    TEXT            REFERENCES predictions(id),
    asset_id         TEXT,           -- denormalised for fast queries; nullable for non-asset events
    model_version    TEXT,
    triggered_at     TEXT NOT NULL,  -- ISO 8601 UTC
    confidence_score REAL,
    trace_id         TEXT,           -- distributed trace identifier propagated from ops-api
    details          TEXT,           -- JSON blob for additional context

    PRIMARY KEY (id)
);

CREATE INDEX IF NOT EXISTS idx_audit_log_asset_ts
    ON audit_log (asset_id, triggered_at DESC);

CREATE INDEX IF NOT EXISTS idx_audit_log_event_type
    ON audit_log (event_type, triggered_at DESC);
