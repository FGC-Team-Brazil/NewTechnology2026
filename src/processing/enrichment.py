#!/usr/bin/env python3
"""
src/processing/enrichment.py
==============================
Responsabilidade única: enriquecer um foco de incêndio (pd.Series)
com dados de APIs externas via src/geodata/collector.py (collect_all),
e persistir o resultado em outputs/enriched/enriched_hotspots.csv.

Não conhece nada de UI ou motor de decisão.
A chamada a collect_all() ocorre aqui; o paralelismo é controlado
externamente pelo pipeline principal (main.py) via ThreadPoolExecutor
quando múltiplos focos forem processados em lote.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_DIR    = Path(__file__).resolve().parents[2]
ENRICHED_PATH  = PROJECT_DIR / "outputs" / "enriched" / "enriched_hotspots.csv"


def _value(data: dict, section: str, field: str, default=None):
    """Extrai com segurança um campo de uma seção do dict de dados regionais."""
    section_data = data.get(section, {})
    return section_data.get(field, default) if isinstance(section_data, dict) else default


def build_enriched_record(row: pd.Series, data: dict, dashboard_path: str) -> dict:
    """
    Constrói o dicionário enriquecido a partir do foco (row) e dos
    dados retornados por collect_all(). Separado para facilitar testes
    unitários sem I/O.
    """
    protected_areas    = data.get("nearby_protected_areas") or []
    fire_history       = data.get("local_fire_history") or {}
    nearest_water      = (_value(data, "water_distance", "nearest_water_body") or {})
    nearest_vegetation = (_value(data, "native_vegetation_distance", "nearest_vegetation_fragment") or {})
    climate            = data.get("historical_weather") or {}

    record = row.to_dict()
    record.update({
        "consolidated_fire_id": (
            f"{row['data_pura']}|{float(row['lat_fogo']):.6f}|{float(row['lon_fogo']):.6f}"
        ),
        "enriched_at_utc":              datetime.now(timezone.utc).isoformat(),
        "dashboard_file":               str(Path(dashboard_path).resolve()),
        "municipality_api":             _value(data, "location", "municipality"),
        "state_api":                    _value(data, "location", "state"),
        "biome_api":                    _value(data, "biome_and_vegetation", "biome"),
        "original_vegetation_api":      _value(data, "biome_and_vegetation", "original_vegetation"),
        "elevation_m_api":              _value(data, "elevation_and_timezone", "elevation_m"),
        "temperature_c_api":            climate.get("temperature_c"),
        "relative_humidity_pct_api":    climate.get("relative_humidity_pct"),
        "precipitation_mm_api":         climate.get("precipitation_mm"),
        "wind_kmh_api":                 climate.get("wind_kmh"),
        "pm2_5_api":                    _value(data, "air_quality", "pm2_5"),
        "pm10_api":                     _value(data, "air_quality", "pm10"),
        "slope_pct_api":                _value(data, "slope_relief", "estimated_slope_pct"),
        "relief_classification_api":    _value(data, "slope_relief", "relief_classification"),
        "erosion_risk_api":             _value(data, "erosion_risk", "erosion_risk_class"),
        "land_use_api": (
            _value(data, "land_use", "land_use")
            or _value(data, "land_use", "mapbiomas_class_code")
        ),
        "nearest_water_body":           nearest_water.get("name"),
        "water_distance_km":            nearest_water.get("distance_km"),
        "nearest_native_vegetation":    nearest_vegetation.get("name"),
        "native_vegetation_distance_km": nearest_vegetation.get("distance_km"),
        "nearby_protected_areas_count": len(protected_areas),
        "nearby_protected_areas":       "; ".join(
            str(a.get("name", "")) for a in protected_areas
        ),
        "fire_hotspots_in_radius":      fire_history.get("total_hotspots_in_radius"),
        "fire_history_years":           "; ".join(
            map(str, fire_history.get("years_with_records", []))
        ),
        "enrichment_errors":            json.dumps(data.get("errors", {}), ensure_ascii=False),
        "enriched_data_json":           json.dumps(data, ensure_ascii=False),
    })
    return record


def save_enriched_record(record: dict) -> None:
    """Persiste (upsert por consolidated_fire_id) o registro enriquecido no CSV acumulado."""
    ENRICHED_PATH.parent.mkdir(parents=True, exist_ok=True)
    new_df = pd.DataFrame([record])

    if ENRICHED_PATH.exists():
        existing = pd.read_csv(ENRICHED_PATH)
        if "consolidated_fire_id" in existing.columns:
            existing = existing[
                existing["consolidated_fire_id"] != record["consolidated_fire_id"]
            ]
        new_df = pd.concat([existing, new_df], ignore_index=True, sort=False)

    new_df.to_csv(ENRICHED_PATH, index=False)
    print(f"Dados enriquecidos salvos em: {ENRICHED_PATH.relative_to(PROJECT_DIR)}")


def enrich_hotspot(row: pd.Series) -> tuple[dict[str, Any], str]:
    """
    Orquestra a obtenção de dados externos para um único foco:
      1. Chama collect_all() (API calls em paralelo internamente)
      2. Gera e salva o dashboard HTML
      3. Persiste o registro enriquecido
      4. Retorna (region_data, dashboard_path)

    O collect_all() já usa ThreadPoolExecutor internamente (em rtdata.py),
    então não há gargalo de I/O serial aqui.
    """
    # Import local para não forçar dependência circular no topo do módulo
    # e para que o collector seja substituível (ex. mock em testes).
    sys.path.insert(0, str(PROJECT_DIR / "geodata"))
    from rtdata import collect_all, print_summary  # type: ignore
    from rtdash import save_dashboard               # type: ignore

    lat  = float(row["lat_fogo"])
    lon  = float(row["lon_fogo"])
    date = datetime.fromisoformat(f"{row['data_pura']}T12:00:00")

    print(f"\nColetando dados para {row.get('municipio', '—')} ({lat:.4f}, {lon:.4f}) — {row['data_pura']}...")
    region_data = collect_all(lat, lon, dt=date)
    print_summary(region_data)

    dashboard_path = save_dashboard(region_data)
    record = build_enriched_record(row, region_data, dashboard_path)
    save_enriched_record(record)

    return region_data, dashboard_path
