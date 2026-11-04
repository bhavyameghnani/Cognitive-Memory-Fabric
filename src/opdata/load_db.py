"""Load the generated CSVs into PostgreSQL (schema cmf_ops) — or into SQLite for an offline smoke test.

    python src/opdata/load_db.py --dsn postgresql://user:pass@localhost:5432/cmf --reset
    CMF_DATABASE_URL=postgresql://... python src/opdata/load_db.py --reset
    python src/opdata/load_db.py --sqlite build/cmf_ops_smoke.db        # no server needed (tests DDL constraints only)

--reset drops and recreates ONLY schema cmf_ops. Without it the loader refuses to touch an existing schema.
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sqlite3
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "opdata"

from opdata.config import OUT_DIR, SCHEMA, SQL_DIR
from opdata.generate import COLUMNS, LOAD_ORDER

SQL_FILES = ["001_cmf_ops_schema.sql", "002_cmf_ops_views.sql"]


def read_sql(name: str) -> str:
    return (SQL_DIR / name).read_text(encoding="utf-8")


def split_statements(sql: str) -> list[str]:
    sql = re.sub(r"--[^\n]*", "", sql)
    return [s.strip() for s in sql.split(";") if s.strip()]


# ------------------------------------------------------------------ PostgreSQL
def load_postgres(dsn: str, data_dir: Path, reset: bool) -> dict[str, int]:
    try:
        import psycopg
    except ImportError:
        raise SystemExit("psycopg (v3) is required: pip install 'psycopg[binary]'")
    counts: dict[str, int] = {}
    with psycopg.connect(dsn, autocommit=False) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM information_schema.schemata WHERE schema_name = %s", (SCHEMA,))
            exists = cur.fetchone() is not None
            if exists and not reset:
                raise SystemExit(f"schema {SCHEMA} already exists; re-run with --reset to drop and recreate it")
            if exists:
                cur.execute(read_sql("000_drop_cmf_ops.sql"))
            for f in SQL_FILES:
                for stmt in split_statements(read_sql(f)):
                    cur.execute(stmt)
            for t in LOAD_ORDER:
                cols = ", ".join(COLUMNS[t])
                with open(data_dir / f"{t}.csv", "rb") as fh, \
                        cur.copy(f"COPY {SCHEMA}.{t} ({cols}) FROM STDIN WITH (FORMAT csv, HEADER true, NULL '')") as cp:
                    while chunk := fh.read(65536):
                        cp.write(chunk)
                cur.execute(f"SELECT count(*) FROM {SCHEMA}.{t}")
                counts[t] = cur.fetchone()[0]
        conn.commit()
    return counts


# ------------------------------------------------------------------ SQLite smoke test
def connect_sqlite(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def load_sqlite(path: str, data_dir: Path) -> dict[str, int]:
    """Same DDL, with the schema prefix stripped (SQLite has no schemas). Exercises every CHECK/FK/UNIQUE constraint."""
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        if Path(path).exists():
            Path(path).unlink()
    conn = connect_sqlite(path)
    for f in SQL_FILES:
        for stmt in split_statements(read_sql(f)):
            if stmt.upper().startswith("CREATE SCHEMA"):
                continue
            conn.execute(stmt.replace(f"{SCHEMA}.", ""))
    counts = {}
    for t in LOAD_ORDER:
        with open(data_dir / f"{t}.csv", newline="", encoding="utf-8") as fh:
            rdr = csv.reader(fh)
            header = next(rdr)
            rows = [[None if v == "" else (1 if v == "true" else 0 if v == "false" else v) for v in r] for r in rdr]
        conn.executemany(f"INSERT INTO {t} ({', '.join(header)}) VALUES ({', '.join('?' * len(header))})", rows)
        counts[t] = len(rows)
    conn.commit()
    conn.close()
    return counts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dsn", default=os.environ.get("CMF_DATABASE_URL") or os.environ.get("DATABASE_URL"))
    ap.add_argument("--data-dir", type=Path, default=OUT_DIR)
    ap.add_argument("--reset", action="store_true", help="drop and recreate schema cmf_ops first")
    ap.add_argument("--sqlite", metavar="FILE", help="load into a SQLite file instead (offline smoke test)")
    a = ap.parse_args(argv)
    if a.sqlite:
        counts = load_sqlite(a.sqlite, a.data_dir)
        target = f"sqlite:{a.sqlite}"
    else:
        if not a.dsn:
            raise SystemExit("no database: pass --dsn or set CMF_DATABASE_URL (or use --sqlite FILE for a smoke test)")
        counts = load_postgres(a.dsn, a.data_dir, a.reset)
        target = f"postgres schema {SCHEMA}"
    print("loaded into", target)
    for t, n in counts.items():
        print(f"  {t:28s} {n:5d}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
