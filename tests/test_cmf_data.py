"""Run: python -m unittest discover -s tests -v   (from repo root). Uses the SQLite copy unless CMF_DATABASE_URL says otherwise.
Compares every access function with the source CSVs, so the same suite proves a PostgreSQL load equivalent."""
import csv
import sqlite3
import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from cmf_data import OperationalData  # noqa: E402

CSV = ROOT / "data" / "operational"


def rows(name):
    with open(CSV / f"{name}.csv", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


class AccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.od = OperationalData()

    @classmethod
    def tearDownClass(cls):
        cls.od.close()

    def test_clients(self):
        self.assertEqual(self.od.list_clients(), [f"CLIENT-00{i}" for i in range(1, 7)])
        with self.assertRaises(KeyError):
            self.od.get_profile("CLIENT-999")

    def test_profile_types_and_values(self):
        src = {r["client_id"]: r for r in rows("client_financial_profiles")}
        for c in self.od.list_clients():
            p = self.od.get_profile(c)
            self.assertIsInstance(p["monthly_expenses"], Decimal)
            self.assertIsInstance(p["profile_as_of_date"], date)
            self.assertEqual(p["monthly_expenses"], Decimal(src[c]["monthly_expenses"]))

    def test_current_holdings_match_csv(self):
        src = rows("portfolio_holdings")
        for c in self.od.list_clients():
            inv = {a["account_id"] for a in rows("portfolio_accounts") if a["client_id"] == c and a["in_investable_portfolio"] == "true"}
            want = sum(Decimal(r["market_value"]) for r in src if r["client_id"] == c and r["holding_status"] == "active" and r["account_id"] in inv)
            got = sum(h["market_value"] for h in self.od.get_current_holdings(c))
            self.assertEqual(got, want, c)
            self.assertTrue(all(h["in_investable_portfolio"] is True for h in self.od.get_current_holdings(c)))
        self.assertGreater(len(self.od.get_current_holdings("CLIENT-004", include_closed=True)), len(self.od.get_current_holdings("CLIENT-004")))

    def test_snapshots_match_csv_and_current(self):
        src = rows("portfolio_snapshots")
        for c in self.od.list_clients():
            dates = self.od.get_snapshot_dates(c)
            self.assertEqual(len(dates), 6)
            self.assertEqual(dates, sorted(dates))
            for d in dates:
                want = sum(Decimal(r["market_value"]) for r in src if r["client_id"] == c and r["snapshot_date"] == d.isoformat())
                got = sum(s["market_value"] for s in self.od.get_snapshot(c, d, investable_only=False))
                self.assertEqual(got, want, (c, d))
            last = sum(s["market_value"] for s in self.od.get_snapshot(c, dates[-1]))
            self.assertEqual(last, sum(h["market_value"] for h in self.od.get_current_holdings(c)))

    def test_transactions_ordering_and_filter(self):
        t = self.od.get_transactions("CLIENT-001")
        self.assertEqual(len(t), sum(r["client_id"] == "CLIENT-001" for r in rows("portfolio_transactions")))
        self.assertEqual([x["transaction_date"] for x in t], sorted(x["transaction_date"] for x in t))
        feb = self.od.get_transactions("CLIENT-001", date(2026, 2, 1), date(2026, 2, 28))
        self.assertTrue(feb and all(date(2026, 2, 1) <= x["transaction_date"] <= date(2026, 2, 28) for x in feb))

    def test_goals_are_point_in_time(self):
        before = {g["goal_id"] for g in self.od.get_goals("CLIENT-001", date(2026, 4, 1))}
        after = {g["goal_id"] for g in self.od.get_goals("CLIENT-001", date(2026, 5, 2))}
        self.assertIn("GOL-001-01a", before); self.assertNotIn("GOL-001-01b", before)
        self.assertIn("GOL-001-01b", after); self.assertNotIn("GOL-001-01a", after)
        self.assertEqual(len(self.od.get_goals("CLIENT-001", include_history=True)), sum(r["client_id"] == "CLIENT-001" for r in rows("financial_goals")))
        self.assertTrue(all(g["valid_to"] is None for g in self.od.get_goals("CLIENT-001")))

    def test_expenses_change_over_time(self):
        self.assertEqual(self.od.get_monthly_expenses("CLIENT-006", date(2026, 3, 1))["monthly_expenses"], Decimal("8900.00"))
        self.assertEqual(self.od.get_monthly_expenses("CLIENT-006", date(2026, 6, 30))["monthly_expenses"], Decimal("10100.00"))

    def test_liabilities_and_evidence(self):
        self.assertEqual(len(self.od.get_liabilities("CLIENT-005")), 0)
        self.assertEqual(self.od.get_liabilities("CLIENT-004")[0]["interest_rate"], Decimal("0.031000"))
        ev = self.od.get_evidence("liability", "LIA-004-01")
        self.assertTrue(ev and all(e["interaction_id"].startswith("INT-") for e in ev))

    def test_read_only(self):
        if self.od.db.kind != "sqlite":
            self.skipTest("read-only enforced by the server session on PostgreSQL")
        with self.assertRaises(sqlite3.OperationalError):
            self.od.db._conn.execute("DELETE FROM portfolio_holdings")

    def test_no_driver_imports_outside_backend(self):
        for p in (ROOT / "src" / "cmf_data").glob("*.py"):
            if p.name != "backend.py":
                txt = p.read_text(encoding="utf-8")
                self.assertNotIn("import sqlite3", txt); self.assertNotIn("import psycopg", txt)


if __name__ == "__main__":
    unittest.main()
