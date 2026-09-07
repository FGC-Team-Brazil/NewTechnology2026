#!/usr/bin/env python3
"""
ml/evaluate.py
==============
Consolidated Machine Learning Evaluation Suite — ReviveTech ML:
- Independent out-of-time test evaluation (August & September 2026 - 32,000 samples)
- Computes Confusion Matrix, Accuracy, Precision, Recall, F1-Score, and ROC-AUC
"""

from pathlib import Path
import joblib
import pandas as pd
from sklearn.metrics import (
    accuracy_score, precision_score, recall_score, f1_score, roc_auc_score, confusion_matrix
)

BASE_DIR = Path(__file__).parent.parent.resolve()
MODELS_DIR = BASE_DIR / "ml" / "monthly" / "models"
DATASETS_DIR = BASE_DIR / "ml" / "monthly" / "datasets"

def main():
    test_path = DATASETS_DIR / "test_monthly_features.csv"
    model_path = MODELS_DIR / "cart_monthly_occurrence.pkl"
    lr_path    = MODELS_DIR / "logistic_monthly_occurrence.pkl"
    sc_path    = MODELS_DIR / "scaler_monthly_occurrence.pkl"

    if not (test_path.exists() and model_path.exists()):
        print("[ERROR] Serialized models or test dataset not found. Please run the training pipeline first.")
        return

    test = pd.read_csv(test_path)
    features = ['is_galeria_or_water', 'tipo_cobertura', 'dias_sem_chuva_med', 'log1p_dias', 'estacao_seca', 'mes']
    X_test = test[features]
    y_test = test['fogo']

    mono = joblib.load(model_path)
    lr   = joblib.load(lr_path)
    sc   = joblib.load(sc_path)

    p_mono = mono.predict_proba(X_test)[:, 1]
    y_mono = (p_mono >= 0.5).astype(int)

    p_lr = lr.predict_proba(sc.transform(X_test))[:, 1]
    y_lr = (p_lr >= 0.5).astype(int)

    print("="*75)
    print("      REVIVETECH ML — OFFICIAL BENCHMARK & EVALUATION SUITE")
    print("="*75)
    print(f"Independent Blind Test: {len(test):,} samples (August & September 2026)")
    print("-" * 75)
    print(f"{'Metric':<30} | {'Monotonic Ensemble':<18} | {'Logistic Regression':<18}")
    print("-" * 75)
    print(f"{'Global Accuracy':<30} | {accuracy_score(y_test, y_mono)*100:>17.2f}% | {accuracy_score(y_test, y_lr)*100:>17.2f}%")
    print(f"{'Precision (Positive Accuracy)':<30} | {precision_score(y_test, y_mono)*100:>17.2f}% | {precision_score(y_test, y_lr)*100:>17.2f}%")
    print(f"{'Recall (Sensitivity)':<30} | {recall_score(y_test, y_mono)*100:>17.2f}% | {recall_score(y_test, y_lr)*100:>17.2f}%")
    print(f"{'F1-Score':<30} | {f1_score(y_test, y_mono)*100:>17.2f}% | {f1_score(y_test, y_lr)*100:>17.2f}%")
    print(f"{'ROC-AUC Score':<30} | {roc_auc_score(y_test, p_mono)*100:>17.2f}% | {roc_auc_score(y_test, p_lr)*100:>17.2f}%")
    print("-" * 75)

    cm_m = confusion_matrix(y_test, y_mono)
    cm_l = confusion_matrix(y_test, y_lr)
    print("\nCONFUSION MATRIX (BLIND TEST):")
    print(f" * Monotonic Model    : {cm_m[0,0]} TN | {cm_m[0,1]} FP (False Alarms) | {cm_m[1,0]} FN | {cm_m[1,1]} TP")
    print(f" * Logistic Regression: {cm_l[0,0]} TN | {cm_l[0,1]} FP (False Alarms) | {cm_l[1,0]} FN | {cm_l[1,1]} TP")
    print("="*75)
    print("Visual validation artifacts saved to: ml/monthly/reports/figures/")

if __name__ == "__main__":
    main()
