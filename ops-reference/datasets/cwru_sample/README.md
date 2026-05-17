# CWRU Bearing Dataset — Sample Directory

## Overview

This directory is the designated location for CWRU (Case Western Reserve
University) Bearing Dataset samples used to train the reference vibration
anomaly autoencoder (`vibration_autoencoder_v1`).

## Dataset

**Source:** Case Western Reserve University Bearing Data Center
**URL:** <https://engineering.case.edu/bearingdatacenter>
**License:** Public domain / academic use — no redistribution restrictions.

## Why No CSV Files Are Committed

The raw CWRU `.mat` files and their CSV derivatives are **not committed** to
this repository for the following reasons:

1. **File size:** The full CWRU dataset is ~100 MB. Committing binary/CSV
   data would bloat the repository history.
2. **Git LFS policy:** Large binary artefacts (`.onnx`, `.mat`) are tracked
   via Git LFS per `.gitattributes`. CSVs derived from the dataset would
   follow the same policy.
3. **Reproducibility via synthetic data:** The training pipeline
   (`train_vibration_autoencoder.py`) detects the absence of real CSV files
   and automatically generates a statistically representative synthetic
   dataset (200 samples: 160 normal + 40 anomalous) for isolated training
   and testing without external dependencies.

## How to Populate This Directory (Optional)

To train on real CWRU data:

1. Download the bearing fault data files from the CWRU data center
   (`Normal_*.mat`, `12k_Drive_End_*.mat`).
2. Convert `.mat` files to CSV using the provided conversion script
   (see `ops-reference/training/convert_mat_to_csv.py` — Fase 2).
3. Place the resulting CSV files in this directory.

**Expected CSV format:**

```
label,s0,s1,...,s1023
0,0.0023,0.0045,...     # normal sample (1024 acceleration values)
1,0.2134,0.3421,...     # anomalous sample
```

- Column `label`: `0` = normal, `1` = anomaly (bearing fault).
- Columns `s0` through `s1023`: raw acceleration samples (m/s²) — 1024
  values per row (one time window).

## Synthetic Data (Default)

When this directory contains no CSV files, the pipeline generates synthetic
data with the following characteristics:

| Class    | Samples | Amplitude     | Notes                              |
|----------|---------|---------------|------------------------------------|
| Normal   | 160     | σ = 0.05 m/s² | Low-amplitude Gaussian noise       |
| Anomaly  | 40      | σ = 1.0 m/s²  | High-amplitude + fault sinusoid    |

The fault signature uses a typical inner-race fault frequency (BPFI ≈ 157 Hz)
as a sinusoidal component added to the noise floor, mimicking a real bearing
defect in the frequency domain.

## Advisory Notice

> **WARNING:** The reference model trained on this dataset is for evaluation
> and demonstration purposes only. It is NOT a safety-rated system and must
> NOT be used as a substitute for certified safety-instrumented functions or
> professional maintenance engineering judgement.
> All API responses carry the header `X-Advisory-Only: true`.
