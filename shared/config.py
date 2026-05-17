"""
shared/config.py
================
Centralised environment-based configuration for all OilOps-PredictiveCore
services, powered by :mod:`pydantic_settings`.

All settings are read from environment variables prefixed with ``OILOPS_``.
No variable is mandatory for a local evaluation deployment: sensible defaults
are provided for every field so that the stack starts out-of-the-box without
any configuration (satisfying RN-08: autossuficiência de deployment).

.. warning::
    ``OILOPS_API_KEY`` is **optional** in Phase 1.  The API is deliberately
    open for local evaluation.  **Do not expose this service to the internet
    without setting ``OILOPS_API_KEY``** or adding a reverse-proxy layer with
    proper authentication.  See the project README for details (RN-06 /
    LAC-02).

Usage
-----
>>> from shared.config import Settings
>>> s = Settings()
>>> s.fft_bins
64
>>> s.max_backfill_days
30
"""

from __future__ import annotations

from typing import Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application settings sourced from environment variables.

    All fields map 1-to-1 to their ``OILOPS_*`` counterpart.  Pydantic
    performs type coercion automatically (e.g. ``OILOPS_FFT_BINS=128``
    is parsed as ``int(128)``).
    """

    model_config = SettingsConfigDict(
        env_prefix="OILOPS_",
        # Allow reading from a .env file when present; silently ignored if absent.
        env_file=".env",
        env_file_encoding="utf-8",
        # Ignore unknown OILOPS_* vars that might exist in the environment
        # (future-proofing: new vars added later won't crash existing deployments).
        extra="ignore",
        # Case-insensitive env var matching on all platforms.
        case_sensitive=False,
    )

    # ------------------------------------------------------------------
    # Security / authentication  (LAC-02)
    # ------------------------------------------------------------------
    api_key: Optional[str] = Field(
        default=None,
        description=(
            "When set, the API Gateway middleware enforces this value in the "
            "'X-API-Key' header.  Leave unset for local evaluation only."
        ),
    )

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------
    data_dir: str = Field(
        default="/data",
        description=(
            "Root directory for DuckDB and SQLite data files.  "
            "Maps to the 'oilops-data' Docker volume in production."
        ),
    )

    # ------------------------------------------------------------------
    # Ingestion pipeline
    # ------------------------------------------------------------------
    max_backfill_days: int = Field(
        default=30,
        ge=1,
        description=(
            "Readings with a timestamp older than this many days relative to "
            "ingestion time are flagged as is_backfill=True (edge case spec)."
        ),
    )

    # ------------------------------------------------------------------
    # Feature engineering  (INIT-US-06)
    # ------------------------------------------------------------------
    feature_window_size: int = Field(
        default=64,
        ge=2,
        description=(
            "Minimum number of samples required to compute a feature window.  "
            "Windows with fewer samples are skipped and a warning is logged "
            "(INIT-US-06-AC2)."
        ),
    )

    fft_bins: int = Field(
        default=64,
        ge=2,
        description=(
            "Number of FFT frequency bins included in each feature record.  "
            "Default matches the reference autoencoder input dimension."
        ),
    )

    # ------------------------------------------------------------------
    # Explainability  (INIT-US-03)
    # ------------------------------------------------------------------
    explain_top_n: int = Field(
        default=5,
        ge=1,
        description=(
            "Number of top features (ranked by SHAP attribution magnitude) "
            "included in each explanation result (INIT-US-03-AC4)."
        ),
    )
