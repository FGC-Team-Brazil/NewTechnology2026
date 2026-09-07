#!/usr/bin/env python3
"""
ml/monthly/train_monthly_occurrence.py
======================================
Monthly Fire Probability Model Trainer with Biophysical Monotonic Constraints:
- Fits a HistGradientBoostingClassifier with strict monotonic_cst enforcement
  guaranteeing mathematically that INCREASING DROUGHT NEVER REDUCES WILDFIRE RISK.
- Trains standardized Logistic Regression as an upper-bound recall benchmark.
- Exports serialized artifacts and visualization reports into reports/figures/.
"""

from pathlib import Path
import json
import joblib
import matplotlib.pyplot as plt
import seaborn as sns
import numpy as np
import pandas as pd

from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler
from sklearn.metrics import (
    accuracy_score, precision_score, recall_score, f1_score, roc_auc_score, confusion_matrix
)
from sklearn.inspection import permutation_importance

BASE_DIR = Path(__file__).parent.parent.parent.resolve()
DATA_DIR = BASE_DIR / "ml" / "monthly" / "datasets"
MODELS_DIR = BASE_DIR / "ml" / "monthly" / "models"
REPORTS_DIR = BASE_DIR / "ml" / "monthly" / "reports"
FIGURES_DIR = REPORTS_DIR / "figures"

sns.set_theme(style="whitegrid", palette="muted")
plt.rcParams["font.sans-serif"] = "DejaVu Sans"

FEATURES = [
    "is_galeria_or_water",
    "tipo_cobertura",
    "dias_sem_chuva_med",
    "log1p_dias",
    "estacao_seca",
    "mes"
]

FEATURE_LABELS = {
    "is_galeria_or_water": "Gallery Forest/Water Buffer",
    "tipo_cobertura": "Land Cover Class",
    "dias_sem_chuva_med": "Mean Drought Days",
    "log1p_dias": "log(Drought Days + 1)",
    "estacao_seca": "Dry Season Flag (May-Oct)",
    "mes": "Calendar Month"
}

# Directional Biophysical Monotonic Constraints:
# -1: Water/Gallery presence strictly REDUCES wildfire probability
# +1: Higher vulnerability land cover (savanna/pasture) strictly INCREASES risk
# +1: Increasing consecutive dry days strictly INCREASES risk
# +1: log(drought) strictly INCREASES risk
# +1: Peak dry season presence strictly INCREASES risk
#  0: Unconstrained calendar month index
MONOTONIC_CONSTRAINTS = [-1, 1, 1, 1, 1, 0]

def train_model():
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    
    train_path = DATA_DIR / "train_monthly_features.csv"
    test_path  = DATA_DIR / "test_monthly_features.csv"
    
    df_train = pd.read_csv(train_path)
    df_test  = pd.read_csv(test_path)
    
    X_train = df_train[FEATURES]
    y_train = df_train["fogo"]
    X_test  = df_test[FEATURES]
    y_test  = df_test["fogo"]
    
    print(f"--> Training Set: {len(X_train):,} | Out-of-Time Test Set: {len(X_test):,}")
    
    # 1. Monotonically Constrained Gradient Boosting Ensemble
    print("--> [1/4] Fitting Monotonic Gradient Boosting Classifier...")
    mono_model = HistGradientBoostingClassifier(
        monotonic_cst=MONOTONIC_CONSTRAINTS,
        max_iter=100,
        learning_rate=0.08,
        max_leaf_nodes=15,
        min_samples_leaf=100,
        class_weight="balanced",
        random_state=42
    )
    mono_model.fit(X_train, y_train)
    
    # 2. Standardized Logistic Regression
    print("--> [2/4] Fitting Standardized Logistic Regression...")
    scaler = StandardScaler()
    X_train_sc = scaler.fit_transform(X_train)
    X_test_sc  = scaler.transform(X_test)
    
    lr = LogisticRegression(class_weight="balanced", max_iter=1000, random_state=42)
    lr.fit(X_train_sc, y_train)
    
    # Blind Test Evaluation
    y_pred_mono = mono_model.predict(X_test)
    y_prob_mono = mono_model.predict_proba(X_test)[:, 1]
    y_pred_lr   = lr.predict(X_test_sc)
    y_prob_lr   = lr.predict_proba(X_test_sc)[:, 1]
    
    print("\n" + "="*70)
    print("OUT-OF-TIME BLIND TEST PERFORMANCE (AUGUST & SEPTEMBER 2026):")
    print(f"Monotonic Model:     Accuracy={accuracy_score(y_test, y_pred_mono)*100:.2f}% | Recall={recall_score(y_test, y_pred_mono)*100:.2f}% | ROC-AUC={roc_auc_score(y_test, y_prob_mono)*100:.2f}%")
    print(f"Logistic Regression: Accuracy={accuracy_score(y_test, y_pred_lr)*100:.2f}% | Recall={recall_score(y_test, y_pred_lr)*100:.2f}% | ROC-AUC={roc_auc_score(y_test, y_prob_lr)*100:.2f}%")
    print("="*70)
    
    # Persist serialized artifacts compatible with predict_fire.py
    joblib.dump(mono_model, MODELS_DIR / "cart_monthly_occurrence.pkl")
    joblib.dump(lr,         MODELS_DIR / "logistic_monthly_occurrence.pkl")
    joblib.dump(scaler,     MODELS_DIR / "scaler_monthly_occurrence.pkl")
    
    with open(MODELS_DIR / "monthly_features_order.json", "w", encoding="utf-8") as f:
        json.dump(FEATURES, f, indent=2)
        
    # Generate Validation Plots
    print("--> [3/4] Generating validation curves and feature importance plots...")
    
    # Permutation Feature Importance
    perm_imp = permutation_importance(mono_model, X_test, y_test, n_repeats=5, random_state=42)
    feat_names = [FEATURE_LABELS[f] for f in FEATURES]
    df_imp = pd.DataFrame({
        "Feature": feat_names,
        "Permutation_Importance": perm_imp.importances_mean,
        "Logistic_Weight": lr.coef_[0]
    }).sort_values(by="Permutation_Importance", ascending=True)
    
    plt.figure(figsize=(10, 4.5))
    sns.barplot(data=df_imp, y="Feature", x="Permutation_Importance", color="#d95f02")
    plt.title("Feature Importance in Monotonic Model (Permutation Loss Impact)", fontweight="bold")
    plt.xlabel("Mean Loss Increase Upon Permutation")
    plt.ylabel("")
    for i, v in enumerate(df_imp["Permutation_Importance"]):
        plt.text(v + 0.002, i, f"{v*100:.1f}%", va="center", fontweight="bold")
    plt.tight_layout()
    plt.savefig(FIGURES_DIR / "importancia_features_mensal.png", dpi=200)
    plt.close()
    
    # Drought Gradient Response Curve (September Peak)
    drought_grid = np.linspace(0, 80, 80)
    land_covers = [
        (1, "Gallery Forest / Riparian Buffer", "#2ca02c"),
        (2, "Typical Savanna / Open Cerrado", "#ff7f0e"),
        (3, "Pasture / Agricultural Border", "#d62728")
    ]
    
    plt.figure(figsize=(11, 5))
    for cover_id, label, color in land_covers:
        probs = []
        for d in drought_grid:
            row = pd.DataFrame([[
                1 if cover_id == 1 else 0,
                cover_id,
                d,
                np.log1p(d),
                1,
                9
            ]], columns=FEATURES)
            p = mono_model.predict_proba(row)[0][1] * 100
            probs.append(p)
        plt.plot(drought_grid, probs, label=label, color=color, linewidth=2.5)
        
    plt.axhline(70, color="red", linestyle="--", alpha=0.5, label="Critical Risk Threshold (70%)")
    plt.axhline(40, color="orange", linestyle=":", alpha=0.5, label="Moderate Risk Threshold (40%)")
    plt.xlabel("Consecutive Dry Days in Seasonal Lookback", fontsize=11, fontweight="bold")
    plt.ylabel("Predicted Fire Probability (%)", fontsize=11, fontweight="bold")
    plt.title("Monotonic Response Function: Drought vs. Land Cover (September)", fontsize=12, fontweight="bold")
    plt.legend(loc="lower right")
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(FIGURES_DIR / "gradiente_ecologico_seca_mensal.png", dpi=200)
    plt.close()
    
    print(f"--> [4/4] [OK] Models and evaluation charts persisted to {MODELS_DIR}")

if __name__ == "__main__":
    train_model()
