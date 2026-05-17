"""
ops-explain/app/shap_explainer.py
===================================
SHAP-based feature attribution engine for the ops-explain service (TASK-019).

Design decision — KernelExplainer vs. DeepExplainer
----------------------------------------------------
The task specification references ``shap.DeepExplainer`` (DA-03), which
requires a live PyTorch/TensorFlow model object in memory.  In Phase 1,
``ops-explain`` works with ONNX artefacts served via ``onnxruntime``
(``OnnxRunner``).  ONNX Runtime does not expose the in-memory graph
objects required by ``DeepExplainer``.

Decision: use ``shap.KernelExplainer`` with a callable wrapper around
``OnnxRunner.run``.  ``KernelExplainer`` is model-agnostic (treats the
model as a black box) and is compatible with any callable that maps
``numpy.ndarray → numpy.ndarray``.  This is the correct fallback for
Phase 1 (ONNX-only serving).

Trade-off: ``KernelExplainer`` is slower than ``DeepExplainer`` for
large feature vectors; however, the feature vector for Phase 1 is small
(4 statistical features + up to 64 FFT bins, typically 68 dimensions),
which keeps computation manageable.  Phase 2 can switch to
``DeepExplainer`` if a PyTorch/TF model is introduced alongside the ONNX
artefact.

Baseline window
---------------
``SHAPExplainer.explain`` accepts an optional ``background_data``
argument.  When provided (numpy array of shape [n_background, n_features]),
it is used directly as the SHAP background distribution.  When omitted,
a small zero-baseline is constructed automatically.  The caller
(``BackgroundTaskManager``) is responsible for fetching historical
feature vectors from ``ops-store`` and passing them here if a richer
baseline is desired.

The ``explain`` method also computes ``baseline_window`` statistics
(mean, std, p5, p95) from whichever background data is used.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

import numpy as np

logger = logging.getLogger(__name__)


def make_onnx_predict_fn(runner: Any) -> Callable[[np.ndarray], np.ndarray]:
    """Create a ``shap.KernelExplainer``-compatible wrapper around an ``OnnxRunner``.

    ``shap.KernelExplainer`` calls its ``model`` callable with a 2-D
    ``numpy.ndarray`` of shape ``(n_samples, n_features)`` and expects a 1-D
    ``numpy.ndarray`` of shape ``(n_samples,)`` in return.

    ``OnnxRunner.run`` accepts a 1-D sequence of floats and returns a dict
    ``{"anomaly_score": float, "confidence_score": float}``.  A naïve
    ``lambda x: runner.run(x)`` would (a) pass the entire 2-D matrix to
    ``run``, which expects 1-D input, and (b) return a ``dict`` that SHAP
    would coerce to a 0-D scalar — both are incorrect.

    This factory iterates over rows of the input matrix, calls ``runner.run``
    per row, extracts ``anomaly_score``, and returns the results as a 1-D
    ``float32`` array.

    Parameters
    ----------
    runner:
        An ``OnnxRunner`` instance (or any object exposing a
        ``run(features: list[float]) -> {"anomaly_score": float, ...}``
        interface).

    Returns
    -------
    Callable[[np.ndarray], np.ndarray]
        A function suitable for use as the ``predict_fn`` argument of
        :class:`SHAPExplainer`.

    Example
    -------
    Typical usage wiring ``SHAPExplainer`` with an ``OnnxRunner``::

        from ops_models.app.serving.onnx_runner import OnnxRunner
        from ops_explain.app.shap_explainer import SHAPExplainer, make_onnx_predict_fn

        runner = OnnxRunner("path/to/model.onnx")
        predict_fn = make_onnx_predict_fn(runner)

        explainer = SHAPExplainer(
            predict_fn=predict_fn,
            feature_names=["rms", "variance", "kurtosis", "skewness"],
            top_n=5,
        )
        result = explainer.explain([0.12, 0.03, 1.8, 0.5])
    """

    def predict_fn(X: np.ndarray) -> np.ndarray:
        # X is 2-D: (n_samples, n_features)
        return np.array(
            [runner.run(row.tolist())["anomaly_score"] for row in X],
            dtype=np.float32,
        )

    return predict_fn


class SHAPExplainer:
    """Compute SHAP feature attributions for ONNX model predictions.

    Parameters
    ----------
    predict_fn:
        Callable that accepts a 2-D ``numpy.ndarray`` of shape
        ``[n_samples, n_features]`` and returns a 1-D array of scores.
        Typically a lambda wrapping ``OnnxRunner.run``.
    feature_names:
        List of feature name strings.  Must match the number of columns
        in the feature vectors passed to :meth:`explain`.
    top_n:
        Number of top features to return, ranked by absolute attribution
        magnitude.  Default: 5 (INIT-US-03-AC4).
    n_background:
        Number of background samples to synthesise when no explicit
        ``background_data`` is provided.  Default: 10.
    """

    def __init__(
        self,
        predict_fn: Any,
        feature_names: list[str],
        top_n: int = 5,
        n_background: int = 10,
    ) -> None:
        self._predict_fn = predict_fn
        self._feature_names = feature_names
        self._top_n = top_n
        self._n_background = n_background

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_background(
        self,
        n_features: int,
        background_data: np.ndarray | None,
    ) -> np.ndarray:
        """Return the background dataset for KernelExplainer.

        If ``background_data`` is provided and has the correct shape it is
        returned as-is.  Otherwise, a zero matrix of shape
        ``(n_background, n_features)`` is used.  This keeps the fallback
        deterministic and reproducible.
        """
        if background_data is not None:
            if background_data.ndim == 2 and background_data.shape[1] == n_features:
                return background_data.astype(np.float32)
            logger.warning(
                "background_data shape %s does not match n_features=%d; "
                "falling back to zero baseline.",
                background_data.shape,
                n_features,
            )
        return np.zeros((self._n_background, n_features), dtype=np.float32)

    @staticmethod
    def _compute_baseline_stats(
        background: np.ndarray,
        feature_names: list[str],
    ) -> dict[str, Any]:
        """Compute per-feature statistics from the background distribution.

        Returns
        -------
        dict
            ``{feature_name: {"mean": f, "std": f, "p5": f, "p95": f}, ...}``
        """
        stats: dict[str, Any] = {}
        for i, name in enumerate(feature_names):
            col = background[:, i].astype(float)
            stats[name] = {
                "mean": float(np.mean(col)),
                "std": float(np.std(col)),
                "p5": float(np.percentile(col, 5)),
                "p95": float(np.percentile(col, 95)),
            }
        return stats

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def explain(
        self,
        features: list[float] | np.ndarray,
        background_data: np.ndarray | None = None,
    ) -> dict[str, Any]:
        """Compute SHAP attributions for a single feature vector.

        Uses ``shap.KernelExplainer`` (model-agnostic, works with ONNX via
        callable wrapper).  Returns the top-N features sorted by
        attribution magnitude descending (rank 1 = highest absolute value).

        Parameters
        ----------
        features:
            1-D sequence of float feature values for a single instance.
        background_data:
            Optional 2-D numpy array ``[n_samples, n_features]`` to use as
            the SHAP background distribution.  If ``None``, a zero baseline
            is used.

        Returns
        -------
        dict with keys:
            - ``"feature_attributions"``: list of dicts with
              ``feature_name``, ``attribution_value``, ``rank``.
              Length >= ``top_n`` (all features when fewer features than
              ``top_n`` are available).
            - ``"baseline_window"``: dict with ``stats_per_feature`` (mean,
              std, p5, p95 per feature).
            - ``"method"``: ``"shap_kernel"``

        Raises
        ------
        ValueError
            If ``features`` length does not match ``len(self._feature_names)``.

        Notes
        -----
        **Using with OnnxRunner:** Do NOT pass a naïve ``lambda x: runner.run(x)``
        as ``predict_fn``.  ``KernelExplainer`` calls ``predict_fn`` with a 2-D
        array ``(n_samples, n_features)`` and expects a 1-D array ``(n_samples,)``
        back; ``OnnxRunner.run`` expects a 1-D input and returns a ``dict``, so a
        naïve wrapper produces incorrect shapes on both ends.  Use the factory
        :func:`make_onnx_predict_fn` instead::

            from ops_explain.app.shap_explainer import SHAPExplainer, make_onnx_predict_fn

            predict_fn = make_onnx_predict_fn(runner)   # runner is OnnxRunner
            explainer = SHAPExplainer(
                predict_fn=predict_fn,
                feature_names=feature_names,
                top_n=5,
            )
            result = explainer.explain(features)
        """
        import shap  # deferred import — not all callers need it at module load

        feature_array = np.array(features, dtype=np.float32)
        n_features = len(self._feature_names)

        if feature_array.shape[0] != n_features:
            raise ValueError(
                f"features length {feature_array.shape[0]} does not match "
                f"feature_names length {n_features}."
            )

        background = self._build_background(n_features, background_data)

        # KernelExplainer wraps any callable: (n_samples, n_features) → (n_samples,)
        explainer = shap.KernelExplainer(
            model=self._predict_fn,
            data=background,
            # Suppress verbose output from shap
            silent=True,
        )

        # Explain a single instance; shape: (1, n_features)
        instance = feature_array.reshape(1, -1)
        shap_values = explainer.shap_values(instance, silent=True)

        # ``shap_values`` is either ndarray (n_samples, n_features) or list
        # of such arrays for multi-output models.  Flatten to 1-D.
        if isinstance(shap_values, list):
            # Multi-output: use the first output (anomaly_score)
            raw_shap = np.array(shap_values[0]).flatten()
        else:
            raw_shap = np.array(shap_values).flatten()

        # Rank by absolute attribution magnitude, descending.
        abs_vals = np.abs(raw_shap)
        sorted_indices = np.argsort(abs_vals)[::-1]

        n_return = max(self._top_n, 1)  # always return at least 1
        # Cap at available features; task spec requires >= top_n items but if
        # there are fewer features than top_n we return all of them.
        n_return = min(n_return, len(self._feature_names))

        feature_attributions = []
        for rank_idx, feat_idx in enumerate(sorted_indices[:n_return], start=1):
            feature_attributions.append(
                {
                    "feature_name": self._feature_names[feat_idx],
                    "attribution_value": float(raw_shap[feat_idx]),
                    "rank": rank_idx,
                }
            )

        # Compute baseline statistics from the background distribution.
        baseline_stats = self._compute_baseline_stats(background, self._feature_names)

        return {
            "method": "shap_kernel",
            "feature_attributions": feature_attributions,
            "baseline_window": {
                "stats_per_feature": baseline_stats,
            },
        }
