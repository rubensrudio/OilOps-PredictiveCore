"""Tests for OnnxRunner.

Uses a dummy ONNX model generated with ``onnx.helper`` — no TensorFlow or
PyTorch required.  The dummy model accepts a 1-D float input of variable
length and returns two sigmoid-clamped float outputs (anomaly_score,
confidence_score).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import onnx
import onnx.helper as oh
from onnx import TensorProto as otp
import pytest

from ops_models.app.serving.onnx_runner import OnnxRunner


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_dummy_onnx_model(n_features: int = 4) -> bytes:
    """Build a minimal ONNX model that accepts (1, n_features) float32 input
    and returns two scalar float32 outputs via GlobalAveragePool trick.

    Architecture (all ops from onnx opset 11):
        input  [1, n_features]
          └─> Flatten → [1, n_features]
          └─> Unsqueeze(axes=[0]) to produce [1, 1, n_features] (channel-last)
          ... simplified: just use a Gemm with weight that produces 2 outputs
    """
    # Weight matrix: (n_features, 2) so that Gemm produces (1, 2).
    weight_vals = np.ones((n_features, 2), dtype=np.float32) / n_features
    bias_vals = np.zeros(2, dtype=np.float32)

    weight_init = oh.make_tensor(
        name="W",
        data_type=otp.FLOAT,
        dims=weight_vals.shape,
        vals=weight_vals.flatten().tolist(),
    )
    bias_init = oh.make_tensor(
        name="B",
        data_type=otp.FLOAT,
        dims=bias_vals.shape,
        vals=bias_vals.tolist(),
    )

    # Node: Gemm(input, W, B) -> pre_sigmoid  shape (1, 2)
    gemm_node = oh.make_node(
        op_type="Gemm",
        inputs=["input", "W", "B"],
        outputs=["pre_sigmoid"],
        transB=0,
    )
    # Node: Sigmoid(pre_sigmoid) -> output  shape (1, 2)
    sigmoid_node = oh.make_node(
        op_type="Sigmoid",
        inputs=["pre_sigmoid"],
        outputs=["output"],
    )

    graph = oh.make_graph(
        nodes=[gemm_node, sigmoid_node],
        name="dummy_model",
        inputs=[
            oh.make_tensor_value_info("input", otp.FLOAT, [1, n_features])
        ],
        outputs=[
            oh.make_tensor_value_info("output", otp.FLOAT, [1, 2])
        ],
        initializer=[weight_init, bias_init],
    )

    model_proto = oh.make_model(graph, opset_imports=[oh.make_opsetid("", 11)])
    onnx.checker.check_model(model_proto)
    return model_proto.SerializeToString()


@pytest.fixture(scope="module")
def dummy_model_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Write a dummy ONNX model to a temp file and return its path."""
    tmp_dir = tmp_path_factory.mktemp("models")
    path = tmp_dir / "dummy.onnx"
    path.write_bytes(_build_dummy_onnx_model(n_features=4))
    return path


@pytest.fixture()
def runner(dummy_model_path: Path) -> OnnxRunner:
    return OnnxRunner(dummy_model_path)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestOnnxRunnerInit:
    def test_loads_valid_model(self, dummy_model_path: Path) -> None:
        """OnnxRunner must load without raising when given a valid ONNX file."""
        runner = OnnxRunner(dummy_model_path)
        assert runner is not None

    def test_raises_file_not_found(self, tmp_path: Path) -> None:
        """OnnxRunner must raise FileNotFoundError for a missing artefact."""
        with pytest.raises(FileNotFoundError):
            OnnxRunner(tmp_path / "nonexistent.onnx")


class TestOnnxRunnerRun:
    def test_returns_expected_keys(self, runner: OnnxRunner) -> None:
        """run() must return a dict with anomaly_score and confidence_score."""
        result = runner.run([0.1, 0.2, 0.3, 0.4])
        assert set(result.keys()) == {"anomaly_score", "confidence_score"}

    def test_scores_are_floats(self, runner: OnnxRunner) -> None:
        result = runner.run([0.1, 0.2, 0.3, 0.4])
        assert isinstance(result["anomaly_score"], float)
        assert isinstance(result["confidence_score"], float)

    def test_anomaly_score_clamped_in_range(self, runner: OnnxRunner) -> None:
        """anomaly_score must be in [0.0, 1.0]."""
        result = runner.run([0.1, 0.2, 0.3, 0.4])
        assert 0.0 <= result["anomaly_score"] <= 1.0

    def test_confidence_score_clamped_in_range(self, runner: OnnxRunner) -> None:
        """confidence_score must be in [0.0, 1.0]."""
        result = runner.run([0.1, 0.2, 0.3, 0.4])
        assert 0.0 <= result["confidence_score"] <= 1.0

    def test_accepts_numpy_array(self, runner: OnnxRunner) -> None:
        """run() must accept a numpy array as input."""
        features = np.array([0.5, 0.6, 0.7, 0.8], dtype=np.float32)
        result = runner.run(features)
        assert 0.0 <= result["anomaly_score"] <= 1.0
        assert 0.0 <= result["confidence_score"] <= 1.0

    def test_raises_value_error_on_empty_list(self, runner: OnnxRunner) -> None:
        """run() must raise ValueError when features is an empty list."""
        with pytest.raises(ValueError, match="non-empty"):
            runner.run([])

    def test_raises_value_error_on_empty_array(self, runner: OnnxRunner) -> None:
        """run() must raise ValueError when features is an empty numpy array."""
        with pytest.raises(ValueError, match="non-empty"):
            runner.run(np.array([], dtype=np.float32))

    def test_deterministic_output(self, runner: OnnxRunner) -> None:
        """Two calls with the same features must return the same scores."""
        features = [0.1, 0.2, 0.3, 0.4]
        r1 = runner.run(features)
        r2 = runner.run(features)
        assert r1 == r2
