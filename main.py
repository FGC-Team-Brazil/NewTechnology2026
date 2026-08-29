#!/usr/bin/env python3
"""
ReviveTech 2026 — Ponto de entrada único
=========================================
Orquestra o pipeline completo na ordem:

  1. [UI]         Seleciona bioma e confirma download de dados
  2. [Ingestion]  Baixa focos do INPE (opcional) e consolida
  3. [Processing] Filtra focos pelo bioma selecionado
  4. [UI]         Seleciona o foco de incêndio específico
  5. [Processing] Enriquece o foco com APIs externas (sem gargalo)
  6. [Engine]     Recomenda espécies e dosagem de biocápsulas
  7. [Output]     Salva dashboard, CSV enriquecido e JSON de recomendação

Uso via terminal:
    python main.py                  # fluxo completo interativo
    python main.py --skip-download  # pula etapas 1-2, usa planilha existente
    python main.py --days 60        # baixa os últimos 60 dias

PLACEHOLDER FRONT-END:
    Quando frontend/ estiver pronto, este arquivo permanece como
    backend Python puro. O front-end chamará as funções de
    src/processing/ e src/engine/ diretamente via API (FastAPI/Streamlit).
    Veja frontend/README.md para detalhes.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Garante que src/ seja encontrável independentemente de onde main.py é chamado
PROJECT_DIR = Path(__file__).parent.resolve()
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--days", type=int, default=30,
        help="Número de dias de histórico a baixar (padrão: 30)",
    )
    parser.add_argument(
        "--skip-download", action="store_true",
        help="Pula download/consolidação; usa planilha_fogos_consolidados.csv existente",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # ------------------------------------------------------------------
    # Imports internos — feitos aqui para que erros de dependência
    # apareçam com mensagens claras depois do argparse.
    # ------------------------------------------------------------------
    from src.ui.terminal import (
        select_biome,
        confirm_download,
        ask_days,
        select_hotspot,
        print_recommendation,
        print_step,
    )
    from src.processing.hotspot_filter import load_consolidated, filter_by_biome
    from src.processing.enrichment import enrich_hotspot
    from src.engine.decision import recommend_biocapsule
    from src.output.exporter import open_dashboard, save_recommendation_json

    # ------------------------------------------------------------------
    # ETAPA 0 — Seleção de bioma (sempre, antes de qualquer I/O)
    # ------------------------------------------------------------------
    biome_key, biome_label = select_biome()

    # ------------------------------------------------------------------
    # ETAPA 1 — Download e consolidação (opcional)
    # ------------------------------------------------------------------
    if not args.skip_download:
        should_download = confirm_download()
        if should_download:
            days = args.days if args.days != 30 else ask_days()
            print_step("Download", 3, 1, f"Baixando focos do INPE (últimos {days} dias)")
            from src.ingestion.unify_hotspots import run as run_unify
            run_unify(days)

            print_step("Consolidação", 3, 2, "Consolidando focos em incêndios reais")
            from src.ingestion.consolidate import run as run_consolidate
            run_consolidate()
    else:
        print("Pulando download/consolidação (--skip-download) — usando planilha existente.")

    # ------------------------------------------------------------------
    # ETAPA 2 — Filtro por bioma
    # ------------------------------------------------------------------
    print_step("Filtro", 3, 3, f"Carregando focos do bioma: {biome_label}")
    df_all     = load_consolidated()
    df_biome   = filter_by_biome(df_all, biome_key)
    print(f"{len(df_biome)} focos encontrados para o bioma {biome_label}.")

    # ------------------------------------------------------------------
    # ETAPA 3 — Seleção interativa do foco
    # ------------------------------------------------------------------
    row = select_hotspot(df_biome, biome_label)
    if row is None:
        print("\nSaindo. Nenhum foco selecionado.")
        sys.exit(0)

    # ------------------------------------------------------------------
    # ETAPA 4 — Enriquecimento com dados externos (sem gargalo de I/O)
    # ------------------------------------------------------------------
    print("\nColetando dados externos para o foco selecionado...")
    region_data, dashboard_path = enrich_hotspot(row)

    # Abre o dashboard HTML no navegador
    open_dashboard(dashboard_path)
    print(f"Dashboard aberto: {dashboard_path}")

    # ------------------------------------------------------------------
    # ETAPA 5 — Recomendação de biocápsulas (motor determinístico)
    # ------------------------------------------------------------------
    print("\nCalculando recomendação de biocápsulas...")
    recommendation = recommend_biocapsule(
        region_data,
        biome_key=biome_key,
    )

    print_recommendation(recommendation)

    # ------------------------------------------------------------------
    # ETAPA 6 — Salvar resultado completo em JSON
    # ------------------------------------------------------------------
    lat = float(row["lat_fogo"])
    lon = float(row["lon_fogo"])
    save_recommendation_json(region_data, recommendation, lat, lon)

    print("\nPipeline concluído.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrompido pelo usuário.")
        sys.exit(0)
