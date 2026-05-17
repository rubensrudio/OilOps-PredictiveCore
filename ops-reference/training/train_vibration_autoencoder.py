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
4. Export model: saves model in ONNX format if skl2onnx is available;
   otherwise serialises a pickle wrapper that exposes an
   ``InferenceSession``-compatible interface (``run()`` method).
5. Save metrics: writes vibration_autoencoder_v1_metrics.json with
   ``precision``, ``recall``, ``f1`` calculated on the hold-out set.

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
import pickle
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
        # TASK-013 placed the extractor in ops-feature/app/extractors/vibration.py
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
) -> Tuple[np.ndarray, np.ndarray]:
    """
    Load the vibration dataset.

    Tries to read CSVs from ``datasets_dir``; if none found or
    ``datasets_dir`` is None, generates synthetic data.

    Returns
    -------
    X : np.ndarray, shape (n_samples, FEATURE_DIM)
    y : np.ndarray, shape (n_samples,)
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
                return X, y
            _logger.warning(
                "No valid CSV data found in %s — falling back to synthetic dataset.",
                ddir,
            )

    _logger.info("Generating synthetic dataset (%d normal + %d anomalous).",
                 _N_NORMAL_SAMPLES, _N_ANOMALY_SAMPLES)
    return generate_synthetic_dataset(fft_bins=fft_bins)


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
    Score is the sigmoid-normalised mean squared reconstruction error,
    clipped to [0.0, 1.0].  Threshold for binary classification is derived
    from the ``_ANOMALY_PERCENTILE`` of training reconstruction errors.
    """

    def __init__(self, n_components: int = _LATENT_DIM_INNER) -> None:
        self._scaler = StandardScaler()
        self._pca = PCA(n_components=n_components, random_state=_SEED)
        self._threshold: float = 0.5
        self._max_error: float = 1.0  # used for sigmoid-like normalisation

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

        # Determine anomaly threshold: 95th percentile of training errors
        errors = self._reconstruction_errors(X_normal)
        self._threshold = float(np.percentile(errors, _ANOMALY_PERCENTILE))
        # Store maximum error from training for normalisation
        self._max_error = float(np.max(errors)) if len(errors) > 0 else 1.0
        if self._max_error == 0.0:
            self._max_error = 1.0

        _logger.info(
            "PCAAutoencoder fitted. Threshold=%.6f, max_error=%.6f",
            self._threshold,
            self._max_error,
        )
        return self

    # ------------------------------------------------------------------
    # Inference
    # ------------------------------------------------------------------

    def _reconstruction_error(self, x: np.ndarray) -> float:
        """Mean squared reconstruction error for a single sample."""
        x2d = x.reshape(1, -1)
        x_scaled = self._scaler.transform(x2d)
        x_latent = self._pca.transform(x_scaled)
        x_reconstructed = self._pca.inverse_transform(x_latent)
        return float(np.mean((x_scaled - x_reconstructed) ** 2))

    def _reconstruction_errors(self, X: np.ndarray) -> np.ndarray:
        """Vectorised reconstruction errors for a batch."""
        X_scaled = self._scaler.transform(X)
        X_latent = self._pca.transform(X_scaled)
        X_reconstructed = self._pca.inverse_transform(X_latent)
        return np.mean((X_scaled - X_reconstructed) ** 2, axis=1)

    def score(self, x: np.ndarray) -> float:
        """
        Return an anomaly score in [0.0, 1.0] for a single feature vector.

        The score is the reconstruction error normalised against the maximum
        training error, then clipped to [0.0, 1.0].  Higher scores indicate
        higher likelihood of anomaly.

        Parameters
        ----------
        x : np.ndarray of shape (FEATURE_DIM,)

        Returns
        -------
        float in [0.0, 1.0]
        """
        err = self._reconstruction_error(x)
        normalised = err / self._max_error
        return float(np.clip(normalised, 0.0, 1.0))

    def predict(self, X: np.ndarray) -> np.ndarray:
        """
        Binary anomaly prediction for a batch.

        Returns
        -------
        np.ndarray of int (0 = normal, 1 = anomaly), shape (n,).
        """
        errors = self._reconstruction_errors(X)
        return (errors > self._threshold).astype(np.int32)

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
        [np.ndarray]: list with one element — reconstruction errors,
                      shape (batch,), dtype float32.
        """
        x = next(iter(input_feed.values()))
        if x.ndim == 1:
            x = x.reshape(1, -1)
        errors = self._reconstruction_errors(x.astype(np.float64))
        # Normalise to [0, 1]
        scores = np.clip(errors / self._max_error, 0.0, 1.0).astype(np.float32)
        return [scores]


# ===========================================================================
# ONNX export
# ===========================================================================

def _try_export_onnx(
    model: _PCAAutoencoder,
    output_path: Path,
    feature_dim: int = FEATURE_DIM,
) -> bool:
    """
    Attempt to export the model to ONNX using skl2onnx.

    Returns True on success, False if skl2onnx is not available.
    """
    try:
        from skl2onnx import convert_sklearn
        from skl2onnx.common.data_types import FloatTensorType

        _logger.info("Exporting model to ONNX via skl2onnx...")

        # skl2onnx converts the PCA pipeline only (scaler + pca transform).
        # We wrap both into an sklearn Pipeline for export.
        from sklearn.pipeline import Pipeline as _SKPipeline

        pipe = _SKPipeline([
            ("scaler", model._scaler),
            ("pca", model._pca),
        ])

        initial_type = [("float_input", FloatTensorType([None, feature_dim]))]
        onnx_model = convert_sklearn(pipe, initial_types=initial_type)

        with open(output_path, "wb") as f:
            f.write(onnx_model.SerializeToString())

        _logger.info("ONNX model saved to %s", output_path)
        return True

    except ImportError:
        _logger.info(
            "skl2onnx not available — skipping ONNX export. "
            "Model will be saved as pickle with InferenceSession-compatible interface."
        )
        return False
    except Exception as exc:  # noqa: BLE001
        _logger.warning("ONNX export failed: %s — falling back to pickle.", exc)
        return False


def _save_pickle(model: _PCAAutoencoder, output_path: Path) -> None:
    """
    Persist the model as a pickle file.

    Serialises a plain dict of sklearn components rather than the
    ``_PCAAutoencoder`` class instance, which avoids the
    ``_pickle.PicklingError`` that arises when the module is loaded via
    ``importlib.util.spec_from_file_location`` (the loaded class has a
    different identity from the one seen by pickle).
    """
    payload = {
        "scaler": model._scaler,
        "pca": model._pca,
        "threshold": model._threshold,
        "max_error": model._max_error,
    }
    with open(output_path, "wb") as f:
        pickle.dump(payload, f, protocol=pickle.HIGHEST_PROTOCOL)
    _logger.info("Model saved as pickle: %s", output_path)


# ===========================================================================
# Model loading (inference interface)
# ===========================================================================

class _ModelWrapper:
    """
    Unified model wrapper that exposes a ``score(vector) -> float`` method
    regardless of whether the underlying artifact is ONNX or pickle.
    """

    def __init__(self, inner: Any, threshold: float, max_error: float) -> None:
        self._inner = inner
        self._threshold = threshold
        self._max_error = max_error

    def score(self, x: np.ndarray) -> float:
        """
        Return anomaly score in [0.0, 1.0] for a single feature vector.
        """
        x = np.asarray(x, dtype=np.float32).reshape(1, -1)
        result = self._inner.run(None, {"float_input": x})
        # result[0] is shape (1,) float32 for ONNX; or shape (1,) for pickle shim
        raw = float(result[0][0])
        return float(np.clip(raw, 0.0, 1.0))


def load_model(models_dir: str = str(_DEFAULT_MODELS_DIR)) -> _ModelWrapper:
    """
    Load the trained model from ``models_dir``.

    Prefers the ONNX artifact if present (``vibration_autoencoder_v1.onnx``);
    falls back to the pickle file (``vibration_autoencoder_v1.pkl``).

    Returns
    -------
    _ModelWrapper with ``score(vector) -> float`` method.
    """
    mdir = Path(models_dir)

    onnx_path = mdir / f"{_MODEL_FILENAME}.onnx"
    pkl_path = mdir / f"{_MODEL_FILENAME}.pkl"

    if onnx_path.exists():
        _logger.info("Loading ONNX model from %s", onnx_path)
        import onnxruntime as _ort

        # Suppress ONNX Runtime verbose logging
        sess_opts = _ort.SessionOptions()
        sess_opts.log_severity_level = 3  # ERROR only

        session = _ort.InferenceSession(str(onnx_path), sess_options=sess_opts)

        # ONNX session does not expose threshold/max_error — use defaults.
        # For the wrapper we need an object with .run() — use InferenceSession.
        # But InferenceSession input name may differ from "float_input".
        input_name = session.get_inputs()[0].name

        class _OnnxAdapter:
            """Adapts InferenceSession to the expected run() signature."""

            def __init__(self, sess: Any, inp_name: str) -> None:
                self._sess = sess
                self._inp_name = inp_name

            def run(
                self,
                output_names: Optional[List[str]],
                input_feed: Dict[str, np.ndarray],
            ) -> List[np.ndarray]:
                x = next(iter(input_feed.values()))
                return self._sess.run(output_names, {self._inp_name: x})

        # Load threshold and max_error from metrics JSON if available
        metrics_path = mdir / _METRICS_FILENAME
        threshold = 0.5
        max_error = 1.0
        if metrics_path.exists():
            meta = json.loads(metrics_path.read_text(encoding="utf-8"))
            threshold = float(meta.get("anomaly_threshold", 0.5))
            max_error = float(meta.get("max_reconstruction_error", 1.0))

        adapter = _OnnxAdapter(session, input_name)
        return _ModelWrapper(adapter, threshold=threshold, max_error=max_error)

    if pkl_path.exists():
        _logger.info("Loading pickle model from %s", pkl_path)
        with open(pkl_path, "rb") as f:
            payload = pickle.load(f)

        # Reconstruct a _PCAAutoencoder from the serialised dict payload
        # (avoids class-identity issues when loaded via importlib).
        autoenc = _PCAAutoencoder.__new__(_PCAAutoencoder)
        autoenc._scaler = payload["scaler"]
        autoenc._pca = payload["pca"]
        autoenc._threshold = payload["threshold"]
        autoenc._max_error = payload["max_error"]
        return _ModelWrapper(autoenc, threshold=autoenc._threshold, max_error=autoenc._max_error)

    raise FileNotFoundError(
        f"No model artifact found in {mdir}. "
        f"Expected: {onnx_path} or {pkl_path}. "
        "Run train_vibration_autoencoder.py first."
    )


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

    # Guard: if all predictions are the same class, sklearn metrics may warn.
    precision = float(
        precision_score(y_test, y_pred, zero_division=0)
    )
    recall = float(
        recall_score(y_test, y_pred, zero_division=0)
    )
    f1 = float(
        f1_score(y_test, y_pred, zero_division=0)
    )

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
    5. Export model (ONNX if skl2onnx available, else pickle).
    6. Save metrics JSON.

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
    X, y = load_dataset(datasets_dir=datasets_dir, fft_bins=fft_bins)
    _logger.info("Dataset loaded: %d samples, %d features.", X.shape[0], X.shape[1])

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
    # 5. Export model
    # ------------------------------------------------------------------
    out_dir = Path(models_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    onnx_path = out_dir / f"{_MODEL_FILENAME}.onnx"
    pkl_path = out_dir / f"{_MODEL_FILENAME}.pkl"

    exported_onnx = _try_export_onnx(autoencoder, onnx_path, feature_dim=X.shape[1])
    if not exported_onnx:
        _save_pickle(autoencoder, pkl_path)
        model_path = str(pkl_path)
    else:
        model_path = str(onnx_path)

    # ------------------------------------------------------------------
    # 6. Save metrics JSON
    # ------------------------------------------------------------------
    metrics_full = {
        **metrics,
        "anomaly_threshold": autoencoder._threshold,
        "max_reconstruction_error": autoencoder._max_error,
        "n_components": n_components,
        "feature_dim": X.shape[1],
        "n_train_normal": len(X_train_normal),
        "n_test": len(X_test),
        "dataset": "synthetic" if datasets_dir is None else "cwru_sample",
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
