#!/usr/bin/env python3
"""
Step 2: reads 'arquivo_unificado.csv' (raw INPE hotspots) and generates
'planilha_fogos_consolidados.csv': one row per consolidated fire. Nearby
hotspots from the same day are first clustered within 4.4 km and then given a
realistic 500 m footprint, as previously implemented only in the notebook.

Usage:
    python generate_spreadsheet.py
"""
import sys
from pathlib import Path

import geopandas as gpd
import pandas as pd

PROJECT_DIR = Path(__file__).parent.resolve()
UNIFIED_FILE_PATH = PROJECT_DIR / "arquivo_unificado.csv"
SPREADSHEET_PATH = PROJECT_DIR / "planilha_fogos_consolidados.csv"
CLUSTER_DISTANCE_METERS = 4400
FIRE_FOOTPRINT_METERS = 500
MIN_HOTSPOTS_PER_FIRE = 2

# How each column from INPE is aggregated within a single cluster (id_fogo):
#   "first" -> categorical fields that should be identical within the cluster
#   "mean"  -> continuous metrics (mean represents the entire cluster)
#   "max"   -> metrics where worst-case scenario matters most (driest day, highest risk)
COLUMN_AGGREGATION = {
    "municipio": "first",
    "estado": "first",
    "pais": "first",
    "bioma": "first",
    "satelite": "first",
    "frp": "mean",
    "precipitacao": "mean",
    "numero_dias_sem_chuva": "max",
    "risco_fogo": "max",
}


def _union_all(geometries):
    """Use the GeoPandas/Shapely union method available in this environment."""
    return geometries.union_all() if hasattr(geometries, "union_all") else geometries.unary_union


def main() -> None:
    if not UNIFIED_FILE_PATH.exists():
        print(f"Error: '{UNIFIED_FILE_PATH.name}' not found. Run 'python unify_hotspots.py' first.", file=sys.stderr)
        sys.exit(1)

    print("1/4 - Loading raw hotspots...")
    df = pd.read_csv(UNIFIED_FILE_PATH)
    df["data_hora_gmt"] = pd.to_datetime(df["data_hora_gmt"], errors="coerce", utc=True)
    df = df.dropna(subset=["data_hora_gmt", "lat", "lon"])
    if df.empty:
        print("Error: no valid hotspots with date and coordinates found.", file=sys.stderr)
        sys.exit(1)
    df["data_pura"] = df["data_hora_gmt"].dt.strftime("%Y-%m-%d")

    geometry = gpd.points_from_xy(df["lon"], df["lat"])
    gdf = gpd.GeoDataFrame(df, geometry=geometry, crs="EPSG:4326")
    gdf_metric = gdf.to_crs(epsg=5880)

    print("2/4 - Grouping neighboring hotspots by day...")
    gdf_temp = gdf_metric.copy()
    gdf_temp["geometry"] = gdf_temp.buffer(CLUSTER_DISTANCE_METERS, resolution=8).make_valid()

    gdf_groups = (
        gdf_temp.groupby("data_pura")["geometry"]
        .apply(_union_all)
        .reset_index()
    )
    gdf_groups = gpd.GeoDataFrame(gdf_groups, geometry="geometry", crs="EPSG:5880")
    gdf_groups = gdf_groups.explode(index_parts=False).reset_index(drop=True)
    gdf_groups["id_fogo"] = gdf_groups.index

    hotspots_with_id = gpd.sjoin(gdf_metric, gdf_groups, how="inner", predicate="within")
    hotspots_with_id = hotspots_with_id[hotspots_with_id["data_pura_left"] == hotspots_with_id["data_pura_right"]]

    counts = hotspots_with_id.groupby("id_fogo").size()
    valid_ids = counts[counts >= MIN_HOTSPOTS_PER_FIRE].index
    valid_hotspots = hotspots_with_id[hotspots_with_id["id_fogo"].isin(valid_ids)].copy()

    if valid_hotspots.empty:
        print(f"Warning: no clusters with >= {MIN_HOTSPOTS_PER_FIRE} hotspots found. Using all hotspots.")
        valid_hotspots = hotspots_with_id.copy()
        counts = valid_hotspots.groupby("id_fogo").size()

    print("3/4 - Building realistic footprints and aggregating wildfire data...")
    valid_hotspots["geometry"] = valid_hotspots.buffer(FIRE_FOOTPRINT_METERS, resolution=8).make_valid()
    fires = (
        valid_hotspots.groupby(["id_fogo", "data_pura_right"])["geometry"]
        .apply(_union_all)
        .reset_index()
        .rename(columns={"data_pura_right": "data_pura"})
    )
    fires = gpd.GeoDataFrame(fires, geometry="geometry", crs="EPSG:5880")
    fires["qtd_focos"] = fires["id_fogo"].map(counts)
    fires["tamanho_hectares"] = fires.geometry.area / 10_000

    applicable_aggregations = {
        col: mode for col, mode in COLUMN_AGGREGATION.items() if col in valid_hotspots.columns
    }
    missing_columns = [c for c in COLUMN_AGGREGATION if c not in valid_hotspots.columns]
    if missing_columns:
        print(f"   Warning: columns not found in INPE CSV (ignored): {missing_columns}")

    if applicable_aggregations:
        df_aggregated = valid_hotspots.groupby("id_fogo").agg(applicable_aggregations).reset_index()
        fires = fires.merge(df_aggregated, on="id_fogo", how="left")

    print("4/4 - Saving consolidated spreadsheet...")
    fires_wgs84 = fires.to_crs(epsg=4326)
    centroids = fires_wgs84.geometry.centroid
    df_spreadsheet = pd.DataFrame({
        "data_pura": fires_wgs84["data_pura"],
        "lat_fogo": centroids.y,
        "lon_fogo": centroids.x,
        "qtd_focos": fires_wgs84["qtd_focos"],
        "tamanho_hectares": fires_wgs84["tamanho_hectares"],
    })
    for col in applicable_aggregations:
        df_spreadsheet[col] = fires_wgs84[col]

    df_spreadsheet = df_spreadsheet.sort_values("qtd_focos", ascending=False).reset_index(drop=True)
    df_spreadsheet.to_csv(SPREADSHEET_PATH, index=False)
    print(f"'{SPREADSHEET_PATH.name}' saved: {len(df_spreadsheet)} consolidated wildfires.")
    print(f"Columns: {list(df_spreadsheet.columns)}")


if __name__ == "__main__":
    main()
