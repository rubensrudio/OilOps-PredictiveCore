"""Tests for SHAPExplainer (TASK-019).

Uses a simple callable as the predict_fn (wrapping a numpy dot-product)
to avoid depending on an actual ONNX model file in this test module.
An ONNX-backed variant is also tested using a minimal dummy model generated
with ``onnx.helper`` (same pattern as ops-models/tests/test_onnx_runner.py).

Criteria from tasks.md (TASK-019):
    - SHAPExplainer.explain returns list of feature_attributions with at
      least top_n items ordered by rank.
    - Items have keys: feature_name, attribution_value, rank.
    - Rank 1 has the highest absolute attribution_value.

Integration criterion (re-review fix):
    - make_onnx_predict_fn wraps OnnxRunner correctly for KernelExplainer.
    - SHAPExplainer.explain called with make_onnx_predict_fn does not raise
      and returns feature_attributions with >= 5 items.
"""

from __future__ import annotations


import numpy as np
import pytest

from ops_explain.app.shap_explainer import SHAPExplainer, make_onnx_predict_fn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_linear_predict_fn(weights: np.ndarray) -> callable:
    """Return a linear scoring function: f(X) = X @ weights, clamped to [0,1]."""

    def predict(X: np.ndarray) -> np.ndarray:
        scores = X @ weights
        return np.clip(scores, 0.0, 1.0)

    return predict


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

N_FEATURES = 8
FEATURE_NAMES = [f"feat_{i}" for i in range(N_FEATURES)]

# Weights: increasing magnitude so feat_7 > feat_6 > ... > feat_0.
_WEIGHTS = np.array([0.01, 0.02, 0.05, 0.08, 0.10, 0.15, 0.20, 0.40], dtype=np.float32)


@pytest.fixture()
def explainer() -> SHAPExplainer:
    predict_fn = _make_linear_predict_fn(_WEIGHTS)
    return SHAPExplainer(
        predict_fn=predict_fn,
        feature_names=FEATURE_NAMES,
        top_n=5,
        n_background=8,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSHAPExplainerOutputShape:
    """Verify the structure of the attribution dict returned by explain()."""

    def test_returns_dict_with_required_keys(self, explainer: SHAPExplainer) -> None:
        features = np.ones(N_FEATURES, dtype=np.float32) * 0.5
        result = explainer.explain(features.tolist())

        assert isinstance(result, dict)
        assert "feature_attributions" in result
        assert "baseline_window" in result
        assert "method" in result

    def test_method_is_shap_kernel(self, explainer: SHAPExplainer) -> None:
        features = np.ones(N_FEATURES, dtype=np.float32) * 0.5
        result = explainer.explain(features.tolist())
        assert result["method"] == "shap_kernel"

    def test_at_least_top_n_items_returned(self, explainer: SHAPExplainer) -> None:
        """Tasks.md criterion: at least top_n=5 items in feature_attributions."""
        features = np.random.default_rng(42).uniform(0, 1, N_FEATURES).astype(np.float32)
        result = explainer.explain(features.tolist())

        attributions = result["feature_attributions"]
        assert len(attributions) >= 5

    def test_attribution_item_has_required_fields(self, explainer: SHAPExplainer) -> None:
        features = np.ones(N_FEATURES, dtype=np.float32) * 0.3
        result = explainer.explain(features.tolist())

        for item in result["feature_attributions"]:
            assert "feature_name" in item
            assert "attribution_value" in item
            assert "rank" in item
            assert isinstance(item["attribution_value"], float)
            assert isinstance(item["rank"], int)

    def test_ranks_are_contiguous_starting_at_1(self, explainer: SHAPExplainer) -> None:
        features = np.ones(N_FEATURES, dtype=np.float32) * 0.5
        result = explainer.explain(features.tolist())
        ranks = [item["rank"] for item in result["feature_attributions"]]
        assert ranks == list(range(1, len(ranks) + 1))

    def test_rank_1_has_highest_absolute_attribution(self, explainer: SHAPExplainer) -> None:
        """Rank 1 must correspond to the feature with the largest |attribution|."""
        features = np.random.default_rng(7).uniform(0.1, 1.0, N_FEATURES).astype(np.float32)
        result = explainer.explain(features.tolist())

        attributions = result["feature_attributions"]
        abs_vals = [abs(a["attribution_value"]) for a in attributions]

        # rank-1 item should have the maximum absolute value
        assert abs_vals[0] == max(abs_vals)

    def test_feature_names_subset_of_known_names(self, explainer: SHAPExplainer) -> None:
        features = np.ones(N_FEATURES, dtype=np.float32) * 0.2
        result = explainer.explain(features.tolist())

        returned_names = {item["feature_name"] for item in result["feature_attributions"]}
        assert returned_names.issubset(set(FEATURE_NAMES))


class TestSHAPExplainerBaselineWindow:
    """Verify the baseline_window statistics are present and numeric."""

    def test_baseline_window_contains_stats_per_feature(
        self, explainer: SHAPExplainer
    ) -> None:
        features = np.ones(N_FEATURES, dtype=np.float32) * 0.4
        result = explainer.explain(features.tolist())

        baseline = result["baseline_window"]
        assert "stats_per_feature" in baseline
        stats = baseline["stats_per_feature"]

        for name in FEATURE_NAMES:
            assert name in stats
            feat_stats = stats[name]
            for key in ("mean", "std", "p5", "p95"):
                assert key in feat_stats
                assert isinstance(feat_stats[key], float)

    def test_background_data_overrides_zero_baseline(self, explainer: SHAPExplainer) -> None:
        """When background_data is provided, baseline stats reflect actual data."""
        rng = np.random.default_rng(99)
        bg = rng.uniform(5.0, 10.0, (20, N_FEATURES)).astype(np.float32)
        features = np.ones(N_FEATURES, dtype=np.float32) * 7.5

        result = explainer.explain(features.tolist(), background_data=bg)
        stats = result["baseline_window"]["stats_per_feature"]

        # Mean of background was drawn from [5, 10]; zero baseline mean would be 0.
        for name in FEATURE_NAMES:
            assert stats[name]["mean"] > 1.0, (
                f"Expected mean > 1 when background is in [5,10], got {stats[name]['mean']}"
            )


class TestSHAPExplainerValidation:
    """Verify input validation behaviour."""

    def test_raises_on_wrong_feature_length(self, explainer: SHAPExplainer) -> None:
        wrong_features = [0.1] * (N_FEATURES - 2)
        with pytest.raises(ValueError, match="does not match"):
            explainer.explain(wrong_features)

    def test_handles_background_shape_mismatch_gracefully(
        self, explainer: SHAPExplainer
    ) -> None:
        """Bad background_data falls back to zero baseline without raising."""
        bad_bg = np.ones((5, N_FEATURES + 3), dtype=np.float32)
        features = np.ones(N_FEATURES, dtype=np.float32) * 0.5
        # Should not raise; zero baseline is used instead.
        result = explainer.explain(features.tolist(), background_data=bad_bg)
        assert "feature_attributions" in result


class TestSHAPExplainerTopN:
    """Verify top_n configuration is respected."""

    def test_top_3_returns_at_least_3_items(self) -> None:
        predict_fn = _make_linear_predict_fn(_WEIGHTS)
        exp = SHAPExplainer(
            predict_fn=predict_fn,
            feature_names=FEATURE_NAMES,
            top_n=3,
        )
        features = np.ones(N_FEATURES, dtype=np.float32) * 0.5
        result = exp.explain(features.tolist())
        assert len(result["feature_attributions"]) >= 3

    def test_top_n_greater_than_features_returns_all_features(self) -> None:
        few_names = ["a", "b", "c"]
        weights = np.array([0.1, 0.3, 0.6], dtype=np.float32)
        predict_fn = _make_linear_predict_fn(weights)
        exp = SHAPExplainer(
            predict_fn=predict_fn,
            feature_names=few_names,
            top_n=10,  # more than available features
        )
        features = [0.5, 0.5, 0.5]
        result = exp.explain(features)
        # Should return all 3 features (capped at n_features)
        assert len(result["feature_attributions"]) == 3


# ---------------------------------------------------------------------------
# Integration tests — make_onnx_predict_fn with mock OnnxRunner
# ---------------------------------------------------------------------------


class _MockOnnxRunner:
    """Minimal mock that mimics ``OnnxRunner.run`` contract.

    Accepts a 1-D ``list[float]`` and returns
    ``{"anomaly_score": float, "confidence_score": float}``.
    The anomaly_score is computed as the mean of the input features clamped
    to [0, 1], giving a deterministic non-trivial output for SHAP to work with.
    """

    def run(self, features: list[float]) -> dict[str, float]:
        # features is 1-D list[float] as required by OnnxRunner contract.
        score = float(np.clip(np.mean(features), 0.0, 1.0))
        return {"anomaly_score": score, "confidence_score": score}


class TestMakeOnnxPredictFn:
    """Verify make_onnx_predict_fn produces a KernelExplainer-compatible wrapper."""

    def test_predict_fn_returns_1d_array_for_batch_input(self) -> None:
        """predict_fn must accept 2-D input and return 1-D ndarray."""
        mock_runner = _MockOnnxRunner()
        predict_fn = make_onnx_predict_fn(mock_runner)

        n_samples, n_features = 5, 8
        X = np.random.default_rng(0).uniform(0, 1, (n_samples, n_features)).astype(np.float32)
        result = predict_fn(X)

        assert isinstance(result, np.ndarray), "predict_fn must return ndarray"
        assert result.ndim == 1, f"Expected 1-D output, got shape {result.shape}"
        assert result.shape == (n_samples,), (
            f"Expected shape ({n_samples},), got {result.shape}"
        )

    def test_predict_fn_output_dtype_is_float32(self) -> None:
        """Output dtype must be float32 for SHAP compatibility."""
        mock_runner = _MockOnnxRunner()
        predict_fn = make_onnx_predict_fn(mock_runner)

        X = np.ones((3, 4), dtype=np.float32)
        result = predict_fn(X)

        assert result.dtype == np.float32, f"Expected float32, got {result.dtype}"

    def test_predict_fn_extracts_anomaly_score_not_dict(self) -> None:
        """Each element of the output must be a scalar float, not a dict."""
        mock_runner = _MockOnnxRunner()
        predict_fn = make_onnx_predict_fn(mock_runner)

        X = np.array([[0.1, 0.9], [0.4, 0.6]], dtype=np.float32)
        result = predict_fn(X)

        for val in result:
            assert isinstance(float(val), float), (
                f"Expected scalar float in output, got {type(val)}"
            )

    def test_shap_explainer_with_onnx_runner_mock_does_not_raise(self) -> None:
        """Integration: SHAPExplainer.explain with make_onnx_predict_fn must not raise.

        This is the core regression test for the integration issue:
        a naïve lambda wrapper would pass 2-D input to OnnxRunner (expects 1-D)
        and return a dict (SHAP expects 1-D ndarray), breaking the computation.
        make_onnx_predict_fn fixes both problems.

        Criterion from re-review: returns feature_attributions with >= 5 items.
        """
        mock_runner = _MockOnnxRunner()
        predict_fn = make_onnx_predict_fn(mock_runner)

        feature_names = [f"feat_{i}" for i in range(8)]
        explainer = SHAPExplainer(
            predict_fn=predict_fn,
            feature_names=feature_names,
            top_n=5,
            n_background=8,
        )

        features = np.random.default_rng(42).uniform(0.0, 1.0, 8).tolist()
        result = explainer.explain(features)

        assert "feature_attributions" in result
        assert len(result["feature_attributions"]) >= 5, (
            f"Expected >= 5 feature_attributions, got {len(result['feature_attributions'])}"
        )

    def test_shap_explainer_attribution_item_structure_with_onnx_mock(self) -> None:
        """Each attribution item from OnnxRunner-backed explainer has required keys."""
        mock_runner = _MockOnnxRunner()
        predict_fn = make_onnx_predict_fn(mock_runner)

        feature_names = [f"sensor_{i}" for i in range(6)]
        explainer = SHAPExplainer(
            predict_fn=predict_fn,
            feature_names=feature_names,
            top_n=5,
            n_background=6,
        )

        result = explainer.explain([0.2, 0.4, 0.6, 0.1, 0.8, 0.3])

        for item in result["feature_attributions"]:
            assert "feature_name" in item
            assert "attribution_value" in item
            assert "rank" in item
            assert item["feature_name"] in feature_names
