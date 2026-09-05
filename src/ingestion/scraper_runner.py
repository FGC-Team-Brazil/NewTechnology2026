#!/usr/bin/env python3
"""
src/ingestion/scraper_runner.py
================================
Responsabilidade única: executar o scraper TypeScript (Bun) que baixa
os focos diários do INPE e gravar os CSVs em focos_diarios/.

Não conhece nada de processamento, UI ou motor de decisão.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[2]
TS_SCRIPT = PROJECT_DIR / "webscrap" / "daily.ts"


def run_ts_scraper(days: int = 30) -> bool:
    """
    Executa 'bun run daily.ts <days>' e retorna True se bem-sucedido.
    O stdout do Bun é transmitido em tempo real para o terminal.
    """
    print(f"[Python] Iniciando captura de focos (últimos {days} dias)...")

    if not TS_SCRIPT.exists():
        print(f"[Erro] Script TypeScript não encontrado: {TS_SCRIPT}", file=sys.stderr)
        return False

    try:
        process = subprocess.Popen(
            ["bun", "run", str(TS_SCRIPT), str(days)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )

        # Stream do stdout linha a linha (sem gargalo de buffering)
        if process.stdout:
            for line in process.stdout:
                print(f"[Bun] {line.rstrip()}")

        _, stderr = process.communicate()

        if process.returncode == 0:
            print("[Python] Script TypeScript concluído com sucesso.")
            return True

        print(f"[Erro] Script TypeScript falhou (exit {process.returncode})", file=sys.stderr)
        if stderr:
            print(f"[Bun stderr]:\n{stderr}", file=sys.stderr)
        return False

    except FileNotFoundError:
        print(
            "[Erro] Executável 'bun' não encontrado no PATH.\n"
            "Instale o Bun: https://bun.sh",
            file=sys.stderr,
        )
        return False
    except Exception as exc:  # noqa: BLE001
        print(f"[Erro inesperado]: {exc}", file=sys.stderr)
        return False


if __name__ == "__main__":
    days_arg = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    success = run_ts_scraper(days_arg)
    sys.exit(0 if success else 1)
