"""Deterministic financial services (Decimal arithmetic) over the read-only OperationalData layer.

No SQL here: every read goes through cmf_data.access.OperationalData. SQLite connections are not shareable across threads
and MCP v2 runs sync tools in worker threads, so each thread lazily opens its own read-only OperationalData.
Every public method returns a plain dict with keys tool, client_id, as_of, data, provenance, limitations (the Envelope shape).
"""
from __future__ import annotations

import threading
from collections import defaultdict
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Callable, Optional

from cmf_data.access import OperationalData
from schemas.tools import Envelope, Provenance

from .session import check_client

CENT = Decimal("0.01")
SYNTH = "All operational values are synthetic."
PORTFOLIO_LIMITS = ["Excludes deferred compensation and private interests (non-investable accounts) by default.",
                    "Units and prices are synthetic."]
NO_LOOKTHROUGH = "No look-through into funds: a fund is treated as a single position."
GROUP_FIELDS = {"asset_class": "asset_class", "liquidity_category": "liquidity", "sector": "sector", "account_type": "account_type"}
DIM_FIELDS = {"instrument": "instrument", "issuer": "issuer", "sector": "sector", "asset_class": "asset_class"}


class ServiceError(ValueError):
    """Input or data problem the caller can fix; the tool layer turns it into ToolError."""


def q2(d: Decimal) -> Decimal:
    return d.quantize(CENT, rounding=ROUND_HALF_UP)


def num(d: Any, places: int = 2) -> Optional[float]:
    if d is None:
        return None
    return float(Decimal(d).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP))


def parse_date(value: Optional[str], name: str = "as_of_date") -> Optional[date]:
    if value in (None, ""):
        return None
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ServiceError(f"{name} must be a date in YYYY-MM-DD format, got {value!r}")


def pcts(values: list[Decimal]) -> list[Decimal]:
    """Percentages with 2 decimals that sum to exactly 100.00 (largest remainder)."""
    total = sum(values, Decimal(0))
    if total <= 0:
        return [Decimal("0.00") for _ in values]
    raw = [v * 100 / total for v in values]
    floor = [r.quantize(CENT, rounding="ROUND_DOWN") for r in raw]
    short = int(((Decimal(100) - sum(floor, Decimal(0))) / CENT).to_integral_value())
    order = sorted(range(len(raw)), key=lambda i: raw[i] - floor[i], reverse=True)
    for i in order[:max(short, 0)]:
        floor[i] += CENT
    return floor


class FinancialServices:
    def __init__(self, data_factory: Optional[Callable[[], OperationalData]] = None) -> None:
        self._factory = data_factory or OperationalData
        self._local = threading.local()

    @property
    def od(self) -> OperationalData:
        if getattr(self._local, "od", None) is None:
            self._local.od = self._factory()
        return self._local.od

    # ------------------------------------------------------------------ helpers
    def _env(self, tool: str, client_id: Optional[str], as_of: Optional[str], data: dict, source: str, limitations: list[str],
             rows: Optional[list[str]] = None) -> dict:
        return Envelope(tool=tool, client_id=client_id, as_of=as_of, data=data, provenance=Provenance(source=source, row_provenance=sorted(set(rows or []))),
                        limitations=limitations).model_dump(mode="json")

    def _client(self, client_id: str) -> str:
        check_client(client_id)
        if client_id not in self.od.list_clients():
            raise ServiceError(f"unknown client {client_id}")
        return client_id

    def _snapshot_date(self, client_id: str, value: Optional[str], name: str = "as_of_date") -> Optional[date]:
        d = parse_date(value, name)
        if d is not None:
            dates = self.od.get_snapshot_dates(client_id)
            if d not in dates:
                raise ServiceError(f"{name} {d} is not a snapshot date; use one of: {', '.join(x.isoformat() for x in dates)}")
        return d

    def _positions(self, client_id: str, as_of: Optional[date], investable_only: bool = True) -> tuple[list[dict], date]:
        """Uniform position dicts at a snapshot date, or the current valuation when as_of is None."""
        acc = {a["account_id"]: a for a in self.od.get_accounts(client_id)}
        if as_of is None:
            rows = self.od.get_current_holdings(client_id, investable_only=investable_only)
            when = max((r["valuation_date"] for r in rows), default=date(2026, 6, 30))
            return [self._pos(r, acc, r["current_price"]) for r in rows], when
        rows = self.od.get_snapshot(client_id, as_of, investable_only=investable_only)
        return [self._pos(r, acc, r["market_price"]) for r in rows], as_of

    @staticmethod
    def _pos(r: dict, acc: dict, price: Decimal) -> dict:
        return {"holding_id": r["holding_id"], "instrument_id": r["instrument_id"], "instrument": r["instrument_name"], "asset_class": r["asset_class"],
                "sector": r["sector"], "issuer": r["issuer"], "liquidity": r["liquidity_category"], "quantity": r["quantity"], "price": price,
                "value": r["market_value"], "account_id": r["account_id"], "account_type": acc.get(r["account_id"], {}).get("account_type", "unknown"),
                "in_investable": bool(r["in_investable_portfolio"])}

    @staticmethod
    def _total(pos: list[dict]) -> Decimal:
        return sum((p["value"] for p in pos), Decimal(0))

    def _group(self, pos: list[dict], field: str) -> list[dict]:
        sums: dict[str, Decimal] = defaultdict(Decimal)
        for p in pos:
            sums[p[field]] += p["value"]
        keys = sorted(sums, key=lambda k: (-sums[k], k))
        ps = pcts([sums[k] for k in keys])
        return [{"group": k, "value": num(sums[k]), "percent": num(p)} for k, p in zip(keys, ps)]

    # ------------------------------------------------------------------ client data
    def get_client_profile(self, client_id: str) -> dict:
        self._client(client_id)
        p = self.od.get_profile(client_id)
        exp = self.od.get_monthly_expenses(client_id, p["profile_as_of_date"])
        data = {"age": p["age"], "employment_status": p["employment_status"], "occupation": p["occupation"], "annual_income": num(p["annual_income"]),
                "monthly_income": num(p["monthly_income"]), "monthly_expenses": num(p["monthly_expenses"]), "dependants": p["dependants"],
                "retirement_target_age": p["retirement_target_age"], "investment_horizon_years": p["investment_horizon_years"],
                "risk_category": p["risk_category"], "liquidity_target_months": p["liquidity_target_months"],
                "liquidity_requirements": p["liquidity_requirements"], "currency": p["currency"], "synthetic_fields": p["synthetic_fields"]}
        return self._env("get_client_profile", client_id, str(p["profile_as_of_date"]), data, "client_financial_profiles",
                         ["Partly synthetic: see synthetic_fields.", "The risk category is a label, not a risk assessment."],
                         [f"{client_id}:profile:{p['data_provenance']}"] + ([exp["expense_period_id"] + ":" + exp["data_provenance"]] if exp else []))

    # ------------------------------------------------------------------ portfolio
    def get_client_portfolio(self, client_id: str, as_of_date: Optional[str] = None, investable_only: bool = True) -> dict:
        self._client(client_id)
        d = self._snapshot_date(client_id, as_of_date)
        pos, when = self._positions(client_id, d, investable_only)
        total = self._total(pos)
        ps = pcts([p["value"] for p in pos]) if pos else []
        out = [{"holding_id": p["holding_id"], "instrument": p["instrument"], "asset_class": p["asset_class"], "sector": p["sector"], "issuer": p["issuer"],
                "account_type": p["account_type"], "quantity": num(p["quantity"], 4), "price": num(p["price"], 4), "market_value": num(p["value"]),
                "percent_of_total": num(pc), "liquidity_category": p["liquidity"], "in_investable_portfolio": p["in_investable"]}
               for p, pc in zip(pos, ps)]
        out.sort(key=lambda x: -x["market_value"])
        lim = list(PORTFOLIO_LIMITS) if investable_only else ["Includes non-investable accounts (deferred compensation, private interests); valuations are synthetic."]
        return self._env("get_client_portfolio", client_id, str(when), {"positions": out, "total_value": num(total), "position_count": len(out),
                                                                          "investable_only": investable_only},
                         "portfolio_snapshots" if d else "portfolio_holdings", lim, [p["holding_id"] for p in pos])

    def get_portfolio_history(self, client_id: str) -> dict:
        self._client(client_id)
        snaps = []
        for d in self.od.get_snapshot_dates(client_id):
            pos, _ = self._positions(client_id, d)
            snaps.append({"date": d.isoformat(), "total": num(self._total(pos)), "positions": len(pos),
                          "allocation_pct": {g["group"]: g["percent"] for g in self._group(pos, "asset_class")}})
        return self._env("get_portfolio_history", client_id, snaps[-1]["date"] if snaps else None, {"snapshots": snaps}, "portfolio_snapshots",
                         ["Only six month-end snapshots (2026-01-31 to 2026-06-30).", "Investable accounts only; values are synthetic."])

    # ------------------------------------------------------------------ goals, liabilities
    def get_client_goals(self, client_id: str, as_of_date: Optional[str] = None, include_history: bool = False) -> dict:
        self._client(client_id)
        d = parse_date(as_of_date)
        goals = [self._goal(g) for g in self.od.get_goals(client_id, d, include_history)]
        return self._env("get_client_goals", client_id, as_of_date, {"goals": goals, "include_history": include_history}, "financial_goals",
                         ["Goal amounts may be synthetic: check amount_provenance of each goal."], [g["goal_id"] for g in goals])

    @staticmethod
    def _goal(g: dict) -> dict:
        return {"goal_id": g["goal_id"], "goal_key": g["goal_key"], "type": g["goal_type"], "description": g["goal_description"],
                "target_amount": num(g["target_amount"]), "target_date": g["target_date"].isoformat() if g["target_date"] else None,
                "confidence_level": g["confidence_level"], "status": g["current_status"], "amount_provenance": g["amount_provenance"],
                "valid_from": g["valid_from"].isoformat(), "valid_to": g["valid_to"].isoformat() if g["valid_to"] else None}

    def get_client_liabilities(self, client_id: str) -> dict:
        self._client(client_id)
        rows = self.od.get_liabilities(client_id)
        libs = [{"liability_id": r["liability_id"], "type": r["liability_type"], "description": r["description"], "outstanding": num(r["outstanding_amount"]),
                 "interest_rate": num(r["interest_rate"], 6), "monthly_payment": num(r["monthly_payment"]),
                 "maturity_date": r["maturity_date"].isoformat() if r["maturity_date"] else None, "as_of": r["as_of_date"].isoformat()} for r in rows]
        pay = sum((r["monthly_payment"] or Decimal(0) for r in rows), Decimal(0))
        return self._env("get_client_liabilities", client_id, max((l["as_of"] for l in libs), default=None),
                         {"liabilities": libs, "total_monthly_payments": num(pay)}, "liabilities",
                         ["Balances are amortised forward synthetically.", "Open-ended obligations may have no outstanding balance or maturity."],
                         [r["liability_id"] for r in rows])

    # ------------------------------------------------------------------ calculations
    def calculate_asset_allocation(self, client_id: str, as_of_date: Optional[str] = None, group_by: str = "asset_class") -> dict:
        self._client(client_id)
        if group_by not in GROUP_FIELDS:
            raise ServiceError(f"group_by must be one of {sorted(GROUP_FIELDS)}")
        d = self._snapshot_date(client_id, as_of_date)
        pos, when = self._positions(client_id, d)
        return self._env("calculate_asset_allocation", client_id, str(when),
                         {"group_by": group_by, "allocation": self._group(pos, GROUP_FIELDS[group_by]), "total": num(self._total(pos))},
                         "portfolio_snapshots" if d else "portfolio_holdings", [NO_LOOKTHROUGH, PORTFOLIO_LIMITS[0]], [p["holding_id"] for p in pos])

    def calculate_portfolio_concentration(self, client_id: str, as_of_date: Optional[str] = None, dimension: str = "issuer", top_n: int = 5) -> dict:
        self._client(client_id)
        if dimension not in DIM_FIELDS:
            raise ServiceError(f"dimension must be one of {sorted(DIM_FIELDS)}")
        if not 1 <= top_n <= 50:
            raise ServiceError("top_n must be between 1 and 50")
        d = self._snapshot_date(client_id, as_of_date)
        pos, when = self._positions(client_id, d)
        groups = self._group(pos, DIM_FIELDS[dimension])
        total = self._total(pos)
        sums = {g["group"]: Decimal(str(g["value"])) for g in groups}
        hhi = sum(((v / total) ** 2 for v in sums.values()), Decimal(0)) if total > 0 else Decimal(0)
        top = groups[:top_n]
        top_pct = q2(sum((Decimal(str(g["percent"])) for g in top), Decimal(0)))
        return self._env("calculate_portfolio_concentration", client_id, str(when),
                         {"dimension": dimension, "top": top, "top_n": top_n, "top_n_percent": num(top_pct), "herfindahl_index": num(hhi, 4),
                          "groups": len(groups), "total": num(total)},
                         "portfolio_snapshots" if d else "portfolio_holdings",
                         ["Funds are grouped by fund house (issuer) and are not looked through.", "Herfindahl index is on a 0-1 scale (1 = a single group).", PORTFOLIO_LIMITS[0]],
                         [p["holding_id"] for p in pos])

    def compare_portfolio_snapshots(self, client_id: str, date_a: str, date_b: str) -> dict:
        self._client(client_id)
        a, b = self._snapshot_date(client_id, date_a, "date_a"), self._snapshot_date(client_id, date_b, "date_b")
        if a is None or b is None:
            raise ServiceError("date_a and date_b are required")
        if a >= b:
            raise ServiceError("date_a must be earlier than date_b")
        pa, _ = self._positions(client_id, a)
        pb, _ = self._positions(client_id, b)
        ma, mb = {p["holding_id"]: p for p in pa}, {p["holding_id"]: p for p in pb}
        short = lambda p: {"holding_id": p["holding_id"], "instrument": p["instrument"], "market_value": num(p["value"])}
        added = [short(mb[h]) for h in mb if h not in ma]
        removed = [short(ma[h]) for h in ma if h not in mb]
        changed = []
        for h in ma.keys() & mb.keys():
            x, y = ma[h], mb[h]
            if x["quantity"] != y["quantity"] or x["price"] != y["price"] or x["value"] != y["value"]:
                changed.append({"holding_id": h, "instrument": y["instrument"], "quantity_change": num(y["quantity"] - x["quantity"], 4),
                                "price_change": num(y["price"] - x["price"], 4), "value_change": num(y["value"] - x["value"]),
                                "value_a": num(x["value"]), "value_b": num(y["value"])})
        changed.sort(key=lambda c: -abs(c["value_change"]))
        ga = {g["group"]: Decimal(str(g["percent"])) for g in self._group(pa, "asset_class")}
        gb = {g["group"]: Decimal(str(g["percent"])) for g in self._group(pb, "asset_class")}
        alloc = [{"asset_class": k, "percent_a": num(ga.get(k, Decimal(0))), "percent_b": num(gb.get(k, Decimal(0))),
                  "change_points": num(gb.get(k, Decimal(0)) - ga.get(k, Decimal(0)))} for k in sorted(ga.keys() | gb.keys())]
        inv = {x["account_id"] for x in self.od.get_accounts(client_id, investable_only=True)}
        txs = [t for t in self.od.get_transactions(client_id, a, b) if t["transaction_date"] > a and t["account_id"] in inv]
        tx = [{"transaction_id": t["transaction_id"], "date": t["transaction_date"].isoformat(), "type": t["transaction_type"], "holding_id": t["holding_id"],
               "amount": num(t["amount"]), "external_flow": t["is_external_flow"]} for t in txs]
        return self._env("compare_portfolio_snapshots", client_id, str(b),
                         {"date_a": str(a), "date_b": str(b), "total_a": num(self._total(pa)), "total_b": num(self._total(pb)),
                          "total_change": num(self._total(pb) - self._total(pa)), "added": added, "removed": removed, "changed": changed,
                          "allocation_change": alloc, "transactions_between": tx}, "portfolio_snapshots + portfolio_transactions",
                         ["Month-end resolution: intra-month movements are not visible.", PORTFOLIO_LIMITS[0]], [t["transaction_id"] for t in txs])

    def calculate_portfolio_returns(self, client_id: str, start_date: str, end_date: str) -> dict:
        self._client(client_id)
        a, b = self._snapshot_date(client_id, start_date, "start_date"), self._snapshot_date(client_id, end_date, "end_date")
        if a is None or b is None:
            raise ServiceError("start_date and end_date are required")
        if a >= b:
            raise ServiceError("start_date must be earlier than end_date")
        pa, _ = self._positions(client_id, a)
        pb, _ = self._positions(client_id, b)
        bmv, emv = self._total(pa), self._total(pb)
        inv = {x["account_id"] for x in self.od.get_accounts(client_id, investable_only=True)}
        flows = [t for t in self.od.get_transactions(client_id, a, b) if t["transaction_date"] > a and t["is_external_flow"] and t["account_id"] in inv]
        days = Decimal((b - a).days)
        net = sum((t["amount"] for t in flows), Decimal(0))
        weighted = sum((t["amount"] * Decimal((b - t["transaction_date"]).days) / days for t in flows), Decimal(0))
        denom = bmv + weighted
        dietz = (emv - bmv - net) / denom * 100 if denom > 0 else None
        ma = {p["holding_id"]: p for p in pa}
        hold = [{"holding_id": p["holding_id"], "instrument": p["instrument"],
                 "price_return_percent": num((p["price"] / ma[p["holding_id"]]["price"] - 1) * 100)}
                for p in pb if p["holding_id"] in ma and ma[p["holding_id"]]["price"] > 0]
        hold.sort(key=lambda h: -h["price_return_percent"])
        return self._env("calculate_portfolio_returns", client_id, str(b),
                         {"start_date": str(a), "end_date": str(b), "beginning_value": num(bmv), "ending_value": num(emv), "simple_change": num(emv - bmv),
                          "net_external_flows": num(net), "flow_adjusted_change": num(emv - bmv - net),
                          "modified_dietz_percent": num(dietz) if dietz is not None else None,
                          "external_flows": [{"transaction_id": t["transaction_id"], "date": t["transaction_date"].isoformat(), "type": t["transaction_type"],
                                              "amount": num(t["amount"])} for t in flows],
                          "holding_price_returns": hold},
                         "portfolio_snapshots + portfolio_transactions",
                         ["Modified Dietz is an approximation, NOT a time-weighted return.", "Price-only: no income, fees, taxes or benchmark.",
                          "History starts 2026-01-31; month-end resolution.", PORTFOLIO_LIMITS[0]], [t["transaction_id"] for t in flows])

    def calculate_liquidity_ratio(self, client_id: str, as_of_date: Optional[str] = None) -> dict:
        self._client(client_id)
        d = self._snapshot_date(client_id, as_of_date)
        pos, when = self._positions(client_id, d)
        exp = self.od.get_monthly_expenses(client_id, when)
        if not exp or not exp["monthly_expenses"]:
            raise ServiceError(f"no monthly expenses on record for {client_id} on {when}")
        me = exp["monthly_expenses"]
        cash = sum((p["value"] for p in pos if p["liquidity"] == "cash_equivalent"), Decimal(0))
        daily = sum((p["value"] for p in pos if p["liquidity"] == "liquid_daily"), Decimal(0))
        target = self.od.get_profile(client_id)["liquidity_target_months"]
        cash_m, both_m = cash / me, (cash + daily) / me
        short_m = max(Decimal(target) - cash_m, Decimal(0)) if target is not None else None
        return self._env("calculate_liquidity_ratio", client_id, str(when),
                         {"monthly_expenses": num(me), "cash_equivalents": num(cash), "cash_months": num(cash_m), "liquid_daily": num(daily),
                          "cash_plus_daily_liquid": num(cash + daily), "cash_plus_daily_liquid_months": num(both_m), "target_months": target,
                          "shortfall_months": num(short_m) if short_m is not None else None,
                          "shortfall_amount": num(short_m * me) if short_m is not None else None, "by_liquidity_category": self._group(pos, "liquidity")},
                         "portfolio_holdings/portfolio_snapshots + client_expense_history",
                         ["Known future expenses are not in the data; monthly expenses are the stated run-rate.",
                          "Shortfall is measured against cash equivalents only.", PORTFOLIO_LIMITS[0]],
                         [exp["expense_period_id"]] + [p["holding_id"] for p in pos])

    def calculate_goal_funding_gap(self, client_id: str, goal_id: Optional[str] = None, goal_type: Optional[str] = None, as_of_date: Optional[str] = None,
                                   resources_basis: str = "investable_portfolio") -> dict:
        self._client(client_id)
        if resources_basis not in ("investable_portfolio", "cash_equivalents"):
            raise ServiceError("resources_basis must be 'investable_portfolio' or 'cash_equivalents'")
        d = self._snapshot_date(client_id, as_of_date)
        pos, when = self._positions(client_id, d)
        resources = self._total(pos) if resources_basis == "investable_portfolio" else sum((p["value"] for p in pos if p["liquidity"] == "cash_equivalent"), Decimal(0))
        goals = self.od.get_goals(client_id, d)
        if goal_id:
            goals = [g for g in goals if g["goal_id"] == goal_id]
            if not goals:
                raise ServiceError(f"goal {goal_id} not found among goals valid on {when}; call get_client_goals first")
        if goal_type:
            have = sorted({g["goal_type"] for g in goals})
            goals = [g for g in goals if g["goal_type"].lower() == goal_type.lower()]
            if not goals:
                raise ServiceError(f"no goal of type {goal_type!r} valid on {when}; available types: {', '.join(have) or 'none'}")
        out, skipped = [], []
        for g in goals:
            t = g["target_amount"]
            if t is None or t <= 0:
                skipped.append({"goal_id": g["goal_id"], "type": g["goal_type"], "reason": "no target amount"})
                continue
            out.append({"goal_id": g["goal_id"], "type": g["goal_type"], "status": g["current_status"], "target": num(t), "resources": num(resources),
                        "simple_gap": num(t - resources), "coverage_percent": num(resources / t * 100), "target_date": g["target_date"].isoformat() if g["target_date"] else None,
                        "amount_provenance": g["amount_provenance"]})
        return self._env("calculate_goal_funding_gap", client_id, str(when),
                         {"resources_basis": resources_basis, "resources": num(resources), "goals": out, "skipped": skipped}, "financial_goals + portfolio_snapshots",
                         ["Simple gap = target - current resources. NOT a projection: no growth, inflation, contributions, drawdown or taxes.",
                          "The same resources are compared with each goal; they are not apportioned across goals.",
                          "A negative gap means resources exceed the target.", "Goal amounts may be synthetic (see amount_provenance)."],
                         [g["goal_id"] for g in goals] + [p["holding_id"] for p in pos])

    # ------------------------------------------------------------------ market, evidence
    def get_market_data(self, as_of_date: Optional[str] = None) -> dict:
        from opdata.config import MONTH_ENDS
        from opdata.market import BOND, EQUITY
        d = parse_date(as_of_date)
        rows = [{"month_end": m.isoformat(), "equity_return": EQUITY[i], "bond_return": BOND[i]} for i, m in enumerate(MONTH_ENDS) if d is None or m <= d]
        return self._env("get_market_data", None, as_of_date, {"months": rows}, "ref_market_path (shared synthetic path)",
                         ["Fictional market path; NOT real market data.", "Individual instruments apply a class beta plus seeded noise."])

    def get_source_evidence(self, client_id: str, entity_type: Optional[str] = None, entity_id: Optional[str] = None,
                            interaction_id: Optional[str] = None, turn_index: Optional[int] = None) -> dict:
        from services.conversations import shared
        self._client(client_id)
        conv = shared()
        if interaction_id is not None or turn_index is not None:
            if interaction_id is None or turn_index is None:
                raise ServiceError("interaction_id and turn_index must be given together")
            t = conv.turn(interaction_id, turn_index)
            if not t or t["client_id"] != client_id:
                raise ServiceError(f"no turn {turn_index} of {interaction_id} for {client_id}")
            return self._env("get_source_evidence", client_id, t["interaction_datetime"][:10],
                             {"turn": {"interaction_id": interaction_id, "turn_index": turn_index, "speaker": t["speaker"], "text": t["utterance"]}},
                             "conversations.csv", ["Conversation text is synthetic; only the referenced turn is returned."], [f"{interaction_id}#{turn_index}"])
        if not entity_type or not entity_id:
            raise ServiceError("give entity_type and entity_id, or interaction_id and turn_index")
        rows = [r for r in self.od.get_evidence(entity_type, entity_id) if r["client_id"] == client_id]
        ev = [{"evidence_id": r["evidence_id"], "field": r["field_name"], "kind": r["evidence_kind"], "excerpt": r["excerpt"], "interaction_id": r["interaction_id"],
               "turn_index": r["turn_index"], "speaker": r["speaker"], "note": r["note"],
               "verified_in_conversations": conv.verify_excerpt(client_id, r["interaction_id"], int(r["turn_index"]), r["excerpt"])} for r in rows]
        return self._env("get_source_evidence", client_id, None, {"entity_type": entity_type, "entity_id": entity_id, "evidence": ev}, "data_evidence_links",
                         ["Only verified references to conversations are listed; fields without evidence are synthetic."], [e["evidence_id"] for e in ev])
