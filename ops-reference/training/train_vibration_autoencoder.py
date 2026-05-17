"""
ops-reference/training/train_vibration_autoencoder.py
======================================================
Reproducible training pipeline for the vibration anomaly autoencoder
(TASK-029 / CAT-06 / INIT-US-06).

Overview
--------
1. Load data: reads CSVs from ``datasets_dir`` or generates synthetic data.
2. Feature extraction: inline extraction (rms, mean, std, FFT bins) per
   time window.  Uses VibrationFeatureExtractor from TASK-013 if importable;
   falls back to equivalent inline implementation.
3. Train autoencoder: implemented as a PCA-based reconstruction autoencoder
   using sklearn (no TensorFlow required).  Architecture mirrors the spec:
   Dense(input_dim) → latent(32 → 16) → Dense(input_dim).
4. Export model: saves model as ONNX using ``onnx.helper`` to build the
   computation graph directly — no ``skl2onnx`` required.

   ONNX graph contract (required by ops-models OnnxRunner):
     Input  : ``input``       float32, shape (batch, n_features)
     Output : ``anomaly_score`` float32, shape (batch,)
   The graph implements:
     x_scaled = (input - scaler_mean) / scaler_scale          # StandardScaler
     x_latent = x_scaled @ pca_components.T                   # PCA transform
     x_recon  = x_latent @ pca_components + pca_mean          # PCA inverse
     mse      = mean((x_scaled - x_recon)^2, axis=features)   # reconstruction error
     anomaly_score = sigmoid(mse)                              # normalised to [0,1]

5. Save metrics: writes vibration_autoencoder_v1_metrics.json with
   ``precision``, ``recall``, ``f1`` calculated on the hold-out set.
   ``dataset`` field is ``"synthetic"`` when no real CSV data is found,
   or ``"cwru_sample"`` when CWRU CSV files are loaded.

Constants
---------
FEATURE_DIM : int
    Dimensionality of the feature vector fed into the autoencoder.
    = 4 scalar features + 64 FFT bins = 68.

Usage (CLI)
-----------
    python ops-reference/training/train_vibration_autoencoder.py

Testing
-------
    python -m pytest ops-reference/tests/test_training_pipeline.py -v

ADVISORY NOTICE
---------------
This model is a reference implementation for evaluation purposes only.
It is NOT a safety-rated system.  Do NOT use as a substitute for
safety-instrumented systems or certified industrial safety functions.
"""

from __future__ import annotations

import json
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from sklearn.decomposition import PCA
from sklearn.metrics import f1_score, precision_score, recall_score
from sklearn.preprocessing import StandardScaler

# ---------------------------------------------------------------------------
# Logging (structured-ready; falls back to plain when python-json-logger is
# absent to keep the training environment lightweight).
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    stream=sys.stdout,
)
_logger = logging.getLogger("ops-reference.training.vibration_autoencoder")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Number of scalar time-domain features extracted per window.
_N_SCALAR_FEATURES: int = 4  # rms, mean, std, peak

#: Number of FFT magnitude bins extracted per window.
_N_FFT_BINS: int = 64

#: Total feature vector dimension fed into the autoencoder.
FEATURE_DIM: int = _N_SCALAR_FEATURES + _N_FFT_BINS  # = 68

#: Latent dimensions of the autoencoder (mirrors Dense(32) → Dense(16)).
_LATENT_DIM_OUTER: int = 32
_LATENT_DIM_INNER: int = 16

#: Number of synthetic samples to generate when real data is unavailable.
_N_NORMAL_SAMPLES: int = 160
_N_ANOMALY_SAMPLES: int = 40

#: Time-domain window size (number of acceleration samples per window).
_WINDOW_SIZE: int = 1024

#: Threshold: reconstruction error above this percentile is anomalous.
_ANOMALY_PERCENTILE: float = 95.0

#: Random seed for reproducibility.
_SEED: int = 42

# ---------------------------------------------------------------------------
# Default paths (relative to repository root)
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DATASETS_DIR = _REPO_ROOT / "ops-reference" / "datasets" / "cwru_sample"
_DEFAULT_MODELS_DIR = _REPO_ROOT / "ops-reference" / "models"
_MODEL_FILENAME = "vibration_autoencoder_v1"
_METRICS_FILENAME = "vibration_autoencoder_v1_metrics.json"


# ===========================================================================
# Feature extraction
# ===========================================================================

def _try_import_vibration_extractor():
    """
    Attempt to import VibrationFeatureExtractor from TASK-013.
    Returns the class if importable, else None.
    """
    try:
        extractor_path = _REPO_ROOT / "ops-feature" / "app" / "extractors" / "vibration.py"
        if not extractor_path.exists():
            return None
        import importlib.util as _ilu
        spec = _ilu.spec_from_file_location("vibration_extractor", extractor_path)
        mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return getattr(mod, "VibrationFeatureExtractor", None)
    except Exception as exc:  # noqa: BLE001
        _logger.debug("Could not import VibrationFeatureExtractor: %s", exc)
        return None


def _extract_features_inline(window: np.ndarray, fft_bins: int = _N_FFT_BINS) -> np.ndarray:
    """
    Minimal inline feature extraction: rms, mean, std, peak + FFT bins.

    Parameters
    ----------
    window:
        1-D array of acceleration values (float64).
    fft_bins:
        Number of FFT magnitude bins to include.

    Returns
    -------
    np.ndarray of shape (4 + fft_bins,).
    """
    signal = np.asarray(window, dtype=np.float64)

    rms = float(np.sqrt(np.mean(signal ** 2)))
    mean = float(np.mean(signal))
    std = float(np.std(signal, ddof=0))
    peak = float(np.max(np.abs(signal)))

    # One-sided FFT magnitude, normalised by N.
    fft_complex = np.fft.rfft(signal)
    magnitude = np.abs(fft_complex) / len(signal)
    if len(magnitude) >= fft_bins:
        fft_vec = magnitude[:fft_bins]
    else:
        pad = np.zeros(fft_bins - len(magnitude), dtype=np.float64)
        fft_vec = np.concatenate([magnitude, pad])

    return np.array([rms, mean, std, peak] + fft_vec.tolist(), dtype=np.float64)


def _extract_features_from_window(
    window: np.ndarray,
    extractor_cls: Optional[Any] = None,
    fft_bins: int = _N_FFT_BINS,
) -> np.ndarray:
    """
    Extract a feature vector from a time-domain window.

    Tries VibrationFeatureExtractor (TASK-013) first; falls back to inline.
    Note: TASK-013 returns {rms, variance, kurtosis, skewness, fft_bins}
    which is 4 scalars + fft_bins — same cardinality as the inline version —
    so FEATURE_DIM stays consistent at 68.
    """
    if extractor_cls is not None:
        try:
            ext = extractor_cls(fft_bins=fft_bins, min_window_size=len(window))
            result = ext.extract(window)
            if result is not None:
                scalars = [
                    result["rms"],
                    result["variance"],
                    result["kurtosis"],
                    result["skewness"],
                ]
                return np.array(scalars + result["fft_bins"], dtype=np.float64)
        except Exception as exc:  # noqa: BLE001
            _logger.debug("VibrationFeatureExtractor failed, using inline: %s", exc)

    return _extract_features_inline(window, fft_bins=fft_bins)


# ===========================================================================
# Synthetic dataset generation
# ===========================================================================

def generate_synthetic_dataset(
    n_normal: int = _N_NORMAL_SAMPLES,
    n_anomaly: int = _N_ANOMALY_SAMPLES,
    window_size: int = _WINDOW_SIZE,
    fft_bins: int = _N_FFT_BINS,
    seed: int = _SEED,
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Generate a synthetic labelled dataset of vibration feature vectors.

    Normal samples    → low-amplitude Gaussian noise (scale=0.05 m/s²).
    Anomalous samples → high-amplitude Gaussian noise (scale=1.0 m/s²)
                        with an added sinusoidal component to simulate a
                        bearing fault frequency.

    Returns
    -------
    X : np.ndarray of shape (n_normal + n_anomaly, FEATURE_DIM)
        Feature matrix.
    y : np.ndarray of shape (n_normal + n_anomaly,)
        Binary labels: 0 = normal, 1 = anomaly.
    """
    rng = np.random.default_rng(seed=seed)
    extractor_cls = _try_import_vibration_extractor()

    features: List[np.ndarray] = []
    labels: List[int] = []

    # Normal samples — low-amplitude Gaussian
    for _ in range(n_normal):
        window = rng.normal(loc=0.0, scale=0.05, size=window_size)
        feat = _extract_features_from_window(window, extractor_cls, fft_bins)
        features.append(feat)
        labels.append(0)

    # Anomalous samples — high-amplitude noise + sinusoidal fault signature
    t = np.linspace(0, 1, window_size)
    fault_freq_hz = 157.0  # typical inner-race fault frequency (BPFI) for CWRU
    for _ in range(n_anomaly):
        noise = rng.normal(loc=0.0, scale=1.0, size=window_size)
        fault_sig = 0.5 * np.sin(2 * np.pi * fault_freq_hz * t)
        window = noise + fault_sig
        feat = _extract_features_from_window(window, extractor_cls, fft_bins)
        features.append(feat)
        labels.append(1)

    X = np.array(features, dtype=np.float64)
    y = np.array(labels, dtype=np.int32)

    return X, y


# ===========================================================================
# Data loading
# ===========================================================================

def _load_csv_dataset(
    datasets_dir: Path,
    fft_bins: int = _N_FFT_BINS,
    extractor_cls: Optional[Any] = None,
) -> Tuple[Optional[np.ndarray], Optional[np.ndarray]]:
    """
    Attempt to load CSV files from datasets_dir.

    Expects one or more CSV files with columns:
      - ``label`` : 0 (normal) or 1 (anomaly)
      - Remaining columns: raw acceleration samples (1024 per row)

    Returns (X, y) if CSVs are found and valid, else (None, None).
    """
    csv_files = list(datasets_dir.glob("*.csv"))
    if not csv_files:
        return None, None

    import csv as _csv

    all_features: List[np.ndarray] = []
    all_labels: List[int] = []

    for csv_path in sorted(csv_files):
        try:
            with csv_path.open(newline="", encoding="utf-8") as f:
                reader = _csv.DictReader(f)
                for row in reader:
                    label = int(row.pop("label"))
                    samples = np.array([float(v) for v in row.values()], dtype=np.float64)
                    if len(samples) < 64:
                        _logger.warning("Skipping row with < 64 samples in %s", csv_path.name)
                        continue
                    feat = _extract_features_from_window(samples, extractor_cls, fft_bins)
                    all_features.append(feat)
                    all_labels.append(label)
        except Exception as exc:  # noqa: BLE001
            _logger.error("Failed to parse CSV %s: %s", csv_path, exc)
            continue

    if not all_features:
        return None, None

    return np.array(all_features, dtype=np.float64), np.array(all_labels, dtype=np.int32)


def load_dataset(
    datasets_dir: Optional[str],
    fft_bins: int = _N_FFT_BINS,
) -> Tuple[np.ndarray, np.ndarray, str]:
    """
    Load the vibration dataset.

    Tries to read CSVs from ``datasets_dir``; if none found or
    ``datasets_dir`` is None, generates synthetic data.

    Returns
    -------
    X : np.ndarray, shape (n_samples, FEATURE_DIM)
    y : np.ndarray, shape (n_samples,)
    dataset_name : str — ``"cwru_sample"`` or ``"synthetic"``
    """
    extractor_cls = _try_import_vibration_extractor()

    if datasets_dir is not None:
        ddir = Path(datasets_dir)
        if ddir.is_dir():
            X, y = _load_csv_dataset(ddir, fft_bins=fft_bins, extractor_cls=extractor_cls)
            if X is not None:
                _logger.info(
                    "Loaded %d samples from CSV files in %s.", len(X), ddir
                )
                return X, y, "cwru_sample"
            _logger.warning(
                "No valid CSV data found in %s — falling back to synthetic dataset.",
                ddir,
            )

    _logger.info(
        "Generating synthetic dataset (%d normal + %d anomalous).",
        _N_NORMAL_SAMPLES,
        _N_ANOMALY_SAMPLES,
    )
    X, y = generate_synthetic_dataset(fft_bins=fft_bins)
    return X, y, "synthetic"


# ===========================================================================
# PCA-based autoencoder
# ===========================================================================

class _PCAAutoencoder:
    """
    Lightweight autoencoder based on PCA reconstruction.

    Architecture analogy
    --------------------
    Encoder : StandardScaler → PCA(n_components=_LATENT_DIM_INNER)
              Equivalent to Dense(input_dim) → Dense(32) → Dense(16)
    Decoder : inverse_transform back to input_dim
              Equivalent to Dense(16) → Dense(32) → Dense(input_dim)

    Anomaly score
    -------------
    Score is the sigmoid of the mean squared reconstruction error
    (in scaled space), clipped to [0.0, 1.0].  Threshold for binary
    classification is derived from the ``_ANOMALY_PERCENTILE`` of
    training reconstruction errors (in MSE space, pre-sigmoid).
    """

    def __init__(self, n_components: int = _LATENT_DIM_INNER) -> None:
        self._scaler = StandardScaler()
        self._pca = PCA(n_components=n_components, random_state=_SEED)
        self._threshold: float = 0.5
        self._n_components = n_components

    # ------------------------------------------------------------------
    # Training
    # ------------------------------------------------------------------

    def fit(self, X_normal: np.ndarray) -> "_PCAAutoencoder":
        """
        Fit the autoencoder on normal (non-anomalous) samples only.

        Parameters
        ----------
        X_normal:
            Feature matrix of normal samples, shape (n, FEATURE_DIM).
        """
        X_scaled = self._scaler.fit_transform(X_normal)
        self._pca.fit(X_scaled)

        # Determine anomaly threshold in sigmoid-score space:
        # use 95th percentile of training anomaly scores.
        train_scores = self._batch_scores(X_normal)
        self._threshold = float(np.percentile(train_scores, _ANOMALY_PERCENTILE))

        _logger.info(
            "PCAAutoencoder fitted. n_components=%d, threshold(sigmoid)=%.6f",
            self._n_components,
            self._threshold,
        )
        return self

    # ------------------------------------------------------------------
    # Inference helpers
    # ------------------------------------------------------------------

    def _reconstruction_mse(self, X: np.ndarray) -> np.ndarray:
        """
        Mean squared reconstruction error in scaled space for each sample.

        Parameters
        ----------
        X : shape (n, feature_dim) — raw (un-scaled) features.

        Returns
        -------
        np.ndarray shape (n,), dtype float64.
        """
        X_scaled = self._scaler.transform(X)
        X_latent = self._pca.transform(X_scaled)
        X_reconstructed = self._pca.inverse_transform(X_latent)
        return np.mean((X_scaled - X_reconstructed) ** 2, axis=1)

    def _batch_scores(self, X: np.ndarray) -> np.ndarray:
        """
        Sigmoid-normalised anomaly scores for a batch.

        Returns
        -------
        np.ndarray shape (n,), dtype float64, values in (0, 1).
        """
        mse = self._reconstruction_mse(X)
        return 1.0 / (1.0 + np.exp(-mse))  # sigmoid

    def score(self, x: np.ndarray) -> float:
        """
        Return an anomaly score in [0.0, 1.0] for a single feature vector.

        Parameters
        ----------
        x : np.ndarray of shape (FEATURE_DIM,)

        Returns
        -------
        float in [0.0, 1.0]
        """
        scores = self._batch_scores(x.reshape(1, -1))
        return float(np.clip(scores[0], 0.0, 1.0))

    def predict(self, X: np.ndarray) -> np.ndarray:
        """
        Binary anomaly prediction for a batch.

        Returns
        -------
        np.ndarray of int (0 = normal, 1 = anomaly), shape (n,).
        """
        scores = self._batch_scores(X)
        return (scores >= self._threshold).astype(np.int32)

    # ------------------------------------------------------------------
    # ONNX-Runtime-compatible interface (onnxruntime.InferenceSession shim)
    # ------------------------------------------------------------------

    def run(
        self,
        output_names: Optional[List[str]],
        input_feed: Dict[str, np.ndarray],
    ) -> List[np.ndarray]:
        """
        Mimic ``onnxruntime.InferenceSession.run()`` signature.

        Parameters
        ----------
        output_names : ignored (single output assumed)
        input_feed   : dict with a single key mapping to input array,
                       shape (batch, FEATURE_DIM) or (FEATURE_DIM,).

        Returns
        -------
        [np.ndarray]: list with one element — anomaly_score,
                      shape (batch,), dtype float32, values in [0,1].
        """
        x = next(iter(input_feed.values()))
        if x.ndim == 1:
            x = x.reshape(1, -1)
        scores = self._batch_scores(x.astype(np.float64))
        return [np.clip(scores, 0.0, 1.0).astype(np.float32)]


# ===========================================================================
# ONNX export — built directly with onnx.helper (no skl2onnx dependency)
# ===========================================================================

def _export_onnx(
    model: _PCAAutoencoder,
    output_path: Path,
    feature_dim: int,
) -> None:
    """
    Export the fitted _PCAAutoencoder to ONNX format.

    The resulting ONNX graph satisfies the ops-models OnnxRunner contract:
      - Input  : ``input``         float32, shape (batch, feature_dim)
      - Output : ``anomaly_score`` float32, shape (batch,)

    Graph topology:
      x_scaled = (input - scaler_mean) / scaler_scale
      x_latent = x_scaled @ pca_components.T
      x_recon  = x_latent @ pca_components + pca_mean_scaled
      mse      = mean((x_scaled - x_recon)^2, axis=1)
      anomaly_score = sigmoid(mse)

    Parameters
    ----------
    model : fitted _PCAAutoencoder instance.
    output_path : destination .onnx file path.
    feature_dim : number of input features (FEATURE_DIM).
    """
    try:
        import onnx
        import onnx.helper as _h
        import onnx.numpy_helper as _nph
        from onnx import TensorProto
    except ImportError as exc:
        raise RuntimeError(
            "The 'onnx' package is required for ONNX export. "
            "Install it with: pip install onnx"
        ) from exc

    # ------------------------------------------------------------------
    # Extract fitted parameters from sklearn objects (float32 for ONNX)
    # ------------------------------------------------------------------
    scaler_mean = model._scaler.mean_.astype(np.float32)       # shape (feature_dim,)
    scaler_scale = model._scaler.scale_.astype(np.float32)     # shape (feature_dim,)
    pca_components = model._pca.components_.astype(np.float32) # shape (n_components, feature_dim)
    # PCA mean in the *scaled* space (zero after StandardScaler, but kept for
    # correctness when pca.mean_ is non-zero due to sklearn internals)
    pca_mean_scaled = model._pca.mean_.astype(np.float32)      # shape (feature_dim,)

    n_components, _feat = pca_components.shape
    assert _feat == feature_dim, (
        f"PCA components shape mismatch: expected feature_dim={feature_dim}, "
        f"got {_feat}"
    )

    # Reshape to (1, feature_dim) for broadcasting over batch dimension
    scaler_mean_2d = scaler_mean.reshape(1, feature_dim)
    scaler_scale_2d = scaler_scale.reshape(1, feature_dim)
    pca_mean_2d = pca_mean_scaled.reshape(1, feature_dim)
    pca_components_T = pca_components.T.copy()  # (feature_dim, n_components)

    # ------------------------------------------------------------------
    # Build initializers (constant tensors embedded in the graph)
    # ------------------------------------------------------------------
    initializers = [
        _nph.from_array(scaler_mean_2d, name="scaler_mean"),
        _nph.from_array(scaler_scale_2d, name="scaler_scale"),
        _nph.from_array(pca_components, name="pca_components"),
        _nph.from_array(pca_components_T, name="pca_components_T"),
        _nph.from_array(pca_mean_2d, name="pca_mean_scaled"),
        # Axes tensor for ReduceMean (opset 18 requires axes as input)
        _nph.from_array(np.array([1], dtype=np.int64), name="reduce_axes"),
        # Exponent for Pow node
        _nph.from_array(np.array([2.0], dtype=np.float32), name="pow_exp"),
    ]

    # ------------------------------------------------------------------
    # Build computation nodes
    # ------------------------------------------------------------------
    nodes = [
        # 1. Standardize: x_scaled = (input - scaler_mean) / scaler_scale
        _h.make_node("Sub", ["input", "scaler_mean"], ["x_centered"], name="sub_mean"),
        _h.make_node("Div", ["x_centered", "scaler_scale"], ["x_scaled"], name="div_scale"),

        # 2. PCA forward transform: x_latent = x_scaled @ pca_components.T
        #    x_scaled: (batch, feature_dim) × pca_components_T: (feature_dim, n_components)
        #    → x_latent: (batch, n_components)
        _h.make_node("MatMul", ["x_scaled", "pca_components_T"], ["x_latent"], name="pca_forward"),

        # 3. PCA inverse transform: x_recon = x_latent @ pca_components + pca_mean_scaled
        #    x_latent: (batch, n_components) × pca_components: (n_components, feature_dim)
        #    → x_recon_centered: (batch, feature_dim)
        _h.make_node("MatMul", ["x_latent", "pca_components"], ["x_recon_centered"], name="pca_inverse"),
        _h.make_node("Add", ["x_recon_centered", "pca_mean_scaled"], ["x_reconstructed"], name="add_pca_mean"),

        # 4. Reconstruction error: diff = x_scaled - x_reconstructed
        _h.make_node("Sub", ["x_scaled", "x_reconstructed"], ["diff"], name="sub_recon"),

        # 5. Squared error: diff_sq = diff ^ 2
        _h.make_node("Pow", ["diff", "pow_exp"], ["diff_sq"], name="pow_2"),

        # 6. Mean over feature dimension → MSE per sample shape (batch,)
        #    Using opset-18 style: axes is an input tensor
        _h.make_node("ReduceMean", ["diff_sq", "reduce_axes"], ["mse"],
                     name="reduce_mean_features", keepdims=0),

        # 7. Sigmoid to map MSE → anomaly_score in (0, 1)
        _h.make_node("Sigmoid", ["mse"], ["anomaly_score"], name="sigmoid"),
    ]

    # ------------------------------------------------------------------
    # Build graph and model
    # ------------------------------------------------------------------
    input_def = _h.make_tensor_value_info("input", TensorProto.FLOAT, [None, feature_dim])
    output_def = _h.make_tensor_value_info("anomaly_score", TensorProto.FLOAT, [None])

    graph = _h.make_graph(
        nodes,
        "vibration_autoencoder_v1",
        [input_def],
        [output_def],
        initializer=initializers,
    )

    model_proto = _h.make_model(
        graph,
        opset_imports=[_h.make_opsetid("", 18)],
    )
    model_proto.doc_string = (
        "OilOps vibration anomaly autoencoder v1. "
        "PCA reconstruction-based anomaly detection. "
        "Output 'anomaly_score' is sigmoid(MSE) in (0,1). "
        "ADVISORY: evaluation purposes only, NOT safety-rated."
    )
    model_proto.model_version = 1

    # ------------------------------------------------------------------
    # Validate graph with onnx checker
    # ------------------------------------------------------------------
    onnx.checker.check_model(model_proto)
    _logger.info("ONNX graph validation passed.")

    # ------------------------------------------------------------------
    # Write to disk
    # ------------------------------------------------------------------
    output_path.write_bytes(model_proto.SerializeToString())
    _logger.info("ONNX model saved to %s  (%d bytes)", output_path, output_path.stat().st_size)


# ===========================================================================
# ONNX model verification — run a smoke test after export
# ===========================================================================

def _verify_onnx(onnx_path: Path, feature_dim: int) -> None:
    """
    Load the exported ONNX model with onnxruntime and run a single forward
    pass to confirm the artifact is valid and the output shape is correct.

    Raises
    ------
    RuntimeError : if onnxruntime is unavailable or validation fails.
    AssertionError : if output shape is not (1,).
    """
    try:
        import onnxruntime as _ort
    except ImportError as exc:
        raise RuntimeError(
            "The 'onnxruntime' package is required for ONNX verification. "
            "Install it with: pip install onnxruntime"
        ) from exc

    opts = _ort.SessionOptions()
    opts.log_severity_level = 3  # ERROR only

    sess = _ort.InferenceSession(str(onnx_path), sess_options=opts)
    input_name = sess.get_inputs()[0].name
    output_name = sess.get_outputs()[0].name

    dummy = np.random.randn(1, feature_dim).astype(np.float32)
    result = sess.run(None, {input_name: dummy})

    score = result[0]
    assert score.shape == (1,), (
        f"Expected anomaly_score shape (1,), got {score.shape}. "
        "ONNX contract violation."
    )
    score_val = float(score[0])
    assert 0.0 <= score_val <= 1.0, (
        f"Expected sigmoid output in [0,1], got {score_val}."
    )
    _logger.info(
        "ONNX verification OK — input_name=%r, output_name=%r, "
        "test_score=%.6f",
        input_name,
        output_name,
        float(score[0]),
    )


# ===========================================================================
# Model loading (inference interface)
# ===========================================================================

class _ModelWrapper:
    """
    Unified model wrapper that exposes a ``score(vector) -> float`` method
    regardless of whether the underlying artifact is ONNX or the in-memory
    _PCAAutoencoder instance (used in tests when the pipeline runs in-process).
    """

    def __init__(self, inner: Any, feature_dim: int) -> None:
        self._inner = inner
        self._feature_dim = feature_dim

    def score(self, x: np.ndarray) -> float:
        """
        Return anomaly score in [0.0, 1.0] for a single feature vector.
        """
        x32 = np.asarray(x, dtype=np.float32).reshape(1, self._feature_dim)
        input_name = self._inner.get_inputs()[0].name
        result = self._inner.run(None, {input_name: x32})
        return float(np.clip(result[0][0], 0.0, 1.0))


def load_model(models_dir: str = str(_DEFAULT_MODELS_DIR)) -> _ModelWrapper:
    """
    Load the trained ONNX model from ``models_dir``.

    Parameters
    ----------
    models_dir : directory containing ``vibration_autoencoder_v1.onnx``.

    Returns
    -------
    _ModelWrapper with ``score(vector) -> float`` method.

    Raises
    ------
    FileNotFoundError : if no ONNX artifact is found.
    """
    try:
        import onnxruntime as _ort
    except ImportError as exc:
        raise RuntimeError(
            "The 'onnxruntime' package is required. "
            "Install it with: pip install onnxruntime"
        ) from exc

    mdir = Path(models_dir)
    onnx_path = mdir / f"{_MODEL_FILENAME}.onnx"

    if not onnx_path.exists():
        raise FileNotFoundError(
            f"ONNX model not found: {onnx_path}. "
            "Run train_vibration_autoencoder.py first."
        )

    opts = _ort.SessionOptions()
    opts.log_severity_level = 3

    session = _ort.InferenceSession(str(onnx_path), sess_options=opts)

    # Determine feature_dim from the model's input shape
    input_info = session.get_inputs()[0]
    feature_dim = input_info.shape[1] if len(input_info.shape) > 1 else FEATURE_DIM

    _logger.info("ONNX model loaded from %s (feature_dim=%d)", onnx_path, feature_dim)
    return _ModelWrapper(session, feature_dim=feature_dim)


# ===========================================================================
# Metrics computation
# ===========================================================================

def compute_metrics(
    model: _PCAAutoencoder,
    X_test: np.ndarray,
    y_test: np.ndarray,
) -> Dict[str, float]:
    """
    Compute precision, recall and f1 on the hold-out test set.

    Returns
    -------
    dict with keys: ``precision``, ``recall``, ``f1``.
    """
    y_pred = model.predict(X_test)

    precision = float(precision_score(y_test, y_pred, zero_division=0))
    recall = float(recall_score(y_test, y_pred, zero_division=0))
    f1 = float(f1_score(y_test, y_pred, zero_division=0))

    return {"precision": precision, "recall": recall, "f1": f1}


# ===========================================================================
# Main pipeline
# ===========================================================================

def run_pipeline(
    datasets_dir: Optional[str] = str(_DEFAULT_DATASETS_DIR),
    models_dir: str = str(_DEFAULT_MODELS_DIR),
    fft_bins: int = _N_FFT_BINS,
    test_split: float = 0.2,
    seed: int = _SEED,
) -> Dict[str, Any]:
    """
    Full end-to-end training pipeline.

    Steps
    -----
    1. Load (or generate) the labelled vibration dataset.
    2. Split into train / test sets (stratified by label).
    3. Train PCAAutoencoder on normal samples only (semi-supervised).
    4. Evaluate on the full test set.
    5. Export model to ONNX (using onnx.helper — no skl2onnx required).
    6. Verify the exported ONNX artifact with onnxruntime.
    7. Save metrics JSON.

    Parameters
    ----------
    datasets_dir:
        Path to directory containing CSV files.  Pass ``None`` to force
        synthetic data generation (used in tests).
    models_dir:
        Output directory for model artifacts and metrics JSON.
    fft_bins:
        FFT bins to extract per window.
    test_split:
        Fraction of data reserved for evaluation.
    seed:
        Random seed for reproducibility.

    Returns
    -------
    dict with keys: ``precision``, ``recall``, ``f1``,
    ``model_path``, ``metrics_path``.
    """
    t_start = time.time()
    _logger.info("=== OilOps Vibration Autoencoder Training Pipeline ===")
    _logger.info("ADVISORY: This model is for evaluation purposes only.")

    # ------------------------------------------------------------------
    # 1. Load data
    # ------------------------------------------------------------------
    X, y, dataset_name = load_dataset(datasets_dir=datasets_dir, fft_bins=fft_bins)
    _logger.info(
        "Dataset loaded: %d samples, %d features. Source: %s",
        X.shape[0],
        X.shape[1],
        dataset_name,
    )

    # ------------------------------------------------------------------
    # 2. Stratified train/test split
    # ------------------------------------------------------------------
    from sklearn.model_selection import StratifiedShuffleSplit

    splitter = StratifiedShuffleSplit(
        n_splits=1, test_size=test_split, random_state=seed
    )
    train_idx, test_idx = next(splitter.split(X, y))

    X_train, y_train = X[train_idx], y[train_idx]
    X_test, y_test = X[test_idx], y[test_idx]

    # Semi-supervised: train on normal samples only
    X_train_normal = X_train[y_train == 0]
    _logger.info(
        "Training split: %d total, %d normal for fitting. Test: %d samples.",
        len(train_idx),
        len(X_train_normal),
        len(test_idx),
    )

    # ------------------------------------------------------------------
    # 3. Train autoencoder
    # ------------------------------------------------------------------
    n_components = min(_LATENT_DIM_INNER, X_train_normal.shape[1], X_train_normal.shape[0] - 1)
    n_components = max(1, n_components)

    autoencoder = _PCAAutoencoder(n_components=n_components)
    autoencoder.fit(X_train_normal)

    # ------------------------------------------------------------------
    # 4. Evaluate on test set
    # ------------------------------------------------------------------
    metrics = compute_metrics(autoencoder, X_test, y_test)
    _logger.info(
        "Evaluation — precision=%.4f  recall=%.4f  f1=%.4f",
        metrics["precision"],
        metrics["recall"],
        metrics["f1"],
    )

    # ------------------------------------------------------------------
    # 5. Export model to ONNX
    # ------------------------------------------------------------------
    out_dir = Path(models_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    onnx_path = out_dir / f"{_MODEL_FILENAME}.onnx"
    feature_dim = int(X.shape[1])

    _export_onnx(autoencoder, onnx_path, feature_dim=feature_dim)

    # ------------------------------------------------------------------
    # 6. Verify the ONNX artifact
    # ------------------------------------------------------------------
    _verify_onnx(onnx_path, feature_dim=feature_dim)

    model_path = str(onnx_path)

    # ------------------------------------------------------------------
    # 7. Save metrics JSON
    # ------------------------------------------------------------------
    metrics_full = {
        **metrics,
        "anomaly_threshold": autoencoder._threshold,
        "n_components": n_components,
        "feature_dim": feature_dim,
        "n_train_normal": len(X_train_normal),
        "n_test": len(X_test),
        # dataset reflects the actual data source: "cwru_sample" if CSV files
        # were loaded from datasets_dir, "synthetic" if generated programmatically.
        "dataset": dataset_name,
        "model_id": "vibration-autoencoder-v1",
        "version": "1.0.0",
        "asset_class": "rotating_equipment",
        "advisory": "This model is for evaluation purposes only. NOT safety-rated.",
    }

    metrics_path = out_dir / _METRICS_FILENAME
    metrics_path.write_text(
        json.dumps(metrics_full, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    _logger.info("Metrics saved to %s", metrics_path)

    elapsed = time.time() - t_start
    _logger.info("Pipeline complete in %.2f seconds.", elapsed)

    return {
        **metrics,
        "model_path": model_path,
        "metrics_path": str(metrics_path),
    }


# ===========================================================================
# CLI entry point
# ===========================================================================

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Train the OilOps vibration anomaly autoencoder.\n\n"
            "ADVISORY: This model is for evaluation purposes only. "
            "It is NOT a safety-rated system."
        )
    )
    parser.add_argument(
        "--datasets-dir",
        default=str(_DEFAULT_DATASETS_DIR),
        help=(
            "Path to directory containing CWRU-style CSV files. "
            "If empty or not found, synthetic data is generated."
        ),
    )
    parser.add_argument(
        "--models-dir",
        default=str(_DEFAULT_MODELS_DIR),
        help="Output directory for model artifacts and metrics JSON.",
    )
    parser.add_argument(
        "--fft-bins",
        type=int,
        default=_N_FFT_BINS,
        help=f"Number of FFT bins per window (default: {_N_FFT_BINS}).",
    )
    parser.add_argument(
        "--test-split",
        type=float,
        default=0.2,
        help="Fraction of data reserved for evaluation (default: 0.2).",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=_SEED,
        help=f"Random seed for reproducibility (default: {_SEED}).",
    )

    args = parser.parse_args()

    result = run_pipeline(
        datasets_dir=args.datasets_dir if Path(args.datasets_dir).is_dir() else None,
        models_dir=args.models_dir,
        fft_bins=args.fft_bins,
        test_split=args.test_split,
        seed=args.seed,
    )

    print(json.dumps(result, indent=2))
