#!/usr/bin/env python3
"""
ReviveTech 2026 — Terminal UI
=============================
Camada de entrada via terminal. Responsável por:
  - Apresentar o menu de biomas
  - Exibir e filtrar a lista de focos por bioma/município/data
  - Confirmar se o usuário quer baixar dados novos antes de processar

Totalmente desacoplada da lógica de negócio: não importa nada de
src/processing, src/engine ou src/geodata — só recebe dados e devolve
a escolha do usuário.

PLACEHOLDER FRONT-END:
    Este módulo pode ser substituído (ou complementado) por um front-end
    web em frontend/. Veja frontend/README.md para detalhes.
"""
from __future__ import annotations

import sys
import unicodedata
from typing import Optional

import pandas as pd

# ── rich (tabelas coloridas) ────────────────────────────────────────────────
try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.prompt import Prompt
    from rich import box
    _RICH = True
except ImportError:
    _RICH = False

# ── questionary (menus interativos) ────────────────────────────────────────
try:
    import questionary
    _QUESTIONARY = True
except ImportError:
    _QUESTIONARY = False

console = Console() if _RICH else None

# ---------------------------------------------------------------------------
# Biomas disponíveis (ID interno → label de exibição)
# ---------------------------------------------------------------------------
BIOMES: dict[str, str] = {
    "cerrado":       "Cerrado",
    "amazonia":      "Amazônia",
    "caatinga":      "Caatinga",
    "mata_atlantica": "Mata Atlântica",
    "pampa":         "Pampa",
    "pantanal":      "Pantanal",
}

# Mapeamento de variações encontradas no CSV do INPE → chave interna
BIOME_ALIASES: dict[str, str] = {
    "cerrado":        "cerrado",
    "amazonia":       "amazonia",
    "amazônia":       "amazonia",
    "caatinga":       "caatinga",
    "mata atlântica": "mata_atlantica",
    "mata atlantica": "mata_atlantica",
    "pampa":          "pampa",
    "pantanal":       "pantanal",
}


def _remove_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(c for c in normalized if not unicodedata.combining(c)).lower()


def _plain_input(prompt: str) -> str:
    """Fallback quando rich/questionary não estão disponíveis."""
    return input(prompt).strip()


# ---------------------------------------------------------------------------
# 1. Seleção de bioma
# ---------------------------------------------------------------------------

def select_biome() -> tuple[str, str]:
    """
    Exibe o menu de biomas e devolve (biome_key, biome_label).
    Exemplo: ("cerrado", "Cerrado")
    """
    choices = [{"name": label, "value": key} for key, label in BIOMES.items()]

    if _RICH:
        console.print(Panel.fit(
            "[bold green]ReviveTech 2026[/bold green] — Sistema de Recomendação de Biocápsulas",
            border_style="green",
        ))
        console.print("\n[bold]Selecione o bioma para análise:[/bold]\n")

    if _QUESTIONARY:
        key = questionary.select(
            "Bioma:",
            choices=choices,
        ).ask()
        if key is None:  # usuário pressionou Ctrl+C
            sys.exit(0)
    else:
        # Fallback plain-text
        print("\n=== ReviveTech 2026 — Selecione o Bioma ===\n")
        for i, (key, label) in enumerate(BIOMES.items(), start=1):
            print(f"  {i}. {label}")
        while True:
            raw = _plain_input("\nNúmero do bioma: ")
            if raw.isdigit() and 1 <= int(raw) <= len(BIOMES):
                key = list(BIOMES.keys())[int(raw) - 1]
                break
            print("Opção inválida. Tente novamente.")

    label = BIOMES[key]
    if _RICH:
        console.print(f"\n[green]✓[/green] Bioma selecionado: [bold]{label}[/bold]\n")
    else:
        print(f"\nBioma selecionado: {label}\n")

    return key, label


# ---------------------------------------------------------------------------
# 2. Confirmação de download de dados
# ---------------------------------------------------------------------------

def confirm_download() -> bool:
    """
    Pergunta se o usuário quer baixar/atualizar os dados do INPE antes
    de processar. Retorna True para baixar, False para usar dados existentes.
    """
    if _QUESTIONARY:
        answer = questionary.confirm(
            "Baixar/atualizar dados de focos do INPE antes de processar?",
            default=False,
        ).ask()
        return bool(answer)
    else:
        raw = _plain_input("Baixar dados novos do INPE? (s/N): ").lower()
        return raw in ("s", "sim", "y", "yes")


def ask_days() -> int:
    """Pergunta quantos dias de histórico baixar."""
    if _QUESTIONARY:
        raw = questionary.text(
            "Quantos dias de histórico baixar?",
            default="30",
            validate=lambda v: v.isdigit() and int(v) > 0 or "Digite um número positivo.",
        ).ask()
        return int(raw) if raw else 30
    else:
        raw = _plain_input("Quantos dias de histórico baixar? [30]: ")
        return int(raw) if raw.isdigit() else 30


# ---------------------------------------------------------------------------
# 3. Exibição e seleção de foco
# ---------------------------------------------------------------------------

def _clean(value, empty: str = "—") -> str:
    text = str(value) if value is not None else ""
    return empty if text.strip().lower() in ("nan", "none", "") else text


def show_hotspot_list(df: pd.DataFrame, biome_label: str) -> None:
    """Exibe até 30 focos em uma tabela formatada."""
    if _RICH:
        table = Table(
            title=f"Focos consolidados — {biome_label}",
            box=box.ROUNDED,
            show_lines=False,
        )
        table.add_column("#", style="bold cyan", justify="right", width=5)
        table.add_column("Data", width=12)
        table.add_column("Município", width=26)
        table.add_column("Bioma", width=16)
        table.add_column("Focos", justify="right", width=8)
        table.add_column("Hectares", justify="right", width=10)

        for i, row in df.head(30).iterrows():
            table.add_row(
                str(i),
                _clean(row.get("data_pura")),
                _clean(row.get("municipio"))[:25],
                _clean(row.get("bioma"))[:15],
                str(int(row["qtd_focos"])),
                f"{float(row.get('tamanho_hectares', 0) or 0):,.1f}",
            )
        console.print(table)
        if len(df) > 30:
            console.print(f"[dim]... e mais {len(df) - 30} focos (use busca para filtrar)[/dim]")
    else:
        print(f"\n{'#':>4}  {'Data':<12} {'Município':<25} {'Bioma':<15} {'Focos':>8}")
        print("-" * 70)
        for i, row in df.head(30).iterrows():
            print(
                f"{i:>4}  {_clean(row.get('data_pura')):<12} "
                f"{_clean(row.get('municipio'))[:24]:<25} "
                f"{_clean(row.get('bioma'))[:14]:<15} "
                f"{int(row['qtd_focos']):>8}"
            )
        if len(df) > 30:
            print(f"... e mais {len(df) - 30} focos")


def select_hotspot(df: pd.DataFrame, biome_label: str) -> Optional[pd.Series]:
    """
    Loop interativo: exibe lista, aceita número, busca ou 'exit'.
    Retorna a linha (pd.Series) do foco selecionado, ou None se o usuário sair.
    """
    df_current = df.copy()

    while True:
        show_hotspot_list(df_current, biome_label)

        if _RICH:
            console.print(
                "\n[dim]Digite o [bold]número[/bold] do foco, um termo de busca "
                "(município ou data YYYY-MM-DD), [bold]'limpar'[/bold] para resetar "
                "filtros, ou [bold]'sair'[/bold]:[/dim]"
            )
            user_input = Prompt.ask(">").strip()
        else:
            print(
                "\nDigite o número do foco, um termo de busca, "
                "'limpar' para resetar, ou 'sair':"
            )
            user_input = _plain_input("> ")

        if user_input.lower() in ("exit", "quit", "q", "sair"):
            return None

        if user_input.lower() in ("limpar", "clear"):
            df_current = df.copy()
            continue

        if user_input.isdigit():
            index = int(user_input)
            if index not in df_current.index:
                _warn("Número inválido — escolha um da lista exibida.")
                continue
            return df_current.loc[index]

        if user_input:
            term = _remove_accents(user_input)
            filtered = df[
                df["municipio"].astype(str).apply(_remove_accents).str.contains(term, na=False)
                | df["data_pura"].astype(str).str.contains(user_input, na=False)
            ].reset_index(drop=True)
            if filtered.empty:
                _warn(f"Nenhum foco encontrado para '{user_input}'. Exibindo todos.")
                df_current = df.copy()
            else:
                df_current = filtered
        else:
            df_current = df.copy()


# ---------------------------------------------------------------------------
# Helpers de output
# ---------------------------------------------------------------------------

def _warn(msg: str) -> None:
    if _RICH:
        console.print(f"[yellow]⚠[/yellow] {msg}")
    else:
        print(f"AVISO: {msg}")


def print_recommendation(recommendation: dict) -> None:
    """Exibe a recomendação de biocápsulas no terminal."""
    if _RICH:
        console.print("\n" + "=" * 70)
        console.print("[bold green] RECOMENDAÇÃO DE BIOCÁPSULAS (motor geodata)[/bold green]")
        console.print("=" * 70)
        console.print(f"pH do solo no ponto: [bold]{recommendation['point_soil_ph']}[/bold]")
        console.print(
            f"Precipitação anual estimada: [bold]{recommendation['point_annual_precipitation_mm']} mm[/bold]"
        )
        for i, rec in enumerate(recommendation["recommendations"], start=1):
            dosage = rec["capsule_dosage"]
            console.print(
                f"\n[cyan]#{i}[/cyan] [bold]{rec['species']}[/bold] "
                f"({rec['scientific_name']}) — score [bold]{rec['final_score']}[/bold]"
            )
            console.print(f"    Sub-scores: {rec['sub_scores']}")
            console.print(
                f"    Dosagem sugerida: [bold]{dosage['biochar_g']} g[/bold] biochar | "
                f"[bold]{dosage['hydrogel_g']} g[/bold] hidrogel"
            )
            console.print(f"    Fonte: [dim]{rec['source']}[/dim]")
    else:
        print("\n" + "=" * 70)
        print(" RECOMENDAÇÃO DE BIOCÁPSULAS (motor geodata)")
        print("=" * 70)
        print(f"pH do solo: {recommendation['point_soil_ph']}")
        print(f"Precipitação anual: {recommendation['point_annual_precipitation_mm']} mm")
        for i, rec in enumerate(recommendation["recommendations"], start=1):
            dosage = rec["capsule_dosage"]
            print(f"\n#{i} {rec['species']} ({rec['scientific_name']}) — score {rec['final_score']}")
            print(f"    Sub-scores: {rec['sub_scores']}")
            print(f"    Dosagem: {dosage['biochar_g']} g biochar | {dosage['hydrogel_g']} g hidrogel")
            print(f"    Fonte: {rec['source']}")


def print_step(step: str, total: int, current: int, label: str) -> None:
    """Exibe o cabeçalho de uma etapa do pipeline."""
    if _RICH:
        console.print(f"\n[bold blue]ETAPA {current}/{total}[/bold blue] — {label}")
        console.print("─" * 70)
    else:
        print(f"\n{'=' * 70}")
        print(f" ETAPA {current}/{total} — {label}")
        print("=" * 70)
