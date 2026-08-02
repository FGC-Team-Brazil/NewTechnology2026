#!/usr/bin/env python3
"""
ReviveTech — Pipeline completo: Latitude/Longitude -> Recomendação de Biocápsula
====================================================================================
Une os dois módulos já construídos:
  1. revivetech_data_collector.py -> coleta dados da região (localização,
     clima, solo, áreas protegidas etc.)
  2. motor_decisao.py              -> transforma esses dados em uma
     recomendação de espécie + dosagem de biochar/hidrogel

Uso:
    python revivetech_pipeline.py -15.60 -47.70
"""

from __future__ import annotations

import argparse
import json
import sys

import rtdata as coletor
import motor_decision as motor


def main() -> None:
    parser = argparse.ArgumentParser(description="Pipeline completo lat/long -> recomendação de biocápsula")
    parser.add_argument("lat", type=float, nargs="?")
    parser.add_argument("lon", type=float, nargs="?")
    parser.add_argument("--especies", default="especies.json")
    parser.add_argument("--tipo-solo", default="latossolo_amarelo",
                         choices=list(motor.CURVA_BIOCHAR_T_HA.keys()))
    parser.add_argument("--capsulas-por-m2", type=float, default=4.0)
    parser.add_argument("--raio", type=float, default=15.0)
    parser.add_argument("--top", type=int, default=3)
    parser.add_argument("--saida", default="saidas")
    args = parser.parse_args()

    lat, lon = args.lat, args.lon
    if lat is None or lon is None:
        try:
            lat = float(input("Latitude: ").strip())
            lon = float(input("Longitude: ").strip())
        except (ValueError, EOFError):
            print("Latitude/longitude inválidas.", file=sys.stderr)
            sys.exit(1)

    print(f"\n[1/2] Coletando dados da região ({lat}, {lon})...")
    dados_regiao = coletor.coletar_tudo(lat, lon, raio_km=args.raio)
    coletor.imprimir_resumo(dados_regiao)
    caminho_regiao = coletor.salvar_json(dados_regiao, pasta_saida=args.saida)
    print(f"Dados da região salvos em: {caminho_regiao}")

    print(f"\n[2/2] Calculando recomendação de biocápsula...")
    recomendacao = motor.recomendar_biocapsula(
        dados_regiao,
        caminho_especies=args.especies,
        tipo_solo=args.tipo_solo,
        capsulas_por_m2=args.capsulas_por_m2,
        top_n=args.top,
    )

    print("\n" + "=" * 70)
    print("  RECOMENDAÇÃO DE BIOCÁPSULA")
    print("=" * 70)
    print(f"pH do solo no ponto: {recomendacao['ph_solo_ponto']}")
    print(f"Precipitação anual estimada: {recomendacao['precipitacao_anual_mm_ponto']} mm")
    for i, rec in enumerate(recomendacao["recomendacoes"], start=1):
        print(f"\n#{i} {rec['especie']} ({rec['nome_cientifico']}) — pontuação {rec['pontuacao_final']}")
        print(f"    Subnotas: {rec['subnotas']}")
        d = rec["dosagem_capsula"]
        print(f"    Dosagem sugerida: {d['biochar_g']} g biochar | {d['hidrogel_g']} g hidrogel")
        print(f"    Fonte: {rec['fonte']}")

    saida_final = {"dados_regiao": dados_regiao, "recomendacao_biocapsula": recomendacao}
    import os
    from datetime import datetime
    os.makedirs(args.saida, exist_ok=True)
    carimbo = datetime.now().strftime("%Y%m%d_%H%M%S")
    caminho_final = os.path.join(args.saida, f"recomendacao_{lat}_{lon}_{carimbo}.json")
    with open(caminho_final, "w", encoding="utf-8") as f:
        json.dump(saida_final, f, ensure_ascii=False, indent=2)
    print(f"\nResultado completo salvo em: {caminho_final}")


if __name__ == "__main__":
    main()