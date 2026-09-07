# frontend/ — Web Interface (Architecture Blueprint)

This directory is designated for the web presentation layer of ReviveTech.

## Planned Architectures

### Option A — Streamlit (Rapid Prototyping)
- Install: `pip install streamlit`
- Create: `frontend/app.py`
- Run: `streamlit run frontend/app.py`

### Option B — FastAPI + React/HTML (Production Grade)
- Install: `pip install fastapi uvicorn`
- Create: `frontend/api.py` + `frontend/static/`
- Run: `uvicorn frontend.api:app --reload`

## Front-End Core Functional Scope

1. **Biome Selection** — Dropdown selector covering all 6 Brazilian biomes.
2. **Wildfire Hotspot Visualization** — Geospatial interactive map (Folium/Leaflet) displaying filtered events.
3. **Target Selection** — Interactive selection of specific fire incidents for diagnostic analysis.
4. **Dashboard & Prescriptions** — Visual rendering of enriched environmental indicators and biocapsule formulations.

## Current State

The primary interaction interface is currently driven by the **Terminal UI** (`src/ui/terminal.py`) powered by `rich` and `questionary`. All data processing modules in `src/` are fully decoupled and immediately consumable by API services.

## Programmatic Integration Example

```python
from src.processing.hotspot_filter import filter_by_biome, load_consolidated
from src.processing.enrichment import enrich_hotspot
from src.engine.decision import recommend_biocapsule
from src.output.exporter import export_all
```
