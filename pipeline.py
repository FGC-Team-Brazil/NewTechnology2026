#!/usr/bin/env python3
"""
ReviveTech — Full Pipeline: Latitude/Longitude -> Biocapsule Recommendation
====================================================================================
Combines the two modules already built:
  1. revivetech_data_collector.py -> collects regional data (location,
     weather, soil, protected areas, etc.)
  2. revivetech_decision_engine.py -> transforms that data into a species +
     biochar/hydrogel dosage recommendation

Usage:
    python revivetech_pipeline.py -15.60 -47.70
"""

from __future__ import annotations

import argparse
import json
import sys

import rtdata as collector
import motor_decision as engine


def main() -> None:
    parser = argparse.ArgumentParser(description="Full lat/long -> biocapsule recommendation pipeline")
    parser.add_argument("lat", type=float, nargs="?")
    parser.add_argument("lon", type=float, nargs="?")
    parser.add_argument("--species", default="species.json")
    parser.add_argument("--soil-type", default="yellow_latosol",
                         choices=list(engine.BIOCHAR_CURVE_T_HA.keys()))
    parser.add_argument("--capsules-per-m2", type=float, default=4.0)
    parser.add_argument("--radius", type=float, default=15.0)
    parser.add_argument("--top", type=int, default=3)
    parser.add_argument("--output", default="outputs")
    args = parser.parse_args()

    lat, lon = args.lat, args.lon
    if lat is None or lon is None:
        try:
            lat = float(input("Latitude: ").strip())
            lon = float(input("Longitude: ").strip())
        except (ValueError, EOFError):
            print("Invalid latitude/longitude.", file=sys.stderr)
            sys.exit(1)

    print(f"\n[1/2] Collecting regional data ({lat}, {lon})...")
    region_data = collector.collect_all(lat, lon, radius_km=args.radius)
    collector.print_summary(region_data)
    region_path = collector.save_json(region_data, output_folder=args.output)
    print(f"Regional data saved to: {region_path}")

    print(f"\n[2/2] Calculating biocapsule recommendation...")
    recommendation = engine.recommend_biocapsule(
        region_data,
        species_path=args.species,
        soil_type=args.soil_type,
        capsules_per_m2=args.capsules_per_m2,
        top_n=args.top,
    )

    print("\n" + "=" * 70)
    print("  BIOCAPSULE RECOMMENDATION")
    print("=" * 70)
    print(f"Soil pH at point: {recommendation['point_soil_ph']}")
    print(f"Estimated annual precipitation: {recommendation['point_annual_precipitation_mm']} mm")
    for i, rec in enumerate(recommendation["recommendations"], start=1):
        print(f"\n#{i} {rec['species']} ({rec['scientific_name']}) — score {rec['final_score']}")
        print(f"    Sub-scores: {rec['sub_scores']}")
        d = rec["capsule_dosage"]
        print(f"    Suggested dosage: {d['biochar_g']} g biochar | {d['hydrogel_g']} g hydrogel")
        print(f"    Source: {rec['source']}")

    final_output = {"region_data": region_data, "biocapsule_recommendation": recommendation}
    import os
    from datetime import datetime
    os.makedirs(args.output, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    final_path = os.path.join(args.output, f"recommendation_{lat}_{lon}_{stamp}.json")
    with open(final_path, "w", encoding="utf-8") as f:
        json.dump(final_output, f, ensure_ascii=False, indent=2)
    print(f"\nFull result saved to: {final_path}")


if __name__ == "__main__":
    main()