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
import sys
from pathlib import Path

import pandas as pd

from scraper_runner import run_ts_scraper

PROJECT_DIR = Path(__file__).parent.resolve()
DAILY_HOTSPOTS_DIR = PROJECT_DIR / "focos_diarios"
UNIFIED_FILE_PATH = PROJECT_DIR / "arquivo_unificado.csv"


def unify_daily_csvs() -> pd.DataFrame:
    files = glob.glob(str(DAILY_HOTSPOTS_DIR / "*.csv"))
    if not files:
        print(f"Error: no .csv files found in '{DAILY_HOTSPOTS_DIR}'.", file=sys.stderr)
        sys.exit(1)

    print(f"Merging {len(files)} daily files from '{DAILY_HOTSPOTS_DIR.name}/'...")
    dfs_list = [pd.read_csv(f) for f in files]
    final_df = pd.concat(dfs_list, ignore_index=True)
    final_df.to_csv(UNIFIED_FILE_PATH, index=False)
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