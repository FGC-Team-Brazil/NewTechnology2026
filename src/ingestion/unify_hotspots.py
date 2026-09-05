#!/usr/bin/env python3
"""Ingestão incremental dos CSVs diários do INPE.

CSV diário continua sendo a fonte auditável. O SQLite registra o hash de cada
arquivo e seus focos, de forma que só arquivos novos ou alterados são lidos
novamente. ``arquivo_unificado.csv`` é mantido apenas por compatibilidade com
o notebook legado.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.config import PIPELINE_DB_PATH, PROJECT_DIR, WILDFIRE_DB_PATH

DAILY_HOTSPOTS_DIR = PROJECT_DIR / "focos_diarios"
UNIFIED_FILE_PATH = PROJECT_DIR / "arquivo_unificado.csv"


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for block in iter(lambda: file.read(1_048_576), b""):
            digest.update(block)
    return digest.hexdigest()


def _connect() -> sqlite3.Connection:
    Path(PIPELINE_DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(PIPELINE_DB_PATH)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS input_files (
            source_path TEXT PRIMARY KEY,
            sha256 TEXT NOT NULL,
            imported_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS raw_hotspots (
            source_path TEXT NOT NULL,
            data_pura TEXT,
            bioma TEXT,
            latitude REAL,
            longitude REAL,
            record_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_raw_biome_day ON raw_hotspots(bioma, data_pura);
        CREATE INDEX IF NOT EXISTS idx_raw_source ON raw_hotspots(source_path);
    """)
    return connection


def _prepare_records(df: pd.DataFrame, source_path: str) -> list[tuple[object, ...]]:
    date_values = pd.to_datetime(df.get("data_hora_gmt"), errors="coerce", utc=True)
    records: list[tuple[object, ...]] = []
    for index, record in df.iterrows():
        data_pura = date_values.iloc[index].strftime("%Y-%m-%d") if pd.notna(date_values.iloc[index]) else None
        records.append((
            source_path,
            data_pura,
            str(record.get("bioma")) if pd.notna(record.get("bioma")) else None,
            float(record["lat"]) if pd.notna(record.get("lat")) else None,
            float(record["lon"]) if pd.notna(record.get("lon")) else None,
            json.dumps(record.to_dict(), ensure_ascii=False, default=str),
        ))
    return records


def synchronize_daily_files() -> tuple[sqlite3.Connection, bool]:
    """Import only CSVs whose content changed since their prior import."""
    files = sorted(DAILY_HOTSPOTS_DIR.glob("*.csv"))
    if not files:
        print(f"Erro: nenhum .csv encontrado em '{DAILY_HOTSPOTS_DIR}'.", file=sys.stderr)
        sys.exit(1)

    connection = _connect()
    changed = False
    for path in files:
        source_path = str(path.resolve())
        checksum = _hash_file(path)
        current = connection.execute("SELECT sha256 FROM input_files WHERE source_path = ?", (source_path,)).fetchone()
        if current and current[0] == checksum:
            continue

        df = pd.read_csv(path)
        records = _prepare_records(df, source_path)
        with connection:
            connection.execute("DELETE FROM raw_hotspots WHERE source_path = ?", (source_path,))
            connection.executemany(
                "INSERT INTO raw_hotspots VALUES (?, ?, ?, ?, ?, ?)", records,
            )
            connection.execute(
                "INSERT OR REPLACE INTO input_files VALUES (?, ?, ?)",
                (source_path, checksum, datetime.now(timezone.utc).isoformat()),
            )
        changed = True
        print(f"Importado: {path.name} ({len(df):,} focos)")

    return connection, changed


def export_compatibility_files(connection: sqlite3.Connection, force: bool) -> None:
    """Update legacy CSV and dashboard history DB only when source data changed."""
    if not force and UNIFIED_FILE_PATH.exists() and Path(WILDFIRE_DB_PATH).exists():
        return

    rows = connection.execute("SELECT record_json FROM raw_hotspots").fetchall()
    raw_df = pd.DataFrame([json.loads(row[0]) for row in rows])
    raw_df.to_csv(UNIFIED_FILE_PATH, index=False)

    history_path = Path(WILDFIRE_DB_PATH)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    history_rows = connection.execute(
        "SELECT latitude, longitude, data_pura FROM raw_hotspots WHERE latitude IS NOT NULL AND longitude IS NOT NULL"
    ).fetchall()
    with sqlite3.connect(history_path) as history:
        history.execute("DROP TABLE IF EXISTS hotspots")
        history.execute("CREATE TABLE hotspots (latitude REAL, longitude REAL, date_time TEXT)")
        history.executemany("INSERT INTO hotspots VALUES (?, ?, ?)", history_rows)
        history.execute("CREATE INDEX idx_hotspots_coordinates ON hotspots(latitude, longitude)")
    print(f"Dados locais atualizados: {len(raw_df):,} focos no cache e histórico.")


def run(days: int = 30) -> None:
    from src.ingestion.scraper_runner import run_ts_scraper

    ok = run_ts_scraper(days)
    if not ok:
        print("Aviso: scraper falhou; verificando os CSVs já disponíveis.")
    connection, changed = synchronize_daily_files()
    try:
        export_compatibility_files(connection, force=changed)
        print("Nenhum CSV alterado; cache incremental reutilizado." if not changed else "Cache incremental atualizado.")
    finally:
        connection.close()


if __name__ == "__main__":
    run(int(sys.argv[1]) if len(sys.argv) > 1 else 30)
