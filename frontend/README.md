# frontend/ — Interface Web (Espaço Reservado)

Este diretório está reservado para o futuro front-end do ReviveTech.

## Opções planejadas

### Opção A — Streamlit (mais rápido de implementar)
- Instalar: `pip install streamlit`
- Criar: `frontend/app.py`
- Rodar: `streamlit run frontend/app.py`

### Opção B — FastAPI + HTML (mais flexível)
- Instalar: `pip install fastapi uvicorn`
- Criar: `frontend/api.py` + `frontend/static/`
- Rodar: `uvicorn frontend.api:app --reload`

## O que o front-end deverá fazer

1. **Seleção de bioma** — menu dropdown com os 6 biomas brasileiros
2. **Visualização de focos** — mapa interativo (Folium/Leaflet) com os focos filtrados
3. **Seleção de foco** — clique no mapa para selecionar o incêndio a analisar
4. **Exibição de resultados** — dashboard com dados enriquecidos + recomendação de biocápsulas

## Estado atual

A interação é feita via **Terminal UI** (`src/ui/terminal.py`) usando `rich` + `questionary`.
Todo o processamento está em `src/` e pode ser reutilizado pelo front-end sem alterações.

## Como conectar ao pipeline

```python
# Exemplo de uso programático (para o front-end chamar)
from src.processing.hotspot_filter import filter_by_biome, load_consolidated
from src.processing.enrichment import enrich_hotspot
from src.engine.decision import recommend_biocapsule
from src.output.exporter import export_all
```
