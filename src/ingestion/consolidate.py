#!/usr/bin/env python3
"""Clusterização incremental de focos, limitada ao bioma e ao dia.

O cache mantém um CSV consolidado por data/bioma. Quando o arquivo diário não
muda, aquele dia não cria buffers, não executa união geométrica e não usa RAM.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
import unicodedata
from pathlib import Path
from typing import Optional

import geopandas as gpd
import pandas as pd

from src.config import PIPELINE_DB_PATH, PROJECT_DIR

UNIFIED_FILE_PATH = PROJECT_DIR / "arquivo_unificado.csv"
SPREADSHEET_PATH = PROJECT_DIR / "planilha_fogos_consolidados.csv"
CACHE_DIR = PROJECT_DIR / "data" / "local" / "consolidated"
CLUSTER_DISTANCE_METERS = 4_400
FIRE_FOOTPRINT_METERS = 500
MIN_HOTSPOTS_PER_FIRE = 2

COLUMN_AGGREGATION = {
    "municipio": "first", "estado": "first", "pais": "first", "bioma": "first",
    "satelite": "first", "frp": "mean", "precipitacao": "mean",
    "numero_dias_sem_chuva": "max", "risco_fogo": "max",
}
BIOME_ALIASES = {
    "cerrado": "cerrado", "amazonia": "amazonia", "amazônia": "amazonia",
    "caatinga": "caatinga", "mata atlantica": "mata_atlantica",
    "mata atlântica": "mata_atlantica", "pampa": "pampa", "pantanal": "pantanal",
}


def _normalize(value: object) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", str(value)) if not unicodedata.combining(c)).lower().strip()


def _union_all(geometries):
    return geometries.union_all() if hasattr(geometries, "union_all") else geometries.unary_union


def _connection() -> sqlite3.Connection:
    if not Path(PIPELINE_DB_PATH).exists():
        raise FileNotFoundError("Cache incremental não existe; execute uma atualização de dados ao menos uma vez.")
    connection = sqlite3.connect(PIPELINE_DB_PATH)
    connection.execute("""CREATE TABLE IF NOT EXISTS consolidation_checkpoints (
        biome_key TEXT NOT NULL, data_pura TEXT NOT NULL, input_signature TEXT NOT NULL,
        PRIMARY KEY (biome_key, data_pura)
    )""")
    return connection


def _source_signature(connection: sqlite3.Connection, biome_key: Optional[str], data_pura: str) -> str:
    query = """SELECT DISTINCT input_files.sha256, raw_hotspots.bioma FROM raw_hotspots
        JOIN input_files ON raw_hotspots.source_path = input_files.source_path
        WHERE raw_hotspots.data_pura = ?"""
    hashes = [
        row[0] for row in connection.execute(query, (data_pura,))
        if biome_key is None or BIOME_ALIASES.get(_normalize(row[1]), _normalize(row[1])) == biome_key
    ]
    return hashlib.sha256("|".join(sorted(hashes)).encode()).hexdigest()


def _load_day(connection: sqlite3.Connection, biome_key: Optional[str], data_pura: str) -> pd.DataFrame:
    query = "SELECT record_json FROM raw_hotspots WHERE data_pura = ?"
    rows = [json.loads(row[0]) for row in connection.execute(query, (data_pura,))]
    if biome_key is not None:
        rows = [
            row for row in rows
            if BIOME_ALIASES.get(_normalize(row.get("bioma")), _normalize(row.get("bioma"))) == biome_key
        ]
    return pd.DataFrame(rows)


def _available_dates(connection: sqlite3.Connection, biome_key: Optional[str]) -> list[str]:
    query = "SELECT DISTINCT data_pura, bioma FROM raw_hotspots WHERE data_pura IS NOT NULL"
    matches: set[str] = set()
    for date, biome in connection.execute(query):
        normalized = BIOME_ALIASES.get(_normalize(biome), _normalize(biome))
        if biome_key is None or normalized == biome_key:
            matches.add(date)
    return sorted(matches)


def _consolidate_day(df: pd.DataFrame) -> pd.DataFrame:
    df["data_hora_gmt"] = pd.to_datetime(df["data_hora_gmt"], errors="coerce", utc=True)
    df = df.dropna(subset=["data_hora_gmt", "lat", "lon"])
    if df.empty:
        return pd.DataFrame()
    geometry = gpd.points_from_xy(df["lon"], df["lat"])
    gdf = gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326").to_crs(epsg=5880)
    temporary = gdf[["geometry"]].copy()
    temporary["geometry"] = temporary.buffer(CLUSTER_DISTANCE_METERS, resolution=8).make_valid()
    groups = gpd.GeoDataFrame({"geometry": [_union_all(temporary.geometry)]}, geometry="geometry", crs=gdf.crs)
    groups = groups.explode(index_parts=False).reset_index(drop=True)
    groups["id_fogo"] = groups.index
    joined = gpd.sjoin(gdf, groups, how="inner", predicate="within")
    counts = joined.groupby("id_fogo").size()
    valid = joined[joined["id_fogo"].isin(counts[counts >= MIN_HOTSPOTS_PER_FIRE].index)].copy()
    if valid.empty:
        valid = joined.copy()
        counts = valid.groupby("id_fogo").size()
    valid["geometry"] = valid.buffer(FIRE_FOOTPRINT_METERS, resolution=8).make_valid()
    fires = valid.groupby("id_fogo")["geometry"].apply(_union_all).reset_index()
    fires = gpd.GeoDataFrame(fires, geometry="geometry", crs=gdf.crs)
    fires["qtd_focos"] = fires["id_fogo"].map(counts)
    fires["tamanho_hectares"] = fires.geometry.area / 10_000
    aggregations = {key: value for key, value in COLUMN_AGGREGATION.items() if key in valid.columns}
    fires = fires.merge(valid.groupby("id_fogo").agg(aggregations).reset_index(), on="id_fogo", how="left")
    fires_wgs84 = fires.to_crs(epsg=4326)
    centroid = fires_wgs84.geometry.centroid
    result = pd.DataFrame({"lat_fogo": centroid.y, "lon_fogo": centroid.x, "qtd_focos": fires_wgs84["qtd_focos"], "tamanho_hectares": fires_wgs84["tamanho_hectares"]})
    for key in aggregations:
        result[key] = fires_wgs84[key].values
    return result


def run(biome_key: Optional[str] = None) -> None:
    """Consolida apenas dias novos/alterados do bioma solicitado."""
    # Primeira execução sem download: importa os CSVs já existentes para o
    # cache local e só então consolida. Nas execuções seguintes não relê os
    # arquivos que não mudaram.
    if not Path(PIPELINE_DB_PATH).exists():
        from src.ingestion.unify_hotspots import export_compatibility_files, synchronize_daily_files
        bootstrap_connection, changed = synchronize_daily_files()
        try:
            export_compatibility_files(bootstrap_connection, force=changed)
        finally:
            bootstrap_connection.close()
    try:
        connection = _connection()
    except FileNotFoundError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        sys.exit(1)
    label = biome_key or "todos os biomas"
    dates = _available_dates(connection, biome_key)
    if not dates:
        print(f"Erro: nenhum foco disponível para {label}.", file=sys.stderr)
        sys.exit(1)

    cache_dir = CACHE_DIR / (biome_key or "all")
    cache_dir.mkdir(parents=True, exist_ok=True)
    outputs: list[pd.DataFrame] = []
    processed = reused = 0
    print(f"Consolidando {label}: {len(dates)} dia(s) disponíveis.")
    for position, date in enumerate(dates, start=1):
        signature = _source_signature(connection, biome_key, date)
        cache_path = cache_dir / f"{date}.csv"
        checkpoint = connection.execute("SELECT input_signature FROM consolidation_checkpoints WHERE biome_key = ? AND data_pura = ?", (biome_key or "all", date)).fetchone()
        if checkpoint and checkpoint[0] == signature and cache_path.exists():
            output = pd.read_csv(cache_path)
            reused += 1
        else:
            day_df = _load_day(connection, biome_key, date)
            output = _consolidate_day(day_df)
            output.insert(0, "data_pura", date)
            output.to_csv(cache_path, index=False)
            with connection:
                connection.execute("INSERT OR REPLACE INTO consolidation_checkpoints VALUES (?, ?, ?)", (biome_key or "all", date, signature))
            processed += 1
        outputs.append(output)
        print(f"  {position}/{len(dates)} — {date}: {'cache' if checkpoint and checkpoint[0] == signature and cache_path.exists() else 'reprocessado'}")
    connection.close()
    final = pd.concat(outputs, ignore_index=True).sort_values("qtd_focos", ascending=False).reset_index(drop=True)
    final.to_csv(SPREADSHEET_PATH, index=False)
    print(f"'{SPREADSHEET_PATH.name}' salvo: {len(final):,} incêndios ({processed} dia(s) processados, {reused} reutilizados).")


if __name__ == "__main__":
    run()
