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
HYDROGEL_MAX_G:  float = 10.0


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

    valid_species = []
    for s in data:
        if s.get("common_name") == "TODO":
            continue
            
        status = s.get("status", "eligible")
        if status not in ("eligible", "trial_candidate"):
            continue
            
        try:
            for field in ["ph_min", "ph_max", "min_precipitation_mm_year", "barrier_formation_speed", "socioeconomic_value"]:
                val = s.get(field)
                if not isinstance(val, (int, float)):
                    raise ValueError(f"Campo numérico '{field}' inválido ou nulo")
            
            flam = s.get("flammability_index")
            if not isinstance(flam, (int, float)) or not (0.0 <= flam <= 1.0):
                raise ValueError("flammability_index fora de [0, 1] ou nulo")
                
            valid_species.append(s)
        except ValueError as e:
            print(f"Aviso: ignorando espécie '{s.get('common_name')}' devido a erro de validação: {e}")

    if not valid_species and data:
        print("Aviso: base de espécies deste bioma ainda não cadastrada (apenas TODOs) ou sem espécies válidas.")

    return valid_species


# --------------------------------------------------------------------------
# 2. Extração de dados do coletor regional
# --------------------------------------------------------------------------

def _extract_ph(region_data: dict) -> Optional[float]:
    phh2o = region_data.get("soil", {}).get("phh2o", {}).get("values", {})
    return phh2o.get("0-5cm")


def _extract_soil_property(region_data: dict, prop: str) -> Optional[float]:
    prop_data = region_data.get("soil", {}).get(prop, {}).get("values", {})
    return prop_data.get("0-5cm")


def _extract_annual_precipitation_mm(region_data: dict) -> Optional[float]:
    normals = region_data.get("climate_normals", {}).get("annual_average", {})
    mm_day  = normals.get("precipitation_mm_day")
    return mm_day * 365 if mm_day is not None else None


def _biome_compatible(species: dict, region_data: dict) -> bool:
    target_biome = species.get("target_biome", "").lower()
    region_biome = region_data.get("biome_and_vegetation", {}).get("biome", "").lower()
    
    import unicodedata
    def clean(s):
        return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c)).strip()
        
    target_biome = clean(target_biome)
    region_biome = clean(region_biome)
    
    if target_biome and region_biome:
        return target_biome in region_biome or region_biome in target_biome
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


def _score_water(precipitation: float, min_precip: float) -> float:
    max_precip = min_precip + 1000.0
    if precipitation < min_precip:
        return max(0.0, precipitation / min_precip)
    elif precipitation <= max_precip:
        return 1.0
    else:
        slack = 1000.0
        distance = precipitation - max_precip
        return max(0.0, 1.0 - (distance / slack))


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
        _score_water(precipitation, species["min_precipitation_mm_year"])
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
# 4. Dosagem de biochar — base empírica (% do recheio da cápsula)
# --------------------------------------------------------------------------
#
# Referência: revestimento de sementes de arroz com biochar (Liu et al.):
#   - ponto ótimo = 30 % do peso do material de revestimento
#   - acima disso o excesso de pH alcalino prejudica germinação
#
# Implementação: usamos o *gap de pH* medido pelo motor (quanto falta para
# atingir o pH alvo da espécie) como acionador proporcional.
#   - gap ≥ BIOCHAR_PH_GAP_MAX (2 unidades) → 30 % do recheio = teto empírico
#   - gap = 0 (solo já adequado)             → 0 %
#   - entre esses extremos: interpolação linear
#
# O teto de 30 % é aplicado sobre CAPSULE_FILL_G (peso estimado do recheio
# de uma cápsula de 4 cm de diâmetro com substrato orgânico de ~0,6 g/cm³).

CAPSULE_FILL_G: float = 15.0       # g — recheio estimado da biocápsula (4 cm Ø)
BIOCHAR_MAX_PCT: float = 0.30      # 30 % → ótimo empírico (Liu et al.)
BIOCHAR_PH_GAP_MAX: float = 2.0   # gap de pH que dispara o teto de biochar


def calculate_biochar_dose_g(
    current_ph: Optional[float],
    target_ph: float,
) -> float:
    """
    Retorna a massa de biochar (g) para incorporar no recheio da cápsula.

    Parameters
    ----------
    current_ph  : pH medido no ponto (SoilGrids / coletor regional)
    target_ph   : pH alvo = média entre ph_min e ph_max da espécie

    Returns
    -------
    float em gramas, no intervalo [0, CAPSULE_FILL_G * BIOCHAR_MAX_PCT] (máx ≈ 4.5 g)
    """
    if current_ph is None or current_ph >= target_ph:
        return 0.0
    ph_gap = min(target_ph - current_ph, BIOCHAR_PH_GAP_MAX)
    pct    = (ph_gap / BIOCHAR_PH_GAP_MAX) * BIOCHAR_MAX_PCT
    return round(pct * CAPSULE_FILL_G, 2)


# Mantida para compatibilidade com motor_ai.py que pode chamar as funções antigas
def calculate_biochar_dose_t_ha(
    current_ph: float,
    target_ph: float,
    soil_type: str = "yellow_latosol",
) -> float:
    """Legado — preferir calculate_biochar_dose_g() para uso na biocápsula."""
    points = BIOCHAR_CURVE_T_HA.get(soil_type, BIOCHAR_CURVE_T_HA["yellow_latosol"])
    (ph1, d1), (ph2, d2) = points
    if current_ph >= target_ph:
        return 0.0
    slope         = (d2 - d1) / (ph2 - ph1)
    dose_at_target = d1 + slope * (target_ph - ph1)
    return max(0.0, round(dose_at_target, 2))


def biochar_dose_per_capsule_g(dose_t_ha: float, capsules_per_m2: float) -> float:
    """Legado — mantido para compatibilidade. Não é chamado pelo fluxo principal."""
    if dose_t_ha <= 0:
        return 0.0
    area_influencia_m2 = 0.01
    grams_per_m2 = dose_t_ha * 100.0
    return min(round(grams_per_m2 * area_influencia_m2, 2), 25.0)


# --------------------------------------------------------------------------
# 5. Dosagem de hidrogel — base empírica (% do recheio da cápsula)
# --------------------------------------------------------------------------
#
# Referência: cápsulas dispersadas por drone para restauração de floresta
# tropical (Hicks et al.): hidrogel usado = 0,05 g em 3,56 g de substrato
# ≈ 1,4 % do recheio.
# Teto seguro recomendado: 2–3 % — acima disso o microambiente fica saturado
# e cria condições anaeróbicas que impedem a germinação.
#
# Implementação: usamos o *déficit relativo de precipitação* já disponível
# no motor para interpolar entre 1,5 % (mínimo funcional, região úmida) e
# 3 % (teto seguro, seca severa).

HYDROGEL_MIN_PCT: float = 0.015   # 1,5 % — mínimo funcional (sem déficit)
HYDROGEL_MAX_PCT: float = 0.030   # 3,0 % — teto anti-encharcamento


def calculate_hydrogel_dose_g(
    annual_precipitation_mm: Optional[float],
    species_min_precipitation: float,
) -> float:
    """
    Retorna a massa de hidrogel em pó (g) para incorporar no recheio da cápsula.

    Parameters
    ----------
    annual_precipitation_mm   : precipitação anual local (coletor regional)
    species_min_precipitation  : precipitação mínima da espécie (JSON)

    Returns
    -------
    float em gramas, no intervalo [HYDROGEL_MIN_PCT, HYDROGEL_MAX_PCT] × CAPSULE_FILL_G
    (≈ 0,23 g a 0,45 g para uma cápsula de 15 g de recheio)
    """
    if annual_precipitation_mm is None or not species_min_precipitation:
        # Sem dados: usa 2 % como valor neutro intermediário
        return round(0.020 * CAPSULE_FILL_G, 2)
    deficit          = max(0.0, species_min_precipitation - annual_precipitation_mm)
    relative_deficit = min(1.0, deficit / species_min_precipitation)
    pct              = HYDROGEL_MIN_PCT + relative_deficit * (HYDROGEL_MAX_PCT - HYDROGEL_MIN_PCT)
    return round(pct * CAPSULE_FILL_G, 2)


# --------------------------------------------------------------------------
# 6. Orquestração principal
# --------------------------------------------------------------------------

def recommend_biocapsule(
    region_data: dict,
    biome_key: Optional[str] = None,
    species_path: Optional[str] = None,
    soil_type: str = "auto",
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
    soil_type      : tipo de solo para a curva de biochar ("auto" detecta por teor de areia)
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
    
    if soil_type == "auto":
        sand = _extract_soil_property(region_data, "sand")
        if sand is not None and sand >= 850:
            soil_type_considered = "quartzarenic_neosol"
        else:
            soil_type_considered = "yellow_latosol"
    else:
        soil_type_considered = soil_type

    region_biome = region_data.get("biome_and_vegetation", {}).get("biome", "").lower()
    is_cerrado = "cerrado" in region_biome if region_biome else True

    recommendations = []
    for r in ranking:
        full_sp   = next(s for s in species_list if s["common_name"] == r["species"])
        target_ph = (full_sp["ph_min"] + full_sp["ph_max"]) / 2.0

        biochar_g  = calculate_biochar_dose_g(current_ph, target_ph)
        hydrogel_g = calculate_hydrogel_dose_g(annual_precipitation, full_sp["min_precipitation_mm_year"])

        # ML_HOOK — ponto de extensão para a camada de aprendizado
        # Quando ml/train.py produzir um modelo, carregue-o aqui e
        # ajuste biochar_g / hydrogel_g com base no output do modelo.
        # Exemplo:
        #   if ml_model := load_ml_model():
        #       biochar_g, hydrogel_g = ml_model.predict(region_data, full_sp)

        recommendations.append({
            **r,
            "capsule_dosage": {
                "biochar_g":    biochar_g,
                "hydrogel_g":   hydrogel_g,
                "capsule_fill_g": CAPSULE_FILL_G,
                "biochar_pct_of_fill":  round(biochar_g / CAPSULE_FILL_G * 100, 1) if CAPSULE_FILL_G else None,
                "hydrogel_pct_of_fill": round(hydrogel_g / CAPSULE_FILL_G * 100, 1) if CAPSULE_FILL_G else None,
                "soil_type_considered": soil_type_considered,
                "biochar_extrapolated_from_cerrado": not is_cerrado,
            },
        })

    if not is_cerrado:
        print("Aviso: A curva de dosagem de biochar utilizada e calibrada para o Cerrado. "
              "Os resultados podem ser uma extrapolacao fragil para este bioma.")

    return {
        "point_soil_ph":              current_ph,
        "point_annual_precipitation_mm": annual_precipitation,
        "capsules_per_m2_considered": capsules_per_m2,
        "recommendations":            recommendations,
    }
