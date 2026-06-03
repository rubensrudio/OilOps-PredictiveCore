# Benchmarks & Model Metrics

> **Scope.** This document reports the measured performance of the reference
> models shipped in `ops-reference/`, and gives exact, reproducible steps to
> regenerate every number from source. It is intended for external reviewers
> who want to verify the figures rather than take them on trust.

> **Advisory notice (RN-06).** All models here are **evaluation-grade and
> advisory-only**. They are NOT safety-rated and the metrics below are
> **baselines on synthetic data**, not production performance on real plant
> telemetry. See [`NOTICE`](../NOTICE).

---

## 1. Reference model: `vibration-autoencoder-v1`

| Property | Value |
|---|---|
| Model ID | `vibration-autoencoder-v1` |
| Asset class | Rotating equipment (bearing vibration) |
| Architecture | PCA-based reconstruction autoencoder (StandardScaler → PCA(16) → inverse → sigmoid(MSE)) |
| Artifact | `ops-reference/models/vibration_autoencoder_v1.onnx` (Git LFS) |
| Runtime | ONNX Runtime (`ops-models`) |
| Feature dim | 68 (4 statistical: rms/mean/std + 64 FFT bins) |
| Training data | **synthetic** (CWRU-shaped; see §3) |
| Random seed | fixed (deterministic pipeline) |

### 1.1 Measured metrics

Source of truth: [`ops-reference/models/vibration_autoencoder_v1_metrics.json`](../ops-reference/models/vibration_autoencoder_v1_metrics.json)

| Metric | Value | Read as |
|---|---:|---|
| Recall | **1.00** | All injected anomalies in the hold-out set were caught. |
| Precision | **0.30** | ~3 in 10 alerts are true anomalies on the synthetic set; the rest are false positives. |
| F1 | **0.46** | Harmonic mean of the two. |
| Anomaly threshold | 0.6662 | Sigmoid(reconstruction-MSE) cutoff. |
| Latent components | 16 | PCA dimensionality. |
| Train (normal) | 128 windows | Trained on normal-only data (one-class setup). |
| Test set | 40 windows | Hold-out, mixed normal + anomalous. |

### 1.2 Honest interpretation

The model is deliberately tuned **recall-first**: in predictive maintenance a
missed bearing fault is far costlier than a false alarm a reliability engineer
can dismiss. Recall = 1.0 with precision = 0.30 reflects that bias.

This is a **synthetic-data baseline**, not a claim of field accuracy. Precision
of 0.30 is expected to move substantially — in either direction — on real CWRU
data and again on operator-specific distributions. The point of the reference
model is to remove the cold-start barrier and provide a re-trainable starting
point, not to ship turnkey field performance. Re-training on operator data is a
first-class workflow (see §2).

---

## 2. Reproducing the metrics

The training pipeline is fully deterministic (fixed seed) and requires **no
external data or TensorFlow** — it builds the ONNX graph directly and falls
back to synthetic data when no CSVs are present.

```bash
# From repo root
pip install -r ops-reference/requirements.txt
make train
# equivalently:
python ops-reference/training/train_vibration_autoencoder.py
```

This regenerates both artifacts:

- `ops-reference/models/vibration_autoencoder_v1.onnx`
- `ops-reference/models/vibration_autoencoder_v1_metrics.json`

Because the seed is fixed, a clean run reproduces the metrics in §1.1 exactly.

### 2.1 Training on real CWRU data (optional)

To replace synthetic data with the real Case Western Reserve University bearing
dataset, populate `ops-reference/datasets/cwru_sample/` per its
[README](../ops-reference/datasets/cwru_sample/README.md) and re-run `make train`.
The pipeline detects the CSVs, sets `"dataset": "cwru_sample"` in the metrics
file, and reports metrics on real data.

### 2.2 Verifying the ONNX contract

`ops-models` consumes the model via a fixed ONNX I/O contract:

```
Input  : input         float32 (batch, 68)
Output : anomaly_score  float32 (batch,)
```

The training script verifies this contract on export (`_verify_onnx`). The
`ops-models` test suite asserts it independently:

```bash
python -m pytest ops-models -v
```

---

## 3. Synthetic dataset characteristics

When no real CSVs are present, the pipeline generates a CWRU-shaped synthetic
set (see the dataset README for the full rationale):

| Class | Samples | Amplitude | Signature |
|---|---:|---|---|
| Normal | 160 | σ = 0.05 m/s² | Low-amplitude Gaussian noise |
| Anomaly | 40 | σ = 1.0 m/s² | High-amplitude + inner-race fault sinusoid (BPFI ≈ 157 Hz) |

---

## 4. Roadmapped models (not yet implemented)

Per the [PRD](PRD_OilOps-PredictiveCore.md), two further reference models are
planned for Phase 2 and are **not** part of v0.1.0:

- Centrifugal pump cavitation classifier (gradient-boosted)
- Pipeline pressure anomaly (sequence model)

These will be documented here with their own metrics when shipped.

---

## 5. Benchmark provenance

| Number | Where it comes from | How to verify |
|---|---|---|
| Precision / Recall / F1 | `vibration_autoencoder_v1_metrics.json` | `make train` regenerates it deterministically |
| Latency target (seconds, not ms) | PRD §7 design target | Not yet load-tested; flagged as roadmap |
| US $125k/hr downtime cost | ABB 2025 survey (PRD §11) | External citation, not a measurement of this system |

Figures sourced from external literature (ABB, DOE, McKinsey, World Bank,
US Census) are **context for the problem**, not benchmarks of this software.
They are cited in the [PRD](PRD_OilOps-PredictiveCore.md#11-references).
