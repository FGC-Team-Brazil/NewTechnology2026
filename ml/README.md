# ReviveTech — Machine Learning Module

This module houses the autonomous fire risk modeling and prediction infrastructure for the Brazilian Cerrado biome.

## Operational Overview

The system operates on an aggregated **monthly temporal resolution**, providing stable, season-aware fire probability estimates while filtering out high-frequency 24-hour meteorological noise.

### Core Architectural Components

1. **Biophysical Data Construction (`ml/monthly/build_monthly_dataset.py`)**:
   - Ingests official satellite fire records from INPE spanning 13 continuous calendar months (September 2025 to September 2026).
   - Combines active fire detections with synthetic ecological non-fire controls respecting seasonal moisture gradients.
   - Enriches records with 4 distinct land cover classes: Water Bodies, Gallery Forests (Riparian Buffers), Typical Savanna, and Agricultural Pastures.

2. **Monotonic Machine Learning Training (`ml/monthly/train_monthly_occurrence.py`)**:
   - Implements a `HistGradientBoostingClassifier` with **directional monotonic constraints** (`monotonic_cst`).
   - Guarantees mathematically that increases in cumulative drought days never produce counter-ecological risk drops.
   - Prevents artificial 100% leaf saturation via regularized tree depth and minimum sample leaf controls.
   - Evaluates performance against an out-of-time blind test dataset (August and September 2026).

3. **Inference Engine (`predict_fire.py`)**:
   - Accepts geographical coordinates and month/year parameters.
   - Queries historical weather reanalysis archives to assess cumulative seasonal dry days.
   - Interrogates OpenStreetMap via Overpass API to detect local drainage networks and gallery forest buffers (< 180m).
   - Generates structured console reports and updates analytical gauge diagrams in `ml/monthly/reports/figures/`.

## Directory Structure

```
ml/
├── evaluate.py                          # Official benchmark & metrics suite
├── README.md                            # Documentation and operational guide
├── datasets/
│   ├── .gitkeep
│   └── dataset_cerrado_clean.csv        # Benchmark reference dataset
└── monthly/
    ├── build_monthly_dataset.py         # Pipeline data builder with seasonal weighting
    ├── train_monthly_occurrence.py      # Monotonic ensemble model trainer
    ├── datasets/
    │   ├── train_monthly_features.csv   # Training dataset (84,100 records)
    │   ├── test_monthly_features.csv    # Blind test dataset (32,000 records)
    │   └── raw/                         # Monthly INPE source CSVs
    ├── models/
    │   ├── cart_monthly_occurrence.pkl  # Serialized monotonic model
    │   ├── logistic_monthly_occurrence.pkl
    │   ├── scaler_monthly_occurrence.pkl
    │   └── monthly_features_order.json
    └── reports/
        └── figures/
            ├── importancia_features_mensal.png
            ├── gradiente_ecologico_seca_mensal.png
            └── ultima_predicao_gauge.png
```

## Execution Commands

### Rebuilding Dataset and Retraining
```bash
python ml/monthly/build_monthly_dataset.py
python ml/monthly/train_monthly_occurrence.py
```

### Model Performance Evaluation
```bash
python ml/evaluate.py
```

### Running Fire Risk Predictions
```bash
python predict_fire.py <latitude> <longitude> --month <1-12> [--year <YYYY>]
```
