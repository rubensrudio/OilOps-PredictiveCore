-- =============================================================================
-- Migration: 001_duckdb_init.sql
-- Database:  DuckDB (ops-store)
-- Purpose:   Initial schema for time-series data (raw_readings) and
--            computed feature vectors (feature_records).
--
-- Execution: run once against a blank DuckDB database (in-process or file).
-- Idempotency: uses CREATE TABLE IF NOT EXISTS so re-running is safe.
--
-- NOTE: DuckDB does not enforce FK constraints at the engine level in the
--       embedded configuration used here; the FK annotations are kept as
--       documentation of the logical relationship.  Enforcement is handled
--       by the application layer (ops-store write path) and by the SQLite
--       side for master-data tables (assets, ingestion_batches).
-- =============================================================================

-- ---------------------------------------------------------------------------
-- Table: raw_readings
-- Stores every normalised telemetry reading received by ops-ingest.
-- Records are IMMUTABLE after insertion (RN-02).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS raw_readings (
    id               VARCHAR(36)  NOT NULL,   -- UUID v4 (PK)
    asset_id         VARCHAR(64)  NOT NULL,   -- FK -> assets (SQLite)
    timestamp        TIMESTAMPTZ  NOT NULL,   -- UTC reading timestamp
    metric_name      VARCHAR(128) NOT NULL,   -- e.g. vibration_x, vibration_y
    value            DOUBLE       NOT NULL,   -- engineering value (float)
    unit             VARCHAR(32)  NOT NULL,   -- engineering unit, e.g. m/s2
    source_protocol  VARCHAR(32)  NOT NULL,   -- rest_batch | mqtt | kafka | opcua
    ingested_at      TIMESTAMPTZ  NOT NULL,   -- UTC timestamp of system ingestion
    ingestion_id     VARCHAR(36)  NOT NULL,   -- FK -> ingestion_batches (SQLite) UUID
    is_backfill      BOOLEAN      NOT NULL DEFAULT FALSE,  -- TRUE when timestamp > max_backfill_window

    PRIMARY KEY (id)
);

-- Index for the most common query pattern: fetch readings for an asset
-- within a time window (used by ops-feature windowing pipeline).
CREATE INDEX IF NOT EXISTS idx_raw_readings_asset_ts
    ON raw_readings (asset_id, timestamp);

-- ---------------------------------------------------------------------------
-- Table: feature_records
-- Stores one row per (asset, time-window, feature pipeline version).
-- Idempotency is guaranteed by the UNIQUE constraint below (CAT-13):
-- concurrent writes for the same window collide on the constraint and the
-- duplicate INSERT is silently ignored (INSERT OR IGNORE in app layer).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS feature_records (
    id              VARCHAR(36)  NOT NULL,   -- UUID v4 (PK)
    asset_id        VARCHAR(64)  NOT NULL,   -- FK -> assets (SQLite)
    window_start    TIMESTAMPTZ  NOT NULL,   -- inclusive window start (UTC)
    window_end      TIMESTAMPTZ  NOT NULL,   -- exclusive window end (UTC)
    raw_record_ids  JSON         NOT NULL,   -- array<UUID> of raw_readings.id
    feature_version VARCHAR(32)  NOT NULL,   -- pipeline version tag, e.g. "v1"
    rms             DOUBLE,                  -- Root Mean Square of window
    variance        DOUBLE,                  -- Variance of window values
    kurtosis        DOUBLE,                  -- Kurtosis of window values
    skewness        DOUBLE,                  -- Skewness of window values
    fft_bins        JSON,                    -- array<double> of FFT bin magnitudes (default 64)
    computed_at     TIMESTAMPTZ  NOT NULL,   -- UTC timestamp when features were computed

    PRIMARY KEY (id)
);

-- UNIQUE index that enforces idempotency of feature computation.
-- Two simultaneous workers computing features for the same asset + window +
-- version will collide here; the second INSERT OR IGNORE is dropped safely.
CREATE UNIQUE INDEX IF NOT EXISTS uq_feature_records_window
    ON feature_records (asset_id, window_start, window_end, feature_version);

-- Index for reading feature records for an asset (used by ops-models).
CREATE INDEX IF NOT EXISTS idx_feature_records_asset_ts
    ON feature_records (asset_id, window_start, window_end);
