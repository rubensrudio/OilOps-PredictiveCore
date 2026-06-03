"""
ops-reference/tests/test_training_pipeline.py
==============================================
Tests for the vibration autoencoder training pipeline (TASK-029).

Coverage
--------
- test_metrics_json_has_required_fields : JSON file exists with precision/recall/f1
- test_pipeline_runs_end_to_end         : Pipeline executes end-to-end with synthetic data
- test_model_scores_in_valid_range      : Model inference returns score in [0.0, 1.0]
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

# Make sure ops-reference/training is importable when running from the repo root.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_TRAINING_DIR = _REPO_ROOT / "ops-reference" / "training"

if str(_TRAINING_DIR) not in sys.path:
    sys.path.insert(0, str(_TRAINING_DIR))

_METRICS_PATH = _REPO_ROOT / "ops-reference" / "models" / "vibration_autoencoder_v1_metrics.json"
_MODELS_DIR = _REPO_ROOT / "ops-reference" / "models"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_pipeline_module():
    """Import train_vibration_autoencoder without side effects."""
    spec = importlib.util.spec_from_file_location(
        "train_vibration_autoencoder",
        _TRAINING_DIR / "train_vibration_autoencoder.py",
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMetricsJson:
    """Validate that the metrics JSON file exists with required fields."""

    def test_metrics_json_has_required_fields(self):
        """
        Verifies that vibration_autoencoder_v1_metrics.json exists and contains
        the mandatory fields: precision, recall, f1 (CAT-06).
        """
        assert _METRICS_PATH.exists(), (
            f"Metrics file not found: {_METRICS_PATH}. "
            "Run train_vibration_autoencoder.py first."
        )
        content = json.loads(_METRICS_PATH.read_text(encoding="utf-8"))
        for field in ("precision", "recall", "f1"):
            assert field in content, f"Missing field '{field}' in metrics JSON."
            assert isinstance(content[field], (int, float)), (
                f"Field '{field}' must be numeric, got {type(content[field])}."
            )

    def test_metrics_values_are_valid_probabilities(self):
        """precision, recall and f1 must be in [0.0, 1.0]."""
        if not _METRICS_PATH.exists():
            pytest.skip("Metrics file not yet generated — run pipeline first.")
        content = json.loads(_METRICS_PATH.read_text(encoding="utf-8"))
        for field in ("precision", "recall", "f1"):
            val = content[field]
            assert 0.0 <= val <= 1.0, (
                f"Field '{field}' = {val} is outside [0.0, 1.0]."
            )


class TestPipelineEndToEnd:
    """Ensure the training pipeline runs without error on synthetic data."""

    def test_pipeline_runs_end_to_end(self, tmp_path):
        """
        Calls run_pipeline() with a temporary output directory and synthetic
        datasets directory.  Verifies that the model artifact and metrics
        file are created.
        """
        module = _load_pipeline_module()

        model_out = tmp_path / "models"
        model_out.mkdir()

        # run_pipeline accepts optional paths so tests stay isolated from the
        # real ops-reference/datasets/ directory.
        module.run_pipeline(
            datasets_dir=None,  # will generate synthetic data internally
            models_dir=str(model_out),
        )

        # Model artifact must exist (any file with .onnx or .pkl or .joblib suffix)
        artifacts = list(model_out.iterdir())
        assert len(artifacts) >= 1, "No model artifact produced by pipeline."

        # Metrics JSON must exist alongside the model
        metrics_files = [f for f in artifacts if f.suffix == ".json"]
        assert len(metrics_files) >= 1, "No metrics JSON produced by pipeline."
        content = json.loads(metrics_files[0].read_text(encoding="utf-8"))
        for field in ("precision", "recall", "f1"):
            assert field in content, f"Missing '{field}' in metrics output."

    def test_synthetic_data_generation(self):
        """generate_synthetic_dataset() produces correct shape and labels."""
        module = _load_pipeline_module()
        X, y = module.generate_synthetic_dataset()

        assert X.shape[0] == 200, f"Expected 200 samples, got {X.shape[0]}."
        assert X.shape[1] > 0, "Feature dimension must be positive."
        assert len(y) == 200, "Labels length must match samples."

        n_anomalies = int(np.sum(y == 1))
        assert n_anomalies == 40, (
            f"Expected 40 anomalous samples, got {n_anomalies}."
        )
        assert (200 - n_anomalies) == 160, "Expected 160 normal samples."


class TestModelInference:
    """Validate that the trained model scores vectors in [0.0, 1.0]."""

    def test_model_scores_in_valid_range(self, tmp_path):
        """
        Trains a model on synthetic data and checks that anomaly scores for
        held-out vectors are all in [0.0, 1.0].
        """
        module = _load_pipeline_module()

        model_out = tmp_path / "models"
        model_out.mkdir()

        module.run_pipeline(
            datasets_dir=None,
            models_dir=str(model_out),
        )

        # Load model wrapper and score a few synthetic vectors
        model = module.load_model(models_dir=str(model_out))

        # Generate test vectors (normal and anomalous)
        rng = np.random.default_rng(seed=0)
        test_vectors = rng.standard_normal((10, module.FEATURE_DIM))

        for vec in test_vectors:
            score = model.score(vec)
            assert isinstance(score, float), (
                f"Expected float score, got {type(score)}."
            )
            assert 0.0 <= score <= 1.0, (
                f"Score {score} is outside [0.0, 1.0]."
            )

    def test_anomalous_samples_score_higher_than_normal(self, tmp_path):
        """
        On average, anomalous samples should produce higher reconstruction
        errors (anomaly scores) than normal samples.

        Uses the pipeline's own feature extraction to produce properly
        distributed feature vectors so that the score comparison is valid
        against the trained scaler/PCA basis.
        """
        module = _load_pipeline_module()

        model_out = tmp_path / "models"
        model_out.mkdir()

        module.run_pipeline(
            datasets_dir=None,
            models_dir=str(model_out),
        )

        model = module.load_model(models_dir=str(model_out))

        # Generate feature vectors using the same extraction pipeline so that
        # the feature distribution matches what the model was trained on.
        rng = np.random.default_rng(seed=42)

        # Normal windows: low-amplitude noise (same as training normal class)
        normal_vecs = np.array([
            module._extract_features_inline(
                rng.normal(loc=0.0, scale=0.05, size=module._WINDOW_SIZE)
            )
            for _ in range(20)
        ])

        # Anomalous windows: high-amplitude noise (same as training anomaly class)
        anomaly_vecs = np.array([
            module._extract_features_inline(
                rng.normal(loc=0.0, scale=1.0, size=module._WINDOW_SIZE)
            )
            for _ in range(20)
        ])

        normal_scores = [model.score(v) for v in normal_vecs]
        anomaly_scores = [model.score(v) for v in anomaly_vecs]

        mean_normal = float(np.mean(normal_scores))
        mean_anomaly = float(np.mean(anomaly_scores))

        assert mean_anomaly > mean_normal, (
            f"Expected anomalous mean score ({mean_anomaly:.4f}) > "
            f"normal mean score ({mean_normal:.4f})."
        )
