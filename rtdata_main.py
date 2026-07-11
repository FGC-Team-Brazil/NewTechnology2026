#!/usr/bin/env python3
"""
Revivetech — Coletor de Dados Regionais (v2 — foco em decisão biológica)
=========================================================================

Recebe uma latitude e longitude (e, opcionalmente, uma data/hora) e retorna
a maior quantidade possível de dados sobre a região, consultando múltiplas
fontes públicas em paralelo.

FONTES 100% ONLINE (sem chave):
  - Nominatim (OpenStreetMap)  -> localização administrativa
  - Open-Meteo                 -> elevação, fuso horário, clima atual e
                                   previsão de 7 dias
  - Open-Meteo Archive         -> clima HISTÓRICO (quando --data-hora é
                                   informado, no lugar do clima atual)
  - Open-Meteo Air Quality     -> qualidade do ar
  - NASA POWER                 -> normais climatológicas mensais (1991-2020)
  - SoilGrids (ISRIC)          -> propriedades físico-químicas do solo
  - Overpass API (OSM)         -> áreas protegidas, corpos d'água e
                                   fragmentos de vegetação nativa próximos

FONTES QUE EXIGEM CONFIGURAÇÃO LOCAL (não têm API pública gratuita simples,
mas são as que mais pesam na decisão de espécie/estratégia de restauração):
  - Bioma e vegetação original  -> GeoJSON do IBGE, baixado 1x
  - Risco de erosão do solo     -> GeoJSON/shapefile da Embrapa GeoInfo, 1x
  - Uso do solo atual           -> MapBiomas via Google Earth Engine
                                   (exige cadastro gratuito + autenticação)
  - Histórico de queimadas      -> cruzamento com o banco próprio do
                                   Revivetech (Fase 1 / dados do INPE)

Este é um MVP: o foco é quantidade e qualidade de dados retornados, não
performance ou robustez de produção. Todas as chamadas são feitas com
tratamento de erro isolado — se uma fonte falhar (ou não estiver
configurada), as demais continuam normalmente e o problema é reportado no
bloco "erros" do resultado final.

Uso:
    # dados atuais
    python revivetech_data_collector.py -23.5505 -46.6333

    # dados históricos (ex: clima de um dia de queimada específico)
    python revivetech_data_collector.py -23.5505 -46.6333 --data-hora 2026-01-15T14:30

    # apontando para os arquivos locais (bioma, erosão, banco de queimadas)
    python revivetech_data_collector.py -15.7801 -47.9292 \
        --biomas-geojson dados_locais/biomas_ibge.geojson \
        --erosao-geojson dados_locais/risco_erosao_embrapa.geojson \
        --banco-queimadas dados_locais/queimadas_inpe.db

Dependências extras (opcionais, só para bioma/erosão):
    pip install geopandas shapely

Saída:
    - Resumo legível impresso no console
    - Arquivo JSON completo salvo em ./saidas/
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

from rtdash import salvar_dashboard

# --------------------------------------------------------------------------
# Configuração geral
# --------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("revivetech")

HTTP_TIMEOUT = 20  # segundos
USER_AGENT = "Revivetech-DataCollector/2.0 (uso educacional/pesquisa ambiental)"

# Bounding box aproximado do Brasil, usado só como sinalização informativa
BRASIL_BBOX = {"lat_min": -33.9, "lat_max": 5.3, "lon_min": -74.0, "lon_max": -28.8}

# Fatores de conversão dos valores "mapeados" do SoilGrids v2.0 para unidades
# convencionais (documentação ISRIC).
SOILGRIDS_CONVERSAO = {
    "bdod": (100, "kg/dm³ (densidade aparente)"),
    "cec": (10, "cmol(c)/kg (CTC)"),
    "cfvo": (10, "% (fragmentos grosseiros)"),
    "clay": (10, "% (argila)"),
    "nitrogen": (100, "g/kg (nitrogênio total)"),
    "phh2o": (10, "pH em água"),
    "sand": (10, "% (areia)"),
    "silt": (10, "% (silte)"),
    "soc": (10, "g/kg (carbono orgânico do solo)"),
    "ocd": (10, "kg/m³ (densidade de carbono orgânico)"),
}

# --------------------------------------------------------------------------
# Configuração das fontes LOCAIS (download único ou banco próprio) — não são
# APIs online. Podem ser sobrescritas por variável de ambiente ou por
# argumento de linha de comando.
# --------------------------------------------------------------------------

# GeoJSON de biomas do Brasil (IBGE), em EPSG:4326.
# Baixe uma vez em:
#   https://www.ibge.gov.br/geociencias/informacoes-ambientais/vegetacao/15842-biomas.html
# (exporte/reprojete para GeoJSON se vier como shapefile).
CAMINHO_BIOMAS_GEOJSON = os.environ.get(
    "REVIVETECH_BIOMAS_GEOJSON", "dados_locais/biomas_ibge.geojson"
)

# GeoJSON/shapefile de risco de erosão do solo (Embrapa GeoInfo):
#   https://www.geoportal.cnptia.embrapa.br/
CAMINHO_EROSAO_GEOJSON = os.environ.get(
    "REVIVETECH_EROSAO_GEOJSON", "dados_locais/risco_erosao_embrapa.geojson"
)

# Banco (SQLite) alimentado pela Fase 1 do projeto (download automático do
# INPE). Assume-se uma tabela com colunas de latitude, longitude e data/hora
# — ajuste os nomes via parâmetros da função se o esquema real for diferente,
# ou troque a conexão sqlite3 por psycopg2/mysql-connector se o banco da
# Fase 1 for Postgres/MySQL.
CAMINHO_BANCO_QUEIMADAS = os.environ.get(
    "REVIVETECH_BANCO_QUEIMADAS", "dados_locais/queimadas_inpe.db"
)

# MapBiomas (uso e cobertura do solo) via Google Earth Engine. Exige cadastro
# gratuito em https://code.earthengine.google.com/ e rodar
# `earthengine authenticate` uma vez na máquina. Desativado por padrão.
MAPBIOMAS_HABILITADO = os.environ.get(
    "REVIVETECH_MAPBIOMAS_HABILITADO", "false"
).lower() == "true"

# geopandas/shapely são opcionais — só necessários para bioma e erosão.
try:
    import geopandas as gpd
    from shapely.geometry import Point

    _GEOPANDAS_DISPONIVEL = True
except ImportError:
    _GEOPANDAS_DISPONIVEL = False

_CACHE_CAMADAS: dict[str, Any] = {}


def _get_json(url: str, params: Optional[dict] = None, headers: Optional[dict] = None) -> dict:
    """Faz um GET e retorna o JSON, lançando exceção clara em caso de erro."""
    headers = headers or {}
    headers.setdefault("User-Agent", USER_AGENT)
    resp = requests.get(url, params=params, headers=headers, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distância aproximada em km entre dois pontos (fórmula de Haversine)."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _carregar_camada(caminho: str):
    """
    Carrega (e mantém em cache) uma camada geográfica local (GeoJSON ou
    shapefile) usando geopandas, reprojetando para EPSG:4326 se necessário.
    """
    if not _GEOPANDAS_DISPONIVEL:
        raise RuntimeError(
            "geopandas/shapely não instalados. Rode: pip install geopandas shapely"
        )
    if caminho in _CACHE_CAMADAS:
        return _CACHE_CAMADAS[caminho]
    if not os.path.exists(caminho):
        raise FileNotFoundError(
            f"Arquivo não encontrado: {caminho} "
            "(baixe a camada uma vez e aponte o caminho correto — ver docstring do módulo)"
        )
    camada = gpd.read_file(caminho)
    if camada.crs is not None and camada.crs.to_epsg() != 4326:
        camada = camada.to_crs(epsg=4326)
    _CACHE_CAMADAS[caminho] = camada
    return camada


# --------------------------------------------------------------------------
# Coletores individuais — cada um é independente e nunca derruba os demais
# --------------------------------------------------------------------------

def coletar_localizacao(lat: float, lon: float) -> dict:
    """Geocodificação reversa via Nominatim (OpenStreetMap)."""
    dados = _get_json(
        "https://nominatim.openstreetmap.org/reverse",
        params={"format": "jsonv2", "lat": lat, "lon": lon, "addressdetails": 1, "zoom": 14},
    )
    endereco = dados.get("address", {})
    return {
        "nome_exibicao": dados.get("display_name"),
        "pais": endereco.get("country"),
        "estado": endereco.get("state"),
        "municipio": (
            endereco.get("city")
            or endereco.get("town")
            or endereco.get("municipality")
            or endereco.get("village")
        ),
        "bairro_distrito": endereco.get("suburb") or endereco.get("district"),
        "cep_aproximado": endereco.get("postcode"),
        "osm_tipo": dados.get("type"),
        "osm_categoria": dados.get("category"),
        "osm_id": dados.get("osm_id"),
    }


def coletar_elevacao_e_fuso(lat: float, lon: float) -> dict:
    """Elevação (m) e fuso horário via Open-Meteo."""
    elevacao = _get_json(
        "https://api.open-meteo.com/v1/elevation",
        params={"latitude": lat, "longitude": lon},
    )
    fuso = _get_json(
        "https://api.open-meteo.com/v1/forecast",
        params={"latitude": lat, "longitude": lon, "current": "temperature_2m", "timezone": "auto"},
    )
    return {
        "elevacao_m": (elevacao.get("elevation") or [None])[0],
        "fuso_horario": fuso.get("timezone"),
        "utc_offset_segundos": fuso.get("utc_offset_seconds"),
    }


def coletar_clima_atual_e_previsao(lat: float, lon: float) -> dict:
    """Clima atual + previsão de 7 dias via Open-Meteo."""
    dados = _get_json(
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
    atual = dados.get("current", {})
    diario = dados.get("daily", {})

    previsao_7dias = []
    datas = diario.get("time", [])
    for i, data_str in enumerate(datas):
        previsao_7dias.append({
            "data": data_str,
            "temp_max_c": diario.get("temperature_2m_max", [None] * len(datas))[i],
            "temp_min_c": diario.get("temperature_2m_min", [None] * len(datas))[i],
            "precipitacao_total_mm": diario.get("precipitation_sum", [None] * len(datas))[i],
            "prob_precipitacao_pct": diario.get("precipitation_probability_max", [None] * len(datas))[i],
            "vento_max_kmh": diario.get("wind_speed_10m_max", [None] * len(datas))[i],
            "indice_uv_max": diario.get("uv_index_max", [None] * len(datas))[i],
        })

    return {
        "condicoes_atuais": {
            "temperatura_c": atual.get("temperature_2m"),
            "umidade_relativa_pct": atual.get("relative_humidity_2m"),
            "sensacao_termica_c": atual.get("apparent_temperature"),
            "precipitacao_mm": atual.get("precipitation"),
            "cobertura_de_nuvens_pct": atual.get("cloud_cover"),
            "pressao_superficie_hpa": atual.get("surface_pressure"),
            "vento_kmh": atual.get("wind_speed_10m"),
            "direcao_vento_graus": atual.get("wind_direction_10m"),
            "codigo_tempo_wmo": atual.get("weather_code"),
        },
        "previsao_7_dias": previsao_7dias,
    }


def coletar_clima_historico(lat: float, lon: float, data_hora: datetime) -> dict:
    """
    Condições climáticas HISTÓRICAS via Open-Meteo Archive API, para a
    data/hora informada — usado no lugar do clima atual quando o usuário
    passa --data-hora (ex: reconstituir o cenário meteorológico do dia de
    uma queimada específica, em vez do clima de agora).
    """
    data_str = data_hora.strftime("%Y-%m-%d")
    dados = _get_json(
        "https://archive-api.open-meteo.com/v1/archive",
        params={
            "latitude": lat,
            "longitude": lon,
            "start_date": data_str,
            "end_date": data_str,
            "hourly": ",".join([
                "temperature_2m", "relative_humidity_2m", "precipitation",
                "wind_speed_10m", "wind_direction_10m", "surface_pressure",
                "cloud_cover",
            ]),
            "timezone": "auto",
        },
    )
    horario = dados.get("hourly", {})
    horas = horario.get("time", [])
    if not horas:
        return {"observacao": "sem dados históricos para esta data/local"}

    alvo = data_hora.strftime("%Y-%m-%dT%H:00")
    if alvo in horas:
        idx = horas.index(alvo)
    else:
        idx = min(range(len(horas)), key=lambda i: abs(i - data_hora.hour))

    def _valor(campo: str):
        lista = horario.get(campo, [])
        return lista[idx] if idx < len(lista) else None

    return {
        "data_hora_solicitada": data_hora.isoformat(),
        "data_hora_encontrada": horas[idx],
        "temperatura_c": _valor("temperature_2m"),
        "umidade_relativa_pct": _valor("relative_humidity_2m"),
        "precipitacao_mm": _valor("precipitation"),
        "vento_kmh": _valor("wind_speed_10m"),
        "direcao_vento_graus": _valor("wind_direction_10m"),
        "pressao_hpa": _valor("surface_pressure"),
        "cobertura_nuvens_pct": _valor("cloud_cover"),
    }


def coletar_qualidade_do_ar(lat: float, lon: float) -> dict:
    """Qualidade do ar atual via Open-Meteo Air Quality API."""
    dados = _get_json(
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
    atual = dados.get("current", {})
    return {
        "pm10": atual.get("pm10"),
        "pm2_5": atual.get("pm2_5"),
        "monoxido_de_carbono": atual.get("carbon_monoxide"),
        "dioxido_de_nitrogenio": atual.get("nitrogen_dioxide"),
        "ozonio": atual.get("ozone"),
        "indice_uv": atual.get("uv_index"),
        "indice_qualidade_ar_europeu": atual.get("european_aqi"),
    }


def coletar_normais_climatologicas(lat: float, lon: float) -> dict:
    """
    Normais climatológicas mensais (1991-2020) via NASA POWER — essenciais
    para planejar época de plantio e espécies tolerantes ao regime hídrico
    local, já que é uma média de longo prazo (não depende do dia da consulta).
    """
    parametros = ",".join([
        "T2M", "T2M_MAX", "T2M_MIN",       # temperatura média/máx/mín (°C)
        "RH2M",                              # umidade relativa (%)
        "PRECTOTCORR",                       # precipitação (mm/dia)
        "ALLSKY_SFC_SW_DWN",                 # radiação solar (kWh/m²/dia)
        "WS2M",                              # velocidade do vento a 2m (m/s)
    ])
    dados = _get_json(
        "https://power.larc.nasa.gov/api/temporal/climatology/point",
        params={
            "parameters": parametros,
            "community": "AG",
            "longitude": lon,
            "latitude": lat,
            "format": "JSON",
        },
    )
    props = dados.get("properties", {}).get("parameter", {})

    meses = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN",
             "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]

    normais_por_mes = {}
    for i, mes in enumerate(meses, start=1):
        normais_por_mes[mes] = {
            "temp_media_c": props.get("T2M", {}).get(mes),
            "temp_max_c": props.get("T2M_MAX", {}).get(mes),
            "temp_min_c": props.get("T2M_MIN", {}).get(mes),
            "umidade_relativa_pct": props.get("RH2M", {}).get(mes),
            "precipitacao_mm_dia": props.get("PRECTOTCORR", {}).get(mes),
            "radiacao_solar_kwh_m2_dia": props.get("ALLSKY_SFC_SW_DWN", {}).get(mes),
            "vento_m_s": props.get("WS2M", {}).get(mes),
        }

    anual = {
        "temp_media_c": props.get("T2M", {}).get("ANN"),
        "umidade_relativa_pct": props.get("RH2M", {}).get("ANN"),
        "precipitacao_mm_dia": props.get("PRECTOTCORR", {}).get("ANN"),
        "radiacao_solar_kwh_m2_dia": props.get("ALLSKY_SFC_SW_DWN", {}).get("ANN"),
        "vento_m_s": props.get("WS2M", {}).get("ANN"),
    }

    return {
        "fonte": "NASA POWER (série histórica 1991-2020)",
        "media_anual": anual,
        "por_mes": normais_por_mes,
    }


def coletar_solo(lat: float, lon: float) -> dict:
    """
    Propriedades físico-químicas do solo via SoilGrids (ISRIC), resolução
    250m, nas camadas de 0-5cm e 5-15cm de profundidade.
    """
    propriedades = list(SOILGRIDS_CONVERSAO.keys())
    params = [("lon", lon), ("lat", lat)]
    for p in propriedades:
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
    dados = resp.json()

    resultado = {}
    camadas = dados.get("properties", {}).get("layers", [])
    for camada in camadas:
        codigo = camada.get("name")
        fator, unidade = SOILGRIDS_CONVERSAO.get(codigo, (1, ""))
        valores_por_profundidade = {}
        for prof in camada.get("depths", []):
            rotulo = prof.get("label")
            bruto = (prof.get("values") or {}).get("mean")
            valor_convertido = round(bruto / fator, 2) if bruto is not None else None
            valores_por_profundidade[rotulo] = valor_convertido
        resultado[codigo] = {"unidade": unidade, "valores": valores_por_profundidade}

    return resultado


def coletar_areas_protegidas(lat: float, lon: float, raio_km: float = 15.0) -> list[dict]:
    """
    Unidades de conservação / áreas protegidas próximas via Overpass API
    (dados do OpenStreetMap, que incorpora boa parte da malha do CNUC/ICMBio).
    """
    raio_m = int(raio_km * 1000)
    query = f"""
    [out:json][timeout:25];
    (
      node(around:{raio_m},{lat},{lon})["boundary"="protected_area"];
      way(around:{raio_m},{lat},{lon})["boundary"="protected_area"];
      relation(around:{raio_m},{lat},{lon})["boundary"="protected_area"];
      node(around:{raio_m},{lat},{lon})["leisure"="nature_reserve"];
      way(around:{raio_m},{lat},{lon})["leisure"="nature_reserve"];
      relation(around:{raio_m},{lat},{lon})["leisure"="nature_reserve"];
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
    elementos = resp.json().get("elements", [])

    areas = []
    for el in elementos:
        tags = el.get("tags", {})
        nome = tags.get("name") or tags.get("name:pt") or "(sem nome no OSM)"
        if "lat" in el and "lon" in el:
            ponto_lat, ponto_lon = el["lat"], el["lon"]
        else:
            centro = el.get("center", {})
            ponto_lat, ponto_lon = centro.get("lat"), centro.get("lon")

        distancia_km = None
        if ponto_lat is not None and ponto_lon is not None:
            distancia_km = round(_haversine_km(lat, lon, ponto_lat, ponto_lon), 2)

        areas.append({
            "nome": nome,
            "categoria_protecao": tags.get("protect_class"),
            "designacao": tags.get("designation") or tags.get("protection_title"),
            "tipo_osm": tags.get("boundary") or tags.get("leisure"),
            "distancia_aproximada_km": distancia_km,
        })

    areas.sort(key=lambda a: (a["distancia_aproximada_km"] is None, a["distancia_aproximada_km"]))
    return areas


def _overpass_feature_mais_proxima(lat: float, lon: float, raio_km: float, filtros_overpass: str) -> Optional[dict]:
    """
    Busca no Overpass API (OSM) as feições que atendem aos filtros passados
    dentro do raio, e retorna a mais próxima do ponto (nome, tipo, distância).
    Generaliza a mesma técnica usada em `coletar_areas_protegidas`, para
    reaproveitar em água e vegetação nativa.
    """
    raio_m = int(raio_km * 1000)
    filtros_preenchidos = (
        filtros_overpass.replace("__RAIO__", str(raio_m))
        .replace("__LAT__", str(lat))
        .replace("__LON__", str(lon))
    )
    query = f"[out:json][timeout:25];({filtros_preenchidos});out center tags;"
    resp = requests.post(
        "https://overpass-api.de/api/interpreter",
        data={"data": query},
        headers={"User-Agent": USER_AGENT},
        timeout=HTTP_TIMEOUT,
    )
    resp.raise_for_status()
    elementos = resp.json().get("elements", [])

    melhor = None
    for el in elementos:
        tags = el.get("tags", {})
        if "lat" in el and "lon" in el:
            plat, plon = el["lat"], el["lon"]
        else:
            centro = el.get("center", {})
            plat, plon = centro.get("lat"), centro.get("lon")
        if plat is None or plon is None:
            continue
        dist = _haversine_km(lat, lon, plat, plon)
        candidato = {
            "nome": tags.get("name") or tags.get("name:pt") or "(sem nome no OSM)",
            "tipo": tags.get("natural") or tags.get("waterway") or tags.get("landuse"),
            "distancia_km": round(dist, 3),
        }
        if melhor is None or candidato["distancia_km"] < melhor["distancia_km"]:
            melhor = candidato
    return melhor


def coletar_distancia_agua(lat: float, lon: float, raio_km: float = 10.0) -> dict:
    """
    Distância até o corpo d'água (rio, córrego, lago, açude) mais próximo —
    importante porque proximidade de água influencia espécie escolhida e
    prioridade de restauração (Área de Preservação Permanente / APP).
    """
    filtros = (
        'node(around:__RAIO__,__LAT__,__LON__)["natural"="water"];'
        'way(around:__RAIO__,__LAT__,__LON__)["natural"="water"];'
        'way(around:__RAIO__,__LAT__,__LON__)["waterway"];'
    )
    resultado = _overpass_feature_mais_proxima(lat, lon, raio_km, filtros)
    if resultado is None:
        return {"corpo_dagua_mais_proximo": None, "observacao": f"nenhum encontrado em {raio_km} km"}
    return {"corpo_dagua_mais_proximo": resultado}


def coletar_distancia_vegetacao_nativa(lat: float, lon: float, raio_km: float = 10.0) -> dict:
    """
    Distância até o fragmento de vegetação nativa (mata/floresta) mais
    próximo — restauração perto de mata remanescente tende a ter taxa de
    sucesso maior (dispersão de sementes por fauna).
    """
    filtros = (
        'way(around:__RAIO__,__LAT__,__LON__)["natural"="wood"];'
        'relation(around:__RAIO__,__LAT__,__LON__)["natural"="wood"];'
        'way(around:__RAIO__,__LAT__,__LON__)["landuse"="forest"];'
    )
    resultado = _overpass_feature_mais_proxima(lat, lon, raio_km, filtros)
    if resultado is None:
        return {"fragmento_vegetacao_mais_proximo": None, "observacao": f"nenhum encontrado em {raio_km} km"}
    return {"fragmento_vegetacao_mais_proximo": resultado}


def coletar_declividade(lat: float, lon: float, distancia_m: float = 100.0) -> dict:
    """
    Estimativa de declividade (%) e classificação de relevo (faixas usadas
    pela Embrapa no Sistema Brasileiro de Classificação de Solos), a partir
    de um pequeno grid de elevação (centro + norte/sul/leste/oeste) via
    Open-Meteo Elevation API. Não substitui um SRTM local em precisão, mas
    já indica se o terreno é plano ou íngreme sem exigir nenhum download.
    """
    delta_lat = distancia_m / 111_320.0
    cos_lat = math.cos(math.radians(lat))
    delta_lon = distancia_m / (111_320.0 * cos_lat) if abs(cos_lat) > 1e-9 else 0.0

    pontos = {
        "centro": (lat, lon),
        "norte": (lat + delta_lat, lon),
        "sul": (lat - delta_lat, lon),
        "leste": (lat, lon + delta_lon),
        "oeste": (lat, lon - delta_lon),
    }

    lats_str = ",".join(str(p[0]) for p in pontos.values())
    lons_str = ",".join(str(p[1]) for p in pontos.values())

    dados = _get_json(
        "https://api.open-meteo.com/v1/elevation",
        params={"latitude": lats_str, "longitude": lons_str},
    )
    elevacoes = dados.get("elevation", [])
    if len(elevacoes) < 5:
        raise RuntimeError("resposta inesperada da API de elevação (esperava 5 pontos)")

    nomes = list(pontos.keys())
    elev = dict(zip(nomes, elevacoes))

    diffs = [abs(elev["centro"] - elev[n]) for n in ("norte", "sul", "leste", "oeste")]
    maior_diferenca = max(diffs)
    declividade_pct = round((maior_diferenca / distancia_m) * 100, 2)

    if declividade_pct < 3:
        classe = "plano"
    elif declividade_pct < 8:
        classe = "suave ondulado"
    elif declividade_pct < 20:
        classe = "ondulado"
    elif declividade_pct < 45:
        classe = "forte ondulado"
    elif declividade_pct < 75:
        classe = "montanhoso"
    else:
        classe = "escarpado"

    return {
        "declividade_pct_estimada": declividade_pct,
        "classificacao_relevo": classe,
        "distancia_amostragem_m": distancia_m,
        "elevacoes_pontos_m": elev,
        "observacao": "estimativa por grid de 5 pontos (Open-Meteo); para precisão maior, use SRTM local",
    }


def coletar_bioma_e_vegetacao(lat: float, lon: float, caminho_geojson: str = CAMINHO_BIOMAS_GEOJSON) -> dict:
    """
    Identifica o bioma (e, se o GeoJSON tiver a coluna, a fitofisionomia/
    vegetação original) via um GeoJSON do IBGE baixado uma única vez. É o
    dado mais importante para decidir espécie/estratégia de restauração e
    não existe API pública online simples para ele — por isso é local.
    """
    camada = _carregar_camada(caminho_geojson)
    ponto = Point(lon, lat)
    correspondencias = camada[camada.contains(ponto)]
    if correspondencias.empty:
        return {
            "bioma": None,
            "vegetacao_original": None,
            "observacao": "ponto fora de todos os polígonos da camada carregada",
        }

    linha = correspondencias.iloc[0]

    def _campo(*nomes: str):
        for n in nomes:
            if n in linha and linha[n] not in (None, ""):
                return linha[n]
        return None

    return {
        "bioma": _campo("Bioma", "bioma", "BIOMA", "NOM_BIOMA"),
        "vegetacao_original": _campo("Vegetacao", "vegetacao", "FITOFISIO", "LEGENDA"),
    }


def coletar_risco_erosao(lat: float, lon: float, caminho_geojson: str = CAMINHO_EROSAO_GEOJSON) -> dict:
    """
    Classe de risco de erosão do solo, a partir do shapefile/GeoJSON da
    Embrapa GeoInfo (download único — não há API pública). Ajuda a decidir
    se a área precisa de técnicas de contenção antes do plantio.
    """
    camada = _carregar_camada(caminho_geojson)
    ponto = Point(lon, lat)
    correspondencias = camada[camada.contains(ponto)]
    if correspondencias.empty:
        return {"classe_risco_erosao": None, "observacao": "ponto fora de todos os polígonos da camada carregada"}

    linha = correspondencias.iloc[0]

    def _campo(*nomes: str):
        for n in nomes:
            if n in linha and linha[n] not in (None, ""):
                return linha[n]
        return None

    return {"classe_risco_erosao": _campo("Risco", "CLASSE", "risco_erosao", "LEGENDA")}


def coletar_uso_do_solo_mapbiomas(lat: float, lon: float, ano: int = 2023) -> dict:
    """
    Classe de uso e cobertura do solo atual (pasto, agricultura, solo
    exposto, floresta etc.) via MapBiomas / Google Earth Engine. Define se
    dá pra semear direto ou se precisa de preparo do solo antes.

    Exige cadastro gratuito e autenticação prévia, por isso fica desativado
    por padrão (MAPBIOMAS_HABILITADO=False). Para habilitar:
      1. pip install earthengine-api
      2. earthengine authenticate   (uma vez, gera um token local)
      3. export REVIVETECH_MAPBIOMAS_HABILITADO=true

    Nota: o nome exato do asset/coleção do MapBiomas muda a cada nova
    coleção lançada — confira o ID atual em https://mapbiomas.org/ antes de
    usar em produção; o valor abaixo é só um ponto de partida.
    """
    if not MAPBIOMAS_HABILITADO:
        return {
            "uso_do_solo": None,
            "observacao": (
                "MapBiomas desativado — requer cadastro no Earth Engine "
                "(ver docstring da função coletar_uso_do_solo_mapbiomas)"
            ),
        }
    try:
        import ee
    except ImportError as exc:
        raise RuntimeError("earthengine-api não instalado (pip install earthengine-api)") from exc

    ee.Initialize()
    asset_id = "projects/mapbiomas-public/assets/brazil/lulc/collection9/mapbiomas_collection90_integration_v1"
    colecao = ee.Image(asset_id)
    ponto = ee.Geometry.Point([lon, lat])
    banda = f"classification_{ano}"
    valor = colecao.select(banda).reduceRegion(
        reducer=ee.Reducer.first(), geometry=ponto, scale=30
    ).getInfo()

    return {
        "ano": ano,
        "codigo_classe_mapbiomas": valor.get(banda),
        "observacao": "consulte a legenda oficial do MapBiomas para traduzir o código em classe de uso do solo",
    }


def coletar_historico_queimadas_local(
    lat: float,
    lon: float,
    caminho_banco: str = CAMINHO_BANCO_QUEIMADAS,
    raio_km: float = 5.0,
    tabela: str = "focos",
    coluna_lat: str = "latitude",
    coluna_lon: str = "longitude",
    coluna_data: str = "data_hora",
) -> dict:
    """
    Cruza o ponto consultado com o banco de focos de queimada já coletado na
    Fase 1 (INPE) — sem precisar de nenhuma API nova. Retorna quantos focos
    (e em quantos anos distintos) já ocorreram perto do ponto, indicando
    recorrência de incêndio na área (área que já queimou 3x em 5 anos pede
    estratégia diferente de área queimando pela 1ª vez).

    Assume um banco SQLite simples com colunas de latitude/longitude/data.
    Ajuste os nomes de tabela/coluna via parâmetros, ou troque a conexão
    sqlite3 por outro driver se o banco da Fase 1 for Postgres/MySQL.
    """
    if not os.path.exists(caminho_banco):
        raise FileNotFoundError(
            f"Banco de queimadas não encontrado em: {caminho_banco} "
            "(aponte para o banco gerado na Fase 1 do projeto)"
        )

    # bounding box grosseiro pra filtrar antes de calcular a distância exata
    delta = raio_km / 111.0
    conn = sqlite3.connect(caminho_banco)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute(
            f"""
            SELECT {coluna_lat} AS lat, {coluna_lon} AS lon, {coluna_data} AS data_hora
            FROM {tabela}
            WHERE {coluna_lat} BETWEEN ? AND ?
              AND {coluna_lon} BETWEEN ? AND ?
            """,
            (lat - delta, lat + delta, lon - delta, lon + delta),
        )
        linhas = cursor.fetchall()
    finally:
        conn.close()

    focos_no_raio = []
    for linha in linhas:
        dist = _haversine_km(lat, lon, linha["lat"], linha["lon"])
        if dist <= raio_km:
            focos_no_raio.append({"data_hora": linha["data_hora"], "distancia_km": round(dist, 2)})

    datas_ordenadas = sorted(f["data_hora"] for f in focos_no_raio if f["data_hora"])
    anos = sorted({d[:4] for d in datas_ordenadas if len(d) >= 4})

    return {
        "raio_consultado_km": raio_km,
        "total_focos_no_raio": len(focos_no_raio),
        "anos_com_registro": anos,
        "recorrencia_anos_distintos": len(anos),
        "focos": focos_no_raio[:50],  # limita o payload no resumo
    }


# --------------------------------------------------------------------------
# Orquestração
# --------------------------------------------------------------------------

def _dentro_do_brasil_aprox(lat: float, lon: float) -> bool:
    return (
        BRASIL_BBOX["lat_min"] <= lat <= BRASIL_BBOX["lat_max"]
        and BRASIL_BBOX["lon_min"] <= lon <= BRASIL_BBOX["lon_max"]
    )


def coletar_tudo(
    lat: float,
    lon: float,
    raio_km: float = 15.0,
    data_hora: Optional[datetime] = None,
    raio_agua_km: float = 10.0,
    raio_vegetacao_km: float = 10.0,
    raio_queimadas_km: float = 5.0,
    caminho_biomas: str = CAMINHO_BIOMAS_GEOJSON,
    caminho_erosao: str = CAMINHO_EROSAO_GEOJSON,
    caminho_banco_queimadas: str = CAMINHO_BANCO_QUEIMADAS,
) -> dict:
    """
    Executa todos os coletores em paralelo e agrega o resultado num único
    dicionário. Cada fonte que falhar (ou não estiver configurada) é
    registrada em "erros" sem interromper as demais.

    Se `data_hora` for informado, o clima é buscado na API de arquivo
    histórico (Open-Meteo Archive) para aquela data/hora específica, em vez
    do clima atual + previsão de 7 dias.
    """
    resultado: dict[str, Any] = {
        "coordenadas": {"latitude": lat, "longitude": lon},
        "data_hora_consultada": data_hora.isoformat() if data_hora else None,
        "gerado_em_utc": datetime.now(timezone.utc).isoformat(),
        "dentro_do_brasil_aprox": _dentro_do_brasil_aprox(lat, lon),
        "erros": {},
    }

    tarefas: dict[str, Callable[[], Any]] = {
        "localizacao": lambda: coletar_localizacao(lat, lon),
        "elevacao_e_fuso": lambda: coletar_elevacao_e_fuso(lat, lon),
        "qualidade_do_ar": lambda: coletar_qualidade_do_ar(lat, lon),
        "normais_climatologicas": lambda: coletar_normais_climatologicas(lat, lon),
        "solo": lambda: coletar_solo(lat, lon),
        "areas_protegidas_proximas": lambda: coletar_areas_protegidas(lat, lon, raio_km),
        "bioma_e_vegetacao": lambda: coletar_bioma_e_vegetacao(lat, lon, caminho_biomas),
        "declividade_relevo": lambda: coletar_declividade(lat, lon),
        "distancia_agua": lambda: coletar_distancia_agua(lat, lon, raio_agua_km),
        "distancia_vegetacao_nativa": lambda: coletar_distancia_vegetacao_nativa(lat, lon, raio_vegetacao_km),
        "risco_erosao": lambda: coletar_risco_erosao(lat, lon, caminho_erosao),
        "uso_do_solo": lambda: coletar_uso_do_solo_mapbiomas(lat, lon),
        "historico_queimadas_local": lambda: coletar_historico_queimadas_local(
            lat, lon, caminho_banco_queimadas, raio_queimadas_km
        ),
    }

    if data_hora is not None:
        tarefas["clima_historico"] = lambda: coletar_clima_historico(lat, lon, data_hora)
    else:
        tarefas["clima_atual_e_previsao"] = lambda: coletar_clima_atual_e_previsao(lat, lon)

    with ThreadPoolExecutor(max_workers=len(tarefas)) as executor:
        futuros = {executor.submit(func): nome for nome, func in tarefas.items()}
        for futuro in as_completed(futuros):
            nome = futuros[futuro]
            try:
                resultado[nome] = futuro.result()
                log.info("OK    %s", nome)
            except Exception as exc:  # noqa: BLE001 - queremos capturar tudo aqui
                resultado["erros"][nome] = str(exc)
                log.warning("FALHA %s -> %s", nome, exc)

    return resultado


# --------------------------------------------------------------------------
# Apresentação e CLI
# --------------------------------------------------------------------------

def imprimir_resumo(dados: dict) -> None:
    lat = dados["coordenadas"]["latitude"]
    lon = dados["coordenadas"]["longitude"]
    print("\n" + "=" * 70)
    print(f"  Dados da região ({lat}, {lon})")
    if dados.get("data_hora_consultada"):
        print(f"  Data/hora consultada: {dados['data_hora_consultada']}")
    print("=" * 70)

    loc = dados.get("localizacao", {})
    if loc:
        print(f"\n📍 Localização")
        print(f"   País: {loc.get('pais')}")
        print(f"   Estado: {loc.get('estado')}")
        print(f"   Município: {loc.get('municipio')}")

    ele = dados.get("elevacao_e_fuso", {})
    if ele:
        print(f"\n⛰  Elevação: {ele.get('elevacao_m')} m   |   Fuso: {ele.get('fuso_horario')}")

    decl = dados.get("declividade_relevo", {})
    if decl:
        print(f"\n📐 Relevo")
        print(f"   Declividade estimada: {decl.get('declividade_pct_estimada')} %   |   Classe: {decl.get('classificacao_relevo')}")

    bioma = dados.get("bioma_e_vegetacao", {})
    if bioma and bioma.get("bioma"):
        print(f"\n🧬 Bioma e vegetação original")
        print(f"   Bioma: {bioma.get('bioma')}   |   Vegetação original: {bioma.get('vegetacao_original')}")

    uso_solo = dados.get("uso_do_solo", {})
    if uso_solo and uso_solo.get("uso_do_solo") is not None:
        print(f"\n🟤 Uso do solo atual: {uso_solo.get('uso_do_solo')}")

    erosao = dados.get("risco_erosao", {})
    if erosao and erosao.get("classe_risco_erosao"):
        print(f"\n⚠️  Risco de erosão do solo: {erosao.get('classe_risco_erosao')}")

    agua = dados.get("distancia_agua", {}).get("corpo_dagua_mais_proximo")
    if agua:
        print(f"\n💧 Corpo d'água mais próximo: {agua.get('nome')} ({agua.get('distancia_km')} km)")

    veg = dados.get("distancia_vegetacao_nativa", {}).get("fragmento_vegetacao_mais_proximo")
    if veg:
        print(f"\n🌲 Fragmento de vegetação nativa mais próximo: {veg.get('nome')} ({veg.get('distancia_km')} km)")

    if dados.get("data_hora_consultada"):
        clima = dados.get("clima_historico", {})
        if clima:
            print(f"\n🌤  Clima histórico ({clima.get('data_hora_encontrada')})")
            print(f"   Temperatura: {clima.get('temperatura_c')} °C   |   Umidade: {clima.get('umidade_relativa_pct')} %")
            print(f"   Precipitação: {clima.get('precipitacao_mm')} mm   |   Vento: {clima.get('vento_kmh')} km/h")
    else:
        clima = dados.get("clima_atual_e_previsao", {}).get("condicoes_atuais", {})
        if clima:
            print(f"\n🌤  Clima agora")
            print(f"   Temperatura: {clima.get('temperatura_c')} °C  (sensação {clima.get('sensacao_termica_c')} °C)")
            print(f"   Umidade relativa: {clima.get('umidade_relativa_pct')} %")
            print(f"   Precipitação: {clima.get('precipitacao_mm')} mm   |   Vento: {clima.get('vento_kmh')} km/h")

    ar = dados.get("qualidade_do_ar", {})
    if ar:
        print(f"\n🫧  Qualidade do ar")
        print(f"   PM2.5: {ar.get('pm2_5')} µg/m³   |   PM10: {ar.get('pm10')} µg/m³   |   Índice europeu: {ar.get('indice_qualidade_ar_europeu')}")

    normais = dados.get("normais_climatologicas", {}).get("media_anual", {})
    if normais:
        print(f"\n📊 Normais climatológicas (média anual, 1991-2020)")
        print(f"   Temp. média: {normais.get('temp_media_c')} °C   |   Umidade: {normais.get('umidade_relativa_pct')} %")
        print(f"   Precipitação: {normais.get('precipitacao_mm_dia')} mm/dia   |   Radiação solar: {normais.get('radiacao_solar_kwh_m2_dia')} kWh/m²/dia")

    solo = dados.get("solo", {})
    if solo:
        print(f"\n🌱 Solo (camada 0-5cm)")
        for prop, info in solo.items():
            valor = info.get("valores", {}).get("0-5cm")
            print(f"   {prop}: {valor} {info.get('unidade')}")

    ucs = dados.get("areas_protegidas_proximas", [])
    if ucs:
        print(f"\n🌳 Áreas protegidas próximas (raio consultado)")
        for uc in ucs[:5]:
            print(f"   - {uc.get('nome')} ({uc.get('distancia_aproximada_km')} km) — {uc.get('tipo_osm')}")
    else:
        print(f"\n🌳 Nenhuma área protegida encontrada no raio consultado (ou fonte indisponível).")

    queimadas = dados.get("historico_queimadas_local", {})
    if queimadas and queimadas.get("total_focos_no_raio") is not None:
        print(f"\n🔥 Histórico de queimadas (banco próprio, raio {queimadas.get('raio_consultado_km')} km)")
        print(f"   Total de focos: {queimadas.get('total_focos_no_raio')}   |   Anos com registro: {queimadas.get('anos_com_registro')}")

    if dados.get("erros"):
        print(f"\n⚠️  Fontes que falharam ou não configuradas nesta execução: {list(dados['erros'].keys())}")

    print("\n" + "=" * 70)
    print("  Dados completos salvos em JSON (ver caminho abaixo).")
    print("=" * 70 + "\n")


def salvar_json(dados: dict, pasta_saida: str = "saidas") -> str:
    os.makedirs(pasta_saida, exist_ok=True)
    lat = dados["coordenadas"]["latitude"]
    lon = dados["coordenadas"]["longitude"]
    carimbo = datetime.now().strftime("%Y%m%d_%H%M%S")
    caminho = os.path.join(pasta_saida, f"regiao_{lat}_{lon}_{carimbo}.json")
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(dados, f, ensure_ascii=False, indent=2)
    return caminho


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Coleta o máximo de dados possível sobre uma região a partir de lat/long, "
                    "com foco em subsidiar decisão de espécie e composição de biocápsula."
    )
    parser.add_argument("lat", type=float, nargs="?", help="Latitude (ex: -23.5505)")
    parser.add_argument("lon", type=float, nargs="?", help="Longitude (ex: -46.6333)")
    parser.add_argument("--lat", dest="lat_flag", type=float, help="Latitude (alternativa)")
    parser.add_argument("--lon", dest="lon_flag", type=float, help="Longitude (alternativa)")
    parser.add_argument("--raio", type=float, default=15.0, help="Raio (km) para busca de áreas protegidas")
    parser.add_argument("--raio-agua", type=float, default=10.0, help="Raio (km) para busca do corpo d'água mais próximo")
    parser.add_argument("--raio-vegetacao", type=float, default=10.0, help="Raio (km) para busca de fragmento de vegetação nativa mais próximo")
    parser.add_argument("--raio-queimadas", type=float, default=5.0, help="Raio (km) para cruzar com o histórico de queimadas do banco próprio (Fase 1)")
    parser.add_argument("--biomas-geojson", type=str, default=CAMINHO_BIOMAS_GEOJSON, help="Caminho do GeoJSON de biomas (IBGE, baixado uma vez)")
    parser.add_argument("--erosao-geojson", type=str, default=CAMINHO_EROSAO_GEOJSON, help="Caminho do GeoJSON/shapefile de risco de erosão (Embrapa GeoInfo)")
    parser.add_argument("--banco-queimadas", type=str, default=CAMINHO_BANCO_QUEIMADAS, help="Caminho do banco (SQLite) de focos de queimada da Fase 1")
    parser.add_argument("--data-hora", type=str, default=None, help="Data/hora ISO (ex: 2026-01-15T14:30) para consultar clima histórico em vez do clima atual")
    parser.add_argument("--saida", type=str, default="saidas", help="Pasta onde salvar o JSON")
    parser.add_argument("--sem-dashboard", action="store_true", help="Não gerar o arquivo HTML da dashboard")
    args = parser.parse_args()

    lat = args.lat if args.lat is not None else args.lat_flag
    lon = args.lon if args.lon is not None else args.lon_flag

    if lat is None or lon is None:
        try:
            lat = float(input("Latitude: ").strip())
            lon = float(input("Longitude: ").strip())
        except (ValueError, EOFError):
            print("Latitude/longitude inválidas.", file=sys.stderr)
            sys.exit(1)

    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        print("Coordenadas fora do intervalo válido.", file=sys.stderr)
        sys.exit(1)

    data_hora_dt = None
    if args.data_hora:
        try:
            data_hora_dt = datetime.fromisoformat(args.data_hora)
        except ValueError:
            print("Formato de --data-hora inválido. Use ISO 8601, ex: 2026-01-15T14:30", file=sys.stderr)
            sys.exit(1)

    log.info("Coletando dados para (%s, %s)...", lat, lon)
    dados = coletar_tudo(
        lat,
        lon,
        raio_km=args.raio,
        data_hora=data_hora_dt,
        raio_agua_km=args.raio_agua,
        raio_vegetacao_km=args.raio_vegetacao,
        raio_queimadas_km=args.raio_queimadas,
        caminho_biomas=args.biomas_geojson,
        caminho_erosao=args.erosao_geojson,
        caminho_banco_queimadas=args.banco_queimadas,
    )

    imprimir_resumo(dados)
    caminho = salvar_json(dados, pasta_saida=args.saida)
    print(f"📄 JSON completo salvo em: {caminho}")

    if not args.sem_dashboard:
        caminho_dashboard = salvar_dashboard(dados, pasta_saida=args.saida)
        print(f"🗺️  Dashboard salva em: {caminho_dashboard}")


if __name__ == "__main__":
    main()