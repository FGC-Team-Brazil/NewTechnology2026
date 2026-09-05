#!/usr/bin/env python3
"""
src/geodata/dashboard.py
=========================
Re-exporta save_dashboard() de geodata/rtdash.py.
"""
from __future__ import annotations

import sys
from pathlib import Path

_GEODATA = Path(__file__).resolve().parents[2] / "geodata"
if str(_GEODATA) not in sys.path:
    sys.path.insert(0, str(_GEODATA))

from rtdash import save_dashboard  # type: ignore  # noqa: F401, E402
