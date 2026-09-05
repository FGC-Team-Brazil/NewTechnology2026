#!/usr/bin/env python3
"""
src/output/exporter.py
=======================
Responsabilidade única: centralizar todas as funções de saída do pipeline.
  - Salvar dashboard HTML (via geodata/rtdash.py)
  - Salvar recomendação completa em JSON
  - Abrir o dashboard no navegador

Não conhece nada de lógica de negócio, UI ou APIs externas.
"""
from __future__ import annotations

import json
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

PROJECT_DIR      = Path(__file__).resolve().parents[2]
OUTPUTS_DIR      = PROJECT_DIR / "outputs"
DASHBOARDS_DIR   = OUTPUTS_DIR / "dashboards"
RECS_DIR         = OUTPUTS_DIR / "recommendations"


def save_recommendation_json(
    region_data: dict[str, Any],
    recommendation: dict[str, Any],
    lat: float,
    lon: float,
) -> Path:
    """Salva region_data + recommendation em outputs/recommendations/<stamp>.json."""
    RECS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path  = RECS_DIR / f"recommendation_{lat:.4f}_{lon:.4f}_{stamp}.json"
    payload = {"region_data": region_data, "biocapsule_recommendation": recommendation}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
    print(f"Recomendação salva em: {path.relative_to(PROJECT_DIR)}")
    return path


def open_dashboard(dashboard_path: str) -> None:
    """Abre o dashboard HTML no navegador padrão do sistema."""
    webbrowser.open(f"file://{Path(dashboard_path).resolve()}")
