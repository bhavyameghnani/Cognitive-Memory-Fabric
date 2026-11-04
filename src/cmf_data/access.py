"""Data-access layer for the operational financial data (schema cmf_ops).

Retrieval only: no financial calculations, no agents, no MCP. Future tools import these functions and never see SQL,
file paths or database drivers. Money values are Decimal; dates are datetime.date; rows are plain dicts.
Table and column names of cmf_ops are the frozen contract (docs/synthetic_operational_data.md).
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from .backend import Database

CLIENT_RE = "CLIENT-"


class OperationalData:
    def __init__(self, db: Optional[Database] = None, url: Optional[str] = None) -> None:
        self.db = db or Database(url)

    # ---- clients
    def list_clients(self) -> list[str]:
        return [r["client_id"] for r in self.db.query("SELECT client_id FROM {S}client_financial_profiles ORDER BY client_id")]

    def get_profile(self, client_id: str) -> dict:
        rows = self.db.query("SELECT * FROM {S}client_financial_profiles WHERE client_id = ?", [client_id])
        if not rows:
            raise KeyError(f"unknown client {client_id}")
        return rows[0]

    def get_monthly_expenses(self, client_id: str, as_of: date) -> Optional[dict]:
        rows = self.db.query(
            "SELECT * FROM {S}client_expense_history WHERE client_id = ? AND effective_from <= ? "
            "AND (effective_to IS NULL OR effective_to >= ?) ORDER BY effective_from DESC", [client_id, as_of, as_of])
        return rows[0] if rows else None

    # ---- accounts and holdings
    def get_accounts(self, client_id: str, investable_only: bool = False) -> list[dict]:
        sql = "SELECT * FROM {S}portfolio_accounts WHERE client_id = ?" + (" AND in_investable_portfolio" if investable_only else "") + " ORDER BY account_id"
        return self.db.query(sql, [client_id])

    def get_current_holdings(self, client_id: str, investable_only: bool = True, include_closed: bool = False) -> list[dict]:
        """Latest valuation (valuation_date) of each position, with its account flags."""
        sql = ("SELECT h.*, a.account_type, a.account_name, a.in_investable_portfolio, a.access_restriction "
               "FROM {S}portfolio_holdings h JOIN {S}portfolio_accounts a ON a.account_id = h.account_id WHERE h.client_id = ?")
        if not include_closed:
            sql += " AND h.holding_status = 'active'"
        if investable_only:
            sql += " AND a.in_investable_portfolio"
        return self.db.query(sql + " ORDER BY h.holding_id", [client_id])

    # ---- history
    def get_snapshot_dates(self, client_id: str) -> list[date]:
        rows = self.db.query("SELECT DISTINCT snapshot_date FROM {S}portfolio_snapshots WHERE client_id = ? ORDER BY snapshot_date", [client_id])
        return [r["snapshot_date"] for r in rows]

    def get_snapshot(self, client_id: str, snapshot_date: date, investable_only: bool = True) -> list[dict]:
        """Positions on one snapshot date, joined to holding attributes (asset_class, issuer, sector, liquidity...)."""
        sql = ("SELECT s.snapshot_id, s.snapshot_date, s.holding_id, s.instrument_id, s.account_id, s.quantity, "
               "s.market_price, s.market_value, h.instrument_name, h.instrument_type, h.asset_class, h.sector, h.issuer, "
               "h.liquidity_category, a.in_investable_portfolio "
               "FROM {S}portfolio_snapshots s JOIN {S}portfolio_holdings h ON h.holding_id = s.holding_id "
               "JOIN {S}portfolio_accounts a ON a.account_id = s.account_id WHERE s.client_id = ? AND s.snapshot_date = ?")
        if investable_only:
            sql += " AND a.in_investable_portfolio"
        return self.db.query(sql + " ORDER BY s.holding_id", [client_id, snapshot_date])

    def get_transactions(self, client_id: str, start: Optional[date] = None, end: Optional[date] = None) -> list[dict]:
        sql, p = "SELECT * FROM {S}portfolio_transactions WHERE client_id = ?", [client_id]
        if start:
            sql += " AND transaction_date >= ?"; p.append(start)
        if end:
            sql += " AND transaction_date <= ?"; p.append(end)
        return self.db.query(sql + " ORDER BY transaction_date, transaction_id", p)

    # ---- goals, liabilities
    def get_goals(self, client_id: str, as_of: Optional[date] = None, include_history: bool = False) -> list[dict]:
        """Goal versions in effect on `as_of` (default: currently open versions). include_history returns every version."""
        sql, p = "SELECT * FROM {S}financial_goals WHERE client_id = ?", [client_id]
        if not include_history:
            if as_of is None:
                sql += " AND valid_to IS NULL"
            else:
                sql += " AND valid_from <= ? AND (valid_to IS NULL OR valid_to > ?)"; p += [as_of, as_of]
        return self.db.query(sql + " ORDER BY goal_key, valid_from", p)

    def get_liabilities(self, client_id: str) -> list[dict]:
        return self.db.query("SELECT * FROM {S}liabilities WHERE client_id = ? ORDER BY liability_id", [client_id])

    # ---- provenance
    def get_evidence(self, entity_type: str, entity_id: str) -> list[dict]:
        return self.db.query("SELECT * FROM {S}data_evidence_links WHERE entity_type = ? AND entity_id = ? ORDER BY evidence_id", [entity_type, entity_id])

    def close(self) -> None:
        self.db.close()
