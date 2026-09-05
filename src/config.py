"""Configuração local, carregada de .env sem depender de biblioteca externa."""
from __future__ import annotations

import os
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_DIR / ".env"
DATA_LOCAL_DIR = PROJECT_DIR / "data" / "local"


def load_env() -> None:
    """Carrega pares KEY=VALUE de .env, sem sobrescrever variáveis do sistema."""
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


load_env()


def setting_path(name: str, default: Path) -> str:
    """Resolve um caminho configurável relativo ao diretório do projeto."""
    configured = os.environ.get(name)
    if not configured:
        return str(default)
    path = Path(configured)
    return str(path if path.is_absolute() else PROJECT_DIR / path)


PIPELINE_DB_PATH = setting_path("REVIVETECH_PIPELINE_DB", DATA_LOCAL_DIR / "pipeline.db")
WILDFIRE_DB_PATH = setting_path("REVIVETECH_WILDFIRE_DB", DATA_LOCAL_DIR / "queimadas_inpe.db")
