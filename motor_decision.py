#!/usr/bin/env python3
"""
ReviveTech — Motor de Decisão de Biocápsulas
================================================
Recebe os dados regionais coletados pelo revivetech_data_collector.py e
devolve: (1) ranking de espécies recomendadas para a região, com a
pontuação explicada por critério, e (2) a dosagem sugerida de biochar e
hidrogel para a cápsula, calibrada a partir de curvas dose-resposta da
literatura.

Este módulo é a "camada determinística" da IA: toda decisão é 100%
rastreável até um critério numérico e uma fonte bibliográfica — não há
geração de texto livre aqui (isso fica a cargo de um módulo de relatório
separado, que só narra o resultado já calculado, nunca decide).
"""

from __future__ import annotations

import json
from typing import Optional

# --------------------------------------------------------------------------
# Pesos do sistema de pontuação (somam 1.0)
# --------------------------------------------------------------------------

PESOS_CRITERIOS = {
    "ph": 0.30,
    "agua": 0.25,
    "inflamabilidade": 0.25,
    "velocidade_formacao_barreira": 0.10,
    "valor_socioeconomico": 0.10,
}

# Curva dose-resposta de biochar (Sousa et al., biochar misto de espécies do
# Cerrado): pontos (pH alvo, dose t/ha) conhecidos por tipo de solo.
CURVA_BIOCHAR_T_HA = {
    "latossolo_amarelo": [(5.5, 18.0), (6.5, 35.8)],
    "neossolo_quartzarenico": [(5.5, 12.7), (6.5, 26.5)],
}

HIDROGEL_BASE_G = 2.0  # dose de referência (BOM atual do projeto)
HIDROGEL_MAX_G = 6.0   # teto plausível para não estourar custo/tamanho da cápsula


# --------------------------------------------------------------------------
# 1. Carregamento da base de conhecimento
# --------------------------------------------------------------------------

def carregar_especies(caminho: str = "especies.json") -> list[dict]:
    with open(caminho, "r", encoding="utf-8") as f:
        return json.load(f)


# --------------------------------------------------------------------------
# 2. Extração dos dados relevantes vindos do coletor regional
# --------------------------------------------------------------------------

def _extrair_ph(dados_regiao: dict) -> Optional[float]:
    solo = dados_regiao.get("solo", {})
    phh2o = solo.get("phh2o", {}).get("valores", {})
    return phh2o.get("0-5cm")


def _extrair_precipitacao_anual_mm(dados_regiao: dict) -> Optional[float]:
    normais = dados_regiao.get("normais_climatologicas", {}).get("media_anual", {})
    mm_dia = normais.get("precipitacao_mm_dia")
    return mm_dia * 365 if mm_dia is not None else None


def _bioma_compativel(especie: dict, dados_regiao: dict) -> bool:
    """
    TODO: o coletor de dados regionais ainda não resolve bioma automaticamente
    (depende do MapBiomas/Earth Engine — ver README do coletor). Por enquanto
    aceita qualquer região; quando o bioma entrar no pipeline, comparar aqui
    com `especie["bioma_alvo"]` e penalizar incompatibilidades.
    """
    return True


# --------------------------------------------------------------------------
# 3. Pontuação de espécies
# --------------------------------------------------------------------------

def _pontuar_faixa(valor: float, minimo: float, maximo: float) -> float:
    """1.0 se valor está dentro da faixa ideal; decai linearmente até 0
    conforme se afasta dos limites (folga de 50% da largura da faixa)."""
    if minimo <= valor <= maximo:
        return 1.0
    folga = (maximo - minimo) * 0.5 or 1.0
    distancia = (minimo - valor) if valor < minimo else (valor - maximo)
    return max(0.0, 1.0 - distancia / folga)


def pontuar_especie(especie: dict, dados_regiao: dict) -> dict:
    """Pontua uma espécie (0 a 1) contra os dados enriquecidos da região."""
    ph_solo = _extrair_ph(dados_regiao)
    precipitacao = _extrair_precipitacao_anual_mm(dados_regiao)
    bioma_ok = _bioma_compativel(especie, dados_regiao)

    nota_ph = _pontuar_faixa(ph_solo, especie["ph_min"], especie["ph_max"]) if ph_solo is not None else 0.5
    nota_agua = min(1.0, precipitacao / especie["precipitacao_min_mm_ano"]) if precipitacao is not None else 0.5
    nota_inflamabilidade = 1.0 - especie["indice_inflamabilidade"]
    nota_velocidade = especie["velocidade_formacao_barreira"]
    nota_socioeconomico = especie["valor_socioeconomico"]

    subnotas = {
        "ph": round(nota_ph, 3),
        "agua": round(nota_agua, 3),
        "inflamabilidade": round(nota_inflamabilidade, 3),
        "velocidade_formacao_barreira": round(nota_velocidade, 3),
        "valor_socioeconomico": round(nota_socioeconomico, 3),
    }

    bruta = sum(subnotas[c] * PESOS_CRITERIOS[c] for c in PESOS_CRITERIOS)
    pontuacao_final = bruta if bioma_ok else bruta * 0.2  # penalidade forte fora do bioma

    return {
        "especie": especie["nome_popular"],
        "nome_cientifico": especie["nome_cientifico"],
        "bioma_compativel": bioma_ok,
        "pontuacao_final": round(pontuacao_final, 3),
        "subnotas": subnotas,
        "fonte": especie.get("fonte"),
    }


def recomendar_especies(dados_regiao: dict, especies: list[dict], top_n: int = 3) -> list[dict]:
    ranking = [pontuar_especie(e, dados_regiao) for e in especies]
    ranking.sort(key=lambda r: r["pontuacao_final"], reverse=True)
    return ranking[:top_n]


# --------------------------------------------------------------------------
# 4. Dosagem de biochar (curva dose-resposta da literatura)
# --------------------------------------------------------------------------

def calcular_dose_biochar_t_ha(ph_atual: float, ph_alvo: float, tipo_solo: str = "latossolo_amarelo") -> float:
    """Interpola/extrapola linearmente a dose de biochar (t/ha) necessária
    para elevar o solo até o pH alvo, a partir dos dois pontos conhecidos
    do estudo em solos de Cerrado."""
    pontos = CURVA_BIOCHAR_T_HA.get(tipo_solo, CURVA_BIOCHAR_T_HA["latossolo_amarelo"])
    (ph1, d1), (ph2, d2) = pontos
    if ph_atual >= ph_alvo:
        return 0.0
    inclinacao = (d2 - d1) / (ph2 - ph1)
    dose_no_alvo = d1 + inclinacao * (ph_alvo - ph1)
    return max(0.0, round(dose_no_alvo, 2))


def dose_biochar_por_capsula_g(dose_t_ha: float, capsulas_por_m2: float) -> float:
    """Converte t/ha para gramas por cápsula, dada a densidade de plantio."""
    if capsulas_por_m2 <= 0:
        return 0.0
    gramas_por_m2 = (dose_t_ha * 1_000_000) / 10_000
    return round(gramas_por_m2 / capsulas_por_m2, 2)


# --------------------------------------------------------------------------
# 5. Dosagem de hidrogel (heurística de déficit hídrico)
# --------------------------------------------------------------------------

def calcular_dose_hidrogel_g(precipitacao_anual_mm: Optional[float], precipitacao_min_especie: float) -> float:
    """Quanto maior o déficit entre a precipitação da região e a necessidade
    mínima da espécie, maior a proporção de hidrogel — até um teto."""
    if precipitacao_anual_mm is None or not precipitacao_min_especie:
        return HIDROGEL_BASE_G
    deficit = max(0.0, precipitacao_min_especie - precipitacao_anual_mm)
    deficit_relativo = min(1.0, deficit / precipitacao_min_especie)
    return round(HIDROGEL_BASE_G + deficit_relativo * (HIDROGEL_MAX_G - HIDROGEL_BASE_G), 2)


# --------------------------------------------------------------------------
# 6. Orquestração — junta tudo num único resultado
# --------------------------------------------------------------------------

def recomendar_biocapsula(
    dados_regiao: dict,
    caminho_especies: str = "especies.json",
    tipo_solo: str = "latossolo_amarelo",
    capsulas_por_m2: float = 4.0,
    top_n: int = 3,
) -> dict:
    especies = carregar_especies(caminho_especies)
    ranking = recomendar_especies(dados_regiao, especies, top_n=top_n)

    ph_atual = _extrair_ph(dados_regiao)
    precipitacao_anual = _extrair_precipitacao_anual_mm(dados_regiao)

    recomendacoes = []
    for r in ranking:
        especie_completa = next(e for e in especies if e["nome_popular"] == r["especie"])
        ph_alvo = especie_completa["ph_min"]
        dose_biochar_t_ha = (
            calcular_dose_biochar_t_ha(ph_atual, ph_alvo, tipo_solo) if ph_atual is not None else None
        )
        dose_biochar_g = (
            dose_biochar_por_capsula_g(dose_biochar_t_ha, capsulas_por_m2)
            if dose_biochar_t_ha is not None else None
        )
        dose_hidrogel_g = calcular_dose_hidrogel_g(precipitacao_anual, especie_completa["precipitacao_min_mm_ano"])

        recomendacoes.append({
            **r,
            "dosagem_capsula": {
                "biochar_g": dose_biochar_g,
                "biochar_t_ha_equivalente": dose_biochar_t_ha,
                "hidrogel_g": dose_hidrogel_g,
                "tipo_solo_considerado": tipo_solo,
            },
        })

    return {
        "ph_solo_ponto": ph_atual,
        "precipitacao_anual_mm_ponto": precipitacao_anual,
        "capsulas_por_m2_consideradas": capsulas_por_m2,
        "recomendacoes": recomendacoes,
    }


# --------------------------------------------------------------------------
# CLI simples: lê um JSON já coletado (saída do revivetech_data_collector.py)
# --------------------------------------------------------------------------

def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(
        description="Recomenda espécie + dosagem de biocápsula a partir de um JSON de dados regionais."
    )
    parser.add_argument("json_dados_regiao", help="Caminho do JSON gerado pelo revivetech_data_collector.py")
    parser.add_argument("--especies", default="especies.json", help="Caminho do JSON de espécies")
    parser.add_argument("--tipo-solo", default="latossolo_amarelo",
                         choices=list(CURVA_BIOCHAR_T_HA.keys()))
    parser.add_argument("--capsulas-por-m2", type=float, default=4.0)
    parser.add_argument("--top", type=int, default=3)
    args = parser.parse_args()

    with open(args.json_dados_regiao, "r", encoding="utf-8") as f:
        dados_regiao = json.load(f)

    resultado = recomendar_biocapsula(
        dados_regiao,
        caminho_especies=args.especies,
        tipo_solo=args.tipo_solo,
        capsulas_por_m2=args.capsulas_por_m2,
        top_n=args.top,
    )

    print(json.dumps(resultado, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()