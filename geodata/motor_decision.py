#!/usr/bin/env python3
"""
ReviveTech — Biocapsule Decision Engine
================================================
Receives the regional data collected by revivetech_data_collector.py and
returns: (1) a ranking of recommended species for the region, with the
score explained by criterion, and (2) the suggested biochar and hydrogel
dosage for the capsule, calibrated from dose-response curves found in the
literature.

This module is the "deterministic layer" of the AI: every decision is
100% traceable to a numeric criterion and a bibliographic source — there
is no free-text generation here (that is handled by a separate reporting
module, which only narrates the already-computed result and never
decides).
"""

from __future__ import annotations

import json
from typing import Optional

# --------------------------------------------------------------------------
# Scoring system weights (sum to 1.0)
# --------------------------------------------------------------------------

CRITERIA_WEIGHTS = {
    "ph": 0.30,
    "water": 0.25,
    "flammability": 0.25,
    "barrier_formation_speed": 0.10,
    "socioeconomic_value": 0.10,
}

# Biochar dose-response curve (Sousa et al., mixed-species Cerrado
# biochar): points (target pH, dose t/ha) known per soil type.
BIOCHAR_CURVE_T_HA = {
    "yellow_latosol": [(5.5, 18.0), (6.5, 35.8)],
    "quartzarenic_neosol": [(5.5, 12.7), (6.5, 26.5)],
}

HYDROGEL_BASE_G = 2.0  # reference dose (project's current default)
HYDROGEL_MAX_G = 6.0   # plausible ceiling to avoid blowing up capsule cost/size


# --------------------------------------------------------------------------
# 1. Loading the knowledge base
# --------------------------------------------------------------------------

def load_species(path: str = "species.json") -> list[dict]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------
# 2. Extracting the relevant data coming from the regional collector
# --------------------------------------------------------------------------

def _extract_ph(region_data: dict) -> Optional[float]:
    soil = region_data.get("soil", {})
    phh2o = soil.get("phh2o", {}).get("values", {})
    return phh2o.get("0-5cm")


def _extract_annual_precipitation_mm(region_data: dict) -> Optional[float]:
    normals = region_data.get("climate_normals", {}).get("annual_average", {})
    mm_day = normals.get("precipitation_mm_day")
    return mm_day * 365 if mm_day is not None else None


def _biome_compatible(species: dict, region_data: dict) -> bool:
    """
    TODO: the regional data collector doesn't automatically resolve biome
    yet (it depends on MapBiomas/Earth Engine — see the collector's
    README). For now it accepts any region; once biome enters the
    pipeline, compare it here against `species["target_biome"]` and
    penalize mismatches.
    """
    return True


# --------------------------------------------------------------------------
# 3. Species scoring
# --------------------------------------------------------------------------

def _score_range(value: float, minimum: float, maximum: float) -> float:
    """1.0 if the value is within the ideal range; decays linearly to 0
    as it moves away from the limits (slack of 50% of the range width)."""
    if minimum <= value <= maximum:
        return 1.0
    slack = (maximum - minimum) * 0.5 or 1.0
    distance = (minimum - value) if value < minimum else (value - maximum)
    return max(0.0, 1.0 - distance / slack)


def score_species(species: dict, region_data: dict) -> dict:
    """Scores a species (0 to 1) against the enriched regional data."""
    soil_ph = _extract_ph(region_data)
    precipitation = _extract_annual_precipitation_mm(region_data)
    biome_ok = _biome_compatible(species, region_data)

    ph_score = _score_range(soil_ph, species["ph_min"], species["ph_max"]) if soil_ph is not None else 0.5
    water_score = min(1.0, precipitation / species["min_precipitation_mm_year"]) if precipitation is not None else 0.5
    flammability_score = 1.0 - species["flammability_index"]
    speed_score = species["barrier_formation_speed"]
    socioeconomic_score = species["socioeconomic_value"]

    sub_scores = {
        "ph": round(ph_score, 3),
        "water": round(water_score, 3),
        "flammability": round(flammability_score, 3),
        "barrier_formation_speed": round(speed_score, 3),
        "socioeconomic_value": round(socioeconomic_score, 3),
    }

    raw = sum(sub_scores[c] * CRITERIA_WEIGHTS[c] for c in CRITERIA_WEIGHTS)
    final_score = raw if biome_ok else raw * 0.2  # heavy penalty when out of biome

    return {
        "species": species["common_name"],
        "scientific_name": species["scientific_name"],
        "biome_compatible": biome_ok,
        "final_score": round(final_score, 3),
        "sub_scores": sub_scores,
        "source": species.get("source"),
    }


def recommend_species(region_data: dict, species_list: list[dict], top_n: int = 3) -> list[dict]:
    ranking = [score_species(e, region_data) for e in species_list]
    ranking.sort(key=lambda r: r["final_score"], reverse=True)
    return ranking[:top_n]


# --------------------------------------------------------------------------
# 4. Biochar dosage (dose-response curve from the literature)
# --------------------------------------------------------------------------

def calculate_biochar_dose_t_ha(current_ph: float, target_ph: float, soil_type: str = "yellow_latosol") -> float:
    """Linearly interpolates/extrapolates the biochar dose (t/ha) needed
    to raise the soil to the target pH, from the two known points of the
    Cerrado-soil study."""
    points = BIOCHAR_CURVE_T_HA.get(soil_type, BIOCHAR_CURVE_T_HA["yellow_latosol"])
    (ph1, d1), (ph2, d2) = points
    if current_ph >= target_ph:
        return 0.0
    slope = (d2 - d1) / (ph2 - ph1)
    dose_at_target = d1 + slope * (target_ph - ph1)
    return max(0.0, round(dose_at_target, 2))


def biochar_dose_per_capsule_g(dose_t_ha: float, capsules_per_m2: float) -> float:
    """Converts t/ha into grams per capsule, given the planting density."""
    if capsules_per_m2 <= 0:
        return 0.0
    grams_per_m2 = (dose_t_ha * 1_000_000) / 10_000
    return round(grams_per_m2 / capsules_per_m2, 2)


# --------------------------------------------------------------------------
# 5. Hydrogel dosage (water-deficit heuristic)
# --------------------------------------------------------------------------

def calculate_hydrogel_dose_g(annual_precipitation_mm: Optional[float], species_min_precipitation: float) -> float:
    """The greater the deficit between the region's precipitation and the
    species' minimum requirement, the higher the hydrogel proportion — up
    to a ceiling."""
    if annual_precipitation_mm is None or not species_min_precipitation:
        return HYDROGEL_BASE_G
    deficit = max(0.0, species_min_precipitation - annual_precipitation_mm)
    relative_deficit = min(1.0, deficit / species_min_precipitation)
    return round(HYDROGEL_BASE_G + relative_deficit * (HYDROGEL_MAX_G - HYDROGEL_BASE_G), 2)


# --------------------------------------------------------------------------
# 6. Orchestration — brings everything together into a single result
# --------------------------------------------------------------------------

def recommend_biocapsule(
    region_data: dict,
    species_path: str = "species.json",
    soil_type: str = "yellow_latosol",
    capsules_per_m2: float = 4.0,
    top_n: int = 3,
) -> dict:
    species_list = load_species(species_path)
    ranking = recommend_species(region_data, species_list, top_n=top_n)

    current_ph = _extract_ph(region_data)
    annual_precipitation = _extract_annual_precipitation_mm(region_data)

    recommendations = []
    for r in ranking:
        full_species = next(e for e in species_list if e["common_name"] == r["species"])
        target_ph = full_species["ph_min"]
        biochar_dose_t_ha = (
            calculate_biochar_dose_t_ha(current_ph, target_ph, soil_type) if current_ph is not None else None
        )
        biochar_dose_g = (
            biochar_dose_per_capsule_g(biochar_dose_t_ha, capsules_per_m2)
            if biochar_dose_t_ha is not None else None
        )
        hydrogel_dose_g = calculate_hydrogel_dose_g(annual_precipitation, full_species["min_precipitation_mm_year"])

        recommendations.append({
            **r,
            "capsule_dosage": {
                "biochar_g": biochar_dose_g,
                "biochar_t_ha_equivalent": biochar_dose_t_ha,
                "hydrogel_g": hydrogel_dose_g,
                "soil_type_considered": soil_type,
            },
        })

    return {
        "point_soil_ph": current_ph,
        "point_annual_precipitation_mm": annual_precipitation,
        "capsules_per_m2_considered": capsules_per_m2,
        "recommendations": recommendations,
    }


# --------------------------------------------------------------------------
# Simple CLI: reads an already-collected JSON (output of
# revivetech_data_collector.py)
# --------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Recommends a species + biocapsule dosage from a regional data JSON."
    )
    parser.add_argument("region_data_json", help="Path to the JSON generated by revivetech_data_collector.py")
    parser.add_argument("--species", default="species.json", help="Path to the species JSON")
    parser.add_argument("--soil-type", default="yellow_latosol",
                         choices=list(BIOCHAR_CURVE_T_HA.keys()))
    parser.add_argument("--capsules-per-m2", type=float, default=4.0)
    parser.add_argument("--top", type=int, default=3)
    args = parser.parse_args()

    with open(args.region_data_json, "r", encoding="utf-8") as f:
        region_data = json.load(f)

    result = recommend_biocapsule(
        region_data,
        species_path=args.species,
        soil_type=args.soil_type,
        capsules_per_m2=args.capsules_per_m2,
        top_n=args.top,
    )

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()