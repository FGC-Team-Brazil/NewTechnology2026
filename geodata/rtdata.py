#!/usr/bin/env python3
"""
Revivetech — Regional Data Collector (v2 — focused on biological decision-making)
=========================================================================

Receives a latitude and longitude (and, optionally, a date/time) and returns
as much data as possible about the region, querying multiple public sources
in parallel.

FULLY ONLINE SOURCES (no API key required):
  - Nominatim (OpenStreetMap)  -> administrative location
  - Open-Meteo                 -> elevation, timezone, current weather and
                                   7-day forecast
  - Open-Meteo Archive         -> HISTORICAL weather (when --datetime is
                                   provided, instead of current weather)
  - Open-Meteo Air Quality     -> air quality
  - NASA POWER                 -> monthly climate normals (1991-2020)
  - SoilGrids (ISRIC)          -> physical-chemical soil properties
  - Overpass API (OSM)         -> protected areas, water bodies and
                                   native vegetation fragments nearby

SOURCES THAT REQUIRE LOCAL CONFIGURATION (no simple free public API,
but the ones that matter most for the species/restoration-strategy
decision):
  - Biome and vegetation         -> IBGE GeoJSON, downloaded once
  - Soil erosion risk            -> Embrapa GeoInfo GeoJSON/shapefile, once
  - Current land use             -> MapBiomas via Google Earth Engine
                                     (requires free registration + authentication)
  - Wildfire history             -> cross-referenced with Revivetech's own
                                     database (Phase 1 / INPE data)

This is an MVP: the focus is on the quantity and quality of the data
returned, not on performance or production robustness. All calls are made
with isolated error handling — if one source fails (or is not configured),
the others continue normally and the problem is reported in the "errors"
block of the final result.

Usage:
    # current data
    python revivetech_data_collector.py -23.5505 -46.6333

    # historical data (e.g. weather for a specific wildfire day)
    python revivetech_data_collector.py -23.5505 -46.6333 --datetime 2026-01-15T14:30

    # pointing to local files (biome, erosion, wildfire database)
    python revivetech_data_collector.py -15.7801 -47.9292 \
        --biomes-geojson local_data/ibge_biomes.geojson \
        --erosion-geojson local_data/embrapa_erosion_risk.geojson \
        --wildfire-db local_data/inpe_wildfires.db

Extra dependencies (optional, only needed for biome/erosion):
    pip install geopandas shapely

Output:
    - Readable summary printed to the console
    - Full JSON file saved to ./outputs/
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sqlite3
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Callable, Optional

import requests

from rtdash import save_dashboard

# --------------------------------------------------------------------------
# General configuration
# --------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("revivetech")

HTTP_TIMEOUT = 20  # seconds
USER_AGENT = "Revivetech-DataCollector/2.0 (educational/environmental research use)"

# Approximate bounding box of Brazil, used only as an informational flag
BRAZIL_BBOX = {"lat_min": -33.9, "lat_max": 5.3, "lon_min": -74.0, "lon_max": -28.8}

# Conversion factors from SoilGrids v2.0 "mapped" values to conventional
# units (ISRIC documentation).
SOILGRIDS_CONVERSION = {
    "bdod": (100, "kg/dm³ (bulk density)"),
    "cec": (10, "cmol(c)/kg (CEC)"),
    "cfvo": (10, "% (coarse fragments)"),
    "clay": (10, "% (clay)"),
    "nitrogen": (100, "g/kg (total nitrogen)"),
    "phh2o": (10, "pH in water"),
    "sand": (10, "% (sand)"),
    "silt": (10, "% (silt)"),
    "soc": (10, "g/kg (soil organic carbon)"),
    "ocd": (10, "kg/m³ (organic carbon density)"),
}

# --------------------------------------------------------------------------
# Configuration of LOCAL sources (one-time download or in-house database) —
# these are not online APIs. Can be overridden via environment variable or
# command-line argument.
# --------------------------------------------------------------------------

# GeoJSON of Brazilian biomes (IBGE), in EPSG:4326.
# Download it once at:
#   https://www.ibge.gov.br/geociencias/informacoes-ambientais/vegetacao/15842-biomas.html
# (export/reproject to GeoJSON if it comes as a shapefile).
BIOMES_GEOJSON_PATH = os.environ.get(
    "REVIVETECH_BIOMES_GEOJSON", "local_data/ibge_biomes.geojson"
)

# GeoJSON/shapefile of soil erosion risk (Embrapa GeoInfo):
#   https://www.geoportal.cnptia.embrapa.br/
EROSION_GEOJSON_PATH = os.environ.get(
    "REVIVETECH_EROSION_GEOJSON", "local_data/embrapa_erosion_risk.geojson"
)

# Database (SQLite) fed by Phase 1 of the project (automatic INPE download).
# Assumes a table with latitude, longitude and datetime columns — adjust the
# names via the function parameters if the actual Phase 1 schema differs, or
# swap the sqlite3 connection for psycopg2/mysql-connector if the Phase 1
# database is Postgres/MySQL.
WILDFIRE_DB_PATH = os.environ.get(
    "REVIVETECH_WILDFIRE_DB", "local_data/inpe_wildfires.db"
)

# MapBiomas (land use and cover) via Google Earth Engine. Requires free
# registration at https://code.earthengine.google.com/ and running
# `earthengine authenticate` once on the machine. Disabled by default.
MAPBIOMAS_ENABLED = os.environ.get(
    "REVIVETECH_MAPBIOMAS_ENABLED", "false"
).lower() == "true"

# geopandas/shapely are optional — only needed for biome and erosion.
try:
    import geopandas as gpd
    from shapely.geometry import Point

    _GEOPANDAS_AVAILABLE = True
except ImportError:
    _GEOPANDAS_AVAILABLE = False

_LAYER_CACHE: dict[str, Any] = {}


def _get_json(url: str, params: Optional[dict] = None, headers: Optional[dict] = None) -> dict:
    """Performs a GET request and returns the JSON, raising a clear exception on error."""
    headers = headers or {}
    headers.setdefault("User-Agent", USER_AGENT)
    resp = requests.get(url, params=params, headers=headers, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Approximate distance in km between two points (Haversine formula)."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _load_layer(path: str):
    """
    Loads (and caches) a local geographic layer (GeoJSON or shapefile) using
    geopandas, reprojecting to EPSG:4326 if necessary.
    """
    if not _GEOPANDAS_AVAILABLE:
        raise RuntimeError(
            "geopandas/shapely not installed. Run: pip install geopandas shapely"
        )
    if path in _LAYER_CACHE:
        return _LAYER_CACHE[path]
    if not os.path.exists(path):
        raise FileNotFoundError(
            f"File not found: {path} "
            "(download the layer once and point to the correct path — see module docstring)"
        )
    layer = gpd.read_file(path)
    if layer.crs is not None and layer.crs.to_epsg() != 4326:
        layer = layer.to_crs(epsg=4326)
    _LAYER_CACHE[path] = layer
    return layer


# --------------------------------------------------------------------------
# Individual collectors — each one is independent and never brings down the others
# --------------------------------------------------------------------------

def collect_location(lat: float, lon: float) -> dict:
    """Reverse geocoding via Nominatim (OpenStreetMap)."""
    data = _get_json(
        "https://nominatim.openstreetmap.org/reverse",
        params={"format": "jsonv2", "lat": lat, "lon": lon, "addressdetails": 1, "zoom": 14},
    )
    address = data.get("address", {})
    return {
        "display_name": data.get("display_name"),
        "country": address.get("country"),
        "state": address.get("state"),
        "municipality": (
            address.get("city")
            or address.get("town")
            or address.get("municipality")
            or address.get("village")
        ),
        "neighborhood_district": address.get("suburb") or address.get("district"),
        "approximate_postal_code": address.get("postcode"),
        "osm_type": data.get("type"),
        "osm_category": data.get("category"),
        "osm_id": data.get("osm_id"),
    }


def collect_elevation_and_timezone(lat: float, lon: float) -> dict:
    """Elevation (m) and timezone via Open-Meteo."""
    elevation = _get_json(
        "https://api.open-meteo.com/v1/elevation",
        params={"latitude": lat, "longitude": lon},
    )
    tz = _get_json(
        "https://api.open-meteo.com/v1/forecast",
        params={"latitude": lat, "longitude": lon, "current": "temperature_2m", "timezone": "auto"},
    )
    return {
        "elevation_m": (elevation.get("elevation") or [None])[0],
        "timezone": tz.get("timezone"),
        "utc_offset_seconds": tz.get("utc_offset_seconds"),
    }


def collect_current_weather_and_forecast(lat: float, lon: float) -> dict:
    """Current weather + 7-day forecast via Open-Meteo."""
    data = _get_json(
        "https://api.open-meteo.com/v1/forecast",
        params={
            "latitude": lat,
            "longitude": lon,
            "current": ",".join([
                "temperature_2m", "relative_humidity_2m", "apparent_temperature",
                "precipitation", "rain", "weather_code", "cloud_cover",
                "surface_pressure", "wind_speed_10m", "wind_direction_10m",
            ]),
            "daily": ",".join([
                "temperature_2m_max", "temperature_2m_min", "precipitation_sum",
                "precipitation_probability_max", "wind_speed_10m_max", "uv_index_max",
            ]),
            "forecast_days": 7,
            "timezone": "auto",
        },
    )
    current = data.get("current", {})
    daily = data.get("daily", {})

    forecast_7_days = []
    dates = daily.get("time", [])
    for i, date_str in enumerate(dates):
        forecast_7_days.append({
            "date": date_str,
            "temp_max_c": daily.get("temperature_2m_max", [None] * len(dates))[i],
            "temp_min_c": daily.get("temperature_2m_min", [None] * len(dates))[i],
            "total_precipitation_mm": daily.get("precipitation_sum", [None] * len(dates))[i],
            "precipitation_probability_pct": daily.get("precipitation_probability_max", [None] * len(dates))[i],
            "max_wind_kmh": daily.get("wind_speed_10m_max", [None] * len(dates))[i],
            "max_uv_index": daily.get("uv_index_max", [None] * len(dates))[i],
        })

    return {
        "current_conditions": {
            "temperature_c": current.get("temperature_2m"),
            "relative_humidity_pct": current.get("relative_humidity_2m"),
            "apparent_temperature_c": current.get("apparent_temperature"),
            "precipitation_mm": current.get("precipitation"),
            "cloud_cover_pct": current.get("cloud_cover"),
            "surface_pressure_hpa": current.get("surface_pressure"),
            "wind_kmh": current.get("wind_speed_10m"),
            "wind_direction_deg": current.get("wind_direction_10m"),
            "wmo_weather_code": current.get("weather_code"),
        },
        "forecast_7_days": forecast_7_days,
    }


def collect_historical_weather(lat: float, lon: float, dt: datetime) -> dict:
    """
    HISTORICAL weather conditions via the Open-Meteo Archive API, for the
    given date/time — used instead of current weather when the user passes
    --datetime (e.g. to reconstruct the meteorological scenario of a
    specific wildfire day, instead of the current weather).
    """
    date_str = dt.strftime("%Y-%m-%d")
    data = _get_json(
        "https://archive-api.open-meteo.com/v1/archive",
        params={
            "latitude": lat,
            "longitude": lon,
            "start_date": date_str,
            "end_date": date_str,
            "hourly": ",".join([
                "temperature_2m", "relative_humidity_2m", "precipitation",
                "wind_speed_10m", "wind_direction_10m", "surface_pressure",
                "cloud_cover",
            ]),
            "timezone": "auto",
        },
    )
    hourly = data.get("hourly", {})
    hours = hourly.get("time", [])
    if not hours:
        return {"note": "no historical data for this date/location"}

    target = dt.strftime("%Y-%m-%dT%H:00")
    if target in hours:
        idx = hours.index(target)
    else:
        idx = min(range(len(hours)), key=lambda i: abs(i - dt.hour))

    def _value(field: str):
        values = hourly.get(field, [])
        return values[idx] if idx < len(values) else None

    return {
        "requested_datetime": dt.isoformat(),
        "found_datetime": hours[idx],
        "temperature_c": _value("temperature_2m"),
        "relative_humidity_pct": _value("relative_humidity_2m"),
        "precipitation_mm": _value("precipitation"),
        "wind_kmh": _value("wind_speed_10m"),
        "wind_direction_deg": _value("wind_direction_10m"),
        "pressure_hpa": _value("surface_pressure"),
        "cloud_cover_pct": _value("cloud_cover"),
    }


def collect_air_quality(lat: float, lon: float) -> dict:
    """Current air quality via the Open-Meteo Air Quality API."""
    data = _get_json(
        "https://air-quality-api.open-meteo.com/v1/air-quality",
        params={
            "latitude": lat,
            "longitude": lon,
            "current": ",".join([
                "pm10", "pm2_5", "carbon_monoxide", "nitrogen_dioxide",
                "ozone", "uv_index", "european_aqi",
            ]),
        },
    )
    current = data.get("current", {})
    return {
        "pm10": current.get("pm10"),
        "pm2_5": current.get("pm2_5"),
        "carbon_monoxide": current.get("carbon_monoxide"),
        "nitrogen_dioxide": current.get("nitrogen_dioxide"),
        "ozone": current.get("ozone"),
        "uv_index": current.get("uv_index"),
        "european_air_quality_index": current.get("european_aqi"),
    }


def collect_climate_normals(lat: float, lon: float) -> dict:
    """
    Monthly climate normals (1991-2020) via NASA POWER — essential for
    planning planting season and species tolerant to the local water
    regime, since it's a long-term average (independent of query date).
    """
    parameters = ",".join([
        "T2M", "T2M_MAX", "T2M_MIN",       # average/max/min temperature (°C)
        "RH2M",                              # relative humidity (%)
        "PRECTOTCORR",                       # precipitation (mm/day)
        "ALLSKY_SFC_SW_DWN",                 # solar radiation (kWh/m²/day)
        "WS2M",                              # wind speed at 2m (m/s)
    ])
    data = _get_json(
        "https://power.larc.nasa.gov/api/temporal/climatology/point",
        params={
            "parameters": parameters,
            "community": "AG",
            "longitude": lon,
            "latitude": lat,
            "format": "JSON",
        },
    )
    props = data.get("properties", {}).get("parameter", {})

    months = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
              "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]

    normals_by_month = {}
    for i, month in enumerate(months, start=1):
        normals_by_month[month] = {
            "avg_temp_c": props.get("T2M", {}).get(month),
            "temp_max_c": props.get("T2M_MAX", {}).get(month),
            "temp_min_c": props.get("T2M_MIN", {}).get(month),
            "relative_humidity_pct": props.get("RH2M", {}).get(month),
            "precipitation_mm_day": props.get("PRECTOTCORR", {}).get(month),
            "solar_radiation_kwh_m2_day": props.get("ALLSKY_SFC_SW_DWN", {}).get(month),
            "wind_m_s": props.get("WS2M", {}).get(month),
        }

    annual = {
        "avg_temp_c": props.get("T2M", {}).get("ANN"),
        "relative_humidity_pct": props.get("RH2M", {}).get("ANN"),
        "precipitation_mm_day": props.get("PRECTOTCORR", {}).get("ANN"),
        "solar_radiation_kwh_m2_day": props.get("ALLSKY_SFC_SW_DWN", {}).get("ANN"),
        "wind_m_s": props.get("WS2M", {}).get("ANN"),
    }

    return {
        "source": "NASA POWER (1991-2020 historical series)",
        "annual_average": annual,
        "by_month": normals_by_month,
    }


def collect_soil(lat: float, lon: float) -> dict:
    """
    Physical-chemical soil properties via SoilGrids (ISRIC), 250m
    resolution, at the 0-5cm and 5-15cm depth layers.
    """
    properties = list(SOILGRIDS_CONVERSION.keys())
    params = [("lon", lon), ("lat", lat)]
    for p in properties:
        params.append(("property", p))
    for d in ("0-5cm", "5-15cm"):
        params.append(("depth", d))
    params.append(("value", "mean"))

    resp = requests.get(
        "https://rest.isric.org/soilgrids/v2.0/properties/query",
        params=params,
        headers={"User-Agent": USER_AGENT},
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    data = resp.json()

    result = {}
    layers = data.get("properties", {}).get("layers", [])
    for layer in layers:
        code = layer.get("name")
        factor, unit = SOILGRIDS_CONVERSION.get(code, (1, ""))
        values_by_depth = {}
        for depth in layer.get("depths", []):
            label = depth.get("label")
            raw = (depth.get("values") or {}).get("mean")
            converted_value = round(raw / factor, 2) if raw is not None else None
            values_by_depth[label] = converted_value
        result[code] = {"unit": unit, "values": values_by_depth}

    return result


def collect_protected_areas(lat: float, lon: float, radius_km: float = 15.0) -> list[dict]:
    """
    Nearby protected areas / conservation units via the Overpass API
    (OpenStreetMap data, which incorporates much of the CNUC/ICMBio mesh).
    """
    radius_m = int(radius_km * 1000)
    query = f"""
    [out:json][timeout:25];
    (
      node(around:{radius_m},{lat},{lon})["boundary"="protected_area"];
      way(around:{radius_m},{lat},{lon})["boundary"="protected_area"];
      relation(around:{radius_m},{lat},{lon})["boundary"="protected_area"];
      node(around:{radius_m},{lat},{lon})["leisure"="nature_reserve"];
      way(around:{radius_m},{lat},{lon})["leisure"="nature_reserve"];
      relation(around:{radius_m},{lat},{lon})["leisure"="nature_reserve"];
    );
    out center tags;
    """
    resp = requests.post(
        "https://overpass-api.de/api/interpreter",
        data={"data": query},
        headers={"User-Agent": USER_AGENT},
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    elements = resp.json().get("elements", [])

    areas = []
    for el in elements:
        tags = el.get("tags", {})
        name = tags.get("name") or tags.get("name:pt") or "(unnamed in OSM)"
        if "lat" in el and "lon" in el:
            point_lat, point_lon = el["lat"], el["lon"]
        else:
            center = el.get("center", {})
            point_lat, point_lon = center.get("lat"), center.get("lon")

        distance_km = None
        if point_lat is not None and point_lon is not None:
            distance_km = round(_haversine_km(lat, lon, point_lat, point_lon), 2)

        areas.append({
            "name": name,
            "protection_category": tags.get("protect_class"),
            "designation": tags.get("designation") or tags.get("protection_title"),
            "osm_type": tags.get("boundary") or tags.get("leisure"),
            "approximate_distance_km": distance_km,
        })

    areas.sort(key=lambda a: (a["approximate_distance_km"] is None, a["approximate_distance_km"]))
    return areas


def _overpass_nearest_feature(lat: float, lon: float, radius_km: float, overpass_filters: str) -> Optional[dict]:
    """
    Queries the Overpass API (OSM) for features matching the given filters
    within the radius, and returns the one closest to the point (name,
    type, distance). Generalizes the same technique used in
    `collect_protected_areas`, to be reused for water and native vegetation.
    """
    radius_m = int(radius_km * 1000)
    filled_filters = (
        overpass_filters.replace("__RADIUS__", str(radius_m))
        .replace("__LAT__", str(lat))
        .replace("__LON__", str(lon))
    )
    query = f"[out:json][timeout:25];({filled_filters});out center tags;"
    resp = requests.post(
        "https://overpass-api.de/api/interpreter",
        data={"data": query},
        headers={"User-Agent": USER_AGENT},
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    elements = resp.json().get("elements", [])

    best = None
    for el in elements:
        tags = el.get("tags", {})
        if "lat" in el and "lon" in el:
            plat, plon = el["lat"], el["lon"]
        else:
            center = el.get("center", {})
            plat, plon = center.get("lat"), center.get("lon")
        if plat is None or plon is None:
            continue
        dist = _haversine_km(lat, lon, plat, plon)
        candidate = {
            "name": tags.get("name") or tags.get("name:pt") or "(unnamed in OSM)",
            "type": tags.get("natural") or tags.get("waterway") or tags.get("landuse"),
            "distance_km": round(dist, 3),
        }
        if best is None or candidate["distance_km"] < best["distance_km"]:
            best = candidate
    return best


def collect_water_distance(lat: float, lon: float, radius_km: float = 10.0) -> dict:
    """
    Distance to the nearest body of water (river, stream, lake, pond) —
    important because proximity to water influences species choice and
    restoration priority (Permanent Preservation Area / APP).
    """
    filters = (
        'node(around:__RADIUS__,__LAT__,__LON__)["natural"="water"];'
        'way(around:__RADIUS__,__LAT__,__LON__)["natural"="water"];'
        'way(around:__RADIUS__,__LAT__,__LON__)["waterway"];'
    )
    result = _overpass_nearest_feature(lat, lon, radius_km, filters)
    if result is None:
        return {"nearest_water_body": None, "note": f"none found within {radius_km} km"}
    return {"nearest_water_body": result}


def collect_native_vegetation_distance(lat: float, lon: float, radius_km: float = 10.0) -> dict:
    """
    Distance to the nearest native vegetation (forest/woodland) fragment —
    restoration near remaining native forest tends to have a higher success
    rate (seed dispersal by fauna).
    """
    filters = (
        'way(around:__RADIUS__,__LAT__,__LON__)["natural"="wood"];'
        'relation(around:__RADIUS__,__LAT__,__LON__)["natural"="wood"];'
        'way(around:__RADIUS__,__LAT__,__LON__)["landuse"="forest"];'
    )
    result = _overpass_nearest_feature(lat, lon, radius_km, filters)
    if result is None:
        return {"nearest_vegetation_fragment": None, "note": f"none found within {radius_km} km"}
    return {"nearest_vegetation_fragment": result}


def collect_slope(lat: float, lon: float, distance_m: float = 100.0) -> dict:
    """
    Estimate of slope (%) and terrain classification (bands used by
    Embrapa in the Brazilian Soil Classification System), from a small
    elevation grid (center + north/south/east/west) via the Open-Meteo
    Elevation API. Does not replace a local SRTM in precision, but already
    indicates whether the terrain is flat or steep without requiring any
    download.
    """
    delta_lat = distance_m / 111_320.0
    cos_lat = math.cos(math.radians(lat))
    delta_lon = distance_m / (111_320.0 * cos_lat) if abs(cos_lat) > 1e-9 else 0.0

    points = {
        "center": (lat, lon),
        "north": (lat + delta_lat, lon),
        "south": (lat - delta_lat, lon),
        "east": (lat, lon + delta_lon),
        "west": (lat, lon - delta_lon),
    }

    lats_str = ",".join(str(p[0]) for p in points.values())
    lons_str = ",".join(str(p[1]) for p in points.values())

    data = _get_json(
        "https://api.open-meteo.com/v1/elevation",
        params={"latitude": lats_str, "longitude": lons_str},
    )
    elevations = data.get("elevation", [])
    if len(elevations) < 5:
        raise RuntimeError("unexpected response from the elevation API (expected 5 points)")

    names = list(points.keys())
    elev = dict(zip(names, elevations))

    diffs = [abs(elev["center"] - elev[n]) for n in ("north", "south", "east", "west")]
    max_difference = max(diffs)
    slope_pct = round((max_difference / distance_m) * 100, 2)

    if slope_pct < 3:
        classification = "flat"
    elif slope_pct < 8:
        classification = "gently rolling"
    elif slope_pct < 20:
        classification = "rolling"
    elif slope_pct < 45:
        classification = "strongly rolling"
    elif slope_pct < 75:
        classification = "mountainous"
    else:
        classification = "steep"

    return {
        "estimated_slope_pct": slope_pct,
        "relief_classification": classification,
        "sampling_distance_m": distance_m,
        "point_elevations_m": elev,
        "note": "estimate from a 5-point grid (Open-Meteo); for higher precision, use local SRTM",
    }


def collect_biome_and_vegetation(lat: float, lon: float, geojson_path: str = BIOMES_GEOJSON_PATH) -> dict:
    """
    Identifies the biome (and, if the GeoJSON has the column, the original
    vegetation/phytophysiognomy) via an IBGE GeoJSON downloaded once. This
    is the most important piece of data for deciding species/restoration
    strategy and there is no simple online public API for it — hence it's
    local.
    """
    layer = _load_layer(geojson_path)
    point = Point(lon, lat)
    matches = layer[layer.contains(point)]
    if matches.empty:
        return {
            "biome": None,
            "original_vegetation": None,
            "note": "point outside all polygons in the loaded layer",
        }

    row = matches.iloc[0]

    def _field(*names: str):
        for n in names:
            if n in row and row[n] not in (None, ""):
                return row[n]
        return None

    return {
        "biome": _field("Bioma", "bioma", "BIOMA", "NOM_BIOMA"),
        "original_vegetation": _field("Vegetacao", "vegetacao", "FITOFISIO", "LEGENDA"),
    }


def collect_erosion_risk(lat: float, lon: float, geojson_path: str = EROSION_GEOJSON_PATH) -> dict:
    """
    Soil erosion risk class, from the Embrapa GeoInfo shapefile/GeoJSON
    (one-time download — no public API available). Helps decide whether
    the area needs containment techniques before planting.
    """
    layer = _load_layer(geojson_path)
    point = Point(lon, lat)
    matches = layer[layer.contains(point)]
    if matches.empty:
        return {"erosion_risk_class": None, "note": "point outside all polygons in the loaded layer"}

    row = matches.iloc[0]

    def _field(*names: str):
        for n in names:
            if n in row and row[n] not in (None, ""):
                return row[n]
        return None

    return {"erosion_risk_class": _field("Risco", "CLASSE", "risco_erosao", "LEGENDA")}


def collect_land_use_mapbiomas(lat: float, lon: float, year: int = 2023) -> dict:
    """
    Current land use and cover class (pasture, agriculture, bare soil,
    forest, etc.) via MapBiomas / Google Earth Engine. Determines whether
    direct seeding is possible or whether soil preparation is needed first.

    Requires free registration and prior authentication, so it is disabled
    by default (MAPBIOMAS_ENABLED=False). To enable:
      1. pip install earthengine-api
      2. earthengine authenticate   (once, generates a local token)
      3. export REVIVETECH_MAPBIOMAS_ENABLED=true

    Note: the exact name of the MapBiomas asset/collection changes with
    every new collection release — check the current ID at
    https://mapbiomas.org/ before using in production; the value below is
    just a starting point.
    """
    if not MAPBIOMAS_ENABLED:
        return {
            "land_use": None,
            "note": (
                "MapBiomas disabled — requires Earth Engine registration "
                "(see the collect_land_use_mapbiomas function docstring)"
            ),
        }
    try:
        import ee
    except ImportError as exc:
        raise RuntimeError("earthengine-api not installed (pip install earthengine-api)") from exc

    ee.Initialize()
    asset_id = "projects/mapbiomas-public/assets/brazil/lulc/collection9/mapbiomas_collection90_integration_v1"
    collection = ee.Image(asset_id)
    point = ee.Geometry.Point([lon, lat])
    band = f"classification_{year}"
    value = collection.select(band).reduceRegion(
        reducer=ee.Reducer.first(), geometry=point, scale=30
    ).getInfo()

    return {
        "year": year,
        "mapbiomas_class_code": value.get(band),
        "note": "consult the official MapBiomas legend to translate the code into a land use class",
    }


def collect_local_fire_history(
    lat: float,
    lon: float,
    db_path: str = WILDFIRE_DB_PATH,
    radius_km: float = 5.0,
    table: str = "hotspots",
    lat_column: str = "latitude",
    lon_column: str = "longitude",
    date_column: str = "date_time",
) -> dict:
    """
    Cross-references the queried point with the wildfire hotspot database
    already collected in Phase 1 (INPE) — without needing any new API.
    Returns how many hotspots (and in how many distinct years) have already
    occurred near the point, indicating fire recurrence in the area (an
    area that has already burned 3x in 5 years calls for a different
    strategy than an area burning for the first time).

    Assumes a simple SQLite database with latitude/longitude/date columns.
    Adjust table/column names via the parameters, or swap the sqlite3
    connection for another driver if the Phase 1 database is
    Postgres/MySQL.
    """
    if not os.path.exists(db_path):
        raise FileNotFoundError(
            f"Wildfire database not found at: {db_path} "
            "(point to the database generated in Phase 1 of the project)"
        )

    # rough bounding box to filter before computing the exact distance
    delta = radius_km / 111.0
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute(
            f"""
            SELECT {lat_column} AS lat, {lon_column} AS lon, {date_column} AS date_time
            FROM {table}
            WHERE {lat_column} BETWEEN ? AND ?
              AND {lon_column} BETWEEN ? AND ?
            """,
            (lat - delta, lat + delta, lon - delta, lon + delta),
        )
        rows = cursor.fetchall()
    finally:
        conn.close()

    hotspots_in_radius = []
    for row in rows:
        dist = _haversine_km(lat, lon, row["lat"], row["lon"])
        if dist <= radius_km:
            hotspots_in_radius.append({"date_time": row["date_time"], "distance_km": round(dist, 2)})

    sorted_dates = sorted(f["date_time"] for f in hotspots_in_radius if f["date_time"])
    years = sorted({d[:4] for d in sorted_dates if len(d) >= 4})

    return {
        "queried_radius_km": radius_km,
        "total_hotspots_in_radius": len(hotspots_in_radius),
        "years_with_records": years,
        "distinct_years_recurrence": len(years),
        "hotspots": hotspots_in_radius[:50],  # limits the payload in the summary
    }


# --------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------

def _is_within_brazil_approx(lat: float, lon: float) -> bool:
    return (
        BRAZIL_BBOX["lat_min"] <= lat <= BRAZIL_BBOX["lat_max"]
        and BRAZIL_BBOX["lon_min"] <= lon <= BRAZIL_BBOX["lon_max"]
    )


def collect_all(
    lat: float,
    lon: float,
    radius_km: float = 15.0,
    dt: Optional[datetime] = None,
    water_radius_km: float = 10.0,
    vegetation_radius_km: float = 10.0,
    fire_radius_km: float = 5.0,
    biomes_path: str = BIOMES_GEOJSON_PATH,
    erosion_path: str = EROSION_GEOJSON_PATH,
    wildfire_db_path: str = WILDFIRE_DB_PATH,
) -> dict:
    """
    Runs all collectors in parallel and aggregates the result into a single
    dictionary. Any source that fails (or is not configured) is recorded
    in "errors" without interrupting the others.

    If `dt` is provided, weather is fetched from the historical archive API
    (Open-Meteo Archive) for that specific date/time, instead of the
    current weather + 7-day forecast.
    """
    result: dict[str, Any] = {
        "coordinates": {"latitude": lat, "longitude": lon},
        "queried_datetime": dt.isoformat() if dt else None,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "within_brazil_approx": _is_within_brazil_approx(lat, lon),
        "errors": {},
    }

    tasks: dict[str, Callable[[], Any]] = {
        "location": lambda: collect_location(lat, lon),
        "elevation_and_timezone": lambda: collect_elevation_and_timezone(lat, lon),
        "air_quality": lambda: collect_air_quality(lat, lon),
        "climate_normals": lambda: collect_climate_normals(lat, lon),
        "soil": lambda: collect_soil(lat, lon),
        "nearby_protected_areas": lambda: collect_protected_areas(lat, lon, radius_km),
        "biome_and_vegetation": lambda: collect_biome_and_vegetation(lat, lon, biomes_path),
        "slope_relief": lambda: collect_slope(lat, lon),
        "water_distance": lambda: collect_water_distance(lat, lon, water_radius_km),
        "native_vegetation_distance": lambda: collect_native_vegetation_distance(lat, lon, vegetation_radius_km),
        "erosion_risk": lambda: collect_erosion_risk(lat, lon, erosion_path),
        "land_use": lambda: collect_land_use_mapbiomas(lat, lon),
        "local_fire_history": lambda: collect_local_fire_history(
            lat, lon, wildfire_db_path, fire_radius_km
        ),
    }

    if dt is not None:
        tasks["historical_weather"] = lambda: collect_historical_weather(lat, lon, dt)
    else:
        tasks["current_weather_and_forecast"] = lambda: collect_current_weather_and_forecast(lat, lon)

    with ThreadPoolExecutor(max_workers=len(tasks)) as executor:
        futures = {executor.submit(func): name for name, func in tasks.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                result[name] = future.result()
                log.info("OK    %s", name)
            except Exception as exc:  # noqa: BLE001 - we want to catch everything here
                result["errors"][name] = str(exc)
                log.warning("FAILED %s -> %s", name, exc)

    return result


# --------------------------------------------------------------------------
# Presentation and CLI
# --------------------------------------------------------------------------

def print_summary(data: dict) -> None:
    lat = data["coordinates"]["latitude"]
    lon = data["coordinates"]["longitude"]
    print("\n" + "=" * 70)
    print(f"  Regional data ({lat}, {lon})")
    if data.get("queried_datetime"):
        print(f"  Queried datetime: {data['queried_datetime']}")
    print("=" * 70)

    loc = data.get("location", {})
    if loc:
        print(f"\n📍 Location")
        print(f"   Country: {loc.get('country')}")
        print(f"   State: {loc.get('state')}")
        print(f"   Municipality: {loc.get('municipality')}")

    elev = data.get("elevation_and_timezone", {})
    if elev:
        print(f"\n⛰  Elevation: {elev.get('elevation_m')} m   |   Timezone: {elev.get('timezone')}")

    slope = data.get("slope_relief", {})
    if slope:
        print(f"\n📐 Relief")
        print(f"   Estimated slope: {slope.get('estimated_slope_pct')} %   |   Class: {slope.get('relief_classification')}")

    biome = data.get("biome_and_vegetation", {})
    if biome and biome.get("biome"):
        print(f"\n🧬 Biome and original vegetation")
        print(f"   Biome: {biome.get('biome')}   |   Original vegetation: {biome.get('original_vegetation')}")

    land_use = data.get("land_use", {})
    if land_use and land_use.get("land_use") is not None:
        print(f"\n🟤 Current land use: {land_use.get('land_use')}")

    erosion = data.get("erosion_risk", {})
    if erosion and erosion.get("erosion_risk_class"):
        print(f"\n⚠️  Soil erosion risk: {erosion.get('erosion_risk_class')}")

    water = data.get("water_distance", {}).get("nearest_water_body")
    if water:
        print(f"\n💧 Nearest body of water: {water.get('name')} ({water.get('distance_km')} km)")

    veg = data.get("native_vegetation_distance", {}).get("nearest_vegetation_fragment")
    if veg:
        print(f"\n🌲 Nearest native vegetation fragment: {veg.get('name')} ({veg.get('distance_km')} km)")

    if data.get("queried_datetime"):
        weather = data.get("historical_weather", {})
        if weather:
            print(f"\n🌤  Historical weather ({weather.get('found_datetime')})")
            print(f"   Temperature: {weather.get('temperature_c')} °C   |   Humidity: {weather.get('relative_humidity_pct')} %")
            print(f"   Precipitation: {weather.get('precipitation_mm')} mm   |   Wind: {weather.get('wind_kmh')} km/h")
    else:
        weather = data.get("current_weather_and_forecast", {}).get("current_conditions", {})
        if weather:
            print(f"\n🌤  Weather now")
            print(f"   Temperature: {weather.get('temperature_c')} °C  (feels like {weather.get('apparent_temperature_c')} °C)")
            print(f"   Relative humidity: {weather.get('relative_humidity_pct')} %")
            print(f"   Precipitation: {weather.get('precipitation_mm')} mm   |   Wind: {weather.get('wind_kmh')} km/h")

    air = data.get("air_quality", {})
    if air:
        print(f"\n🫧  Air quality")
        print(f"   PM2.5: {air.get('pm2_5')} µg/m³   |   PM10: {air.get('pm10')} µg/m³   |   European index: {air.get('european_air_quality_index')}")

    normals = data.get("climate_normals", {}).get("annual_average", {})
    if normals:
        print(f"\n📊 Climate normals (annual average, 1991-2020)")
        print(f"   Avg. temp: {normals.get('avg_temp_c')} °C   |   Humidity: {normals.get('relative_humidity_pct')} %")
        print(f"   Precipitation: {normals.get('precipitation_mm_day')} mm/day   |   Solar radiation: {normals.get('solar_radiation_kwh_m2_day')} kWh/m²/day")

    soil = data.get("soil", {})
    if soil:
        print(f"\n🌱 Soil (0-5cm layer)")
        for prop, info in soil.items():
            value = info.get("values", {}).get("0-5cm")
            print(f"   {prop}: {value} {info.get('unit')}")

    protected_areas = data.get("nearby_protected_areas", [])
    if protected_areas:
        print(f"\n🌳 Nearby protected areas (queried radius)")
        for pa in protected_areas[:5]:
            print(f"   - {pa.get('name')} ({pa.get('approximate_distance_km')} km) — {pa.get('osm_type')}")
    else:
        print(f"\n🌳 No protected areas found in the queried radius (or source unavailable).")

    fires = data.get("local_fire_history", {})
    if fires and fires.get("total_hotspots_in_radius") is not None:
        print(f"\n🔥 Wildfire history (in-house database, radius {fires.get('queried_radius_km')} km)")
        print(f"   Total hotspots: {fires.get('total_hotspots_in_radius')}   |   Years with records: {fires.get('years_with_records')}")

    if data.get("errors"):
        print(f"\n⚠️  Sources that failed or were not configured in this run: {list(data['errors'].keys())}")

    print("\n" + "=" * 70)
    print("  Full data saved as JSON (see path below).")
    print("=" * 70 + "\n")


def save_json(data: dict, output_folder: str = "outputs") -> str:
    os.makedirs(output_folder, exist_ok=True)
    lat = data["coordinates"]["latitude"]
    lon = data["coordinates"]["longitude"]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(output_folder, f"region_{lat}_{lon}_{stamp}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Collects as much data as possible about a region from lat/long, "
                    "focused on informing species and biocapsule-composition decisions."
    )
    parser.add_argument("lat", type=float, nargs="?", help="Latitude (e.g. -23.5505)")
    parser.add_argument("lon", type=float, nargs="?", help="Longitude (e.g. -46.6333)")
    parser.add_argument("--lat", dest="lat_flag", type=float, help="Latitude (alternative)")
    parser.add_argument("--lon", dest="lon_flag", type=float, help="Longitude (alternative)")
    parser.add_argument("--radius", type=float, default=15.0, help="Radius (km) for protected area search")
    parser.add_argument("--water-radius", type=float, default=10.0, help="Radius (km) for the nearest body of water search")
    parser.add_argument("--vegetation-radius", type=float, default=10.0, help="Radius (km) for the nearest native vegetation fragment search")
    parser.add_argument("--fire-radius", type=float, default=5.0, help="Radius (km) to cross-reference with the in-house wildfire history (Phase 1)")
    parser.add_argument("--biomes-geojson", type=str, default=BIOMES_GEOJSON_PATH, help="Path to the biomes GeoJSON (IBGE, downloaded once)")
    parser.add_argument("--erosion-geojson", type=str, default=EROSION_GEOJSON_PATH, help="Path to the erosion risk GeoJSON/shapefile (Embrapa GeoInfo)")
    parser.add_argument("--wildfire-db", type=str, default=WILDFIRE_DB_PATH, help="Path to the (SQLite) wildfire hotspot database from Phase 1")
    parser.add_argument("--datetime", type=str, default=None, help="ISO datetime (e.g. 2026-01-15T14:30) to query historical weather instead of current weather")
    parser.add_argument("--output", type=str, default="outputs", help="Folder to save the JSON in")
    parser.add_argument("--no-dashboard", action="store_true", help="Do not generate the dashboard HTML file")
    args = parser.parse_args()

    lat = args.lat if args.lat is not None else args.lat_flag
    lon = args.lon if args.lon is not None else args.lon_flag

    if lat is None or lon is None:
        try:
            lat = float(input("Latitude: ").strip())
            lon = float(input("Longitude: ").strip())
        except (ValueError, EOFError):
            print("Invalid latitude/longitude.", file=sys.stderr)
            sys.exit(1)

    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        print("Coordinates out of valid range.", file=sys.stderr)
        sys.exit(1)

    dt = None
    if args.datetime:
        try:
            dt = datetime.fromisoformat(args.datetime)
        except ValueError:
            print("Invalid --datetime format. Use ISO 8601, e.g. 2026-01-15T14:30", file=sys.stderr)
            sys.exit(1)

    log.info("Collecting data for (%s, %s)...", lat, lon)
    data = collect_all(
        lat,
        lon,
        radius_km=args.radius,
        dt=dt,
        water_radius_km=args.water_radius,
        vegetation_radius_km=args.vegetation_radius,
        fire_radius_km=args.fire_radius,
        biomes_path=args.biomes_geojson,
        erosion_path=args.erosion_geojson,
        wildfire_db_path=args.wildfire_db,
    )

    print_summary(data)
    path = save_json(data, output_folder=args.output)
    print(f"📄 Full JSON saved to: {path}")

    if not args.no_dashboard:
        dashboard_path = save_dashboard(data, output_folder=args.output)
        print(f"🗺️  Dashboard saved to: {dashboard_path}")


if __name__ == "__main__":
    main()