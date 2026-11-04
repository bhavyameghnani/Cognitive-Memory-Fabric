"""CLI: generate the synthetic operational data layer as CSV files (deterministic, stdlib only).

    python -m opdata.generate            (run from ./src)      or      python src/opdata/generate.py
Never writes to data/conversations.csv; refuses to run if its checksum changed.
"""
from __future__ import annotations

import csv
import hashlib
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

if __package__ in (None, ""):  # allow `python src/opdata/generate.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "opdata"

from opdata.build import build_all
from opdata.config import CONVERSATIONS, CONVERSATIONS_MD5, OUT_DIR, ConvIndex, md5_of

COLUMNS = {
    "ref_asset_classes": ["asset_class", "description"],
    "ref_liquidity_categories": ["liquidity_category", "description", "liquidity_rank"],
    "ref_instruments": ["instrument_id", "instrument_name", "instrument_type", "asset_class", "sector", "issuer", "currency",
                        "liquidity_category", "data_provenance", "provenance_note"],
    "ref_market_path": ["month_end", "equity_return", "bond_return", "note"],
    "client_financial_profiles": ["client_id", "age", "employment_status", "occupation", "annual_income", "monthly_income",
                                  "monthly_expenses", "dependants", "retirement_target_age", "investment_horizon",
                                  "investment_horizon_years", "risk_category", "liquidity_requirements", "liquidity_target_months",
                                  "currency", "profile_as_of_date", "data_provenance", "synthetic_fields"],
    "client_expense_history": ["expense_period_id", "client_id", "effective_from", "effective_to", "monthly_expenses", "currency",
                               "data_provenance", "provenance_note"],
    "portfolio_accounts": ["account_id", "client_id", "account_type", "account_name", "currency", "account_status", "opened_date",
                           "closed_date", "in_investable_portfolio", "access_restriction", "data_provenance", "provenance_note"],
    "portfolio_holdings": ["holding_id", "account_id", "client_id", "instrument_id", "instrument_name", "instrument_type",
                           "asset_class", "sector", "issuer", "quantity", "purchase_price", "purchase_date", "current_price",
                           "market_value", "currency", "valuation_date", "liquidity_category", "holding_status", "closed_date",
                           "data_provenance", "provenance_note"],
    "portfolio_transactions": ["transaction_id", "client_id", "account_id", "holding_id", "instrument_id", "transaction_date",
                               "transaction_type", "quantity", "price", "amount", "currency", "is_external_flow", "group_id",
                               "description", "data_provenance", "provenance_note"],
    "portfolio_snapshots": ["snapshot_id", "client_id", "snapshot_date", "holding_id", "instrument_id", "account_id", "quantity",
                            "market_price", "market_value", "currency", "data_provenance"],
    "financial_goals": ["goal_id", "goal_key", "client_id", "goal_type", "goal_description", "target_amount", "target_date",
                        "priority", "confidence_level", "current_status", "currency", "amount_provenance", "data_provenance",
                        "provenance_note", "valid_from", "valid_to"],
    "liabilities": ["liability_id", "client_id", "liability_type", "description", "outstanding_amount", "interest_rate",
                    "monthly_payment", "maturity_date", "as_of_date", "currency", "data_provenance", "provenance_note"],
    "data_evidence_links": ["evidence_id", "client_id", "entity_type", "entity_id", "field_name", "source_file", "interaction_id",
                            "turn_index", "interaction_date", "speaker", "evidence_kind", "excerpt", "note"],
}
# load order respects foreign keys
LOAD_ORDER = list(COLUMNS)


def fmt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, Decimal):
        return format(v, "f")
    if isinstance(v, float):
        return repr(round(v, 6))
    if isinstance(v, date):
        return v.isoformat()
    return str(v)


def write_tables(tables: dict, out_dir: Path) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    sums = {}
    for name, cols in COLUMNS.items():
        path = out_dir / f"{name}.csv"
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(cols)
            for r in tables[name]:
                w.writerow([fmt(r.get(c)) for c in cols])
        sums[name] = hashlib.md5(path.read_bytes()).hexdigest()
    return sums


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    out = Path(argv[argv.index("--out") + 1]) if "--out" in argv else OUT_DIR
    before = md5_of(CONVERSATIONS)
    if before != CONVERSATIONS_MD5:
        print(f"ERROR: data/conversations.csv checksum {before} != frozen {CONVERSATIONS_MD5}", file=sys.stderr)
        return 2
    conv = ConvIndex()
    tables = build_all(conv)
    sums = write_tables(tables, out)
    assert md5_of(CONVERSATIONS) == before, "conversations.csv changed?!"
    for w in tables["_warnings"]:
        print("WARN", w)
    print("wrote", len(sums), "tables to", out)
    for n in COLUMNS:
        print(f"  {n:28s} {len(tables[n]):5d} rows  md5={sums[n]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
