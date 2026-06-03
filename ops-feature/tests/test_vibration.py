"""
ops-feature/tests/test_vibration.py
=====================================
Unit tests for VibrationFeatureExtractor (TASK-013).

Criterion (CAT-09 / TASK-013):
  - With a synthetic sinusoidal signal:
      * RMS, variance, kurtosis, skewness each return a float.
      * fft_bins returns a list of exactly 64 floats.
  - A window with 0 samples returns None and logs a warning (INIT-US-06-AC2 /
    CAT-09).
"""

from __future__ import annotations

import logging
import math

import numpy as np
import pytest

from app.extractors.vibration import VibrationFeatureExtractor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sine_wave(n: int = 256, freq: float = 50.0, sample_rate: float = 1000.0) -> np.ndarray:
    """Return a clean sinusoidal signal of *n* samples."""
    t = np.linspace(0.0, (n - 1) / sample_rate, n)
    return np.sin(2.0 * math.pi * freq * t).astype(np.float64)


# ---------------------------------------------------------------------------
# Tests: normal operation (sufficient samples)
# ---------------------------------------------------------------------------

class TestVibrationExtractorNormalSignal:
    """Verify scalar statistics and FFT output with a well-formed sine wave."""

    @pytest.fixture(autouse=True)
    def extractor(self) -> VibrationFeatureExtractor:
        self._extractor = VibrationFeatureExtractor()
        return self._extractor

    @pytest.fixture(autouse=True)
    def result(self, extractor: VibrationFeatureExtractor) -> dict:
        signal = _sine_wave(n=256)
        self._result = extractor.extract(signal)
        return self._result

    def test_extract_returns_dict_not_none(self):
        assert self._result is not None, "Expected a dict, got None"

    def test_rms_is_float(self):
        assert isinstance(self._result["rms"], float), (
            f"rms should be float, got {type(self._result['rms'])}"
        )

    def test_variance_is_float(self):
        assert isinstance(self._result["variance"], float), (
            f"variance should be float, got {type(self._result['variance'])}"
        )

    def test_kurtosis_is_float(self):
        assert isinstance(self._result["kurtosis"], float), (
            f"kurtosis should be float, got {type(self._result['kurtosis'])}"
        )

    def test_skewness_is_float(self):
        assert isinstance(self._result["skewness"], float), (
            f"skewness should be float, got {type(self._result['skewness'])}"
        )

    def test_fft_bins_is_list_of_64_floats(self):
        fft_bins = self._result["fft_bins"]
        assert isinstance(fft_bins, list), (
            f"fft_bins should be a list, got {type(fft_bins)}"
        )
        assert len(fft_bins) == 64, (
            f"fft_bins should have 64 elements, got {len(fft_bins)}"
        )
        for i, val in enumerate(fft_bins):
            assert isinstance(val, float), (
                f"fft_bins[{i}] should be float, got {type(val)}: {val!r}"
            )

    def test_rms_value_correct_for_sine(self):
        """RMS of a pure sine wave sin(x) is 1/sqrt(2) ≈ 0.7071."""
        expected_rms = 1.0 / math.sqrt(2.0)
        assert abs(self._result["rms"] - expected_rms) < 0.01, (
            f"RMS expected ~{expected_rms:.4f}, got {self._result['rms']:.4f}"
        )

    def test_fft_bins_are_non_negative(self):
        """FFT magnitude bins must be non-negative."""
        for i, val in enumerate(self._result["fft_bins"]):
            assert val >= 0.0, f"fft_bins[{i}] = {val} is negative"


# ---------------------------------------------------------------------------
# Tests: edge case — configurable fft_bins
# ---------------------------------------------------------------------------

class TestVibrationExtractorConfigurableFftBins:
    """Verify that fft_bins count follows the constructor parameter."""

    def test_custom_fft_bins_32(self):
        extractor = VibrationFeatureExtractor(fft_bins=32)
        signal = _sine_wave(n=256)
        result = extractor.extract(signal)
        assert result is not None
        assert len(result["fft_bins"]) == 32

    def test_custom_fft_bins_128(self):
        extractor = VibrationFeatureExtractor(fft_bins=128)
        signal = _sine_wave(n=512)
        result = extractor.extract(signal)
        assert result is not None
        assert len(result["fft_bins"]) == 128


# ---------------------------------------------------------------------------
# Tests: edge case — insufficient window (returns None + warning)
# ---------------------------------------------------------------------------

class TestVibrationExtractorInsufficientWindow:
    """INIT-US-06-AC2 / CAT-09: empty/small window must return None and log warning."""

    def test_empty_window_returns_none(self):
        extractor = VibrationFeatureExtractor()
        result = extractor.extract(np.array([], dtype=np.float64))
        assert result is None, "Expected None for empty window"

    def test_empty_window_logs_warning(self, caplog: pytest.LogCaptureFixture):
        extractor = VibrationFeatureExtractor()
        # shared.get_logger sets propagate=False; temporarily re-enable so that
        # pytest caplog (which injects a handler at the root level) can capture
        # the records emitted by the module-level logger.
        module_logger = logging.getLogger("app.extractors.vibration")
        original_propagate = module_logger.propagate
        module_logger.propagate = True
        try:
            with caplog.at_level(logging.WARNING, logger="app.extractors.vibration"):
                extractor.extract(np.array([], dtype=np.float64))
        finally:
            module_logger.propagate = original_propagate
        assert len(caplog.records) > 0, "Expected at least one log record"
        assert any(r.levelno == logging.WARNING for r in caplog.records), (
            "Expected a WARNING-level log record"
        )

    def test_window_below_min_size_returns_none(self):
        """Window with fewer samples than min_window_size must also return None."""
        extractor = VibrationFeatureExtractor(min_window_size=64)
        # 10 samples < 64 = min_window_size
        result = extractor.extract(_sine_wave(n=10))
        assert result is None, "Expected None when n_samples < min_window_size"

    def test_window_below_min_size_logs_warning(self, caplog: pytest.LogCaptureFixture):
        extractor = VibrationFeatureExtractor(min_window_size=64)
        module_logger = logging.getLogger("app.extractors.vibration")
        original_propagate = module_logger.propagate
        module_logger.propagate = True
        try:
            with caplog.at_level(logging.WARNING, logger="app.extractors.vibration"):
                extractor.extract(_sine_wave(n=10))
        finally:
            module_logger.propagate = original_propagate
        assert any(r.levelno == logging.WARNING for r in caplog.records), (
            "Expected a WARNING-level log record for undersized window"
        )

    def test_exactly_min_size_does_not_return_none(self):
        """Window with exactly min_window_size samples must succeed."""
        min_size = 32
        extractor = VibrationFeatureExtractor(min_window_size=min_size, fft_bins=16)
        result = extractor.extract(_sine_wave(n=min_size))
        assert result is not None, (
            "Window with exactly min_window_size samples should produce a result"
        )
