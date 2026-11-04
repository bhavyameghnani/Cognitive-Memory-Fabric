"""Database backend switch: the ONLY module that imports a database driver.

CMF_DATABASE_URL selects the backend:
    sqlite:///build/cmf_ops_smoke.db        (default; opened READ-ONLY)
    postgresql://user:pass@host:5432/db     (needs psycopg v3; schema cmf_ops)
Callers write SQL with '?' placeholders and the token {S} for the schema prefix; both are translated here.
"""
from __future__ import annotations

import os
import sqlite3
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_URL = "sqlite:///build/cmf_ops_smoke.db"

# Column families normalised identically on every backend (SQLite returns floats/strings, PostgreSQL Decimal/date).
MONEY = {"annual_income", "monthly_income", "monthly_expenses", "amount", "market_value", "purchase_price_total",
         "target_amount", "outstanding_amount", "monthly_payment", "investable_value", "value"}
UNIT = {"quantity", "price", "market_price", "purchase_price", "current_price"}
RATE = {"interest_rate", "equity_return", "bond_return"}
DATES = {"opened_date", "closed_date", "purchase_date", "valuation_date", "transaction_date", "snapshot_date", "valid_from",
         "valid_to", "target_date", "maturity_date", "as_of_date", "profile_as_of_date", "effective_from", "effective_to",
         "interaction_date", "month_end"}
BOOLS = {"in_investable_portfolio", "is_external_flow"}


def _norm(col: str, v: Any) -> Any:
    if v is None:
        return None
    if col in MONEY:
        return Decimal(str(v)).quantize(Decimal("0.01"))
    if col in UNIT:
        return Decimal(str(v)).quantize(Decimal("0.0001"))
    if col in RATE:
        return Decimal(str(v)).quantize(Decimal("0.000001"))
    if col in DATES:
        if isinstance(v, datetime):
            return v.date()
        return v if isinstance(v, date) else date.fromisoformat(str(v))
    if col in BOOLS:
        return bool(v)
    return v


class Database:
    """Thin read-only wrapper. rows are dicts with normalised Decimal / date / bool values."""

    def __init__(self, url: str | None = None) -> None:
        self.url = url or os.environ.get("CMF_DATABASE_URL") or DEFAULT_URL
        if self.url.startswith("sqlite:///"):
            self.kind = "sqlite"
            p = Path(self.url[len("sqlite:///"):])
            p = p if p.is_absolute() else ROOT / p
            if not p.exists():
                raise FileNotFoundError(f"{p} not found; create it with: python src/opdata/load_db.py --sqlite {p}")
            self._conn = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
            self._conn.execute("PRAGMA query_only = ON")
            self._schema = ""
        elif self.url.startswith(("postgresql://", "postgres://")):
            self.kind = "postgresql"
            import psycopg  # only needed for this backend
            self._conn = psycopg.connect(self.url, autocommit=True, options="-c default_transaction_read_only=on")
            self._schema = "cmf_ops."
        else:
            raise ValueError(f"unsupported CMF_DATABASE_URL: {self.url!r}")

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict]:
        sql = sql.replace("{S}", self._schema)
        if self.kind == "postgresql":
            sql = sql.replace("?", "%s")
        cur = self._conn.cursor()
        cur.execute(sql, tuple(p.isoformat() if self.kind == "sqlite" and isinstance(p, date) else p for p in params))
        cols = [d[0] for d in cur.description]
        return [{c: _norm(c, v) for c, v in zip(cols, row)} for row in cur.fetchall()]

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "Database":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
