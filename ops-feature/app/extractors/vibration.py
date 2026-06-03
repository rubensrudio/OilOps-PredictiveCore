"""
ops-feature/app/extractors/vibration.py
=========================================
VibrationFeatureExtractor — NumPy/SciPy feature extraction for vibration
signals (TASK-013 / INIT-US-06 / CAT-09).

Features computed per window
-----------------------------
- **rms**       — Root Mean Square of the signal amplitude.
- **variance**  — Statistical variance (ddof=0, population variance).
- **kurtosis**  — Fisher kurtosis (excess kurtosis; normal ≈ 0).
- **skewness**  — Fisher skewness (symmetric ≈ 0).
- **fft_bins**  — Magnitude spectrum: ``fft_bins`` bins from the one-sided
                  FFT magnitude (normalised by N).

Design decisions
----------------
- The extractor is **pure** (no I/O, no side-effects beyond logging).
- ``fft_bins`` is configurable; the default (64) matches the reference
  autoencoder input dimension (OILOPS_FFT_BINS default).
- ``min_window_size`` guards against degenerate windows (INIT-US-06-AC2).
- When the window is too small the extractor returns ``None`` and logs a
  WARNING so the caller (WindoingPipeline) can skip the record without
  crashing (CAT-09).

Usage example
-------------
>>> import numpy as np
>>> from ops_feature.app.extractors.vibration import VibrationFeatureExtractor
>>> ext = VibrationFeatureExtractor()
>>> result = ext.extract(np.random.randn(256))
>>> assert result is not None
>>> assert len(result["fft_bins"]) == 64
"""

from __future__ import annotations

from typing import Optional

import numpy as np
from scipy.stats import kurtosis as scipy_kurtosis
from scipy.stats import skew as scipy_skew

from shared.logging_config import get_logger

_logger = get_logger("app.extractors.vibration")

# Default values mirror OILOPS_* environment variable defaults defined in
# shared/config.py so the extractor can be instantiated without loading
# Settings() (keeps it decoupled and easily unit-testable).
_DEFAULT_FFT_BINS: int = 64
_DEFAULT_MIN_WINDOW_SIZE: int = 64


class VibrationFeatureExtractor:
    """
    Extract time-domain and frequency-domain features from a 1-D vibration
    signal window.

    Parameters
    ----------
    fft_bins:
        Number of frequency-domain bins to include in ``result["fft_bins"]``.
        Defaults to 64 (matches the reference autoencoder input dimension,
        ``OILOPS_FFT_BINS`` env-var default).
    min_window_size:
        Minimum number of samples required to produce a feature record.
        Windows shorter than this are rejected: ``extract()`` returns ``None``
        and logs a WARNING (INIT-US-06-AC2 / CAT-09).
        Defaults to 64 (``OILOPS_FEATURE_WINDOW_SIZE`` env-var default).
    """

    def __init__(
        self,
        fft_bins: int = _DEFAULT_FFT_BINS,
        min_window_size: int = _DEFAULT_MIN_WINDOW_SIZE,
    ) -> None:
        if fft_bins < 1:
            raise ValueError(f"fft_bins must be >= 1, got {fft_bins}")
        if min_window_size < 1:
            raise ValueError(f"min_window_size must be >= 1, got {min_window_size}")

        self._fft_bins = fft_bins
        self._min_window_size = min_window_size

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def extract(self, window: np.ndarray) -> Optional[dict]:
        """
        Compute features for a vibration signal window.

        Parameters
        ----------
        window:
            1-D NumPy array of float64 amplitude samples.

        Returns
        -------
        dict or None
            On success, returns a dict with keys:
            ``rms``, ``variance``, ``kurtosis``, ``skewness``, ``fft_bins``.
            All scalar values are Python ``float``; ``fft_bins`` is a
            ``list[float]`` of length ``self._fft_bins``.

            Returns ``None`` when ``len(window) < min_window_size``, after
            logging a WARNING.
        """
        n_samples = len(window)

        if n_samples < self._min_window_size:
            _logger.warning(
                "Window too small to compute features — skipping.",
                extra={
                    "n_samples": n_samples,
                    "min_window_size": self._min_window_size,
                },
            )
            return None

        # ----------------------------------------------------------------
        # Ensure float64 for numerical stability
        # ----------------------------------------------------------------
        signal = np.asarray(window, dtype=np.float64)

        # ----------------------------------------------------------------
        # Time-domain statistics
        # ----------------------------------------------------------------
        rms: float = float(np.sqrt(np.mean(signal ** 2)))
        variance: float = float(np.var(signal, ddof=0))

        # scipy kurtosis / skew: fisher=True gives excess kurtosis (normal=0),
        # bias=True is the standard biased estimator consistent with numpy.
        kurtosis: float = float(scipy_kurtosis(signal, fisher=True, bias=True))
        skewness: float = float(scipy_skew(signal, bias=True))

        # ----------------------------------------------------------------
        # Frequency-domain: one-sided FFT magnitude spectrum
        # ----------------------------------------------------------------
        fft_bins: list[float] = self._compute_fft_bins(signal)

        return {
            "rms": rms,
            "variance": variance,
            "kurtosis": kurtosis,
            "skewness": skewness,
            "fft_bins": fft_bins,
        }

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _compute_fft_bins(self, signal: np.ndarray) -> list[float]:
        """
        Compute the one-sided FFT magnitude spectrum and return the first
        ``self._fft_bins`` bins as a Python list of floats.

        The magnitude is normalised by N (number of samples) so that the
        amplitude is independent of window size:
            magnitude[k] = |FFT[k]| / N

        If the FFT yields fewer bins than ``self._fft_bins`` (signal shorter
        than ``2 * fft_bins``), the list is zero-padded to the requested
        length.
        """
        n = len(signal)
        # numpy.fft.rfft returns the positive-frequency half (n//2 + 1 bins)
        fft_complex = np.fft.rfft(signal)
        # Normalised magnitude
        magnitude = np.abs(fft_complex) / n  # shape: (n//2 + 1,)

        available_bins = len(magnitude)
        if available_bins >= self._fft_bins:
            bins = magnitude[: self._fft_bins]
        else:
            # Zero-pad if the signal is too short to produce enough bins
            padding = np.zeros(self._fft_bins - available_bins, dtype=np.float64)
            bins = np.concatenate([magnitude, padding])

        return [float(v) for v in bins]
