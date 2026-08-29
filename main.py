#!/usr/bin/env python3
"""
Runs the complete ReviveTech pipeline in sequence:
  1. unify_hotspots.py       -> downloads (TS scraper) and merges into unified_file.csv
  2. generate_spreadsheet.py -> merges nearby hotspots into consolidated real wildfires
  3. select_hotspots.py      -> interactive search + dashboard via rtdash.py

Usage:
    python main.py                  # runs everything, last 30 days
    python main.py --days 60        # runs everything, last 60 days
    python main.py --skip-download  # skips steps 1 and 2, goes straight to search
"""
import argparse
import sys

import generate_spreadsheet
import select_hotspots
import unify_hotspots


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--days", type=int, default=30, help="Number of days of hotspots to download (default: 30)")
    parser.add_argument(
        "--skip-download",
        action="store_true",
        help="Skips downloading/merging/consolidation and goes straight to search (consolidated_fires_spreadsheet.csv must already exist)",
    )
    args = parser.parse_args()

    if not args.skip_download:
        print("=" * 70)
        print(" STEP 1/3 — Downloading and merging hotspots")
        print("=" * 70)
        unify_hotspots.main(args.days)

        print("\n" + "=" * 70)
        print(" STEP 2/3 — Consolidating hotspots into wildfires")
        print("=" * 70)
        generate_spreadsheet.main()
    else:
        print("Skipping download/consolidation (--skip-download) — using existing spreadsheet.")

    print("\n" + "=" * 70)
    print(" STEP 3/3 — Interactive search")
    print("=" * 70)
    select_hotspots.main()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
        sys.exit(0)
