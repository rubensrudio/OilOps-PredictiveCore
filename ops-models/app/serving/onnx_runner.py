"""ONNX model runner for ops-models serving layer.

Wraps ``onnxruntime.InferenceSession`` and provides a clean ``run`` interface
that returns a normalised ``anomaly_score`` and ``confidence_score``, both
clamped to [0.0, 1.0].
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import onnxruntime as ort


class OnnxRunner:
    """Load and run inference on an ONNX model artefact.

    Parameters
    ----------
    model_path:
        Filesystem path to the ``.onnx`` model file.

    Raises
    ------
    FileNotFoundError
        If *model_path* does not exist.
    """

    def __init__(self, model_path: str | Path) -> None:
        model_path = Path(model_path)
        if not model_path.exists():
            raise FileNotFoundError(
                f"ONNX model artefact not found: {model_path}"
            )
        self._session = ort.InferenceSession(
            str(model_path),
            providers=["CPUExecutionProvider"],
        )
        # Cache input / output metadata resolved once at construction.
        self._input_name: str = self._session.get_inputs()[0].name
        self._output_names: list[str] = [
            o.name for o in self._session.get_outputs()
        ]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self, features: list[float] | np.ndarray) -> dict[str, float]:
        """Execute inference and return anomaly and confidence scores.

        Parameters
        ----------
        features:
            1-D sequence of float feature values.  Must be non-empty.

        Returns
        -------
        dict
            ``{"anomaly_score": float, "confidence_score": float}`` where
            both values are clamped to [0.0, 1.0].

        Raises
        ------
        ValueError
            If *features* is empty.

        Notes
        -----
        **Single-output fallback:** if the ONNX model exposes only one output
        tensor (``len(self._output_names) == 1``), the flattened tensor will
        contain a single scalar value.  In that case ``confidence_score`` is
        set equal to ``anomaly_score`` (i.e. ``flat[0]`` is used for both).
        This is implemented via the expression
        ``flat[1] if len(flat) > 1 else flat[0]``, so no code change is
        needed when swapping between single- and dual-output model artefacts.
        """
        if features is None or len(features) == 0:
            raise ValueError("features must be a non-empty sequence of floats")

        input_array = np.array(features, dtype=np.float32).reshape(1, -1)
        raw_outputs: list[np.ndarray] = self._session.run(
            self._output_names,
            {self._input_name: input_array},
        )

        # Flatten all outputs into a single 1-D array; take the first two
        # values as anomaly_score and confidence_score respectively.
        flat = np.concatenate([o.flatten() for o in raw_outputs])

        anomaly_score = float(np.clip(flat[0], 0.0, 1.0))
        confidence_score = float(np.clip(flat[1] if len(flat) > 1 else flat[0], 0.0, 1.0))

        return {
            "anomaly_score": anomaly_score,
            "confidence_score": confidence_score,
        }
