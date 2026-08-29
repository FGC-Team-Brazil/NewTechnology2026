#!/usr/bin/env python3
"""
Revivetech — Regional Data Dashboard
===========================================

Generates a single self-contained HTML file from the dictionary returned by
`collect_all()` (see revivetech_data_collector.py), showing:

  - Satellite-view map of the point (Leaflet + Esri World Imagery, no API
    key required), with the search radii for water/vegetation/protected
    areas drawn on the map
  - All collected data organized into modules/cards, with clear states
    for missing data or a source that failed
  - A small chart of the monthly climate normals (Chart.js)

Typical usage:
    from revivetech_data_collector import collect_all
    from revivetech_dashboard import save_dashboard

    data = collect_all(lat, lon)
    html_path = save_dashboard(data, output_folder="outputs")

The generated HTML makes no network calls beyond loading the map tiles and
the fonts/libraries via CDN — the data itself is already embedded in the
file, so it can be opened offline (only the map and fonts require
internet access).
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from typing import Any

_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>Revivetech — Regional Dossier</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,300;9..144,400;9..144,500;9..144,600&family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css" />
<script src="https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"></script>
<script src="https://cdnjs.cloudflare.com/ajax/libs/Chart.js/4.4.3/chart.umd.min.js"></script>
<script id="data-json" type="application/json">__REVIVETECH_DATA_JSON__</script>
<style>
  :root{
    --bg: #12171a;
    --surface: #1a2320;
    --surface-2: #212b27;
    --border: #2c3833;
    --text: #eae6da;
    --text-muted: #8ea298;
    --accent-moss: #74a888;
    --accent-ember: #c1603e;
    --accent-gold: #d7ac5c;
    --accent-water: #5f93aa;
    --radius: 10px;
  }
  *{box-sizing:border-box;}
  html,body{margin:0;padding:0;}
  body{
    background:
      radial-gradient(ellipse at top left, rgba(116,168,136,0.06), transparent 45%),
      radial-gradient(ellipse at bottom right, rgba(193,96,62,0.05), transparent 50%),
      var(--bg);
    color:var(--text);
    font-family:'IBM Plex Sans', sans-serif;
    -webkit-font-smoothing:antialiased;
  }
  .shell{max-width:1280px;margin:0 auto;padding:28px 24px 64px;}

  /* ---------- HERO ---------- */
  .hero{
    border:1px solid var(--border);
    border-radius:var(--radius);
    background:linear-gradient(180deg, rgba(255,255,255,0.02), transparent);
    padding:20px 26px 24px;
    margin-bottom:22px;
    animation:fadeIn .5s ease both;
  }
  .hero-top{
    display:flex;align-items:center;gap:10px;
    font-family:'IBM Plex Mono', monospace;
    font-size:11px;letter-spacing:.14em;text-transform:uppercase;
    color:var(--text-muted);
    margin-bottom:18px;
  }
  .hero-top .wordmark{color:var(--accent-moss);font-weight:600;}
  .hero-top .timestamp{margin-left:auto;color:var(--text-muted);}
  .hero-main{display:flex;align-items:flex-end;gap:32px;flex-wrap:wrap;}
  .hero-place h1{
    font-family:'Fraunces', serif;
    font-weight:500;font-size:clamp(28px,4vw,40px);
    margin:0 0 4px;letter-spacing:-0.01em;
  }
  .hero-place p{margin:0;color:var(--text-muted);font-size:14px;}
  .hero-coords{
    font-family:'IBM Plex Mono', monospace;
    display:flex;flex-direction:column;gap:2px;
  }
  .hero-coords .coord-label{font-size:10px;letter-spacing:.14em;color:var(--text-muted);}
  .hero-coords .coord-value{font-size:19px;color:var(--accent-gold);}
  .biome-badge{display:flex;align-items:center;gap:12px;margin-left:auto;}
  .biome-ring{
    width:46px;height:46px;border-radius:50%;
    background:conic-gradient(var(--accent-moss) 0 100%);
    display:flex;align-items:center;justify-content:center;
    box-shadow:0 0 0 1px var(--border);
  }
  .biome-ring::after{
    content:'';width:34px;height:34px;border-radius:50%;background:var(--bg);
  }
  .biome-text{display:flex;flex-direction:column;font-family:'IBM Plex Mono',monospace;}
  .biome-text .biome-label{font-size:10px;letter-spacing:.14em;color:var(--text-muted);}
  .biome-text .biome-name{font-size:14px;color:var(--text);}

  /* ---------- LAYOUT ---------- */
  .grid-main{display:grid;grid-template-columns:1.5fr 1fr;gap:18px;margin-bottom:18px;}
  @media (max-width:920px){.grid-main{grid-template-columns:1fr;}}

  .map-panel{position:relative;border-radius:var(--radius);overflow:hidden;border:1px solid var(--border);}
  .map-frame{position:relative;height:100%;min-height:420px;}
  #map{position:absolute;inset:0;filter:saturate(1.05) contrast(1.03);}
  .reticle{position:absolute;width:22px;height:22px;border-color:var(--accent-gold);opacity:.85;z-index:500;pointer-events:none;}
  .reticle.tl{top:10px;left:10px;border-top:2px solid;border-left:2px solid;}
  .reticle.tr{top:10px;right:10px;border-top:2px solid;border-right:2px solid;}
  .reticle.bl{bottom:10px;left:10px;border-bottom:2px solid;border-left:2px solid;}
  .reticle.br{bottom:10px;right:10px;border-bottom:2px solid;border-right:2px solid;}
  .map-legend{
    position:absolute;bottom:10px;left:42px;z-index:500;
    display:flex;gap:14px;flex-wrap:wrap;
    background:rgba(18,23,26,0.78);backdrop-filter:blur(3px);
    border:1px solid var(--border);border-radius:8px;
    padding:6px 10px;font-family:'IBM Plex Mono',monospace;font-size:10.5px;color:var(--text-muted);
  }
  .map-legend span{display:flex;align-items:center;gap:5px;}
  .map-legend i{width:9px;height:9px;border-radius:50%;display:inline-block;}

  .priority-modules{display:flex;flex-direction:column;gap:14px;}

  .modules-grid{
    display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));
    gap:14px;margin-bottom:18px;
  }

  /* ---------- CARDS ---------- */
  .card{
    background:var(--surface);
    border:1px solid var(--border);
    border-radius:var(--radius);
    padding:16px 18px;
    animation:fadeIn .5s ease both;
    transition:border-color .15s ease, transform .15s ease;
  }
  .card:hover{border-color:var(--accent-moss);transform:translateY(-1px);}
  .card.unavailable{opacity:.55;}
  .card .eyebrow{
    font-family:'IBM Plex Mono',monospace;font-size:10px;letter-spacing:.14em;
    color:var(--accent-moss);text-transform:uppercase;margin-bottom:6px;display:block;
  }
  .card h3{font-family:'Fraunces',serif;font-weight:500;font-size:17px;margin:0 0 10px;}
  .card .stat-row{display:flex;justify-content:space-between;align-items:baseline;padding:5px 0;border-bottom:1px dashed var(--border);font-size:13.5px;}
  .card .stat-row:last-child{border-bottom:none;}
  .card .stat-row .k{color:var(--text-muted);}
  .card .stat-row .v{font-family:'IBM Plex Mono',monospace;color:var(--text);text-align:right;}
  .card .big-stat{font-family:'IBM Plex Mono',monospace;font-size:26px;color:var(--accent-gold);}
  .card .sub{color:var(--text-muted);font-size:12.5px;margin-top:2px;}
  .card .note{font-size:12px;color:var(--text-muted);margin-top:8px;font-style:italic;}
  .chip{
    display:inline-block;padding:2px 8px;border-radius:20px;font-size:11px;
    font-family:'IBM Plex Mono',monospace;border:1px solid var(--border);color:var(--text-muted);
    margin:2px 4px 0 0;
  }
  .chip.ember{color:var(--accent-ember);border-color:var(--accent-ember);}
  .chip.moss{color:var(--accent-moss);border-color:var(--accent-moss);}
  .chip.gold{color:var(--accent-gold);border-color:var(--accent-gold);}
  .chart-wrap{height:150px;margin-top:8px;}
  .soil-table{width:100%;border-collapse:collapse;font-size:12px;font-family:'IBM Plex Mono',monospace;}
  .soil-table th{text-align:left;color:var(--text-muted);font-weight:500;font-size:10.5px;padding-bottom:6px;}
  .soil-table td{padding:3px 0;border-top:1px dashed var(--border);}
  .protected-list{list-style:none;margin:0;padding:0;}
  .protected-list li{display:flex;justify-content:space-between;padding:5px 0;border-bottom:1px dashed var(--border);font-size:13px;}
  .protected-list li:last-child{border-bottom:none;}

  /* ---------- DIAGNOSTICS ---------- */
  .diagnostics{
    border:1px solid var(--accent-ember);border-radius:var(--radius);
    background:rgba(193,96,62,0.06);padding:16px 18px;
  }
  .diagnostics h2{
    font-family:'IBM Plex Mono',monospace;font-size:11px;letter-spacing:.14em;
    color:var(--accent-ember);text-transform:uppercase;margin:0 0 10px;
  }
  .diagnostics ul{margin:0;padding-left:18px;font-size:13px;color:var(--text-muted);}
  .diagnostics li{margin-bottom:4px;}
  .diagnostics code{color:var(--accent-ember);}

  @keyframes fadeIn{from{opacity:0;transform:translateY(6px);}to{opacity:1;transform:translateY(0);}}
  @media (prefers-reduced-motion:reduce){*{animation:none !important;transition:none !important;}}
</style>
</head>
<body>
<div class="shell">

  <header class="hero">
    <div class="hero-top">
      <span class="wordmark">REVIVETECH</span>
      <span>›</span>
      <span>REGIONAL DOSSIER</span>
      <span class="timestamp" id="js-timestamp"></span>
    </div>
    <div class="hero-main">
      <div class="hero-place">
        <h1 id="js-municipality">—</h1>
        <p id="js-state-country"></p>
      </div>
      <div class="hero-coords">
        <span class="coord-label">LAT / LON</span>
        <span class="coord-value" id="js-coords">—, —</span>
      </div>
      <div class="biome-badge">
        <div class="biome-ring" id="js-biome-ring"></div>
        <div class="biome-text">
          <span class="biome-label">BIOME</span>
          <span class="biome-name" id="js-biome-name">—</span>
        </div>
      </div>
    </div>
  </header>

  <div class="grid-main">
    <section class="map-panel">
      <div class="map-frame">
        <div id="map"></div>
        <div class="reticle tl"></div>
        <div class="reticle tr"></div>
        <div class="reticle bl"></div>
        <div class="reticle br"></div>
        <div class="map-legend" id="js-map-legend"></div>
      </div>
    </section>

    <section class="priority-modules" id="js-priority-modules"></section>
  </div>

  <section class="modules-grid" id="js-secondary-modules"></section>

  <footer class="diagnostics" id="js-diagnostics" hidden>
    <h2>Collection diagnostics</h2>
    <ul id="js-diagnostics-list"></ul>
  </footer>

</div>

<script>
const DATA = JSON.parse(document.getElementById('data-json').textContent);

const BIOME_COLOR = {
  "Amazônia": "#2f6b4f", "Cerrado": "#c08a3e", "Mata Atlântica": "#3e8564",
  "Caatinga": "#b97a46", "Pampa": "#8a9b4e", "Pantanal": "#3e8c93"
};

function fmt(v, unit = "", fallback = "—"){
  if (v === null || v === undefined || v === "") return fallback;
  return `${v}${unit}`;
}

function el(tag, cls, html){
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html !== undefined) e.innerHTML = html;
  return e;
}

function card({eyebrow, title, body, unavailable=false, note=null}){
  const c = el('div', 'card' + (unavailable ? ' unavailable' : ''));
  c.appendChild(el('span', 'eyebrow', eyebrow));
  c.appendChild(el('h3', null, title));
  const b = el('div', null, body);
  c.appendChild(b);
  if (note) c.appendChild(el('div', 'note', note));
  return c;
}

function statRow(k, v){
  return `<div class="stat-row"><span class="k">${k}</span><span class="v">${v}</span></div>`;
}

// ---------- Header ----------
document.getElementById('js-timestamp').textContent =
  DATA.queried_datetime ? `historical query · ${DATA.queried_datetime}` :
  (DATA.generated_at_utc ? new Date(DATA.generated_at_utc).toLocaleString('en-US') : '');

const loc = DATA.location || {};
document.getElementById('js-municipality').textContent = loc.municipality || 'Location not identified';
document.getElementById('js-state-country').textContent =
  [loc.state, loc.country].filter(Boolean).join(' · ') || (DATA.errors && DATA.errors.location ? 'source unavailable' : '');

const lat = DATA.coordinates.latitude, lon = DATA.coordinates.longitude;
document.getElementById('js-coords').textContent = `${lat.toFixed(4)}, ${lon.toFixed(4)}`;

const biome = (DATA.biome_and_vegetation || {}).biome;
document.getElementById('js-biome-name').textContent = biome || 'not configured';
const ring = document.getElementById('js-biome-ring');
ring.style.background = `conic-gradient(${BIOME_COLOR[biome] || '#74a888'} 0 100%)`;

// ---------- Map ----------
const map = L.map('map', {zoomControl:true, attributionControl:false}).setView([lat, lon], 13);
L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}', {
  maxZoom: 18
}).addTo(map);
L.tileLayer('https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}', {
  maxZoom: 18, opacity: 0.9
}).addTo(map);

const marker = L.circleMarker([lat, lon], {
  radius: 7, color: '#d7ac5c', fillColor:'#d7ac5c', fillOpacity: 1, weight: 2
}).addTo(map).bindPopup('Queried point');

const legendItems = [];

function radiusCircle(radiusKm, color, label){
  if (!radiusKm) return;
  L.circle([lat, lon], {
    radius: radiusKm * 1000, color: color, weight: 1.5, fillOpacity: 0.03, dashArray: '4 5'
  }).addTo(map);
  legendItems.push(`<span><i style="background:${color}"></i>${label} (${radiusKm} km)</span>`);
}
radiusCircle((DATA.nearby_protected_areas || []).length ? 15 : null, '#74a888', 'protected area search');
radiusCircle(10, '#5f93aa', 'water search');
radiusCircle(10, '#c1603e', 'native vegetation search');

const water = (DATA.water_distance || {}).nearest_water_body;
if (water) legendItems.push(`<span><i style="background:#5f93aa"></i>water at ${water.distance_km} km</span>`);
const nativeVeg = (DATA.native_vegetation_distance || {}).nearest_vegetation_fragment;
if (nativeVeg) legendItems.push(`<span><i style="background:#c1603e"></i>forest at ${nativeVeg.distance_km} km</span>`);

document.getElementById('js-map-legend').innerHTML = legendItems.join('');

// ---------- Priority modules ----------
const priority = document.getElementById('js-priority-modules');

// Weather
(function(){
  const historical = DATA.queried_datetime;
  const weather = historical ? (DATA.historical_weather || {}) : (DATA.current_weather_and_forecast || {}).current_conditions || {};
  const unavailable = DATA.errors && (DATA.errors.current_weather_and_forecast || DATA.errors.historical_weather);
  priority.appendChild(card({
    eyebrow: historical ? 'Weather · historical' : 'Weather · now',
    title: 'Atmospheric conditions',
    unavailable: !!unavailable,
    body: unavailable ? '<p class="sub">Source unavailable in this collection.</p>' : `
      <div class="big-stat">${fmt(weather.temperature_c, ' °C')}</div>
      ${statRow('Relative humidity', fmt(weather.relative_humidity_pct, ' %'))}
      ${statRow('Precipitation', fmt(weather.precipitation_mm, ' mm'))}
      ${statRow('Wind', fmt(weather.wind_kmh, ' km/h'))}
      ${statRow('Pressure', fmt(weather.surface_pressure_hpa || weather.pressure_hpa, ' hPa'))}
    `
  }));
})();

// Relief
(function(){
  const d = DATA.slope_relief || {};
  const unavailable = DATA.errors && DATA.errors.slope_relief;
  priority.appendChild(card({
    eyebrow: 'Relief',
    title: 'Estimated slope',
    unavailable: !!unavailable,
    body: unavailable ? '<p class="sub">Source unavailable in this collection.</p>' : `
      <div class="big-stat">${fmt(d.estimated_slope_pct, ' %')}</div>
      ${statRow('Classification', fmt(d.relief_classification))}
      ${statRow('Elevation', fmt((DATA.elevation_and_timezone||{}).elevation_m, ' m'))}
    `,
    note: d.note || null
  }));
})();

// Biome and vegetation
(function(){
  const b = DATA.biome_and_vegetation || {};
  const unavailable = DATA.errors && DATA.errors.biome_and_vegetation;
  priority.appendChild(card({
    eyebrow: 'Biome & vegetation',
    title: 'Original cover',
    unavailable: !!unavailable || !b.biome,
    body: (unavailable || !b.biome) ? '<p class="sub">Requires a configured biomes GeoJSON (see --biomes-geojson).</p>' : `
      ${statRow('Biome', b.biome)}
      ${statRow('Original vegetation', fmt(b.original_vegetation))}
    `
  }));
})();

// Land use + erosion risk (same card, both "advanced")
(function(){
  const use = DATA.land_use || {};
  const erosion = DATA.erosion_risk || {};
  priority.appendChild(card({
    eyebrow: 'Soil · use & risk',
    title: 'Current use and erosion',
    unavailable: !use.land_use && !erosion.erosion_risk_class,
    body: `
      ${statRow('Land use', fmt(use.land_use || use.mapbiomas_class_code))}
      ${statRow('Erosion risk', fmt(erosion.erosion_risk_class))}
    `,
    note: (use.note || erosion.note || null)
  }));
})();

// Nearby water and native vegetation
(function(){
  const water = (DATA.water_distance || {}).nearest_water_body;
  const veg = (DATA.native_vegetation_distance || {}).nearest_vegetation_fragment;
  priority.appendChild(card({
    eyebrow: 'Ecological proximity',
    title: 'Water & native vegetation',
    unavailable: !water && !veg,
    body: `
      ${statRow('Nearest water', water ? `${water.name} · ${water.distance_km} km` : 'none in radius')}
      ${statRow('Native vegetation', veg ? `${veg.name} · ${veg.distance_km} km` : 'none in radius')}
    `
  }));
})();

// Wildfire history
(function(){
  const q = DATA.local_fire_history || {};
  const unavailable = DATA.errors && DATA.errors.local_fire_history;
  const years = (q.years_with_records || []).map(a => `<span class="chip ember">${a}</span>`).join('');
  priority.appendChild(card({
    eyebrow: 'Local history (INPE)',
    title: 'Wildfire recurrence',
    unavailable: !!unavailable,
    body: unavailable ? '<p class="sub">Phase 1 database not configured (see --wildfire-db).</p>' : `
      <div class="big-stat">${fmt(q.total_hotspots_in_radius)}</div>
      <p class="sub">hotspots recorded within a ${fmt(q.queried_radius_km, ' km')} radius</p>
      <div style="margin-top:8px;">${years || '<span class="sub">no years with records</span>'}</div>
    `
  }));
})();

// ---------- Secondary modules ----------
const secondary = document.getElementById('js-secondary-modules');

// Air quality
(function(){
  const air = DATA.air_quality || {};
  const unavailable = DATA.errors && DATA.errors.air_quality;
  secondary.appendChild(card({
    eyebrow: 'Air', title: 'Air quality',
    unavailable: !!unavailable,
    body: unavailable ? '<p class="sub">Source unavailable.</p>' : `
      ${statRow('PM2.5', fmt(air.pm2_5, ' µg/m³'))}
      ${statRow('PM10', fmt(air.pm10, ' µg/m³'))}
      ${statRow('Ozone', fmt(air.ozone, ' µg/m³'))}
      ${statRow('European index (AQI)', fmt(air.european_air_quality_index))}
    `
  }));
})();

// Climate normals (chart)
(function(){
  const normals = DATA.climate_normals || {};
  const unavailable = DATA.errors && DATA.errors.climate_normals;
  const c = card({
    eyebrow: 'NASA POWER · 1991–2020', title: 'Climate normals',
    unavailable: !!unavailable,
    body: unavailable ? '<p class="sub">Source unavailable.</p>' : `<div class="chart-wrap"><canvas id="js-chart-normals"></canvas></div>`
  });
  secondary.appendChild(c);
  if (!unavailable && normals.by_month){
    const months = ["JAN","FEB","MAR","APR","MAY","JUN","JUL","AUG","SEP","OCT","NOV","DEC"];
    const temp = months.map(m => (normals.by_month[m]||{}).avg_temp_c);
    const precip = months.map(m => (normals.by_month[m]||{}).precipitation_mm_day);
    new Chart(document.getElementById('js-chart-normals'), {
      data: {
        labels: months,
        datasets: [
          {type:'bar', label:'Precipitation (mm/day)', data: precip, backgroundColor:'rgba(95,147,170,0.55)', yAxisID:'y1', borderRadius:3},
          {type:'line', label:'Avg. temp (°C)', data: temp, borderColor:'#d7ac5c', backgroundColor:'#d7ac5c', yAxisID:'y2', tension:.35, pointRadius:2}
        ]
      },
      options: {
        responsive:true, maintainAspectRatio:false,
        plugins:{legend:{labels:{color:'#8ea298', font:{size:9}}}},
        scales:{
          x:{ticks:{color:'#8ea298', font:{size:9}}, grid:{display:false}},
          y1:{position:'left', ticks:{color:'#8ea298', font:{size:9}}, grid:{color:'#2c3833'}},
          y2:{position:'right', ticks:{color:'#8ea298', font:{size:9}}, grid:{display:false}}
        }
      }
    });
  }
})();

// Protected areas
(function(){
  const areas = DATA.nearby_protected_areas || [];
  const unavailable = DATA.errors && DATA.errors.nearby_protected_areas;
  const items = areas.slice(0,6).map(a =>
    `<li><span>${a.name}</span><span class="v" style="font-family:'IBM Plex Mono',monospace;">${fmt(a.approximate_distance_km,' km')}</span></li>`
  ).join('');
  secondary.appendChild(card({
    eyebrow: 'OSM / CNUC', title: 'Nearby protected areas',
    unavailable: !!unavailable || areas.length === 0,
    body: unavailable ? '<p class="sub">Source unavailable.</p>' :
      (areas.length ? `<ul class="protected-list">${items}</ul>` : '<p class="sub">None found in the queried radius.</p>')
  }));
})();

// Soil
(function(){
  const soil = DATA.soil || {};
  const unavailable = DATA.errors && DATA.errors.soil;
  const rows = Object.entries(soil).map(([prop, info]) => `
    <tr><td>${prop}</td><td>${fmt((info.values||{})['0-5cm'])}</td><td>${fmt((info.values||{})['5-15cm'])}</td><td style="color:#8ea298;">${info.unit||''}</td></tr>
  `).join('');
  secondary.appendChild(card({
    eyebrow: 'SoilGrids / ISRIC', title: 'Soil (physical-chemical properties)',
    unavailable: !!unavailable,
    body: unavailable ? '<p class="sub">Source unavailable.</p>' : `
      <table class="soil-table">
        <tr><th>Property</th><th>0–5cm</th><th>5–15cm</th><th>Unit</th></tr>
        ${rows}
      </table>
    `
  }));
})();

// ---------- Diagnostics ----------
const errors = DATA.errors || {};
if (Object.keys(errors).length){
  document.getElementById('js-diagnostics').hidden = false;
  const list = document.getElementById('js-diagnostics-list');
  Object.entries(errors).forEach(([source, msg]) => {
    list.appendChild(el('li', null, `<code>${source}</code> — ${msg}`));
  });
}
</script>
</body>
</html>
"""


def generate_dashboard_html(data: dict) -> str:
    """
    Receives the dictionary returned by `collect_all()` and returns the
    full dashboard HTML, with the data already embedded.
    """
    data_json = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return _TEMPLATE.replace("__REVIVETECH_DATA_JSON__", data_json)


def save_dashboard(data: dict, output_folder: str = "outputs") -> str:
    """Generates and saves the dashboard HTML, returning the file path."""
    os.makedirs(output_folder, exist_ok=True)
    lat = data["coordinates"]["latitude"]
    lon = data["coordinates"]["longitude"]
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(output_folder, f"dashboard_{lat}_{lon}_{stamp}.html")
    with open(path, "w", encoding="utf-8") as f:
        f.write(generate_dashboard_html(data))
    return path