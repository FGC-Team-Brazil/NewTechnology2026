#!/usr/bin/env python3
"""
src/processing/hotspot_filter.py
==================================
Responsabilidade única: carregar o CSV consolidado e filtrá-lo
por bioma (e opcionalmente por município/data).

Remove o gargalo de exibir/processar TODOS os focos quando o usuário
só quer trabalhar com um bioma específico.
"""
from __future__ import annotations

import sys
import unicodedata
from pathlib import Path

import pandas as pd

PROJECT_DIR      = Path(__file__).resolve().parents[2]
SPREADSHEET_PATH = PROJECT_DIR / "planilha_fogos_consolidados.csv"

# Mapeamento de variações do INPE → chave interna do bioma
BIOME_ALIASES: dict[str, str] = {
    "cerrado":        "cerrado",
    "amazonia":       "amazonia",
    "amazônia":       "amazonia",
    "caatinga":       "caatinga",
    "mata atlântica": "mata_atlantica",
    "mata atlantica": "mata_atlantica",
    "pampa":          "pampa",
    "pantanal":       "pantanal",
}


def normalize_biome_key(text: str) -> str:
    """Remove acentos e converte um rótulo de bioma para chave interna."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", str(text))
        if not unicodedata.combining(c)
    ).lower().strip()


def load_consolidated() -> pd.DataFrame:
    """
    Carrega planilha_fogos_consolidados.csv.
    Aborta com mensagem amigável se o arquivo não existir.
    """
    if not SPREADSHEET_PATH.exists():
        print(
            f"Erro: '{SPREADSHEET_PATH.name}' não encontrado.\n"
            "Execute primeiro a etapa de consolidação (ou use --skip-download).",
            file=sys.stderr,
        )
        sys.exit(1)

    df = pd.read_csv(SPREADSHEET_PATH)
    for col in ("municipio", "bioma"):
        if col not in df.columns:
            df[col] = ""
    return df.sort_values("qtd_focos", ascending=False).reset_index(drop=True)


def filter_by_biome(df: pd.DataFrame, biome_key: str) -> pd.DataFrame:
    """
    Filtra o DataFrame pelo bioma selecionado.

    Parameters
    ----------
    df        : DataFrame completo de planilha_fogos_consolidados.csv
    biome_key : chave interna (ex. "cerrado", "amazonia") —
                veja src/ui/terminal.py::BIOMES para a lista completa

    Returns
    -------
    DataFrame filtrado, ordenado por qtd_focos decrescente.
    Se o CSV não tiver coluna 'bioma' ou nenhum foco casar, retorna
    o df completo com um aviso.
    """
    if biome_key == "all":
        result = df.copy()
        result.attrs["biome_key"] = "all"
        return result

    if "bioma" not in df.columns or df["bioma"].isna().all():
        raise ValueError("A coluna 'bioma' está ausente ou vazia; não é seguro exibir focos de outro bioma.")

    normalized_key = normalize_biome_key(biome_key)

    def _matches(val: str) -> bool:
        normalized_val = normalize_biome_key(val)
        # Verifica alias direto
        mapped = BIOME_ALIASES.get(normalized_val, normalized_val)
        return mapped == normalized_key or normalized_val == normalized_key

    mask = df["bioma"].astype(str).apply(_matches)
    filtered = df[mask].reset_index(drop=True)

    if filtered.empty:
        raise ValueError(f"Nenhum foco encontrado para o bioma '{biome_key}'.")

    filtered.attrs["biome_key"] = normalized_key
    return filtered


def search_hotspots(df: pd.DataFrame, term: str) -> pd.DataFrame:
    """
    Filtra por município ou data (texto livre).
    Retorna df original se não encontrar nada.
    """
    norm = normalize_biome_key(term)
    result = df[
        df["municipio"].astype(str).apply(normalize_biome_key).str.contains(norm, na=False)
        | df["data_pura"].astype(str).str.contains(term, na=False)
    ].reset_index(drop=True)
    return result if not result.empty else df
