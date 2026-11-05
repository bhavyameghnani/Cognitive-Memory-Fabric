"""MCP server tests: discovery, stdio transport, input errors, allow-list, no write/SQL tool, and calculations against hand-computed values."""
import asyncio
import json
import os
import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents.tool_selector.toolbox import ToolCallError, Toolbox  # noqa: E402
from cmf_data.access import OperationalData  # noqa: E402
from mcp_server.tools import TOOL_TABLE  # noqa: E402
from schemas.tools import TOOL_NAMES  # noqa: E402

K, R = "CLIENT-001", "CLIENT-002"  # Kenji, Rebecca


def _run(name, args):
    async def go():
        async with Toolbox() as tb:
            try:
                return True, await tb.call(name, args)
            except ToolCallError as e:  # caught inside the client context (an escaping error is wrapped in an ExceptionGroup)
                return False, str(e)
    return asyncio.run(go())


def call(name, **args):
    ok, res = _run(name, args)
    if not ok:
        raise AssertionError(f"{name} failed: {res}")
    return res


def call_err(name, **args):
    ok, res = _run(name, args)
    if ok:
        raise AssertionError("expected a tool error")
    return res


class Discovery(unittest.TestCase):
    def test_lists_exactly_registered_tools_with_schemas(self):
        async def go():
            async with Toolbox() as tb:
                return await tb.list_tools()
        tools = asyncio.run(go())
        self.assertEqual(sorted(t["name"] for t in tools), sorted(TOOL_NAMES))
        self.assertEqual(sorted(n for n, _ in TOOL_TABLE), sorted(TOOL_NAMES))
        for t in tools:
            self.assertEqual(t["input_schema"]["type"], "object")
            self.assertTrue(t["description"])
        by = {t["name"]: t["input_schema"] for t in tools}
        self.assertEqual(by["calculate_asset_allocation"]["properties"]["group_by"]["enum"], ["asset_class", "liquidity_category", "sector", "account_type"])
        self.assertEqual(by["calculate_portfolio_concentration"]["properties"]["dimension"]["enum"], ["instrument", "issuer", "sector", "asset_class"])
        self.assertIn("client_id", by["get_client_profile"]["required"])

    def test_no_write_or_sql_tool(self):
        bad = {"sql", "query", "execute", "write", "insert", "update", "delete", "create", "drop", "set", "save", "pending", "metrics", "rebalancing", "search"}
        for n in TOOL_NAMES:
            self.assertTrue(n.split("_")[0] in ("get", "calculate", "compare"), n)
            self.assertFalse(bad & set(n.split("_")), n)
        for _, fn in TOOL_TABLE:
            self.assertFalse(any(a in ("sql", "query", "statement") for a in fn.__annotations__))

    def test_stdio_transport(self):
        async def go():
            async with Toolbox("stdio") as tb:
                tools = await tb.list_tools()
                res = await tb.call("calculate_asset_allocation", {"client_id": K})
                return tools, res
        tools, res = asyncio.run(go())
        self.assertEqual(len(tools), 13)
        self.assertEqual(res["data"]["total"], 3541489.73)

    def test_stdio_allow_list_enforced_by_server(self):
        async def go():
            async with Toolbox("stdio", env={"CMF_ALLOWED_CLIENTS": K}) as tb:
                ok = await tb.call("get_client_profile", {"client_id": K})
                try:
                    await tb.call("get_client_profile", {"client_id": R})
                except ToolCallError as e:
                    return ok, str(e)
        ok, err = asyncio.run(go())
        self.assertEqual(ok["client_id"], K)
        self.assertIn("not allowed", err)


class InputErrors(unittest.TestCase):
    def test_messages_reach_client(self):
        self.assertIn("not a snapshot date", call_err("get_client_portfolio", client_id=K, as_of_date="2026-07-31"))
        self.assertIn("YYYY-MM-DD", call_err("get_client_portfolio", client_id=K, as_of_date="June 30"))
        self.assertIn("unknown client", call_err("get_client_profile", client_id="CLIENT-099"))
        self.assertIn("invalid client_id", call_err("get_client_profile", client_id="x; DROP TABLE"))
        self.assertIn("group_by", call_err("calculate_asset_allocation", client_id=K, group_by="colour"))
        # the SDK ignores unknown arguments (the Tool Selector validator rejects them before any call); they must have no effect
        self.assertEqual(self._unknown_arg(), call("get_client_profile", client_id=K))
        self.assertIn("no goal of type", call_err("calculate_goal_funding_gap", client_id=K, goal_type="Yacht"))
        self.assertIn("earlier", call_err("compare_portfolio_snapshots", client_id=K, date_a="2026-06-30", date_b="2026-05-31"))

    @staticmethod
    def _unknown_arg():
        return call("get_client_profile", client_id=K, sql="select 1")

    def test_allow_list_in_process(self):
        with mock.patch.dict(os.environ, {"CMF_ALLOWED_CLIENTS": f"{K}"}):
            self.assertEqual(call("get_client_profile", client_id=K)["client_id"], K)
            self.assertIn("not allowed", call_err("get_client_portfolio", client_id=R))
        with mock.patch.dict(os.environ, {"CMF_ALLOWED_CLIENTS": f"{K}, {R}"}):
            self.assertEqual(call("get_client_profile", client_id=R)["client_id"], R)

    def test_envelope_shape_and_synthetic_provenance(self):
        for name, args in [("get_client_profile", {"client_id": K}), ("get_client_goals", {"client_id": K}), ("calculate_liquidity_ratio", {"client_id": K}),
                           ("get_market_data", {})]:
            e = call(name, **args)
            self.assertEqual(e["tool"], name)
            self.assertTrue(e["provenance"]["synthetic"])
            self.assertIn("synthetic", e["provenance"]["note"].lower())
            self.assertTrue(e["limitations"] or name == "get_client_goals")
            self.assertEqual({"tool", "client_id", "as_of", "data", "provenance", "limitations"}, set(e))


class Calculations(unittest.TestCase):
    """Expected values were computed by hand from the smoke database (see comments)."""

    def test_allocation_sums_to_100_and_matches(self):
        e = call("calculate_asset_allocation", client_id=K)["data"]
        self.assertEqual(e["total"], 3541489.73)  # sum of the 13 investable holding values at 2026-06-30
        got = {g["group"]: g["percent"] for g in e["allocation"]}
        self.assertEqual(got, {"Equity": 62.18, "Fixed Income": 33.42, "Cash & Equivalents": 4.40})  # 2,201,928.90 / 1,183,732.70 / 155,828.13 of 3,541,489.73
        for by in ("asset_class", "liquidity_category", "sector", "account_type"):
            for c in (K, R):
                a = call("calculate_asset_allocation", client_id=c, group_by=by)["data"]["allocation"]
                self.assertEqual(round(sum(Decimal(str(g["percent"])) for g in a), 2), Decimal("100.00"), (by, c))
        self.assertAlmostEqual(sum(g["value"] for g in e["allocation"]), 3541489.73, places=2)  # totals reconcile to the sum of rounded holdings

    def test_snapshot_allocation_uses_snapshot_values(self):
        e = call("calculate_asset_allocation", client_id=K, as_of_date="2026-01-31")["data"]
        self.assertEqual(e["total"], 3429999.99)

    def test_liquidity_ratio(self):
        d = call("calculate_liquidity_ratio", client_id=K)["data"]
        self.assertEqual(d["monthly_expenses"], 11800.0)
        self.assertEqual(d["cash_equivalents"], 155828.13)
        self.assertEqual(d["cash_months"], 13.21)  # 155,828.13 / 11,800 = 13.2057
        self.assertEqual(d["target_months"], 15)
        self.assertEqual(d["shortfall_months"], 1.79)  # 15 - 13.2057 = 1.7943
        self.assertEqual(d["shortfall_amount"], 21171.87)  # 1.79430 * 11,800 = 15 * 11,800 - 155,828.13 = 21,171.87
        self.assertEqual(d["cash_plus_daily_liquid"], 3356211.84)

    def test_goal_funding_gap(self):
        d = call("calculate_goal_funding_gap", client_id=K, goal_id="GOL-001-01b")["data"]
        g = d["goals"][0]
        self.assertEqual((g["target"], g["resources"], g["simple_gap"]), (4500000.0, 3541489.73, 958510.27))  # 4,500,000 - 3,541,489.73
        self.assertEqual(g["coverage_percent"], 78.70)  # 3,541,489.73 / 4,500,000 = 78.70%
        cash = call("calculate_goal_funding_gap", client_id=K, goal_id="GOL-001-01b", resources_basis="cash_equivalents")["data"]["goals"][0]
        self.assertEqual(cash["simple_gap"], 4500000.0 - 155828.13)
        by_type = call("calculate_goal_funding_gap", client_id=K, goal_type="retirement")["data"]["goals"]
        self.assertIn("GOL-001-01b", [x["goal_id"] for x in by_type])
        lim = " ".join(call("calculate_goal_funding_gap", client_id=K)["limitations"])
        self.assertIn("NOT a projection", lim)

    def test_snapshot_comparison(self):
        d = call("compare_portfolio_snapshots", client_id=K, date_a="2026-05-31", date_b="2026-06-30")["data"]
        self.assertEqual((d["total_a"], d["total_b"]), (3506777.02, 3541489.73))
        self.assertEqual(d["total_change"], 34712.71)  # 3,541,489.73 - 3,506,777.02
        c = next(x for x in d["changed"] if x["holding_id"] == "HLD-001-01")
        self.assertEqual(c["value_change"], 13441.57)  # 815,286.49 - 801,844.92
        self.assertEqual(round(sum(a["change_points"] for a in d["allocation_change"]), 2), 0.0)
        wide = call("compare_portfolio_snapshots", client_id=K, date_a="2026-01-31", date_b="2026-06-30")["data"]
        self.assertTrue(all("2026-01-31" < t["date"] <= "2026-06-30" for t in wide["transactions_between"]))

    def test_returns_modified_dietz(self):
        d = call("calculate_portfolio_returns", client_id=K, start_date="2026-01-31", end_date="2026-06-30")["data"]
        self.assertEqual((d["beginning_value"], d["ending_value"]), (3429999.99, 3541489.73))
        self.assertEqual(d["simple_change"], 111489.74)
        self.assertEqual(d["net_external_flows"], 46964.2)
        self.assertEqual(d["flow_adjusted_change"], 64525.54)  # 111,489.74 - 46,964.20
        # independent Modified Dietz from the dated flows
        end, start = date(2026, 6, 30), date(2026, 1, 31)
        days = Decimal((end - start).days)
        w = sum(Decimal(str(f["amount"])) * Decimal((end - date.fromisoformat(f["date"])).days) / days for f in d["external_flows"])
        expect = (Decimal("3541489.73") - Decimal("3429999.99") - Decimal("46964.20")) / (Decimal("3429999.99") + w) * 100
        self.assertEqual(d["modified_dietz_percent"], float(expect.quantize(Decimal("0.01"))))
        self.assertIn("NOT a time-weighted", " ".join(call("calculate_portfolio_returns", client_id=K, start_date="2026-01-31", end_date="2026-02-28")["limitations"]))

    def test_dietz_without_flows_equals_simple_return(self):
        od = OperationalData()
        tx = [t for t in od.get_transactions(K, date(2026, 5, 31), date(2026, 6, 30)) if t["is_external_flow"] and t["transaction_date"] > date(2026, 5, 31)]
        d = call("calculate_portfolio_returns", client_id=K, start_date="2026-05-31", end_date="2026-06-30")["data"]
        if not tx:
            self.assertEqual(d["modified_dietz_percent"], round(d["simple_change"] / d["beginning_value"] * 100, 2))
        else:
            self.assertEqual(len(d["external_flows"]), len(tx))

    def test_concentration(self):
        d = call("calculate_portfolio_concentration", client_id=R, dimension="issuer")["data"]
        self.assertEqual(d["top"][0], {"group": "Meridian Asset Management", "value": 1015604.4, "percent": 48.22})
        pos = call("get_client_portfolio", client_id=R)["data"]["positions"]
        total = sum(Decimal(str(p["market_value"])) for p in pos)
        by = {}
        for p in pos:
            by[p["issuer"]] = by.get(p["issuer"], Decimal(0)) + Decimal(str(p["market_value"]))
        hhi = sum((v / total) ** 2 for v in by.values())
        self.assertEqual(d["herfindahl_index"], float(hhi.quantize(Decimal("0.0001"))))
        self.assertEqual(d["top_n_percent"], round(sum(g["percent"] for g in d["top"]), 2))
        one = call("calculate_portfolio_concentration", client_id=R, dimension="asset_class", top_n=1)["data"]
        self.assertEqual(len(one["top"]), 1)

    def test_portfolio_excludes_non_investable_by_default(self):
        inv = call("get_client_portfolio", client_id=K)["data"]
        allp = call("get_client_portfolio", client_id=K, investable_only=False)["data"]
        self.assertLess(inv["total_value"], allp["total_value"])
        self.assertTrue(all(p["in_investable_portfolio"] for p in inv["positions"]))

    def test_history_profile_goals_liabilities_market_evidence(self):
        h = call("get_portfolio_history", client_id=K)["data"]["snapshots"]
        self.assertEqual([s["date"] for s in h], ["2026-01-31", "2026-02-28", "2026-03-31", "2026-04-30", "2026-05-31", "2026-06-30"])
        self.assertEqual(h[0]["total"], 3429999.99)
        self.assertEqual(round(sum(h[3]["allocation_pct"].values()), 2), 100.0)
        p = call("get_client_profile", client_id=K)["data"]
        self.assertEqual((p["retirement_target_age"], p["monthly_expenses"], p["liquidity_target_months"]), (63, 11800.0, 15))
        g = call("get_client_goals", client_id=K, include_history=True)["data"]["goals"]
        self.assertTrue({"GOL-001-01a", "GOL-001-01b"} <= {x["goal_id"] for x in g})  # 65 -> 63 versions
        old = call("get_client_goals", client_id=K, as_of_date="2026-02-28")["data"]["goals"]
        self.assertIn("GOL-001-01a", [x["goal_id"] for x in old])
        self.assertNotIn("GOL-001-01b", [x["goal_id"] for x in old])
        lia = call("get_client_liabilities", client_id=K)["data"]
        self.assertEqual(lia["liabilities"][0]["outstanding"], 395000.0)
        m = call("get_market_data", as_of_date="2026-03-31")["data"]["months"]
        self.assertEqual([x["equity_return"] for x in m], [0.008, -0.052, -0.008])
        ev = call("get_source_evidence", client_id=K, entity_type="goal", entity_id="GOL-001-01b")["data"]["evidence"]
        self.assertTrue(ev and ev[0]["verified_in_conversations"])
        self.assertIn("text", call("get_source_evidence", client_id=K, interaction_id="INT-090", turn_index=11)["data"]["turn"])
        self.assertIn("no turn", call_err("get_source_evidence", client_id=R, interaction_id="INT-090", turn_index=11))

    def test_worker_threads(self):
        async def go():
            async with Toolbox() as tb:
                return await asyncio.gather(*[tb.call("calculate_asset_allocation", {"client_id": c}) for c in (K, R, K, R, K, R)])
        out = asyncio.run(go())
        self.assertEqual(len(out), 6)


if __name__ == "__main__":
    unittest.main()
