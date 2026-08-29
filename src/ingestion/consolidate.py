#!/usr/bin/env python3
"""
src/ingestion/consolidate.py
=============================
Responsabilidade única: lê arquivo_unificado.csv (focos brutos do INPE)
e gera planilha_fogos_consolidados.csv — um registro por incêndio real,
agrupando focos próximos no mesmo dia por clustering espacial.

Não conhece nada de UI, motor de decisão ou enriquecimento.
"""
from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

PROJECT_DIR        = Path(__file__).resolve().parents[2]
UNIFIED_FILE_PATH  = PROJECT_DIR / "arquivo_unificado.csv"
SPREADSHEET_PATH   = PROJECT_DIR / "planilha_fogos_consolidados.csv"

CLUSTER_DISTANCE_METERS  = 4_400
FIRE_FOOTPRINT_METERS    = 500
MIN_HOTSPOTS_PER_FIRE    = 2

# Como cada coluna INPE é agregada dentro de um cluster:
COLUMN_AGGREGATION: dict[str, str] = {
    "municipio":             "first",
    "estado":                "first",
    "pais":                  "first",
    "bioma":                 "first",
    "satelite":              "first",
    "frp":                   "mean",
    "precipitacao":          "mean",
    "numero_dias_sem_chuva": "max",
    "risco_fogo":            "max",
}


def _union_all(geometries):
    """Compatível com versões antigas e novas do GeoPandas/Shapely."""
    return (
        geometries.union_all()
        if hasattr(geometries, "union_all")
        else geometries.unary_union
    )


def run() -> None:
    """Ponto de entrada chamado pelo pipeline principal."""
    if not UNIFIED_FILE_PATH.exists():
        print(
            f"Erro: '{UNIFIED_FILE_PATH.name}' não encontrado. "
            "Execute primeiro a etapa de unificação.",
            file=sys.stderr,
        )
        sys.exit(1)

    print("1/4 — Carregando focos brutos...")
    df = pd.read_csv(UNIFIED_FILE_PATH)
    df["data_hora_gmt"] = pd.to_datetime(df["data_hora_gmt"], errors="coerce", utc=True)
    df = df.dropna(subset=["data_hora_gmt", "lat", "lon"])
    if df.empty:
        print("Erro: nenhum foco válido com data e coordenadas.", file=sys.stderr)
        sys.exit(1)
    df["data_pura"] = df["data_hora_gmt"].dt.strftime("%Y-%m-%d")

    geometry = gpd.points_from_xy(df["lon"], df["lat"])
    gdf = gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326").to_crs(epsg=5880)

    print("2/4 — Agrupando focos vizinhos por dia...")
    gdf_tmp = gdf.copy()
    gdf_tmp["geometry"] = gdf_tmp.buffer(CLUSTER_DISTANCE_METERS, resolution=8).make_valid()

    groups = (
        gdf_tmp.groupby("data_pura")["geometry"]
        .apply(_union_all)
        .reset_index()
    )
    groups = gpd.GeoDataFrame(groups, geometry="geometry", crs="EPSG:5880")
    groups = groups.explode(index_parts=False).reset_index(drop=True)
    groups["id_fogo"] = groups.index

    joined = gpd.sjoin(gdf, groups, how="inner", predicate="within")
    joined = joined[joined["data_pura_left"] == joined["data_pura_right"]]

    counts = joined.groupby("id_fogo").size()
    valid_ids = counts[counts >= MIN_HOTSPOTS_PER_FIRE].index
    valid = joined[joined["id_fogo"].isin(valid_ids)].copy()

    if valid.empty:
        print(f"Aviso: nenhum cluster com >= {MIN_HOTSPOTS_PER_FIRE} focos. Usando todos.")
        valid = joined.copy()
        counts = valid.groupby("id_fogo").size()

    print("3/4 — Construindo footprints realistas e agregando...")
    valid["geometry"] = valid.buffer(FIRE_FOOTPRINT_METERS, resolution=8).make_valid()
    fires = (
        valid.groupby(["id_fogo", "data_pura_right"])["geometry"]
        .apply(_union_all)
        .reset_index()
        .rename(columns={"data_pura_right": "data_pura"})
    )
    fires = gpd.GeoDataFrame(fires, geometry="geometry", crs="EPSG:5880")
    fires["qtd_focos"]        = fires["id_fogo"].map(counts)
    fires["tamanho_hectares"] = fires.geometry.area / 10_000

    applicable = {c: m for c, m in COLUMN_AGGREGATION.items() if c in valid.columns}
    missing    = [c for c in COLUMN_AGGREGATION if c not in valid.columns]
    if missing:
        print(f"   Aviso: colunas ausentes no CSV do INPE (ignoradas): {missing}")

    if applicable:
        agg = valid.groupby("id_fogo").agg(applicable).reset_index()
        fires = fires.merge(agg, on="id_fogo", how="left")

    print("4/4 — Salvando planilha consolidada...")
    fires_wgs84 = fires.to_crs(epsg=4326)
    centroids   = fires_wgs84.geometry.centroid
    out = pd.DataFrame({
        "data_pura":        fires_wgs84["data_pura"],
        "lat_fogo":         centroids.y,
        "lon_fogo":         centroids.x,
        "qtd_focos":        fires_wgs84["qtd_focos"],
        "tamanho_hectares": fires_wgs84["tamanho_hectares"],
    })
    for col in applicable:
        out[col] = fires_wgs84[col].values

    out = out.sort_values("qtd_focos", ascending=False).reset_index(drop=True)
    out.to_csv(SPREADSHEET_PATH, index=False)
    print(f"'{SPREADSHEET_PATH.name}' salvo: {len(out)} incêndios consolidados.")
    print(f"Colunas: {list(out.columns)}")


if __name__ == "__main__":
    run()
