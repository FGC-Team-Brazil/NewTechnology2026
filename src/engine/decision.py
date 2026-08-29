#!/usr/bin/env python3
"""
src/engine/decision.py
=======================
Responsabilidade única: motor de recomendação determinístico.
Recebe dados regionais e devolve ranking de espécies + dosagem de biocápsula.

Todas as decisões são 100% rastreáveis a critérios numéricos e fontes
bibliográficas — nenhuma geração de texto livre ocorre aqui.

Diferença em relação ao arquivo original (geodata/motor_decision.py):
  - load_species() procura automaticamente em data/species/<bioma>.json
    quando um biome_key é fornecido, eliminando a necessidade de
    passar o caminho manualmente.

# ML_HOOK
    Quando o módulo ml/ estiver pronto, insira aqui a lógica de
    consulta ao modelo treinado como camada adicional (após o score
    determinístico). Veja ml/README.md para detalhes.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

PROJECT_DIR  = Path(__file__).resolve().parents[2]
SPECIES_DIR  = PROJECT_DIR / "data" / "species"

# --------------------------------------------------------------------------
# Pesos do sistema de pontuação (soma = 1.0)
# --------------------------------------------------------------------------
CRITERIA_WEIGHTS: dict[str, float] = {
    "ph":                     0.30,
    "water":                  0.25,
    "flammability":           0.25,
    "barrier_formation_speed": 0.10,
    "socioeconomic_value":    0.10,
}

# Curva dose-resposta de biochar (Sousa et al., Cerrado)
BIOCHAR_CURVE_T_HA: dict[str, list[tuple[float, float]]] = {
    "yellow_latosol":     [(5.5, 18.0), (6.5, 35.8)],
    "quartzarenic_neosol": [(5.5, 12.7), (6.5, 26.5)],
}

HYDROGEL_BASE_G: float = 2.0
HYDROGEL_MAX_G:  float = 6.0


# --------------------------------------------------------------------------
# 1. Carregamento da base de conhecimento
# --------------------------------------------------------------------------

def load_species(
    biome_key: Optional[str] = None,
    path: Optional[str] = None,
) -> list[dict]:
    """
    Carrega a lista de espécies.

    Prioridade:
      1. `path`      — caminho explícito (compatibilidade retroativa)
      2. `biome_key` — busca em data/species/<biome_key>.json
      3. fallback    — data/species/cerrado.json (default histórico)
    """
    if path:
        target = Path(path)
    elif biome_key:
        target = SPECIES_DIR / f"{biome_key}.json"
        if not target.exists():
            print(
                f"Aviso: '{target.name}' não encontrado em data/species/. "
                "Usando cerrado.json como fallback."
            )
            target = SPECIES_DIR / "cerrado.json"
    else:
        target = SPECIES_DIR / "cerrado.json"

    with open(target, "r", encoding="utf-8") as f:
        data = json.load(f)

    # Filtra entradas esqueleto (TODO) de biomas ainda não preenchidos
    return [s for s in data if s.get("common_name") != "TODO"]


# --------------------------------------------------------------------------
# 2. Extração de dados do coletor regional
# --------------------------------------------------------------------------

def _extract_ph(region_data: dict) -> Optional[float]:
    phh2o = region_data.get("soil", {}).get("phh2o", {}).get("values", {})
    return phh2o.get("0-5cm")


def _extract_annual_precipitation_mm(region_data: dict) -> Optional[float]:
    normals = region_data.get("climate_normals", {}).get("annual_average", {})
    mm_day  = normals.get("precipitation_mm_day")
    return mm_day * 365 if mm_day is not None else None


def _biome_compatible(species: dict, region_data: dict) -> bool:
    """
    TODO: integrar comparação de bioma quando MapBiomas/Earth Engine
    estiver no pipeline. Por enquanto aceita qualquer região.
    """
    return True


# --------------------------------------------------------------------------
# 3. Pontuação de espécies
# --------------------------------------------------------------------------

def _score_range(value: float, minimum: float, maximum: float) -> float:
    """1.0 dentro do intervalo ideal; decai linearmente para 0 fora dele."""
    if minimum <= value <= maximum:
        return 1.0
    slack    = (maximum - minimum) * 0.5 or 1.0
    distance = (minimum - value) if value < minimum else (value - maximum)
    return max(0.0, 1.0 - distance / slack)


def score_species(species: dict, region_data: dict) -> dict:
    """Pontua uma espécie (0–1) contra os dados regionais enriquecidos."""
    soil_ph       = _extract_ph(region_data)
    precipitation = _extract_annual_precipitation_mm(region_data)
    biome_ok      = _biome_compatible(species, region_data)

    ph_score = (
        _score_range(soil_ph, species["ph_min"], species["ph_max"])
        if soil_ph is not None else 0.5
    )
    water_score = (
        min(1.0, precipitation / species["min_precipitation_mm_year"])
        if precipitation is not None else 0.5
    )
    flammability_score = 1.0 - species["flammability_index"]
    speed_score        = species["barrier_formation_speed"]
    socioeconomic_score = species["socioeconomic_value"]

    sub_scores = {
        "ph":                     round(ph_score, 3),
        "water":                  round(water_score, 3),
        "flammability":           round(flammability_score, 3),
        "barrier_formation_speed": round(speed_score, 3),
        "socioeconomic_value":    round(socioeconomic_score, 3),
    }
    raw         = sum(sub_scores[c] * CRITERIA_WEIGHTS[c] for c in CRITERIA_WEIGHTS)
    final_score = raw if biome_ok else raw * 0.2

    return {
        "species":          species["common_name"],
        "scientific_name":  species["scientific_name"],
        "biome_compatible": biome_ok,
        "final_score":      round(final_score, 3),
        "sub_scores":       sub_scores,
        "source":           species.get("source"),
    }


def rank_species(region_data: dict, species_list: list[dict], top_n: int = 3) -> list[dict]:
    ranking = [score_species(s, region_data) for s in species_list]
    ranking.sort(key=lambda r: r["final_score"], reverse=True)
    return ranking[:top_n]


# --------------------------------------------------------------------------
# 4. Dosagem de biochar (curva dose-resposta da literatura)
# --------------------------------------------------------------------------

def calculate_biochar_dose_t_ha(
    current_ph: float,
    target_ph: float,
    soil_type: str = "yellow_latosol",
) -> float:
    points = BIOCHAR_CURVE_T_HA.get(soil_type, BIOCHAR_CURVE_T_HA["yellow_latosol"])
    (ph1, d1), (ph2, d2) = points
    if current_ph >= target_ph:
        return 0.0
    slope         = (d2 - d1) / (ph2 - ph1)
    dose_at_target = d1 + slope * (target_ph - ph1)
    return max(0.0, round(dose_at_target, 2))


def biochar_dose_per_capsule_g(dose_t_ha: float, capsules_per_m2: float) -> float:
    if capsules_per_m2 <= 0:
        return 0.0
    grams_per_m2 = (dose_t_ha * 1_000_000) / 10_000
    return round(grams_per_m2 / capsules_per_m2, 2)


# --------------------------------------------------------------------------
# 5. Dosagem de hidrogel (heurística de déficit hídrico)
# --------------------------------------------------------------------------

def calculate_hydrogel_dose_g(
    annual_precipitation_mm: Optional[float],
    species_min_precipitation: float,
) -> float:
    if annual_precipitation_mm is None or not species_min_precipitation:
        return HYDROGEL_BASE_G
    deficit          = max(0.0, species_min_precipitation - annual_precipitation_mm)
    relative_deficit = min(1.0, deficit / species_min_precipitation)
    return round(HYDROGEL_BASE_G + relative_deficit * (HYDROGEL_MAX_G - HYDROGEL_BASE_G), 2)


# --------------------------------------------------------------------------
# 6. Orquestração principal
# --------------------------------------------------------------------------

def recommend_biocapsule(
    region_data: dict,
    biome_key: Optional[str] = None,
    species_path: Optional[str] = None,
    soil_type: str = "yellow_latosol",
    capsules_per_m2: float = 4.0,
    top_n: int = 3,
) -> dict:
    """
    Ponto de entrada principal do motor.

    Parameters
    ----------
    region_data    : dict retornado por collect_all()
    biome_key      : chave do bioma (ex. "cerrado") — carrega data/species/<biome_key>.json
    species_path   : caminho explícito para o JSON de espécies (sobrepõe biome_key)
    soil_type      : tipo de solo para a curva de biochar
    capsules_per_m2: densidade de plantio de cápsulas
    top_n          : quantas espécies retornar no ranking

    Returns
    -------
    dict com point_soil_ph, point_annual_precipitation_mm, recommendations[]
    """
    species_list = load_species(biome_key=biome_key, path=species_path)
    ranking      = rank_species(region_data, species_list, top_n=top_n)

    current_ph          = _extract_ph(region_data)
    annual_precipitation = _extract_annual_precipitation_mm(region_data)

    recommendations = []
    for r in ranking:
        full_sp     = next(s for s in species_list if s["common_name"] == r["species"])
        target_ph   = full_sp["ph_min"]
        biochar_t   = (
            calculate_biochar_dose_t_ha(current_ph, target_ph, soil_type)
            if current_ph is not None else None
        )
        biochar_g   = (
            biochar_dose_per_capsule_g(biochar_t, capsules_per_m2)
            if biochar_t is not None else None
        )
        hydrogel_g  = calculate_hydrogel_dose_g(annual_precipitation, full_sp["min_precipitation_mm_year"])

        # ML_HOOK — ponto de extensão para a camada de aprendizado
        # Quando ml/train.py produzir um modelo, carregue-o aqui e
        # ajuste biochar_g / hydrogel_g com base no output do modelo.
        # Exemplo:
        #   if ml_model := load_ml_model():
        #       biochar_g, hydrogel_g = ml_model.predict(region_data, full_sp)

        recommendations.append({
            **r,
            "capsule_dosage": {
                "biochar_g":             biochar_g,
                "biochar_t_ha_equivalent": biochar_t,
                "hydrogel_g":            hydrogel_g,
                "soil_type_considered":  soil_type,
            },
        })

    return {
        "point_soil_ph":              current_ph,
        "point_annual_precipitation_mm": annual_precipitation,
        "capsules_per_m2_considered": capsules_per_m2,
        "recommendations":            recommendations,
    }
