#!/usr/bin/env python3
"""
predict_fire.py
===============
Monthly Fire Risk Prediction Engine — ReviveTech ML (Autonomous Inference)

Usage:
  python predict_fire.py <lat> <lon> --month <1-12> [--year <YYYY>]
  (Also supports --date YYYY-MM-DD for backward compatibility)

Core Features:
1. Monthly Aggregation: Robust seasonal stability, removing 24h transient rainfall noise.
2. Autonomous Environmental Mapping via OSM: Identifies Gallery Forest / Riparian Buffer vs. Open Savanna / Pasture.
3. Monotonically Constrained ML Models: Eliminates artificial saturation and prevents counter-ecological inversions.
4. Dynamic Visualization: Automatically updates analytical gauges and reports in figures/.
"""

import sys
import os
import argparse
from pathlib import Path
from datetime import datetime, timedelta
import json
import urllib.request
import joblib
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns

BASE_DIR = Path(__file__).parent.resolve()
MONTHLY_MODELS_DIR = BASE_DIR / "ml" / "monthly" / "models"
REPORTS_DIR = BASE_DIR / "ml" / "monthly" / "reports"
FIGURES_DIR = REPORTS_DIR / "figures"
RAW_DIR = BASE_DIR / "ml" / "monthly" / "datasets" / "raw"

geodata_dir = BASE_DIR / "geodata"
if str(geodata_dir) not in sys.path:
    sys.path.insert(0, str(geodata_dir))

try:
    from rtdata import collect_water_distance
except ImportError:
    collect_water_distance = None

sns.set_theme(style="whitegrid")
plt.rcParams["font.sans-serif"] = "DejaVu Sans"

MONTH_NAMES = {
    1: "January", 2: "February", 3: "March", 4: "April",
    5: "May", 6: "June", 7: "July", 8: "August",
    9: "September", 10: "October", 11: "November", 12: "December"
}

def calculate_monthly_weather(lat: float, lon: float, year: int, month: int) -> dict:
    """
    Computes representative drought indicators for the specified coordinates and month.
    Queries the Open-Meteo Reanalysis archive over a continuous 60-day preceding window.
    """
    try:
        # Use latest available historical baseline year if future dates are requested
        query_year = min(year, 2025) if year > 2025 else year
        
        # Cerrado drought is cumulative across the season.
        # Query a 60-day lookback window up to mid-month:
        dt_month = datetime(query_year, month, 15)
        start_date = (dt_month - timedelta(days=60)).strftime("%Y-%m-%d")
        end_date = dt_month.strftime("%Y-%m-%d")
            
        url = (
            f"https://archive-api.open-meteo.com/v1/archive?latitude={lat}&longitude={lon}"
            f"&start_date={start_date}&end_date={end_date}"
            f"&daily=precipitation_sum&timezone=auto"
        )
        req = urllib.request.Request(url, headers={"User-Agent": "ReviveTech-Fire/3.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            prec_list = data.get("daily", {}).get("precipitation_sum", [])
            
            # Measure longest consecutive dry sequence (< 1.0 mm)
            max_dry = 0
            cur_dry = 0
            for p in prec_list:
                if p is not None and p < 1.0:
                    cur_dry += 1
                    if cur_dry > max_dry:
                        max_dry = cur_dry
                else:
                    cur_dry = 0
                    
            calculated_drought = float(max_dry if max_dry > 0 else 10.0)
            
            return {
                "dias_sem_chuva_med": calculated_drought,
                "source": f"Reanalysis Meteorological Series ({MONTH_NAMES[month]}/{query_year})"
            }
    except Exception:
        # Cerrado benchmark seasonal climatology fallback
        if month in [8, 9]:
            default_days = 45.0
        elif month in [6, 7]:
            default_days = 25.0
        elif month in [5, 10]:
            default_days = 15.0
        else:
            default_days = 4.0
            
        return {
            "dias_sem_chuva_med": default_days,
            "source": f"Cerrado Regional Climatological Baseline ({MONTH_NAMES[month]})"
        }

def auto_detect_environment(lat: float, lon: float) -> dict:
    """Autonomously classifies ground cover and proximity to water bodies via OpenStreetMap."""
    is_galeria_or_water = 0
    tipo_cobertura = 2  # Default: Typical Savanna
    env_desc = "Typical Cerrado / Savanna (Exposed Fine Biomass)"
    dist_water_km = None
    water_name = None

    if collect_water_distance is not None:
        try:
            water_data = collect_water_distance(lat, lon, radius_km=5.0)
            nearest = water_data.get("nearest_water_body")
            if nearest:
                dist_water_km = nearest.get("distance_km")
                water_name = nearest.get("name")
                
                # Proximity <= 180m indicates Gallery Forest / Riparian Protection Buffer
                if dist_water_km is not None and dist_water_km <= 0.18:
                    is_galeria_or_water = 1
                    tipo_cobertura = 1
                    env_desc = f"Gallery Forest / Riparian Buffer (~{dist_water_km*1000:.0f}m from {water_name})"
                elif dist_water_km is not None and dist_water_km <= 0.03:
                    is_galeria_or_water = 1
                    tipo_cobertura = 0
                    env_desc = f"Water Body / Fluvial Channel ({water_name})"
        except Exception:
            pass

    return {
        "is_galeria_or_water": is_galeria_or_water,
        "tipo_cobertura": tipo_cobertura,
        "description": env_desc,
        "dist_water_km": dist_water_km,
        "water_name": water_name
    }

def generate_prediction_charts(prob_mono: float, prob_lr: float, drought_days: float, 
                               month: int, year: int, lat: float, lon: float, env_desc: str):
    """Generates and persists updated analytical gauge visualizations."""
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    
    fig, ax = plt.subplots(figsize=(8.5, 4.2))
    models = ["Monotonic Ensemble (Monthly)", "Logistic Regression"]
    values = [prob_mono, prob_lr]
    colors = ["#d95f02" if v >= 50 else "#2ca25f" for v in values]
    
    bars = ax.barh(models, values, color=colors, height=0.45)
    ax.set_xlim(0, 100)
    ax.set_xlabel("Monthly Fire Probability (%)", fontsize=11, fontweight="bold")
    ax.set_title(f"Fire Occurrence Risk Prediction — {MONTH_NAMES[month]}/{year}\nCoord: ({lat:.4f}, {lon:.4f}) | {env_desc}", 
                 fontsize=10, fontweight="bold", pad=12)
    
    for bar in bars:
        w = bar.get_width()
        ax.text(w + 1.5, bar.get_y() + bar.get_height()/2, f"{w:.1f}%", 
                va="center", fontsize=11, fontweight="bold")
        
    ax.axvline(70, color="red", linestyle="--", alpha=0.7, label="Critical Risk Threshold (70%)")
    ax.axvline(40, color="orange", linestyle=":", alpha=0.7, label="Moderate Risk Threshold (40%)")
    ax.legend(loc="lower right")
    plt.tight_layout()
    
    chart_path = FIGURES_DIR / "ultima_predicao_gauge.png"
    plt.savefig(chart_path, dpi=200)
    plt.close()

def main():
    parser = argparse.ArgumentParser(description="Monthly Fire Risk Prediction Engine in the Cerrado — Autonomous AI")
    parser.add_argument("lat", type=float, help="Latitude (e.g. -9.7250)")
    parser.add_argument("lon", type=float, help="Longitude (e.g. -50.8246)")
    parser.add_argument("--month", "--mes", dest="month", type=int, default=None, help="Target month (1 to 12)")
    parser.add_argument("--year", "--ano", dest="year", type=int, default=datetime.now().year, help="Target year (default: current year)")
    parser.add_argument("--date", "--data", dest="date", type=str, default=None, help="Date format compatibility (YYYY-MM-DD)")
    args = parser.parse_args()

    # Determine month and year
    if args.month is not None:
        month = args.month
        year = args.year
    elif args.date is not None:
        dt = pd.to_datetime(args.date)
        month = dt.month
        year = dt.year
    else:
        now = datetime.now()
        month = now.month
        year = now.year

    if not (1 <= month <= 12):
        print(f"[ERROR] Invalid month: {month}. Please specify a value between 1 and 12.", file=sys.stderr)
        sys.exit(1)

    mono_path = MONTHLY_MODELS_DIR / "cart_monthly_occurrence.pkl"
    log_path  = MONTHLY_MODELS_DIR / "logistic_monthly_occurrence.pkl"
    scaler_path = MONTHLY_MODELS_DIR / "scaler_monthly_occurrence.pkl"
    features_path = MONTHLY_MODELS_DIR / "monthly_features_order.json"

    if not mono_path.exists():
        print("[ERROR] Serialized models not found. Please run ml/monthly/train_monthly_occurrence.py first.", file=sys.stderr)
        sys.exit(1)

    mono_model = joblib.load(mono_path)
    log_reg = joblib.load(log_path)
    scaler = joblib.load(scaler_path)
    with open(features_path, encoding="utf-8") as f:
        features_order = json.load(f)

    # 1. Monthly Climatic Characterization
    print(f"\n[1/3] Computing climatological drought metrics for ({args.lat:.4f}, {args.lon:.4f}) in {MONTH_NAMES[month]}/{year}...")
    weather = calculate_monthly_weather(args.lat, args.lon, year, month)
    drought_days = weather["dias_sem_chuva_med"]
    dry_season = int(month in [5, 6, 7, 8, 9, 10])

    print(f" -> Climate Source: {weather['source']}")
    print(f" -> Continuous Dry Days: ~{drought_days:.1f} days without rainfall")

    # 2. Autonomous Land Cover Characterization
    print(f"[2/3] Mapping land cover, drainage networks, and water bodies...")
    env = auto_detect_environment(args.lat, args.lon)
    print(f" -> Classified Geoenvironment: {env['description']}")

    # 3. Model Inference
    row_dict = {
        "is_galeria_or_water": env["is_galeria_or_water"],
        "tipo_cobertura": env["tipo_cobertura"],
        "dias_sem_chuva_med": drought_days,
        "log1p_dias": np.log1p(drought_days),
        "estacao_seca": dry_season,
        "mes": month
    }
    input_df = pd.DataFrame([{k: row_dict[k] for k in features_order}])

    prob_mono = mono_model.predict_proba(input_df)[0][1] * 100
    prob_lr   = log_reg.predict_proba(scaler.transform(input_df))[0][1] * 100

    # 4. Generate Visualization
    print(f"[3/3] Generating analytical visual reports in figures/...")
    generate_prediction_charts(
        prob_mono, prob_lr, drought_days, 
        month, year, args.lat, args.lon, env["description"]
    )

    # 5. Executive Report
    print("\n" + "="*75)
    print(f"        MONTHLY FIRE RISK INFERENCE REPORT — REVIVETECH ML")
    print("="*75)
    print(f"Coordinates: Lat {args.lat:.4f}, Lon {args.lon:.4f} (Cerrado Biome)")
    print(f"Analysis Window: {MONTH_NAMES[month]} {year} ({'Dry Season' if dry_season else 'Rainy Season'})")
    print(f"Identified Geoenvironment: {env['description']}")
    print(f"Climatic Status: ~{drought_days:.0f} continuous dry days in seasonal window")
    print("-" * 75)
    print(f"ESTIMATED MONTHLY FIRE PROBABILITY:")
    print(f" -> Monotonic Model (Monthly): {prob_mono:.1f}%")
    print(f" -> Logistic Regression:       {prob_lr:.1f}%")
    print("-" * 75)

    if prob_mono >= 70:
        status = "[RED ALERT] CRITICAL MONTHLY FIRE RISK"
        recommendation = "Critical conditions for rapid wildfire spread. Establish firebreaks and mobilize brigades."
    elif prob_mono >= 40:
        status = "[YELLOW ALERT] MODERATE RISK / TRANSITION"
        recommendation = "Vegetation in hydrological transition. Maintain heightened preventive surveillance."
    else:
        status = "[GREEN ALERT] LOW RISK / PROTECTED ZONE"
        if env["is_galeria_or_water"] == 1:
            recommendation = "Gallery Forest / Riparian Buffer: Edaphic moisture and water table inhibit fire spread despite atmospheric drought."
        else:
            recommendation = "Sufficient moisture conditions or inadequate cumulative drought for sustained combustion."

    print(f"Status: {status}")
    print(f"Management Recommendation: {recommendation}")
    print("="*75)
    print(f"Visual dashboard saved to: ml/monthly/reports/figures/ultima_predicao_gauge.png\n")

if __name__ == "__main__":
    main()
