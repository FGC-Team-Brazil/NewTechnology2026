#!/usr/bin/env python3
"""
ReviveTech — AI Proportion Recommender (learned layer)
====================================================================================
Extends `motor_decision.py` (the deterministic dose-response engine) with a
layer that CAN BE FED real field results and gets better at recommending the
biochar/hydrogel proportion the more data it receives.

Why a separate module instead of changing motor_decision.py:
  `motor_decision.py` is explicitly the deterministic, 100%-traceable layer
  (dose-response curve from the literature). This module is the opposite
  kind of component on purpose: a *learned* layer, whose output quality
  depends entirely on the examples it has been fed. Keeping them apart means
  you always know, for any given recommendation, whether the number came
  from a published curve or from accumulated field evidence.

How the "AI" works (MVP — case-based reasoning / weighted k-NN):
  1. Every time a biocapsule is planted, you record a *trial*: the
     environmental features at that point (pH, precipitation, slope,
     temperature, fire recurrence, proximity to water/native vegetation,
     biome) plus the biochar/hydrogel dosage actually used.
  2. Months later, once you know how it went, you feed the *outcome* back
     (survival rate and/or a growth score).
  3. To recommend a proportion for a NEW point, the model finds the most
     similar past trials (weighted distance in feature space, with a
     penalty for a different biome) that also HAD a good outcome, and
     returns a weighted average of the dosage they used.
  4. If there isn't enough labeled field data yet for a trustworthy answer,
     `predict_proportion` returns `None` (with the reason) instead of
     guessing — the caller (see `recommend_biocapsule_hybrid` below) then
     falls back to the deterministic dose-response curve.

No machine-learning library is required for this MVP (pure Python, so it
runs anywhere the rest of the pipeline runs). The distance/weighting logic
is intentionally simple and inspectable — every prediction returned by
`predict_proportion` includes the exact neighbor trials that produced it,
so it stays as auditable as the deterministic engine, even though the
number itself is learned rather than looked up on a curve.

Feeding the model (CLI):
    # after planting a capsule, log the trial (outcome unknown yet)
    python motor_ai.py add-trial region_20260115.json \
        --species "Ingá" --biochar-g 12.5 --hydrogel-g 3.0 --notes "plot A3"
    -> prints a trial id, e.g. 7f1a2b3c9d4e

    # months later, once you know how it went
    python motor_ai.py record-outcome 7f1a2b3c9d4e --survival-rate-pct 82

    # ask for a learned recommendation for a new point
    python motor_ai.py predict region_new_point.json --species "Ingá"

Programmatic use (see `recommend_biocapsule_hybrid` for the full pipeline
integration):
    from motor_ai import predict_proportion, add_trial, record_outcome
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import uuid
from datetime import datetime, timezone
from typing import Optional

DEFAULT_DATASET_PATH = "ai_training_data.jsonl"
DEFAULT_K = 5
DEFAULT_MIN_EXAMPLES = 3  # below this many usable neighbors, refuse to predict

# Numeric environmental features compared between trials. Kept in sync by
# hand with the fields `motor_decision.py` reads from region_data — if the
# collector's schema changes, update `extract_features()` below.
NUMERIC_FEATURES = [
    "soil_ph",
    "annual_precipitation_mm",
    "slope_pct",
    "elevation_m",
    "avg_temp_c",
    "fire_recurrence_years",
    "water_distance_km",
    "native_vegetation_distance_km",
]

# Added directly to the distance (in standardized-units scale) when two
# trials are in different biomes. Large enough that same-biome precedents
# are strongly preferred, but not so large that a cross-biome precedent can
# never be used when nothing else is available.
BIOME_MISMATCH_PENALTY = 2.5

_EPSILON = 1e-6


# --------------------------------------------------------------------------
# 1. Turning region_data (from revivetech_data_collector / rtdata) into a
#    flat feature vector
# --------------------------------------------------------------------------

def extract_features(region_data: dict) -> dict[str, Optional[float]]:
    """
    Pulls the environmental features the recommender compares on out of the
    dict returned by `collect_all()`. Missing sources simply become `None`
    — they are skipped pairwise at distance-computation time, never
    fabricated.
    """
    soil = region_data.get("soil", {}) or {}
    ph_values = (soil.get("phh2o", {}) or {}).get("values", {}) or {}

    climate_normals = (region_data.get("climate_normals", {}) or {}).get("annual_average", {}) or {}
    precipitation_mm_day = climate_normals.get("precipitation_mm_day")
    annual_precipitation_mm = precipitation_mm_day * 365 if precipitation_mm_day is not None else None

    slope = region_data.get("slope_relief", {}) or {}
    elevation = region_data.get("elevation_and_timezone", {}) or {}
    fire_history = region_data.get("local_fire_history", {}) or {}
    water = (region_data.get("water_distance", {}) or {}).get("nearest_water_body") or {}
    native_veg = (region_data.get("native_vegetation_distance", {}) or {}).get("nearest_vegetation_fragment") or {}
    biome = (region_data.get("biome_and_vegetation", {}) or {}).get("biome")

    return {
        "soil_ph": ph_values.get("0-5cm"),
        "annual_precipitation_mm": annual_precipitation_mm,
        "slope_pct": slope.get("estimated_slope_pct"),
        "elevation_m": elevation.get("elevation_m"),
        "avg_temp_c": climate_normals.get("avg_temp_c"),
        "fire_recurrence_years": fire_history.get("distinct_years_recurrence"),
        "water_distance_km": water.get("distance_km"),
        "native_vegetation_distance_km": native_veg.get("distance_km"),
        "biome": biome,
    }


# --------------------------------------------------------------------------
# 2. Feeding the model: recording trials and, later, their outcomes
# --------------------------------------------------------------------------

def add_trial(
    features: dict,
    species: str,
    dosage: dict,
    outcome: Optional[dict] = None,
    metadata: Optional[dict] = None,
    dataset_path: str = DEFAULT_DATASET_PATH,
) -> str:
    """
    Appends one trial to the dataset (JSON Lines — one record per line, so
    feeding never requires rewriting the whole file). Returns the trial id,
    to be used later with `record_outcome()` once the result is known.

    `dosage` must contain at least `biochar_g` and `hydrogel_g` — the
    amounts actually used in that trial, which is what the model learns
    from later if the outcome turns out to be good.
    """
    trial_id = uuid.uuid4().hex[:12]
    record = {
        "id": trial_id,
        "recorded_at_utc": datetime.now(timezone.utc).isoformat(),
        "species": species,
        "features": features,
        "dosage": {"biochar_g": dosage.get("biochar_g"), "hydrogel_g": dosage.get("hydrogel_g")},
        "outcome": outcome,  # None until record_outcome() is called
        "metadata": metadata or {},
    }
    os.makedirs(os.path.dirname(dataset_path) or ".", exist_ok=True)
    with open(dataset_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return trial_id


def record_outcome(
    trial_id: str,
    outcome: dict,
    dataset_path: str = DEFAULT_DATASET_PATH,
) -> bool:
    """
    Fills in the outcome of a previously logged trial (found by id) and
    rewrites the dataset file. Returns False if no trial with that id was
    found. This is the step that actually turns a logged trial into
    training data — until this is called, the trial exists but is ignored
    by `predict_proportion`.
    """
    if not os.path.exists(dataset_path):
        return False

    with open(dataset_path, "r", encoding="utf-8") as f:
        lines = [json.loads(line) for line in f if line.strip()]

    found = False
    for record in lines:
        if record["id"] == trial_id:
            record["outcome"] = outcome
            record["outcome_recorded_at_utc"] = datetime.now(timezone.utc).isoformat()
            found = True
            break

    if not found:
        return False

    with open(dataset_path, "w", encoding="utf-8") as f:
        for record in lines:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    return True


def _load_all_trials(dataset_path: str) -> list[dict]:
    if not os.path.exists(dataset_path):
        return []
    with open(dataset_path, "r", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def _outcome_score(outcome: Optional[dict]) -> Optional[float]:
    """
    Reduces an outcome dict to a single 0–1 quality score the weighting
    step can use. Accepts whichever of these fields is present:
      - "score": already 0–1, used as-is
      - "survival_rate_pct": 0–100, divided by 100
      - "growth_score": assumed already 0–1
    Returns None if the trial has no outcome yet (still pending field data).
    """
    if not outcome:
        return None
    if outcome.get("score") is not None:
        return float(outcome["score"])
    if outcome.get("survival_rate_pct") is not None:
        return max(0.0, min(1.0, float(outcome["survival_rate_pct"]) / 100.0))
    if outcome.get("growth_score") is not None:
        return float(outcome["growth_score"])
    return None


def _labeled_trials(dataset_path: str, species: Optional[str] = None) -> list[dict]:
    """Trials that have both features and a usable outcome score, optionally
    filtered to one species."""
    trials = []
    for record in _load_all_trials(dataset_path):
        score = _outcome_score(record.get("outcome"))
        if score is None:
            continue
        if species is not None and record.get("species") != species:
            continue
        trials.append({**record, "_outcome_score": score})
    return trials


# --------------------------------------------------------------------------
# 3. Distance / weighting — the core of the case-based recommender
# --------------------------------------------------------------------------

def _feature_stats(trials: list[dict]) -> dict[str, dict[str, float]]:
    """Mean/std per numeric feature across the given trials, used to put
    every feature on a comparable (standardized) scale before computing
    distances."""
    stats = {}
    for feature in NUMERIC_FEATURES:
        values = [t["features"].get(feature) for t in trials]
        values = [v for v in values if v is not None]
        if not values:
            stats[feature] = {"mean": 0.0, "std": 1.0}
            continue
        mean = sum(values) / len(values)
        if len(values) < 2:
            std = 1.0
        else:
            variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
            std = math.sqrt(variance) or 1.0
        stats[feature] = {"mean": mean, "std": std}
    return stats


def _distance(a: dict, b: dict, stats: dict[str, dict[str, float]]) -> Optional[float]:
    """
    Standardized Euclidean-style distance between two feature dicts,
    computed only over the dimensions present on both sides (so a missing
    reading never silently becomes a "0 difference"). Returns None if no
    dimension could be compared at all. Biome mismatches add a fixed
    penalty rather than participating in the same scale as numeric features.
    """
    squared_terms = []
    for feature in NUMERIC_FEATURES:
        va, vb = a.get(feature), b.get(feature)
        if va is None or vb is None:
            continue
        std = stats.get(feature, {}).get("std", 1.0) or 1.0
        z = (va - vb) / std
        squared_terms.append(z * z)

    if not squared_terms:
        return None

    distance = math.sqrt(sum(squared_terms) / len(squared_terms))

    biome_a, biome_b = a.get("biome"), b.get("biome")
    if biome_a and biome_b and biome_a != biome_b:
        distance += BIOME_MISMATCH_PENALTY

    return distance


# --------------------------------------------------------------------------
# 4. Prediction
# --------------------------------------------------------------------------

def predict_proportion(
    features: dict,
    species: Optional[str] = None,
    dataset_path: str = DEFAULT_DATASET_PATH,
    k: int = DEFAULT_K,
    min_examples: int = DEFAULT_MIN_EXAMPLES,
) -> Optional[dict]:
    """
    Returns a learned biochar/hydrogel recommendation for the given
    environmental features, or None if there isn't enough labeled field
    data yet to trust one (the caller should fall back to the deterministic
    dose-response curve in that case).

    The returned dict is fully auditable: `neighbors` lists exactly which
    past trials were used and how much weight each got, so the number is
    never a black box even though it's learned rather than curve-derived.
    """
    same_species_trials = _labeled_trials(dataset_path, species=species) if species else []
    cross_species_used = False

    trials = same_species_trials
    if len(trials) < min_examples:
        all_trials = _labeled_trials(dataset_path, species=None)
        if len(all_trials) >= min_examples:
            trials = all_trials
            cross_species_used = bool(species)
        else:
            trials = all_trials  # still not enough, will be caught below

    if len(trials) < min_examples:
        return None

    stats = _feature_stats(trials)

    scored_neighbors = []
    for trial in trials:
        dist = _distance(features, trial["features"], stats)
        if dist is None:
            continue
        scored_neighbors.append((dist, trial))

    if len(scored_neighbors) < min_examples:
        return None

    scored_neighbors.sort(key=lambda pair: pair[0])
    nearest = scored_neighbors[:k]

    weights = []
    for dist, trial in nearest:
        weight = trial["_outcome_score"] / (dist + _EPSILON)
        weights.append(weight)

    total_weight = sum(weights) or _EPSILON
    biochar_g = sum(
        w * trial["dosage"]["biochar_g"]
        for w, (_, trial) in zip(weights, nearest)
        if trial["dosage"].get("biochar_g") is not None
    ) / total_weight
    hydrogel_g = sum(
        w * trial["dosage"]["hydrogel_g"]
        for w, (_, trial) in zip(weights, nearest)
        if trial["dosage"].get("hydrogel_g") is not None
    ) / total_weight

    avg_distance = sum(d for d, _ in nearest) / len(nearest)
    coverage = len(nearest) / k
    confidence_score = round(max(0.0, min(1.0, coverage * math.exp(-avg_distance))), 3)
    if confidence_score >= 0.66:
        confidence_label = "high"
    elif confidence_score >= 0.33:
        confidence_label = "medium"
    else:
        confidence_label = "low"

    return {
        "biochar_g": round(biochar_g, 2),
        "hydrogel_g": round(hydrogel_g, 2),
        "confidence_score": confidence_score,
        "confidence_label": confidence_label,
        "based_on_n_trials": len(nearest),
        "used_cross_species_precedents": cross_species_used,
        "avg_neighbor_distance": round(avg_distance, 3),
        "neighbors": [
            {
                "trial_id": trial["id"],
                "species": trial["species"],
                "distance": round(dist, 3),
                "outcome_score": trial["_outcome_score"],
                "dosage": trial["dosage"],
            }
            for dist, trial in nearest
        ],
    }


# --------------------------------------------------------------------------
# 5. Hybrid recommendation — wires this module into motor_decision.py
#    without changing the deterministic engine at all
# --------------------------------------------------------------------------

def recommend_biocapsule_hybrid(
    region_data: dict,
    species_path: str = "species.json",
    soil_type: str = "yellow_latosol",
    capsules_per_m2: float = 4.0,
    top_n: int = 3,
    ai_dataset_path: str = DEFAULT_DATASET_PATH,
    ai_k: int = DEFAULT_K,
    ai_min_examples: int = DEFAULT_MIN_EXAMPLES,
) -> dict:
    """
    Runs the existing deterministic pipeline (`motor_decision.recommend_biocapsule`)
    for species ranking and the baseline dose-response dosage, then, for
    each recommended species, asks the learned layer whether accumulated
    field data suggests a better proportion for THIS environment.

    Each recommendation ends up with both numbers side by side:
      - "baseline_dosage"   -> from the literature dose-response curve
                               (motor_decision.py), always present
      - "ai_dosage"         -> from field precedents (this module), only
                               present once enough data has been fed
      - "suggested_dosage"  -> ai_dosage if its confidence is at least
                               "medium", baseline_dosage otherwise, with a
                               "suggested_dosage_source" field saying which
    Nothing is silently overridden — both numbers and the reason for the
    final pick are always in the output.
    """
    try:
        import motor_decision as engine
    except ImportError as exc:
        raise RuntimeError(
            "motor_decision.py not found — recommend_biocapsule_hybrid needs it "
            "for species ranking and the baseline dose-response curve."
        ) from exc

    baseline_result = engine.recommend_biocapsule(
        region_data,
        species_path=species_path,
        soil_type=soil_type,
        capsules_per_m2=capsules_per_m2,
        top_n=top_n,
    )

    features = extract_features(region_data)

    for rec in baseline_result["recommendations"]:
        baseline_dosage = {
            "biochar_g": rec["capsule_dosage"]["biochar_g"],
            "hydrogel_g": rec["capsule_dosage"]["hydrogel_g"],
        }
        ai_dosage = predict_proportion(
            features,
            species=rec["species"],
            dataset_path=ai_dataset_path,
            k=ai_k,
            min_examples=ai_min_examples,
        )

        rec["capsule_dosage"]["baseline_dosage"] = baseline_dosage
        rec["capsule_dosage"]["ai_dosage"] = ai_dosage

        use_ai = ai_dosage is not None and ai_dosage["confidence_label"] in ("medium", "high")
        rec["capsule_dosage"]["suggested_dosage"] = (
            {"biochar_g": ai_dosage["biochar_g"], "hydrogel_g": ai_dosage["hydrogel_g"]}
            if use_ai else baseline_dosage
        )
        rec["capsule_dosage"]["suggested_dosage_source"] = (
            f"learned_from_{ai_dosage['based_on_n_trials']}_field_trials" if use_ai
            else ("dose_response_curve_heuristic" if ai_dosage is None
                  else "dose_response_curve_heuristic (AI confidence too low: "
                       f"{ai_dosage['confidence_label']})")
        )

    return {
        **baseline_result,
        "point_features_used_by_ai": features,
        "ai_dataset_path": ai_dataset_path,
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------

def _load_region_json(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _cli_add_trial(args: argparse.Namespace) -> None:
    region_data = _load_region_json(args.region_json)
    features = extract_features(region_data)
    outcome = None
    if args.survival_rate_pct is not None or args.score is not None:
        outcome = {}
        if args.survival_rate_pct is not None:
            outcome["survival_rate_pct"] = args.survival_rate_pct
        if args.score is not None:
            outcome["score"] = args.score
        if args.notes:
            outcome["notes"] = args.notes

    trial_id = add_trial(
        features=features,
        species=args.species,
        dosage={"biochar_g": args.biochar_g, "hydrogel_g": args.hydrogel_g},
        outcome=outcome,
        metadata={"notes": args.notes, "region_json": args.region_json},
        dataset_path=args.dataset,
    )
    print(f"Trial recorded: {trial_id}")
    if outcome is None:
        print("No outcome yet — run 'record-outcome' once field results are known:")
        print(f"  python motor_ai.py record-outcome {trial_id} --survival-rate-pct <0-100>")


def _cli_record_outcome(args: argparse.Namespace) -> None:
    outcome = {}
    if args.survival_rate_pct is not None:
        outcome["survival_rate_pct"] = args.survival_rate_pct
    if args.score is not None:
        outcome["score"] = args.score
    if args.notes:
        outcome["notes"] = args.notes
    if not outcome:
        print("Provide at least --survival-rate-pct or --score.", file=sys.stderr)
        sys.exit(1)

    ok = record_outcome(args.trial_id, outcome, dataset_path=args.dataset)
    if not ok:
        print(f"No trial found with id {args.trial_id} in {args.dataset}", file=sys.stderr)
        sys.exit(1)
    print(f"Outcome recorded for trial {args.trial_id}")


def _cli_predict(args: argparse.Namespace) -> None:
    region_data = _load_region_json(args.region_json)
    features = extract_features(region_data)
    result = predict_proportion(
        features,
        species=args.species,
        dataset_path=args.dataset,
        k=args.k,
        min_examples=args.min_examples,
    )
    if result is None:
        print(json.dumps({
            "prediction": None,
            "reason": "not enough labeled field trials yet for this species/region",
            "features_used": features,
        }, indent=2, ensure_ascii=False))
    else:
        print(json.dumps({"prediction": result, "features_used": features}, indent=2, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Learned biochar/hydrogel proportion recommender, fed from field trial outcomes."
    )
    parser.add_argument("--dataset", default=DEFAULT_DATASET_PATH, help="Path to the trials JSONL file")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p_add = subparsers.add_parser("add-trial", help="Log a new trial (outcome optional at this point)")
    p_add.add_argument("region_json", help="Path to a region_data JSON produced by revivetech_data_collector.py")
    p_add.add_argument("--species", required=True)
    p_add.add_argument("--biochar-g", type=float, required=True)
    p_add.add_argument("--hydrogel-g", type=float, required=True)
    p_add.add_argument("--survival-rate-pct", type=float, default=None, help="Fill in only if already known")
    p_add.add_argument("--score", type=float, default=None, help="0-1 outcome score, alternative to survival rate")
    p_add.add_argument("--notes", default=None)
    p_add.set_defaults(func=_cli_add_trial)

    p_outcome = subparsers.add_parser("record-outcome", help="Attach a result to a previously logged trial")
    p_outcome.add_argument("trial_id")
    p_outcome.add_argument("--survival-rate-pct", type=float, default=None)
    p_outcome.add_argument("--score", type=float, default=None)
    p_outcome.add_argument("--notes", default=None)
    p_outcome.set_defaults(func=_cli_record_outcome)

    p_predict = subparsers.add_parser("predict", help="Ask the learned layer for a proportion")
    p_predict.add_argument("region_json", help="Path to a region_data JSON produced by revivetech_data_collector.py")
    p_predict.add_argument("--species", default=None)
    p_predict.add_argument("--k", type=int, default=DEFAULT_K)
    p_predict.add_argument("--min-examples", type=int, default=DEFAULT_MIN_EXAMPLES)
    p_predict.set_defaults(func=_cli_predict)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()