#!/usr/bin/env python3
"""
Step 1: downloads the most recent hotspots (TypeScript scraper, via
scraper_runner.py) and unifies all daily CSVs from 'focos_diarios/'
into a single 'arquivo_unificado.csv'.

Usage:
    python unify_hotspots.py           # last 30 days (default)
    python unify_hotspots.py 60        # last 60 days
"""
import glob
import sqlite3
import sys
from pathlib import Path

import pandas as pd

from scraper_runner import run_ts_scraper

PROJECT_DIR = Path(__file__).parent.resolve()
DAILY_HOTSPOTS_DIR = PROJECT_DIR / "focos_diarios"
UNIFIED_FILE_PATH = PROJECT_DIR / "arquivo_unificado.csv"
HISTORY_DATABASE_PATH = PROJECT_DIR / "dados_locais" / "queimadas_inpe.db"


def build_wildfire_history_database(df: pd.DataFrame) -> None:
    """Create the local history database consumed by the enrichment dashboard."""
    required_columns = {"lat", "lon", "data_hora_gmt"}
    if not required_columns.issubset(df.columns):
        print("Warning: wildfire history database was not updated; required INPE columns are missing.")
        return

    history_df = df.loc[:, ["lat", "lon", "data_hora_gmt"]].dropna().rename(columns={
        "lat": "latitude", "lon": "longitude", "data_hora_gmt": "data_hora",
    })
    HISTORY_DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(HISTORY_DATABASE_PATH) as connection:
        history_df.to_sql("focos", connection, if_exists="replace", index=False)
        connection.execute("CREATE INDEX idx_focos_coordenadas ON focos(latitude, longitude)")
    print(f"'{HISTORY_DATABASE_PATH.relative_to(PROJECT_DIR)}' updated with {len(history_df)} hotspots for local history.")


def unify_daily_csvs() -> pd.DataFrame:
    files = glob.glob(str(DAILY_HOTSPOTS_DIR / "*.csv"))
    if not files:
        print(f"Error: no .csv files found in '{DAILY_HOTSPOTS_DIR}'.", file=sys.stderr)
        sys.exit(1)

    print(f"Merging {len(files)} daily files from '{DAILY_HOTSPOTS_DIR.name}/'...")
    dfs_list = [pd.read_csv(f) for f in files]
    final_df = pd.concat(dfs_list, ignore_index=True)
    final_df.to_csv(UNIFIED_FILE_PATH, index=False)
    build_wildfire_history_database(final_df)
    print(f"'{UNIFIED_FILE_PATH.name}' saved with {len(final_df)} raw hotspots.")
    print(f"Available columns: {list(final_df.columns)}")
    return final_df


def main(days: int = 30) -> None:
    success = run_ts_scraper(days)
    if not success:
        print(
            "Warning: the scraper failed or did not run — attempting to merge "
            "existing CSVs in focos_diarios/ anyway."
        )
    unify_daily_csvs()


if __name__ == "__main__":
    days_arg = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    main(days_arg)
