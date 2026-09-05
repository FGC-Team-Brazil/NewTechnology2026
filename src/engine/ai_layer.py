#!/usr/bin/env python3
"""
src/engine/ai_layer.py
=======================
Camada de aprendizado (k-NN ponderado por similaridade) que complementa
o motor determinístico (src/engine/decision.py) quando dados de campo
suficientes estiverem disponíveis.

Movido de geodata/motor_ai.py sem mudanças funcionais. Mantido isolado
do motor determinístico por design: qualquer recomendação produzida
aqui é identificada como "aprendida" vs. "curva de literatura".

Para alimentar o modelo:
    python -m src.engine.ai_layer add-trial region.json \\
        --species "Lobeira" --biochar-g 10.5 --hydrogel-g 3.0

Para registrar o resultado após meses:
    python -m src.engine.ai_layer record-outcome <trial_id> --survival-rate-pct 78

Ver geodata/motor_ai.py para a documentação completa.
"""

# Re-exporta tudo de geodata/motor_ai.py para manter compatibilidade
# e evitar duplicação de código enquanto o refactor é incremental.
import sys
from pathlib import Path

# Adiciona geodata/ ao path para o import funcionar
_GEODATA = Path(__file__).resolve().parents[2] / "geodata"
if str(_GEODATA) not in sys.path:
    sys.path.insert(0, str(_GEODATA))

from motor_ai import (  # type: ignore  # noqa: F401, E402
    DEFAULT_DATASET_PATH,
    DEFAULT_K,
    DEFAULT_MIN_EXAMPLES,
    add_trial,
    record_outcome,
    predict_proportion,
    recommend_biocapsule_hybrid,
)
