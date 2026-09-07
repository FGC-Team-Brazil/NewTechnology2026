/* ---------------------------------------------------------
   ReviveTech — wizard que espelha o pipeline de main.py:
     1. select_biome()
     2. confirm_download() [+ ask_days()]
     3. filter_by_biome() -> select_hotspot()
     4. enrich_hotspot()
     5. recommend_biocapsule() -> print_recommendation()

   TUDO ABAIXO MARCADO COM "// MOCK" simula uma etapa que hoje
   roda em Python. Pra plugar o backend real, troque as funções
   MOCK por chamadas fetch('/api/...') mantendo o mesmo formato
   de retorno — o resto da UI não precisa mudar.
   --------------------------------------------------------- */

const BIOMES = [
  { key: "amazonia", label: "Amazônia", emoji: "🌳", accent: "var(--leaf)", bbox: { latMin: -9, latMax: 4, lonMin: -73, lonMax: -50 } },
  { key: "cerrado", label: "Cerrado", emoji: "🌾", accent: "var(--sand)", bbox: { latMin: -24, latMax: -3, lonMin: -60, lonMax: -41 } },
  { key: "mata_atlantica", label: "Mata Atlântica", emoji: "🌿", accent: "var(--forest)", bbox: { latMin: -30, latMax: -6, lonMin: -55, lonMax: -35 } },
  { key: "caatinga", label: "Caatinga", emoji: "🌵", accent: "var(--amber)", bbox: { latMin: -16, latMax: -3, lonMin: -45, lonMax: -35 } },
  { key: "pantanal", label: "Pantanal", emoji: "💧", accent: "var(--sky)", bbox: { latMin: -22, latMax: -16, lonMin: -59, lonMax: -55 } },
  { key: "pampa", label: "Pampa", emoji: "🌱", accent: "var(--azure)", bbox: { latMin: -33, latMax: -28, lonMin: -57, lonMax: -49 } },
  { key: "all", label: "Todos os biomas", emoji: "🇧🇷", accent: "var(--navy)", bbox: { latMin: -33, latMax: 5, lonMin: -73, lonMax: -34 } },
];

const SPECIES_BY_BIOME = {
  amazonia: [
    ["Castanheira-do-pará", "Bertholletia excelsa"],
    ["Ipê-amarelo", "Handroanthus serratifolius"],
    ["Andiroba", "Carapa guianensis"],
    ["Cumaru", "Dipteryx odorata"],
  ],
  cerrado: [
    ["Ipê-roxo", "Handroanthus impetiginosus"],
    ["Pequi", "Caryocar brasiliense"],
    ["Baru", "Dipteryx alata"],
    ["Jatobá-do-cerrado", "Hymenaea stigonocarpa"],
  ],
  mata_atlantica: [
    ["Jequitibá-rosa", "Cariniana legalis"],
    ["Pau-brasil", "Paubrasilia echinata"],
    ["Jacarandá-da-bahia", "Dalbergia nigra"],
    ["Ipê-amarelo", "Handroanthus chrysotrichus"],
  ],
  caatinga: [
    ["Angico", "Anadenanthera colubrina"],
    ["Aroeira", "Myracrodruon urundeuva"],
    ["Umbuzeiro", "Spondias tuberosa"],
    ["Catingueira", "Poincianella pyramidalis"],
  ],
  pantanal: [
    ["Piúva", "Handroanthus heptaphyllus"],
    ["Cambará", "Vochysia divergens"],
    ["Paratudo", "Tabebuia aurea"],
    ["Carandá", "Copernicia alba"],
  ],
  pampa: [
    ["Butiazeiro", "Butia odorata"],
    ["Angico-vermelho", "Parapiptadenia rigida"],
    ["Canelinha", "Nectandra megapotamica"],
    ["Timbaúva", "Enterolobium contortisiliquum"],
  ],
};
SPECIES_BY_BIOME.all = SPECIES_BY_BIOME.cerrado;

const SATELLITES = ["AQUA_M-T", "TERRA_M-T", "NOAA-20", "GOES-19", "METOP-B"];

const ENRICH_STEPS = [
  "Consultando temperatura e umidade atuais",
  "Analisando tipo e textura do solo",
  "Calculando distância a cursos d'água",
  "Estimando cobertura vegetal residual",
  "Verificando precipitação dos últimos 7 dias",
];

/* ---------- estado ---------- */

const state = {
  step: 1,
  biome: null,
  download: null, // true | false
  days: 30,
  hotspots: [],
  selectedHotspot: null,
  enrichment: null,
  recommendation: null,
};

/* ---------- utilidades determinísticas ---------- */

function hashSeed(str) {
  let h = 1779033703 ^ str.length;
  for (let i = 0; i < str.length; i++) {
    h = Math.imul(h ^ str.charCodeAt(i), 3432918353);
    h = (h << 13) | (h >>> 19);
  }
  return () => {
    h = Math.imul(h ^ (h >>> 16), 2246822507);
    h = Math.imul(h ^ (h >>> 13), 3266489909);
    h ^= h >>> 16;
    return (h >>> 0) / 4294967296;
  };
}

function clamp(v, min, max) { return Math.max(min, Math.min(max, v)); }

function pad(n) { return String(n).padStart(2, "0"); }

/* ---------- MOCK: geração de focos (equivalente a filter_by_biome) ---------- */

function generateHotspots(biomeKey, days) {
  const biome = BIOMES.find((b) => b.key === biomeKey);
  const rng = hashSeed(`focos|${biomeKey}|${days}`);
  const count = 6 + Math.floor(rng() * 8);
  const rows = [];
  const now = new Date();

  for (let i = 0; i < count; i++) {
    const lat = biome.bbox.latMin + rng() * (biome.bbox.latMax - biome.bbox.latMin);
    const lon = biome.bbox.lonMin + rng() * (biome.bbox.lonMax - biome.bbox.lonMin);
    const daysAgo = Math.floor(rng() * days);
    const d = new Date(now.getTime() - daysAgo * 86400000);
    const confidence = Math.round(45 + rng() * 55);
    rows.push({
      id: `f${i}`,
      datetime: `${pad(d.getDate())}/${pad(d.getMonth() + 1)}/${d.getFullYear()} ${pad(Math.floor(rng() * 24))}:${pad(Math.floor(rng() * 60))}`,
      lat, lon,
      municipio: `Município ${String.fromCharCode(65 + (i % 26))} / ${["AM", "MT", "BA", "PA", "GO", "RS", "MG"][i % 7]}`,
      satelite: SATELLITES[Math.floor(rng() * SATELLITES.length)],
      confidence,
    });
  }
  return rows.sort((a, b) => b.confidence - a.confidence);
}

/* ---------- MOCK: enrich_hotspot ---------- */

function generateEnrichment(hotspot) {
  const rng = hashSeed(`enrich|${hotspot.lat.toFixed(3)}|${hotspot.lon.toFixed(3)}`);
  return {
    temp: Math.round((20 + rng() * 18) * 10) / 10,
    humidity: Math.round(clamp(20 + rng() * 60, 10, 90)),
    solo: ["Latossolo", "Argissolo", "Neossolo", "Cambissolo"][Math.floor(rng() * 4)],
    distAgua: Math.round((0.3 + rng() * 6) * 10) / 10,
    coberturaResidual: Math.round(clamp(rng() * 70, 3, 70)),
    precip7d: Math.round(clamp(rng() * 45, 0, 45)),
  };
}

/* ---------- MOCK: recommend_biocapsule ---------- */

function generateRecommendation(biomeKey, hotspot, enrichment) {
  const rng = hashSeed(`rec|${biomeKey}|${hotspot.lat.toFixed(3)}|${hotspot.lon.toFixed(3)}`);
  const species = (SPECIES_BY_BIOME[biomeKey] || SPECIES_BY_BIOME.all).map(([name, sci]) => {
    const qty = Math.round(15 + rng() * 35);
    return { name, sci, pct: qty };
  });
  const totalPct = species.reduce((s, x) => s + x.pct, 0);
  species.forEach((s) => { s.pct = Math.round((s.pct / totalPct) * 100); });

  const density = Math.round((2 + rng() * 4) * 10) / 10; // cápsulas / m²
  const areaHa = Math.round((0.5 + rng() * 4) * 10) / 10;
  const totalCapsulas = Math.round(density * areaHa * 10000);

  return { species, density, areaHa, totalCapsulas };
}

/* ---------- navegação ---------- */

function goToStep(n) {
  state.step = n;
  document.querySelectorAll(".step").forEach((el) => {
    el.classList.toggle("is-active", Number(el.dataset.step) === n);
  });
  document.querySelectorAll(".step-node").forEach((el) => {
    const s = Number(el.dataset.step);
    el.classList.toggle("is-current", s === n);
    el.classList.toggle("is-done", s < n);
  });
}

function showToast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg;
  t.classList.add("is-visible");
  setTimeout(() => t.classList.remove("is-visible"), 2200);
}

/* ---------- STEP 1 — biomas ---------- */

function renderBiomeGrid() {
  const grid = document.getElementById("biome-grid");
  grid.innerHTML = "";
  BIOMES.forEach((b) => {
    const btn = document.createElement("button");
    btn.className = "biome-card";
    btn.style.setProperty("--accent", b.accent);
    btn.innerHTML = `
      <span class="biome-emoji">${b.emoji}</span>
      <span class="biome-name">${b.label}</span>
      <span class="biome-key">bioma = "${b.key}"</span>
    `;
    btn.addEventListener("click", () => {
      state.biome = b.key;
      document.querySelectorAll(".biome-card").forEach((c) => c.classList.remove("is-selected"));
      btn.classList.add("is-selected");
      setTimeout(() => goToStep(2), 180);
    });
    grid.appendChild(btn);
  });
}

/* ---------- STEP 2 — INPE ---------- */

function setupStep2() {
  const yesBtn = document.getElementById("download-yes");
  const noBtn = document.getElementById("download-no");
  const daysBox = document.getElementById("days-box");
  const consolidateBtn = document.getElementById("consolidate-btn");

  yesBtn.addEventListener("click", () => {
    state.download = true;
    yesBtn.classList.add("is-selected");
    noBtn.classList.remove("is-selected");
    daysBox.hidden = false;
    consolidateBtn.disabled = false;
  });

  noBtn.addEventListener("click", () => {
    state.download = false;
    noBtn.classList.add("is-selected");
    yesBtn.classList.remove("is-selected");
    daysBox.hidden = true;
    consolidateBtn.disabled = false;
  });

  document.getElementById("days-input").addEventListener("input", (e) => {
    state.days = clamp(parseInt(e.target.value || "30", 10), 1, 90);
  });

  consolidateBtn.addEventListener("click", () => {
    showToast(state.download
      ? `Baixando focos do INPE (últimos ${state.days} dias)…`
      : "Reutilizando cache local (--skip-download)…");
    consolidateBtn.disabled = true;
    setTimeout(() => {
      state.hotspots = generateHotspots(state.biome, state.days);
      renderHotspotTable();
      consolidateBtn.disabled = false;
      goToStep(3);
    }, 650);
  });
}

/* ---------- STEP 3 — tabela de focos ---------- */

function renderHotspotTable() {
  const biome = BIOMES.find((b) => b.key === state.biome);
  document.getElementById("hotspot-summary").textContent =
    `${state.hotspots.length} focos encontrados para o bioma "${biome.label}".`;

  const tbody = document.getElementById("hotspot-tbody");
  tbody.innerHTML = "";
  state.hotspots.forEach((h) => {
    const tr = document.createElement("tr");
    tr.dataset.id = h.id;
    const confColor = h.confidence >= 75 ? "var(--leaf-light)" : h.confidence >= 50 ? "var(--amber)" : "var(--sand)";
    tr.innerHTML = `
      <td><span class="radio-dot"></span></td>
      <td>${h.datetime}</td>
      <td>${h.lat.toFixed(4)}</td>
      <td>${h.lon.toFixed(4)}</td>
      <td>${h.municipio}</td>
      <td>${h.satelite}</td>
      <td><span class="confidence-badge" style="background:${confColor}22; color:${confColor};">${h.confidence}%</span></td>
    `;
    tr.addEventListener("click", () => {
      document.querySelectorAll(".hotspot-table tbody tr").forEach((r) => r.classList.remove("is-selected"));
      tr.classList.add("is-selected");
      state.selectedHotspot = h;
      document.getElementById("select-hotspot-btn").disabled = false;
    });
    tbody.appendChild(tr);
  });
}

function setupStep3() {
  document.getElementById("select-hotspot-btn").addEventListener("click", () => {
    goToStep(4);
    runEnrichment();
  });
}

/* ---------- STEP 4 — enriquecimento (auto) ---------- */

function runEnrichment() {
  const list = document.getElementById("enrich-list");
  list.innerHTML = "";
  ENRICH_STEPS.forEach((label, i) => {
    const li = document.createElement("li");
    li.innerHTML = `<span class="enrich-check"></span><span>${label}</span>`;
    list.appendChild(li);
  });

  const items = list.querySelectorAll("li");
  items.forEach((li, i) => {
    setTimeout(() => {
      li.classList.add("is-done");
      li.querySelector(".enrich-check").textContent = "✓";
      if (i === items.length - 1) {
        setTimeout(() => {
          state.enrichment = generateEnrichment(state.selectedHotspot);
          state.recommendation = generateRecommendation(state.biome, state.selectedHotspot, state.enrichment);
          renderResult();
          goToStep(5);
        }, 400);
      }
    }, 350 * (i + 1));
  });
}

/* ---------- STEP 5 — resultado ---------- */

function renderResult() {
  const biome = BIOMES.find((b) => b.key === state.biome);
  const h = state.selectedHotspot;
  const e = state.enrichment;
  const r = state.recommendation;

  document.getElementById("result-sub").textContent =
    `Bioma ${biome.label} · foco em ${h.lat.toFixed(4)}, ${h.lon.toFixed(4)} (${h.datetime})`;

  const speciesList = document.getElementById("species-list");
  speciesList.innerHTML = "";
  r.species.forEach((s) => {
    const div = document.createElement("div");
    div.className = "species-row";
    div.innerHTML = `
      <div>
        <div class="species-name">${s.name}</div>
        <div class="species-sci">${s.sci}</div>
      </div>
      <div class="species-qty">${s.pct}%</div>
    `;
    speciesList.appendChild(div);
  });

  document.getElementById("dose-summary").innerHTML = `
    Densidade recomendada: <strong>${r.density} cápsulas/m²</strong><br/>
    Área estimada do foco: <strong>${r.areaHa} ha</strong><br/>
    Total estimado: <strong>${r.totalCapsulas.toLocaleString("pt-BR")} biocápsulas</strong>
  `;

  const factorGrid = document.getElementById("result-factor-grid");
  factorGrid.innerHTML = "";
  const factors = [
    { label: "Temperatura", value: `${e.temp}°C` },
    { label: "Umidade relativa", value: `${e.humidity}%` },
    { label: "Tipo de solo", value: e.solo },
    { label: "Dist. curso d'água", value: `${e.distAgua} km` },
    { label: "Cobertura residual", value: `${e.coberturaResidual}%` },
    { label: "Chuva (7d)", value: `${e.precip7d} mm` },
  ];
  factors.forEach((f) => {
    const div = document.createElement("div");
    div.className = "factor";
    div.innerHTML = `<div class="factor-label">${f.label}</div><div class="factor-value">${f.value}</div>`;
    factorGrid.appendChild(div);
  });

  renderMap(h.lat, h.lon);
}

function renderMap(lat, lon) {
  const W = 900, H = 360, cx = W / 2, cy = H / 2;
  const svg = `
  <svg viewBox="0 0 ${W} ${H}">
    <defs>
      <radialGradient id="terrain" cx="50%" cy="45%" r="75%">
        <stop offset="0%" stop-color="#12264A"/>
        <stop offset="100%" stop-color="#0B0B0B"/>
      </radialGradient>
      <radialGradient id="pulse" cx="50%" cy="50%" r="50%">
        <stop offset="0%" stop-color="var(--amber)" stop-opacity="0.45"/>
        <stop offset="100%" stop-color="var(--amber)" stop-opacity="0"/>
      </radialGradient>
    </defs>
    <rect x="0" y="0" width="${W}" height="${H}" fill="url(#terrain)"/>
    ${[-3, -2, -1, 0, 1, 2, 3].map((i) => `<line x1="${cx + i * 110}" y1="0" x2="${cx + i * 110}" y2="${H}" stroke="var(--azure)" stroke-opacity="0.18" stroke-width="1"/>`).join("")}
    ${[-1, 0, 1].map((i) => `<line x1="0" y1="${cy + i * 110}" x2="${W}" y2="${cy + i * 110}" stroke="var(--azure)" stroke-opacity="0.18" stroke-width="1"/>`).join("")}
    <circle cx="${cx}" cy="${cy}" r="150" fill="none" stroke="var(--sand)" stroke-opacity="0.35" stroke-width="1.2" stroke-dasharray="3 7"/>
    <circle cx="${cx}" cy="${cy}" r="120" fill="url(#pulse)"/>
    <line x1="${cx - 16}" y1="${cy}" x2="${cx + 16}" y2="${cy}" stroke="var(--white)" stroke-width="1"/>
    <line x1="${cx}" y1="${cy - 16}" x2="${cx}" y2="${cy + 16}" stroke="var(--white)" stroke-width="1"/>
    <circle cx="${cx}" cy="${cy}" r="8" fill="var(--amber)" stroke="var(--white)" stroke-width="2"/>
    <text x="${cx + 16}" y="${cy - 16}" font-size="13" fill="var(--white)" font-family="Inter, sans-serif" font-weight="600">${lat.toFixed(4)}°, ${lon.toFixed(4)}°</text>
  </svg>`;
  document.getElementById("map-frame").innerHTML = svg;
}

/* ---------- exportação ---------- */

function downloadBlob(filename, content, mime) {
  const blob = new Blob([content], { type: mime });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(url);
}

function setupExports() {
  document.getElementById("export-json").addEventListener("click", () => {
    const payload = {
      bioma: state.biome,
      foco: state.selectedHotspot,
      enriquecimento: state.enrichment,
      recomendacao: state.recommendation,
    };
    downloadBlob("revivetech_recomendacao.json", JSON.stringify(payload, null, 2), "application/json");
  });

  document.getElementById("export-csv").addEventListener("click", () => {
    const rows = [["especie", "nome_cientifico", "percentual"]];
    state.recommendation.species.forEach((s) => rows.push([s.name, s.sci, s.pct]));
    const csv = rows.map((r) => r.join(",")).join("\n");
    downloadBlob("revivetech_especies.csv", csv, "text/csv");
  });

  document.getElementById("restart-btn").addEventListener("click", () => {
    state.selectedHotspot = null;
    state.enrichment = null;
    state.recommendation = null;
    document.getElementById("select-hotspot-btn").disabled = true;
    document.querySelectorAll(".hotspot-table tbody tr").forEach((r) => r.classList.remove("is-selected"));
    goToStep(3);
  });
}

/* ---------- back buttons ---------- */

function setupBackButtons() {
  document.querySelectorAll("[data-back]").forEach((btn) => {
    btn.addEventListener("click", () => goToStep(Number(btn.dataset.back)));
  });
}

/* ---------- init ---------- */

function init() {
  renderBiomeGrid();
  setupStep2();
  setupStep3();
  setupExports();
  setupBackButtons();
  goToStep(1);
}

document.addEventListener("DOMContentLoaded", init);