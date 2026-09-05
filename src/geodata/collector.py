#!/usr/bin/env python3
"""
src/geodata/collector.py
=========================
Re-exporta collect_all() e print_summary() de geodata/rtdata.py.
Wrapper de compatibilidade que mantém geodata/ como pacote legado
e expõe a interface limpa para o restante do src/.
"""
from __future__ import annotations

import sys
from pathlib import Path

_GEODATA = Path(__file__).resolve().parents[2] / "geodata"
if str(_GEODATA) not in sys.path:
    sys.path.insert(0, str(_GEODATA))

from rtdata import collect_all, print_summary, save_json  # type: ignore  # noqa: F401, E402
