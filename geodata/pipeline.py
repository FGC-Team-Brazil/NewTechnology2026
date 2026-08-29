#!/usr/bin/env python3
"""
ReviveTech — Full Pipeline: Latitude/Longitude -> Biocapsule Recommendation
====================================================================================
Combines the three modules already built:
  1. rtdata (revivetech_data_collector.py) -> collects regional data
     (location, weather, soil, protected areas, etc.)
  2. motor_decision (revivetech_decision_engine.py) -> deterministic,
     100%-traceable species + dosage recommendation from a literature
     dose-response curve
  3. motor_ai (this extension) -> learned layer: if enough field trial
     results have been fed for this species/environment, it proposes an
     evidence-based proportion alongside the deterministic one; otherwise
     it stays out of the way and the deterministic curve is used as-is

Nothing about steps 1–2 changes when the AI layer is enabled — it is
purely additive. Use `--use-ai` to turn it on; without that flag this
script behaves exactly like the original deterministic-only pipeline.

Usage:
    python revivetech_pipeline.py -15.60 -47.70
    python revivetech_pipeline.py -15.60 -47.70 --use-ai --ai-dataset ai_training_data.jsonl
"""

from __future__ import annotations

import argparse
import json
import sys

import rtdata as collector
import motor_decision as engine
import motor_ai as ai


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
    parser.add_argument("--use-ai", action="store_true",
                         help="Also consult the learned layer (motor_ai) for field-evidence-based dosage")
    parser.add_argument("--ai-dataset", default=ai.DEFAULT_DATASET_PATH,
                         help="Path to the trials JSONL file fed via motor_ai.py add-trial/record-outcome")
    parser.add_argument("--ai-k", type=int, default=ai.DEFAULT_K,
                         help="How many nearest field trials to weigh when the AI layer predicts a dosage")
    parser.add_argument("--ai-min-examples", type=int, default=ai.DEFAULT_MIN_EXAMPLES,
                         help="Minimum labeled trials required before the AI layer will propose anything")
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

    if args.use_ai:
        print(f"\n[2/2] Calculating biocapsule recommendation (deterministic curve + learned layer)...")
        recommendation = ai.recommend_biocapsule_hybrid(
            region_data,
            species_path=args.species,
            soil_type=args.soil_type,
            capsules_per_m2=args.capsules_per_m2,
            top_n=args.top,
            ai_dataset_path=args.ai_dataset,
            ai_k=args.ai_k,
            ai_min_examples=args.ai_min_examples,
        )
    else:
        print(f"\n[2/2] Calculating biocapsule recommendation (deterministic curve only)...")
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
        if args.use_ai:
            suggested = d["suggested_dosage"]
            print(f"    Suggested dosage: {suggested['biochar_g']} g biochar | {suggested['hydrogel_g']} g hydrogel")
            print(f"    Source: {d['suggested_dosage_source']}")
            print(f"    (deterministic curve: {d['baseline_dosage']['biochar_g']} g biochar | "
                  f"{d['baseline_dosage']['hydrogel_g']} g hydrogel)")
            if d["ai_dosage"] is not None:
                print(f"    (learned layer: {d['ai_dosage']['biochar_g']} g biochar | "
                      f"{d['ai_dosage']['hydrogel_g']} g hydrogel — "
                      f"confidence {d['ai_dosage']['confidence_label']}, "
                      f"based on {d['ai_dosage']['based_on_n_trials']} field trials)")
            else:
                print(f"    (learned layer: not enough field trials yet for this species/region)")
        else:
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