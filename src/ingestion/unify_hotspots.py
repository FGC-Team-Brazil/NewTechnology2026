#!/usr/bin/env python3
"""
src/ingestion/unify_hotspots.py
================================
Responsabilidade única: unificar os CSVs diários de focos_diarios/
em um único arquivo_unificado.csv e construir o banco de histórico local.

Não conhece nada de UI, motor de decisão ou enriquecimento.
"""
from __future__ import annotations

import glob
import sqlite3
import sys
from pathlib import Path

import pandas as pd

PROJECT_DIR = Path(__file__).resolve().parents[2]
DAILY_HOTSPOTS_DIR = PROJECT_DIR / "focos_diarios"
UNIFIED_FILE_PATH  = PROJECT_DIR / "arquivo_unificado.csv"
HISTORY_DB_PATH    = PROJECT_DIR / "data" / "local" / "queimadas_inpe.db"


def build_history_database(df: pd.DataFrame) -> None:
    """Cria/atualiza o banco SQLite de histórico consumido pelo enriquecimento."""
    required = {"lat", "lon", "data_hora_gmt"}
    if not required.issubset(df.columns):
        print(
            "Aviso: banco de histórico não atualizado; "
            "colunas INPE esperadas ausentes no CSV."
        )
        return

    history = (
        df[["lat", "lon", "data_hora_gmt"]]
        .dropna()
        .rename(columns={"lat": "latitude", "lon": "longitude", "data_hora_gmt": "data_hora"})
    )
    HISTORY_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(HISTORY_DB_PATH) as conn:
        history.to_sql("focos", conn, if_exists="replace", index=False)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_focos_coord ON focos(latitude, longitude)")
    print(
        f"Banco histórico atualizado: {HISTORY_DB_PATH.relative_to(PROJECT_DIR)} "
        f"({len(history)} focos)"
    )


def unify_daily_csvs() -> pd.DataFrame:
    """Lê todos os CSVs de focos_diarios/ e grava arquivo_unificado.csv."""
    files = glob.glob(str(DAILY_HOTSPOTS_DIR / "*.csv"))
    if not files:
        print(
            f"Erro: nenhum .csv encontrado em '{DAILY_HOTSPOTS_DIR}'.",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"Unificando {len(files)} arquivos de '{DAILY_HOTSPOTS_DIR.name}/'...")
    df = pd.concat([pd.read_csv(f) for f in files], ignore_index=True)
    df.to_csv(UNIFIED_FILE_PATH, index=False)
    build_history_database(df)
    print(f"'{UNIFIED_FILE_PATH.name}' salvo com {len(df)} focos brutos.")
    print(f"Colunas disponíveis: {list(df.columns)}")
    return df


def run(days: int = 30) -> None:
    """Ponto de entrada chamado pelo pipeline principal."""
    from src.ingestion.scraper_runner import run_ts_scraper

    ok = run_ts_scraper(days)
    if not ok:
        print(
            "Aviso: scraper falhou ou não executou — "
            "tentando unificar CSVs existentes em focos_diarios/."
        )
    unify_daily_csvs()


if __name__ == "__main__":
    days_arg = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    run(days_arg)
