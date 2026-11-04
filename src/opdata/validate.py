"""Validation + conversation-consistency audit for the synthetic operational layer.

    python src/opdata/validate.py                       # validates data/operational/*.csv (no database needed)
    python src/opdata/validate.py --sqlite build/x.db   # additionally runs the SQL reconciliation queries on a SQLite load
    python src/opdata/validate.py --dsn postgresql://.. # ... or on the PostgreSQL schema cmf_ops
Writes reports/operational_validation_report.json and .md.  Exit code 1 if any HARD check fails.
Audit items that disagree with the conversations because the conversations disagree with themselves are reported
as KNOWN_SOURCE_INCONSISTENCY, never silently passed.
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "opdata"

from opdata.config import CONVERSATIONS, CONVERSATIONS_MD5, OUT_DIR, REPORT_DIR, ConvIndex, md5_of
from opdata.generate import COLUMNS

ZERO = Decimal(0)
CENT = Decimal("0.01")
MONTH_ENDS = [date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30), date(2026, 5, 31), date(2026, 6, 30)]
CLIENTS = [f"CLIENT-00{i}" for i in range(1, 7)]
PROVS = {"synthetic", "conversation_anchored"}
DEC_COLS = {"quantity", "price", "amount", "market_value", "market_price", "purchase_price", "current_price", "annual_income",
            "monthly_income", "monthly_expenses", "target_amount", "outstanding_amount", "monthly_payment", "interest_rate"}
DATE_COLS = {"opened_date", "closed_date", "purchase_date", "valuation_date", "transaction_date", "snapshot_date", "valid_from",
             "valid_to", "target_date", "maturity_date", "as_of_date", "profile_as_of_date", "effective_from", "effective_to",
             "interaction_date", "month_end"}
INT_COLS = {"age", "dependants", "turn_index", "priority", "retirement_target_age", "investment_horizon_years",
            "liquidity_target_months", "liquidity_rank"}
BOOL_COLS = {"in_investable_portfolio", "is_external_flow"}


def load_table(name: str, data_dir: Path) -> list[dict]:
    out = []
    with open(data_dir / f"{name}.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            for k, v in list(r.items()):
                if v == "":
                    r[k] = None
                elif k in DEC_COLS:
                    r[k] = Decimal(v)
                elif k in DATE_COLS:
                    r[k] = date.fromisoformat(v)
                elif k in INT_COLS:
                    r[k] = int(v)
                elif k in BOOL_COLS:
                    r[k] = v == "true"
            out.append(r)
    return out


class Report:
    def __init__(self) -> None:
        self.checks: list[dict] = []

    def add(self, category: str, name: str, ok: bool, detail: str = "", status: str | None = None, items: list | None = None) -> bool:
        self.checks.append(dict(category=category, name=name, status=status or ("PASS" if ok else "FAIL"), detail=detail,
                                items=items or []))
        return ok

    def failed(self) -> list[dict]:
        return [c for c in self.checks if c["status"] == "FAIL"]


def r2(x: Decimal) -> Decimal:
    return x.quantize(CENT, rounding="ROUND_HALF_UP")


# ============================================================================================================
def validate(data_dir: Path, conv: ConvIndex, rep: Report) -> dict:
    T = {n: load_table(n, data_dir) for n in COLUMNS}
    prof = {r["client_id"]: r for r in T["client_financial_profiles"]}
    acct = {r["account_id"]: r for r in T["portfolio_accounts"]}
    hold = {r["holding_id"]: r for r in T["portfolio_holdings"]}
    inst = {r["instrument_id"]: r for r in T["ref_instruments"]}
    stats = {n: len(T[n]) for n in COLUMNS}
    stats.update(clients=len(prof), accounts=len(acct), holdings=len(hold), snapshots=len(T["portfolio_snapshots"]),
                 goals=len(T["financial_goals"]), liabilities=len(T["liabilities"]), transactions=len(T["portfolio_transactions"]))

    # ---------------------------------------------------------------- 0. source immutability + scope
    md5 = md5_of(CONVERSATIONS)
    rep.add("source", "conversations.csv checksum unchanged", md5 == CONVERSATIONS_MD5, f"md5={md5}")
    rep.add("source", "all six client IDs of conversations.csv are represented",
            set(prof) == {r["client_id"] for r in conv.rows} == set(CLIENTS), f"{sorted(prof)}")

    # ---------------------------------------------------------------- 1. ids and referential integrity
    for name, key in [("client_financial_profiles", "client_id"), ("portfolio_accounts", "account_id"), ("portfolio_holdings", "holding_id"),
                      ("portfolio_transactions", "transaction_id"), ("portfolio_snapshots", "snapshot_id"), ("financial_goals", "goal_id"),
                      ("liabilities", "liability_id"), ("data_evidence_links", "evidence_id"), ("ref_instruments", "instrument_id"),
                      ("client_expense_history", "expense_period_id")]:
        ids = [r[key] for r in T[name]]
        rep.add("referential", f"{name}.{key} unique", len(ids) == len(set(ids)), f"{len(ids)} ids")

    def fk(child, col, parent_ids, label):
        bad = [r[col] for r in T[child] if r[col] not in parent_ids]
        rep.add("referential", f"{child}.{col} -> {label}", not bad, f"{len(T[child])} rows, {len(bad)} orphans", items=bad[:5])

    cl = set(prof)
    for t in ("client_expense_history", "portfolio_accounts", "portfolio_holdings", "portfolio_transactions", "portfolio_snapshots",
              "financial_goals", "liabilities", "data_evidence_links"):
        fk(t, "client_id", cl, "client_financial_profiles")
    fk("portfolio_holdings", "account_id", set(acct), "portfolio_accounts")
    fk("portfolio_holdings", "instrument_id", set(inst), "ref_instruments")
    for t in ("portfolio_transactions", "portfolio_snapshots"):
        fk(t, "account_id", set(acct), "portfolio_accounts")
        fk(t, "holding_id", set(hold), "portfolio_holdings")
        fk(t, "instrument_id", set(inst), "ref_instruments")
    # a child's client must match its parent's client
    mism = [h["holding_id"] for h in hold.values() if acct[h["account_id"]]["client_id"] != h["client_id"]]
    for t in ("portfolio_transactions", "portfolio_snapshots"):
        mism += [r.get("transaction_id", r.get("snapshot_id")) for r in T[t]
                 if hold[r["holding_id"]]["client_id"] != r["client_id"] or hold[r["holding_id"]]["account_id"] != r["account_id"]
                 or hold[r["holding_id"]]["instrument_id"] != r["instrument_id"]]
    rep.add("referential", "holding/transaction/snapshot agree with parent client, account and instrument", not mism, f"{len(mism)} mismatches", items=mism[:5])
    dup = defaultdict(list)
    for h in hold.values():
        dup[(h["account_id"], h["instrument_id"])].append(h["holding_id"])
    rep.add("portfolio", "no duplicate instrument within one account", all(len(v) == 1 for v in dup.values()),
            f"{sum(len(v) > 1 for v in dup.values())} duplicates")
    ent = {"profile": set(prof), "account": set(acct), "holding": set(hold), "transaction": {r["transaction_id"] for r in T["portfolio_transactions"]},
           "goal": {r["goal_id"] for r in T["financial_goals"]}, "liability": {r["liability_id"] for r in T["liabilities"]}}
    bad = [e["evidence_id"] for e in T["data_evidence_links"] if e["entity_id"] not in ent[e["entity_type"]]]
    rep.add("referential", "data_evidence_links.entity_id exists in its entity table", not bad, f"{len(bad)} orphans", items=bad[:5])
    badc = [e["evidence_id"] for e in T["data_evidence_links"]
            if e["entity_type"] in ("holding", "account", "goal", "liability", "transaction") and
            _entity_client(e, T) != e["client_id"]]
    rep.add("referential", "evidence client matches its entity's client", not badc, f"{len(badc)} mismatches", items=badc[:5])

    # ---------------------------------------------------------------- 2. portfolio consistency
    for t, cols in [("portfolio_snapshots", ("quantity", "market_price", "market_value")), ("portfolio_holdings", ("quantity", "current_price", "market_value"))]:
        bad = [r.get("snapshot_id", r.get("holding_id")) for r in T[t] if r2(r[cols[0]] * r[cols[1]]) != r[cols[2]]]
        rep.add("portfolio", f"{t}: market_value == ROUND(quantity x price, 2)", not bad, f"{len(T[t])} rows, {len(bad)} off (rounding rule: half-up to the cent)", items=bad[:5])
    neg = [r for t in ("portfolio_snapshots", "portfolio_holdings") for r in T[t] if r["quantity"] < 0 or r["market_value"] < 0]
    rep.add("portfolio", "no negative quantities or market values", not neg, f"{len(neg)} negatives")
    rep.add("portfolio", "currency explicit and USD everywhere", all(r["currency"] == "USD" for t in
            ("portfolio_snapshots", "portfolio_holdings", "portfolio_transactions", "portfolio_accounts", "financial_goals", "liabilities") for r in T[t]), "")
    rep.add("portfolio", "provenance values restricted to the documented vocabulary",
            all(r["data_provenance"] in PROVS for t in COLUMNS for r in T[t] if "data_provenance" in COLUMNS[t]), "")
    # current holdings == last snapshot
    last = MONTH_ENDS[-1]
    lastsnap = {r["holding_id"]: r for r in T["portfolio_snapshots"] if r["snapshot_date"] == last}
    bad = [h["holding_id"] for h in hold.values() if (h["holding_status"] == "active") != (h["holding_id"] in lastsnap)
           or (h["holding_id"] in lastsnap and (lastsnap[h["holding_id"]]["quantity"] != h["quantity"] or lastsnap[h["holding_id"]]["market_value"] != h["market_value"]
                                                 or lastsnap[h["holding_id"]]["market_price"] != h["current_price"]))]
    rep.add("portfolio", "holdings table == 2026-06-30 snapshot (quantity, price, value; active iff present)", not bad, f"{len(bad)} differences", items=bad[:5])
    rep.add("portfolio", "holdings valuation_date == 2026-06-30", all(h["valuation_date"] == last for h in hold.values()), "")
    # totals: portfolio == sum of holdings, asset class totals == portfolio total, per client/date
    tot = defaultdict(Decimal); cls_tot = defaultdict(Decimal); tot_all = defaultdict(Decimal)
    for s in T["portfolio_snapshots"]:
        tot_all[(s["client_id"], s["snapshot_date"])] += s["market_value"]
        if acct[s["account_id"]]["in_investable_portfolio"]:
            tot[(s["client_id"], s["snapshot_date"])] += s["market_value"]
            cls_tot[(s["client_id"], s["snapshot_date"], hold[s["holding_id"]]["asset_class"])] += s["market_value"]
    ok = True; items = []
    for (c, d), v in tot.items():
        s_cls = sum(x for (cc, dd, _), x in cls_tot.items() if cc == c and dd == d)
        if s_cls != v:
            ok = False; items.append((c, str(d), str(v), str(s_cls)))
    rep.add("portfolio", "asset-class totals reconcile with investable portfolio total (every client x date)", ok, f"{len(tot)} client-dates", items=items[:5])
    cur = defaultdict(Decimal)
    for h in hold.values():
        if h["holding_status"] == "active" and acct[h["account_id"]]["in_investable_portfolio"]:
            cur[h["client_id"]] += h["market_value"]
    rep.add("portfolio", "current portfolio total == sum of current holdings (== 2026-06-30 snapshot total)",
            all(cur[c] == tot[(c, last)] for c in CLIENTS), "; ".join(f"{c[-1]}:{cur[c]:,.2f}" for c in CLIENTS))
    rep.add("portfolio", "each client has 8-16 holdings (rows) and >= 8 active at 2026-06-30",
            all(8 <= sum(h["client_id"] == c for h in hold.values()) <= 16 and sum(h["client_id"] == c and h["holding_status"] == "active" for h in hold.values()) >= 8 for c in CLIENTS),
            "; ".join(f"{c[-1]}:{sum(h['client_id'] == c for h in hold.values())}/{sum(h['client_id'] == c and h['holding_status'] == 'active' for h in hold.values())}" for c in CLIENTS))

    # ---------------------------------------------------------------- 3. historical consistency
    sd = defaultdict(set)
    for s in T["portfolio_snapshots"]:
        sd[s["client_id"]].add(s["snapshot_date"])
    rep.add("historical", "all six month-end snapshots (Jan-Jun 2026) exist for every client", all(sd[c] == set(MONTH_ENDS) for c in CLIENTS),
            f"{len(MONTH_ENDS)} dates x {len(CLIENTS)} clients")
    rep.add("historical", "snapshot dates are valid month-ends in chronological order", MONTH_ENDS == sorted(MONTH_ENDS) and all((d + timedelta(days=1)).day == 1 for d in MONTH_ENDS), "")
    tx = defaultdict(list)
    for t in T["portfolio_transactions"]:
        tx[t["holding_id"]].append(t)
    # ledger replay
    problems = []
    snap = {(s["holding_id"], s["snapshot_date"]): s for s in T["portfolio_snapshots"]}
    for hid, h in hold.items():
        q = ZERO
        ts = sorted(tx[hid], key=lambda t: (t["transaction_date"], t["transaction_id"]))
        for d in MONTH_ENDS:
            q = sum((t["quantity"] for t in ts if t["transaction_date"] <= d), ZERO)
            s = snap.get((hid, d))
            if q < 0:
                problems.append((hid, str(d), "negative ledger quantity"))
            if (q > 0) != (s is not None) or (s is not None and s["quantity"] != q):
                problems.append((hid, str(d), f"ledger {q} vs snapshot {s['quantity'] if s else None}"))
        if q != h["quantity"]:
            problems.append((hid, "current", f"ledger {q} vs holding {h['quantity']}"))
    rep.add("historical", "every snapshot quantity == ledger replay (opening balance + dated transactions); no holding appears/disappears without a transaction",
            not problems, f"{len(hold)} holdings x {len(MONTH_ENDS)} dates", items=problems[:5])
    rep.add("historical", "transactions: amount == ROUND(quantity x price, 2)", all(r2(t["quantity"] * t["price"]) == t["amount"] for t in T["portfolio_transactions"]), "")
    rep.add("historical", "transaction dates within 2026-01-31 .. 2026-06-30 and after the account was opened",
            all(MONTH_ENDS[0] <= t["transaction_date"] <= last and t["transaction_date"] >= acct[t["account_id"]]["opened_date"] for t in T["portfolio_transactions"]), "")
    # no holding acquired before its account opened / closed accounts empty
    bad = [h["holding_id"] for h in hold.values() if acct[h["account_id"]]["account_status"] == "closed" and h["holding_status"] != "closed"]
    rep.add("historical", "closed accounts contain no active holdings", not bad, f"{len(bad)} violations")
    # opening txns only at 2026-01-31; closed holdings have a closing transaction
    rep.add("historical", "opening_balance transactions only on 2026-01-31", all(t["transaction_date"] == MONTH_ENDS[0] for t in T["portfolio_transactions"] if t["transaction_type"] == "opening_balance"), "")
    sign_ok = all((t["quantity"] > 0) == (t["transaction_type"] in ("opening_balance", "buy", "transfer_in", "contribution", "vest_receipt", "secondary_sale_proceeds")) for t in T["portfolio_transactions"])
    rep.add("historical", "transaction signs match their types (buys/receipts positive; sells/outflows negative)", sign_ok, "")
    # internal transfers net to ~zero; sells always paired with a buy/transfer
    grp = defaultdict(list)
    for t in T["portfolio_transactions"]:
        grp[t["group_id"]].append(t)
    bad = []
    for g, rows in grp.items():
        if g.endswith("OPEN") or "-OPEN" in g or rows[0]["transaction_type"] == "opening_balance":
            continue
        ext = sum((r["amount"] for r in rows if r["is_external_flow"]), ZERO)
        internal = sum((r["amount"] for r in rows if not r["is_external_flow"]), ZERO)
        types = {r["transaction_type"] for r in rows}
        if types & {"sell", "transfer_out"} and not (types & {"buy", "transfer_in", "secondary_sale_proceeds"}):
            bad.append((g, "sell without matching buy"))
        elif "secondary_sale_proceeds" in types:
            if abs(sum((r["amount"] for r in rows), ZERO) - sum((r["amount"] for r in rows if r["transaction_type"] == "secondary_sale_proceeds"), ZERO) + sum((r["amount"] for r in rows if r["transaction_type"] == "sell"), ZERO) * 0) > 10**9:
                bad.append((g, "x"))
            sold = -sum((r["amount"] for r in rows if r["transaction_type"] == "sell"), ZERO)
            got = sum((r["amount"] for r in rows if r["transaction_type"] == "secondary_sale_proceeds"), ZERO)
            if abs(sold - got) > Decimal("1.00"):
                bad.append((g, f"sale {sold} vs allocation {got}"))
        elif types & {"sell", "transfer_out"} and not (types & {"tax_payment", "withdrawal", "expense_payment", "qcd"}) and abs(internal) > Decimal("1.00"):
            bad.append((g, f"internal transfer does not net to zero: {internal}"))
    rep.add("historical", "every internal transfer nets to ~0 (<= $1 quantity rounding); sale proceeds fully allocated", not bad, f"{len(grp)} transaction groups", items=bad[:5])
    # price continuity: historical snapshot prices are not the current price copied backwards
    byh = defaultdict(dict)
    for s in T["portfolio_snapshots"]:
        byh[s["holding_id"]][s["snapshot_date"]] = s["market_price"]
    flat_ok = {"cash_equivalent"}
    stale = [h for h, p in byh.items() if len(p) >= 3 and len(set(p.values())) == 1 and hold[h]["liquidity_category"] not in flat_ok and hold[h]["asset_class"] != "Other Illiquid"]
    rep.add("historical", "historical prices are not overwritten by the current price (priced instruments vary month to month)", not stale, f"{len(stale)} constant-price non-cash holdings", items=stale[:5])
    jumps = []
    for h, p in byh.items():
        ds = sorted(p)
        for a, b in zip(ds, ds[1:]):
            if p[a] > 0 and abs(float(p[b] / p[a] - 1)) > 0.40:
                jumps.append((h, str(b), float(p[b] / p[a] - 1)))
    rep.add("historical", "no month-over-month price move beyond +/-40% (plausibility)", not jumps, f"{len(jumps)} outliers", items=jumps[:5])
    # implied flow-adjusted monthly portfolio return plausibility
    ext = defaultdict(Decimal)
    for t in T["portfolio_transactions"]:
        if t["is_external_flow"] and acct[t["account_id"]]["in_investable_portfolio"]:
            ext[(t["client_id"], t["transaction_date"])] += t["amount"]
    out_of_range = []
    for c in CLIENTS:
        for a, b in zip(MONTH_ENDS, MONTH_ENDS[1:]):
            flow = sum((v for (cc, d), v in ext.items() if cc == c and a < d <= b), ZERO)
            ret = float((tot[(c, b)] - tot[(c, a)] - flow) / tot[(c, a)])
            if abs(ret) > 0.12:
                out_of_range.append((c, str(b), round(ret, 4)))
    rep.add("historical", "implied monthly flow-adjusted portfolio return within +/-12% for every client-month", not out_of_range, "", items=out_of_range[:5])
    # tx types vs account capabilities: external flows never touch non-investable accounts except via founder sale (internal)
    # evolution: each client's holding set changes plausibly (<= 60% of holdings change between consecutive months)
    churn = []
    for c in CLIENTS:
        for a, b in zip(MONTH_ENDS, MONTH_ENDS[1:]):
            A = {s["holding_id"] for s in T["portfolio_snapshots"] if s["client_id"] == c and s["snapshot_date"] == a}
            B = {s["holding_id"] for s in T["portfolio_snapshots"] if s["client_id"] == c and s["snapshot_date"] == b}
            if len(A ^ B) > 0.6 * len(A | B):
                churn.append((c, str(b)))
    rep.add("historical", "holding set does not churn arbitrarily (<= 60% membership change per month)", not churn, "", items=churn)
    # goals / liabilities / profile
    gbad = [g["goal_id"] for g in T["financial_goals"] if g["valid_to"] and g["valid_to"] < g["valid_from"]]
    rep.add("historical", "goal validity windows are well-formed", not gbad, "")
    overlap = []
    byk = defaultdict(list)
    for g in T["financial_goals"]:
        byk[g["goal_key"]].append(g)
    for k, gs in byk.items():
        gs.sort(key=lambda g: g["valid_from"])
        for a, b in zip(gs, gs[1:]):
            if a["valid_to"] is None or a["valid_to"] > b["valid_from"]:
                overlap.append((k, a["goal_id"], b["goal_id"]))
        if sum(g["valid_to"] is None for g in gs) > 1:
            overlap.append((k, "multiple open versions"))
    rep.add("historical", "versions of one goal never overlap and at most one is open", not overlap, f"{len(byk)} goal lineages", items=overlap[:5])
    ex = defaultdict(list)
    for r in T["client_expense_history"]:
        ex[r["client_id"]].append(r)
    okx = all(sorted(v, key=lambda r: r["effective_from"])[-1]["monthly_expenses"] == prof[c]["monthly_expenses"] for c, v in ex.items()) and set(ex) == set(prof)
    rep.add("historical", "latest expense-history row equals the profile's monthly_expenses", okx, "")

    # ---------------------------------------------------------------- 4. evidence integrity
    evbad = []
    for e in T["data_evidence_links"]:
        if e["evidence_kind"] == "design_doc":
            if e["interaction_id"] is not None or e["source_file"] != "docs/dataset_design.md":
                evbad.append((e["evidence_id"], "design_doc row malformed"))
            continue
        r = conv.by_turn.get((e["interaction_id"], e["turn_index"]))
        if r is None:
            evbad.append((e["evidence_id"], "turn does not exist")); continue
        if r["client_id"] != e["client_id"]:
            evbad.append((e["evidence_id"], "turn belongs to another client"))
        if e["excerpt"] not in r["utterance"]:
            evbad.append((e["evidence_id"], "excerpt not in utterance"))
        if r["interaction_datetime"][:10] != e["interaction_date"].isoformat() or r["speaker"] != e["speaker"]:
            evbad.append((e["evidence_id"], "date/speaker mismatch"))
    rep.add("evidence", f"every evidence link points to a real conversations.csv turn of the same client whose utterance contains the excerpt ({sum(e['evidence_kind'] != 'design_doc' for e in T['data_evidence_links'])} conversation links)",
            not evbad, "", items=evbad[:5])
    needs = [e for e in T["data_evidence_links"]]
    have = defaultdict(int)
    for e in needs:
        have[(e["entity_type"], e["entity_id"])] += 1
    anch_missing = []
    for typ, tbl, key in [("profile", "client_financial_profiles", "client_id"), ("account", "portfolio_accounts", "account_id"), ("holding", "portfolio_holdings", "holding_id"),
                          ("transaction", "portfolio_transactions", "transaction_id"), ("goal", "financial_goals", "goal_id"), ("liability", "liabilities", "liability_id")]:
        for r in T[tbl]:
            if r["data_provenance"] == "conversation_anchored" and have[(typ, r[key])] == 0:
                anch_missing.append(r[key])
            if r["data_provenance"] == "synthetic" and have[(typ, r[key])] > 0:
                anch_missing.append(r[key] + " (synthetic but has evidence)")
    rep.add("evidence", "conversation_anchored rows have evidence links; purely synthetic rows have none", not anch_missing, "", items=anch_missing[:5])

    # goal temporal rule: a goal version may not rest on a statement made after the version begins, unless that statement closes it
    tbad = []
    gmap = {g["goal_id"]: g for g in T["financial_goals"]}
    for e in T["data_evidence_links"]:
        if e["entity_type"] == "goal" and e["evidence_kind"] != "design_doc":
            g = gmap[e["entity_id"]]
            if e["interaction_date"] > g["valid_from"] and (g["valid_to"] is None or e["interaction_date"] > g["valid_to"]):
                tbad.append((g["goal_id"], str(e["interaction_date"]), str(g["valid_from"])))
    rep.add("historical", "no goal version is backed by a statement dated after it started (unless the statement ends it) -> no retroactive goals", not tbad, "", items=tbad[:5])
    first_ok = []
    for gid, g in gmap.items():
        dates = [e["interaction_date"] for e in T["data_evidence_links"] if e["entity_id"] == gid and e["evidence_kind"] != "design_doc"]
        if dates and min(dates) > g["valid_from"]:
            first_ok.append(gid)
    rep.add("historical", "every anchored goal version starts on or after its earliest supporting statement", not first_ok, "", items=first_ok[:5])

    return dict(T=T, stats=stats, tot=tot, tot_all=tot_all, cls_tot=cls_tot, ext=ext, prof=prof, acct=acct, hold=hold)


def _entity_client(e: dict, T: dict) -> str | None:
    tbl = {"holding": ("portfolio_holdings", "holding_id"), "account": ("portfolio_accounts", "account_id"), "goal": ("financial_goals", "goal_id"),
           "liability": ("liabilities", "liability_id"), "transaction": ("portfolio_transactions", "transaction_id")}[e["entity_type"]]
    for r in T[tbl[0]]:
        if r[tbl[1]] == e["entity_id"]:
            return r["client_id"]
    return None


# ============================================================================================================
# Conversation consistency audit
# ============================================================================================================
def value_at(ctx: dict, client: str, d: date, shift: int = 0) -> dict:
    """Investable value/asset-class split on date d reconstructed from the ledger and snapshot prices (linear in time)."""
    T, hold, acct = ctx["T"], ctx["hold"], ctx["acct"]
    d = max(d + timedelta(days=shift), MONTH_ENDS[0])  # before the first snapshot the opening position is assumed
    cache = ctx.setdefault("_cache", {})
    if "ledger" not in cache:
        led = defaultdict(list); pri = defaultdict(dict)
        for t in T["portfolio_transactions"]:
            led[t["holding_id"]].append(t)
        for s in T["portfolio_snapshots"]:
            pri[s["holding_id"]][s["snapshot_date"]] = s["market_price"]
        cache["ledger"], cache["pri"] = led, pri
    led, pri = cache["ledger"], cache["pri"]
    out = defaultdict(Decimal)
    for hid, h in hold.items():
        if h["client_id"] != client or not acct[h["account_id"]]["in_investable_portfolio"]:
            continue
        q = sum((t["quantity"] for t in led[hid] if t["transaction_date"] <= d), ZERO)
        if q <= 0:
            continue
        p = pri[hid]
        pts = sorted(p.items())
        # add the first transaction price as an anchor if the holding is younger than the first snapshot it appears in
        first = min(t["transaction_date"] for t in led[hid])
        anchors = [(first, [t for t in led[hid] if t["transaction_date"] == first][0]["price"])] + pts
        anchors = sorted(set(anchors))
        price = anchors[0][1] if d <= anchors[0][0] else anchors[-1][1]
        for (d0, p0), (d1, p1) in zip(anchors, anchors[1:]):
            if d0 <= d <= d1:
                w = Decimal((d - d0).days) / Decimal(max((d1 - d0).days, 1))
                price = p0 + w * (p1 - p0)
                break
        out[h["asset_class"]] += q * price
        out["_names"] += 0
        out[("h", hid)] = q * price
    out["_total"] = sum((v for k, v in out.items() if isinstance(k, str) and not k.startswith("_")), ZERO)
    return out


def flows_to(ctx: dict, client: str, d: date) -> Decimal:
    acct = ctx["acct"]
    return sum((t["amount"] for t in ctx["T"]["portfolio_transactions"]
                if t["client_id"] == client and t["is_external_flow"] and acct[t["account_id"]]["in_investable_portfolio"]
                and MONTH_ENDS[0] < t["transaction_date"] <= d), ZERO)


NUMWORD = {"million": 1_000_000}
RE_TOTAL = re.compile(r"(?:investable portfolio|portfolio)[^.$]*?\$([\d,.]+)( million)?", re.I)
RE_EQ = [re.compile(r"equity share is about (\d+) percent", re.I), re.compile(r"equities (?:near|are around) (\d+) percent", re.I)]
RE_CASH = re.compile(r"you hold about \$([\d,]+) in cash and short-term holdings", re.I)

# statements where the conversation contradicts itself or runs ahead of execution (documented in docs/synthetic_operational_data.md)
KNOWN_INCONSISTENCIES = {
    ("INT-031", 5): "same meeting states both 72% and 75% equity (INT-031.5 vs INT-031.9)",
    ("INT-031", 9): "same meeting states both 72% and 75% equity (INT-031.5 vs INT-031.9)",
}


def audit(ctx: dict, conv: ConvIndex, rep: Report) -> dict:
    prof, T = ctx["prof"], ctx["T"]
    res: dict = dict(total=[], equity=[], cash=[], events=[])
    # ---- A. portfolio totals quoted by the advisor
    for r in conv.rows:
        if r["speaker"] != "Advisor":
            continue
        c, d = r["client_id"], date.fromisoformat(r["interaction_datetime"][:10])
        m = RE_TOTAL.search(r["utterance"])
        if m and c in CLIENTS:
            v = Decimal(m.group(1).rstrip(".").replace(",", "")) * (1_000_000 if m.group(2) else 1)
            vd = value_at(ctx, c, d)["_total"]
            vd = vd if d > MONTH_ENDS[0] else value_at(ctx, c, MONTH_ENDS[0])["_total"]
            raw = float((vd - v) / v)
            adj = float((vd - flows_to(ctx, c, d) - v) / v)
            best = min(abs(raw), abs(adj))
            res["total"].append(dict(client=c, interaction=r["interaction_id"], turn=r["turn_index"], date=str(d), stated=float(v),
                                     model=float(vd), dev_raw=round(raw, 4), dev_flow_adjusted=round(adj, 4), best=round(best, 4)))
        for rx in RE_EQ:
            m = rx.search(r["utterance"])
            if m:
                stated = Decimal(m.group(1)) / 100
                shares = []
                for sh in (0, 14):  # statement may describe the target agreed in that very meeting, executed within two weeks
                    vv = value_at(ctx, c, d, sh)
                    t = vv["_total"]
                    shares.append(float((vv["Equity"] + vv["Alternatives"]) / t) if t else 0.0)
                dev = min(abs(s - float(stated)) for s in shares)
                key = (r["interaction_id"], r["turn_index"])
                res["equity"].append(dict(client=c, interaction=r["interaction_id"], turn=r["turn_index"], date=str(d), stated=float(stated),
                                          model_same_day=round(shares[0], 4), model_plus14d=round(shares[1], 4), best_dev=round(dev, 4),
                                          known_inconsistency=KNOWN_INCONSISTENCIES.get(key)))
                break
        m = RE_CASH.search(r["utterance"])
        if m:
            v = Decimal(m.group(1).replace(",", ""))
            vv = value_at(ctx, c, d, 0)
            res["cash"].append(dict(client=c, interaction=r["interaction_id"], turn=r["turn_index"], date=str(d), stated=float(v),
                                    model=float(vv["Cash & Equivalents"]), dev=round(float((vv["Cash & Equivalents"] - v) / v), 4)))
    # ---- verdicts for A
    def verdict(kind, rows, tol, key):
        bad = [x for x in rows if x[key] > tol and not x.get("known_inconsistency")]
        known = [x for x in rows if x[key] > tol and x.get("known_inconsistency")]
        n = len(rows)
        rep.add("conversation_audit", f"{kind}: {n} advisor statements; {n - len(bad) - len(known)} within tolerance, {len(known)} known source inconsistencies, {len(bad)} outside",
                not bad, f"tolerance {tol}; worst={max((x[key] for x in rows), default=0):.3f}",
                items=bad[:8])
    for x in res["total"]:
        x["abs_best"] = x["best"]
    for x in res["cash"]:
        x["abs_dev"] = abs(x["dev"])
    verdict("portfolio totals (best of raw / flow-adjusted)", res["total"], 0.06, "abs_best")
    verdict("equity share (equity + crypto; same day or +14 days)", res["equity"], 0.06, "best_dev")
    verdict("cash and short-term holdings", res["cash"], 0.12, "abs_dev")

    # ---- B. explicit event / fact checks
    def ev_check(name, refs, ok, detail):
        for (iid, turn, ex) in refs:
            conv.verify(next(iter({r["client_id"] for r in conv.rows if r["interaction_id"] == iid})), iid, turn, ex)  # raises if fabricated
        res["events"].append(dict(check=name, refs=[f"{a}.{b}" for a, b, _ in refs], ok=bool(ok), detail=detail))
        rep.add("conversation_audit", name, ok, detail + "  [evidence " + ", ".join(f"{a}.{b}" for a, b, _ in refs) + "]")

    hold, acct, tx = ctx["hold"], ctx["acct"], T["portfolio_transactions"]
    def share(c, d, cls=None, issuer=None, hname=None):
        v = value_at(ctx, c, d)
        t = v["_total"]
        if issuer:
            num = sum((val for k, val in v.items() if isinstance(k, tuple) and hold[k[1]]["issuer"] == issuer), ZERO)
        elif hname:
            num = sum((val for k, val in v.items() if isinstance(k, tuple) and hold[k[1]]["instrument_name"].startswith(hname)), ZERO)
        else:
            num = v[cls]
        return float(num / t)
    D = date
    # CLIENT-001
    s1 = share("CLIENT-001", D(2026, 3, 2), issuer="Northgate Freight Corp."); s2 = share("CLIENT-001", D(2026, 4, 3), issuer="Northgate Freight Corp.")
    ev_check("C1 Northgate stock ~9% of investable assets on 2 Mar (after the 27 Feb vest)", [("INT-046", 5, "At about nine percent of your investable assets")], abs(s1 - 0.09) <= 0.025, f"model {s1:.3%}")
    ev_check("C1 Northgate stock ~6% on 3 Apr (after selling about a third)", [("INT-067", 13, "six percent")], abs(s2 - 0.06) <= 0.02, f"model {s2:.3%}")
    sold = [t for t in tx if t["client_id"] == "CLIENT-001" and t["transaction_type"] == "sell" and hold[t["holding_id"]]["issuer"] == "Northgate Freight Corp."]
    pre = sum((t["quantity"] for t in tx if t["holding_id"] == sold[0]["holding_id"] and t["transaction_date"] < sold[0]["transaction_date"]), ZERO)
    ev_check("C1 sold about a third of the vested Northgate shares, after (not before) the 31 Mar decision", [("INT-064", 15, "sell about a third of the vested Northgate shares")],
             len(sold) == 1 and abs(float(-sold[0]["quantity"] / pre) - 1 / 3) < 0.01 and sold[0]["transaction_date"] > D(2026, 3, 31),
             f"sold {-sold[0]['quantity']} of {pre} on {sold[0]['transaction_date']}")
    vest = [t for t in tx if t["client_id"] == "CLIENT-001" and t["transaction_type"] == "vest_receipt"]
    ev_check("C1 vest recorded on 27 Feb (not earlier)", [("INT-046", 2, "vested on Friday, February 27")], len(vest) == 1 and vest[0]["transaction_date"] == D(2026, 2, 27), f"{vest[0]['transaction_date']}")
    bonds_sold = sum(-t["amount"] for t in tx if t["client_id"] == "CLIENT-001" and t["transaction_date"] == D(2026, 2, 12) and t["transaction_type"] == "sell" and hold[t["holding_id"]]["asset_class"] == "Fixed Income")
    ev_check("C1 about $90k moved from bonds into equity index funds on 12 Feb", [("INT-031", 15, "move about ninety thousand dollars from bonds into the equity index funds")], abs(bonds_sold - 90_000) < 1_500, f"${bonds_sold:,.0f}")
    e_mar, e_may = share("CLIENT-001", D(2026, 3, 31), "Equity"), share("CLIENT-001", D(2026, 5, 15), "Equity")
    ev_check("C1 equity ~75% before and ~60% after the April decision (retirement accounts first)", [("INT-067", 15, "reduce the equity allocation from about 75 percent to 60 percent")],
             abs(e_mar - 0.75) < 0.04 and abs(e_may - 0.60) < 0.04, f"31 Mar {e_mar:.1%}; 15 May {e_may:.1%}")
    rt = [t for t in tx if t["client_id"] == "CLIENT-001" and t["transaction_date"] == D(2026, 4, 8) and t["transaction_type"] == "sell" and hold[t["holding_id"]]["asset_class"] == "Equity"]
    ev_check("C1 de-risking sells came only from retirement accounts (401(k)/Roth), not the taxable account",
             [("INT-067", 15, "using the retirement accounts first")], rt and all(acct[t["account_id"]]["access_restriction"] == "retirement_age_restricted" for t in rt), f"{len(rt)} sells")
    cash1 = share("CLIENT-001", D(2026, 6, 30), "Cash & Equivalents") * float(ctx["tot"][("CLIENT-001", MONTH_ENDS[-1])]) / float(prof["CLIENT-001"]["monthly_expenses"])
    ev_check("C1 cash covers ~13 months of spending", [("INT-022", 19, "$150,000 in cash")], abs(cash1 - 13) <= 1.0, f"{cash1:.1f} months")
    ev_check("C1 deferred compensation ~$640k is held outside the investable portfolio", [("INT-022", 4, "around six hundred forty thousand")],
             any(hold[h]["instrument_name"].startswith("Northgate Deferred") and not acct[hold[h]["account_id"]]["in_investable_portfolio"] and abs(hold[h]["market_value"] - 640_000) < 12_000 for h in hold), "")
    g1 = [g for g in T["financial_goals"] if g["client_id"] == "CLIENT-001" and g["goal_key"] == "GK-001-01"]
    ev_check("C1 retirement goal: 65 until 1 May, then 63 (not earlier); 60 stays exploratory", [("INT-090", 11, "You've decided to retire at 63"), ("INT-049", 17, "65 remains the planning date")],
             sorted((g["target_date"].year, str(g["valid_from"])) for g in g1) == [(2031, "2026-05-01"), (2033, "2026-01-02")] and
             all(g["confidence_level"] == "exploratory" for g in T["financial_goals"] if g["goal_key"] == "GK-001-02") and prof["CLIENT-001"]["retirement_target_age"] == 63, "")
    # CLIENT-002
    ev_check("C2 no investment was sold: 'hold off any change' until the sale is clear (only cash payments leave)", [("INT-004", 15, "hold off on any change")],
             not [t for t in tx if t["client_id"] == "CLIENT-002" and t["transaction_type"] == "sell"], "")
    pay = [t for t in tx if t["client_id"] == "CLIENT-002" and t["transaction_type"] == "expense_payment"]
    ev_check("C2 $55k legal/accounting costs paid from cash on/after 8 Jun (not from investments)", [("INT-126", 15, "about fifty-five thousand dollars, from cash and not from investments")],
             len(pay) == 1 and pay[0]["amount"] == -55_000 and pay[0]["transaction_date"] >= D(2026, 6, 8) and hold[pay[0]["holding_id"]]["asset_class"] == "Cash & Equivalents", "")
    ev_check("C2 cash ~$220k before the payment (covers ~15 months)", [("INT-112", 9, "$220,000 in cash and short-term holdings")],
             abs(float(value_at(ctx, "CLIENT-002", D(2026, 5, 21))["Cash & Equivalents"]) - 220_000) < 5_000, "")
    ev_check("C2 sale proceeds (24M headline) and the care reserve are NOT in the investable portfolio (sale has not closed)", [("INT-099", 15, "set aside six hundred thousand dollars of the proceeds at closing")],
             ctx["tot"][("CLIENT-002", MONTH_ENDS[-1])] < 3_000_000 and any(g["goal_type"] == "Healthcare" and g["current_status"] == "conditional" for g in T["financial_goals"] if g["client_id"] == "CLIENT-002"), "")
    # CLIENT-003
    cash3 = [s for s in T["portfolio_snapshots"] if s["snapshot_date"] == MONTH_ENDS[2] and hold[s["holding_id"]]["asset_class"] == "Cash & Equivalents" and s["client_id"] == "CLIENT-003"]
    ev_check("C3 cash reserve reaches $50,000 by 2 Mar (not before the 5 Feb decision)", [("INT-045", 15, "$50,000 in cash and short-term holdings")],
             abs(sum(s["market_value"] for s in cash3) - 50_000) < 600 and not [t for t in tx if t["client_id"] == "CLIENT-003" and t["transaction_type"] == "contribution" and t["transaction_date"] < D(2026, 2, 5) and hold[t["holding_id"]]["asset_class"] == "Cash & Equivalents"], f"31 Mar cash ${sum(s['market_value'] for s in cash3):,.0f}")
    hf = [s for s in T["portfolio_snapshots"] if s["snapshot_date"] == MONTH_ENDS[-1] and acct[s["account_id"]]["account_name"].startswith("House Fund")]
    hv = sum(s["market_value"] for s in hf)
    ev_check("C3 house fund holds ~$40,000 in short-term bonds/cash, created 13 May and untouched afterwards", [("INT-139", 4, "That forty thousand is house money")],
             abs(hv - 40_000) < 800 and all(t["transaction_date"] <= D(2026, 5, 13) for t in tx if t["client_id"] == "CLIENT-003" and acct[t["account_id"]]["account_name"].startswith("House Fund")), f"${hv:,.0f}")
    e3a, e3b = share("CLIENT-003", D(2026, 3, 31), "Equity"), share("CLIENT-003", D(2026, 6, 30), "Equity")
    ev_check("C3 retirement equity ~90% before 1 May and ~75% after", [("INT-089", 13, "cut retirement equities from about 90 percent to 75 percent")], abs(e3a - 0.90) < 0.05 and abs(e3b - 0.75) < 0.04, f"31 Mar {e3a:.1%}; 30 Jun {e3b:.1%}")
    ls = [l for l in T["liabilities"] if l["client_id"] == "CLIENT-003"][0]
    ev_check("C3 student loan ~$48k at 5.2% (amortised slightly lower by June)", [("INT-019", 2, "I still owe about forty-eight thousand in student loans at five point two percent")],
             ls["interest_rate"] == Decimal("0.052") and Decimal(46_000) < ls["outstanding_amount"] < Decimal(48_000), f"${ls['outstanding_amount']:,.2f}")
    house = [g for g in T["financial_goals"] if g["goal_key"] == "GK-003-02"]
    ev_check("C3 house purchase never recorded as a confirmed decision (the advisor's 'decided' note was wrong; corrected 10 Apr)", [("INT-071", 13, "was too strong; you were only considering it")],
             all(g["confidence_level"] != "confirmed" for g in house) and any(g["current_status"] == "postponed" and g["valid_from"] == D(2026, 4, 10) for g in house), "")
    # CLIENT-004
    tb = [s for s in T["portfolio_snapshots"] if s["client_id"] == "CLIENT-004" and s["snapshot_date"] == MONTH_ENDS[1] and acct[s["account_id"]]["account_name"].startswith("Folake")]
    tbv = sum(s["market_value"] for s in tb)
    ev_check("C4 ~$80k tuition bucket in short-term bonds exists from 20 Feb (not in January)", [("INT-036", 11, "tuition bucket of about eighty thousand dollars in short-term bonds")],
             abs(tbv - 80_000) < 800 and not [s for s in T["portfolio_snapshots"] if s["client_id"] == "CLIENT-004" and s["snapshot_date"] == MONTH_ENDS[0] and acct[s["account_id"]]["account_name"].startswith("Folake")], f"28 Feb ${tbv:,.0f}")
    g = [t for t in tx if t["client_id"] == "CLIENT-004" and t["transaction_type"] == "buy" and hold[t["holding_id"]]["instrument_name"].startswith("Global Equity")]
    ev_check("C4 first equity step on 1 Jun = ~$24,000 of the global equity fund (and nothing earlier)", [("INT-122", 3, "On June first we bought about twenty-four thousand dollars of the global equity fund")],
             len(g) == 1 and g[0]["transaction_date"] == D(2026, 6, 1) and abs(g[0]["amount"] - 24_000) < 100, "")
    e4 = [share("CLIENT-004", d, "Equity") for d in MONTH_ENDS]
    ev_check("C4 equity stays ~15% (capital preservation) through May", [("INT-020", 11, "keep the insurance payout in safe, accessible investments for now")], all(abs(x - 0.15) < 0.03 for x in e4[:5]), " ".join(f"{x:.1%}" for x in e4))
    inh = [a for a in acct.values() if a["client_id"] == "CLIENT-004" and a["account_type"] == "Inherited IRA"][0]
    ev_check("C4 inherited account closed on rollover into her own name before 1 Jun; same instruments continue in the new account", [("INT-117", 6, "the retirement account you rolled into your own name")],
             inh["account_status"] == "closed" and inh["closed_date"] < D(2026, 6, 1) and
             {hold[h]["instrument_id"] for h in hold if hold[h]["account_id"] == inh["account_id"]} <= {hold[h]["instrument_id"] for h in hold if acct[hold[h]["account_id"]]["account_type"] == "Rollover IRA"}, "")
    ml = [l for l in T["liabilities"] if l["client_id"] == "CLIENT-004"][0]
    ev_check("C4 mortgage kept: 3.1% rate, balance a little below the $140k quoted in February", [("INT-030", 2, "We owe about a hundred forty thousand"), ("INT-084", 13, "keep the mortgage rather than pay it off")],
             ml["interest_rate"] == Decimal("0.031") and Decimal(130_000) < ml["outstanding_amount"] < Decimal(140_000), f"${ml['outstanding_amount']:,.2f}")
    ev_check("C4 retirement age 63 in profile and the only open retirement goal", [("INT-117", 13, "You've decided to work until 63")],
             prof["CLIENT-004"]["retirement_target_age"] == 63 and [g["current_status"] for g in T["financial_goals"] if g["goal_key"] == "GK-004-01" and g["valid_to"] is None] == ["active"], "")
    # CLIENT-005
    vt = [h for h in hold.values() if h["client_id"] == "CLIENT-005" and h["instrument_name"].startswith("Vantora")][0]
    sh_j, sh_j2 = share("CLIENT-005", D(2026, 1, 9), hname="Vantora"), share("CLIENT-005", D(2026, 6, 9), hname="Vantora")
    ev_check("C5 Vantora ~1/3 of the liquid portfolio in January and ~1/6 after the trims", [("INT-034", 8, "why we hold your Vantora Systems shares")], abs(sh_j - 1 / 3) < 0.05 and abs(sh_j2 - 1 / 6) < 0.04, f"9 Jan {sh_j:.1%}; 9 Jun {sh_j2:.1%}")
    ql = {t["transaction_date"]: -t["quantity"] for t in tx if t["holding_id"] == vt["holding_id"] and t["transaction_type"] == "sell"}
    q0 = sum((t["quantity"] for t in tx if t["holding_id"] == vt["holding_id"] and t["transaction_type"] == "opening_balance"), ZERO)
    ev_check("C5 Vantora trimmed by 40% in three equal steps after 21 Apr; 60% remains", [("INT-080", 13, "sell forty percent of the Vantora position in three steps"), ("INT-127", 8, "Sixty percent stays")],
             len(ql) == 3 and min(ql) > D(2026, 4, 21) and abs(float(sum(ql.values()) / q0) - 0.4) < 0.001 and abs(float(vt["quantity"] / q0) - 0.6) < 0.001, f"{sorted(str(d) for d in ql)}")
    cr = [h for h in hold.values() if h["client_id"] == "CLIENT-005" and h["instrument_type"] == "Crypto Asset"][0]
    c5 = share("CLIENT-005", D(2026, 3, 1), "Alternatives")
    ev_check("C5 crypto ~5% before and fully sold on 11 May (not earlier)", [("INT-051", 11, "It's about five percent"), ("INT-101", 11, "sell the entire crypto holding")],
             cr["holding_status"] == "closed" and cr["closed_date"] == D(2026, 5, 11) and abs(c5 - 0.05) < 0.02, f"1 Mar {c5:.1%}; closed {cr['closed_date']}")
    fs = [t for t in tx if t["client_id"] == "CLIENT-005" and t["transaction_type"] == "sell" and hold[t["holding_id"]]["instrument_name"].startswith("Moreau")]
    ev_check("C5 half of the offered $150k founder shares sold ($75k) on/after 14 Apr decision", [("INT-073", 13, "sell half, about seventy-five thousand dollars, of the offered founder shares")],
             len(fs) == 1 and abs(-fs[0]["amount"] - 75_000) < 5 and fs[0]["transaction_date"] > D(2026, 4, 14), "")
    cash5 = value_at(ctx, "CLIENT-005", D(2026, 5, 29))["Cash & Equivalents"] / prof["CLIENT-005"]["monthly_expenses"]
    ev_check("C5 cash reserve >= 9 months of spending by end of May (decided 11 Mar), and < 9 months in Jan-Mar", [("INT-051", 15, "build the cash reserve to nine months of expenses")],
             cash5 >= 9 and value_at(ctx, "CLIENT-005", D(2026, 3, 11))["Cash & Equivalents"] / prof["CLIENT-005"]["monthly_expenses"] < 6, f"29 May {cash5:.1f} months")
    ev_check("C5 restaurant investment never funded (declined 16 Mar)", [("INT-054", 11, "You've decided not to invest in Hugo's restaurant")],
             not any("restaurant" in (h["instrument_name"] + h["issuer"]).lower() for h in hold.values()) and any(g["current_status"] == "declined" for g in T["financial_goals"] if g["client_id"] == "CLIENT-005"), "")
    # CLIENT-006
    e6 = {d: share("CLIENT-006", d, "Equity") for d in MONTH_ENDS}
    ev_check("C6 equity 35% (Jan-Feb) -> ~25% after 26 Mar -> ~30% after 29 Apr", [("INT-060", 13, "cut the equity allocation from 35 percent to 25 percent"), ("INT-087", 11, "raise equities from 25 percent to 30 percent")],
             abs(e6[MONTH_ENDS[0]] - .35) < .03 and abs(e6[MONTH_ENDS[2]] - .25) < .03 and abs(e6[MONTH_ENDS[4]] - .30) < .03, " ".join(f"{x:.1%}" for x in e6.values()))
    wd = defaultdict(Decimal)
    for t in tx:
        if t["client_id"] == "CLIENT-006" and t["transaction_type"] == "withdrawal":
            wd[t["transaction_date"].month] += -t["amount"]
    ev_check("C6 monthly portfolio draw ~$2,600 in Feb-Mar and ~$3,800 from April", [("INT-009", 3, "Each month you take about twenty-six hundred from the portfolio"), ("INT-119", 7, "about thirty-eight hundred a month")],
             wd[2] == 2600 and wd[3] == 2600 and wd[4] == wd[5] == wd[6] == 3800, f"{dict(sorted(wd.items()))}")
    ev_check("C6 monthly spending 8,900 until 1 Jun then 10,100 (profile = latest)", [("INT-119", 19, "about ten thousand one hundred dollars")],
             prof["CLIENT-006"]["monthly_expenses"] == 10_100 and [(str(r["effective_to"]), r["monthly_expenses"]) for r in T["client_expense_history"] if r["client_id"] == "CLIENT-006"] == [("2026-06-01", Decimal(8900)), ("None", Decimal(10100))], "")
    # profiles: risk category vs equity share; horizon vs retirement age; expenses; liquidity
    bands = {"Conservative": (0, .25), "Moderately Conservative": (.25, .40), "Moderate": (.40, .68), "Moderately Aggressive": (.68, .83), "Aggressive": (.83, 1.01)}
    rc = {c: share(c, MONTH_ENDS[-1], "Equity") + share(c, MONTH_ENDS[-1], "Alternatives") for c in CLIENTS}
    rep.add("conversation_audit", "risk_category consistent with the 30-Jun equity share for all six clients (capital-preservation clients are not aggressive)",
            all(bands[prof[c]["risk_category"]][0] <= rc[c] < bands[prof[c]["risk_category"]][1] for c in CLIENTS), "; ".join(f"{c[-1]}:{prof[c]['risk_category']} {rc[c]:.0%}" for c in CLIENTS))
    hz = [(c, p["age"], p["retirement_target_age"], p["investment_horizon_years"]) for c, p in prof.items() if p["retirement_target_age"]]
    rep.add("conversation_audit", "investment_horizon_years == retirement_target_age - age where a retirement age exists", all(h[3] == h[2] - h[1] for h in hz), str(hz))
    mex = []
    for e in T["data_evidence_links"]:
        if e["entity_type"] == "profile" and e["field_name"] == "monthly_expenses":
            mex.append((e["client_id"], e["excerpt"]))
    rep.add("conversation_audit", "every client's monthly_expenses is backed by an advisor-stated figure", len({c for c, _ in mex}) == 6, str(len(mex)) + " links")
    liq = {c: float(value_at(ctx, c, date(2026, 6, 30))["Cash & Equivalents"] / prof[c]["monthly_expenses"]) for c in CLIENTS}
    rep.add("conversation_audit", "liquidity ratio (cash & equivalents / monthly expenses) is computable for every client", all(v > 0 for v in liq.values()), "; ".join(f"{c[-1]}:{v:.1f}m vs target {prof[c]['liquidity_target_months']}m" for c, v in liq.items()), status="INFO" )
    return res


# ============================================================================================================
def sql_checks(conn, schema_prefix: str, ctx: dict, rep: Report, label: str) -> None:
    q = lambda s: conn.execute(s.replace("S.", schema_prefix)).fetchall()
    rep.add("database", f"{label}: row counts equal CSV", all(q(f"SELECT count(*) FROM S.{t}")[0][0] == len(ctx["T"][t]) for t in COLUMNS), "")
    rows = q("SELECT count(*) FROM S.portfolio_snapshots s LEFT JOIN S.portfolio_holdings h ON h.holding_id=s.holding_id WHERE h.holding_id IS NULL")
    rep.add("database", f"{label}: no orphan snapshots", rows[0][0] == 0, "")
    rows = q("SELECT count(*) FROM (SELECT client_id, snapshot_date, SUM(market_value) v FROM S.portfolio_snapshots GROUP BY 1,2) a "
             "JOIN (SELECT client_id, snapshot_date, SUM(value) v FROM S.v_snapshot_asset_class_totals GROUP BY 1,2) b USING (client_id, snapshot_date) "
             "JOIN S.v_snapshot_investable_totals t USING (client_id, snapshot_date) WHERE ABS(b.v - t.investable_value) > 0.005")
    rep.add("database", f"{label}: view totals reconcile (asset-class view == investable view)", rows[0][0] == 0, "")
    rows = q("SELECT count(*) FROM S.portfolio_snapshots WHERE ROUND(quantity*market_price,2) <> market_value")
    rep.add("database", f"{label}: market_value == ROUND(quantity*price,2) in SQL", rows[0][0] == 0, "")
    rows = q("SELECT count(*) FROM S.v_current_investable_holdings")
    rep.add("database", f"{label}: current investable holdings view returns rows", rows[0][0] > 0, f"{rows[0][0]} rows")


def determinism(rep: Report) -> None:
    with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
        gen = Path(__file__).with_name("generate.py")
        for d in (a, b):
            subprocess.run([sys.executable, str(gen), "--out", d], check=True, capture_output=True)
        same = all(hashlib.md5((Path(a) / f"{t}.csv").read_bytes()).digest() == hashlib.md5((Path(b) / f"{t}.csv").read_bytes()).digest() for t in COLUMNS)
        committed = all(hashlib.md5((Path(a) / f"{t}.csv").read_bytes()).digest() == hashlib.md5((OUT_DIR / f"{t}.csv").read_bytes()).digest() for t in COLUMNS)
    rep.add("determinism", "two fresh generator runs produce byte-identical CSVs", same, "13 files compared by MD5")
    rep.add("determinism", "committed data/operational/*.csv equals a fresh run", committed, "")


def render_md(rep: Report, ctx: dict, aud: dict, path: Path) -> None:
    st = ctx["stats"]
    by = defaultdict(list)
    for c in rep.checks:
        by[c["category"]].append(c)
    n_pass = sum(c["status"] == "PASS" for c in rep.checks); n_fail = sum(c["status"] == "FAIL" for c in rep.checks)
    L = ["# Operational data validation report", "",
         f"Overall: **{'PASS' if not n_fail else 'FAIL'}** ({n_pass} passed, {n_fail} failed, {len(rep.checks)} checks). Generated by `src/opdata/validate.py`.", "",
         "## Counts", "", "| Item | Count |", "|---|---|"]
    for k in ("clients", "accounts", "holdings", "snapshots", "goals", "liabilities", "transactions"):
        L.append(f"| {k} | {st[k]} |")
    L += [f"| evidence links | {st['data_evidence_links']} |", f"| instruments | {st['ref_instruments']} |", ""]
    tot, hold, acct = ctx["tot"], ctx["hold"], ctx["acct"]
    L += ["## Investable portfolio value by month (USD)", "", "| Client | " + " | ".join(d.strftime("%b") for d in MONTH_ENDS) + " |", "|---|" + "---|" * 6]
    for c in CLIENTS:
        L.append(f"| {c} | " + " | ".join(f"{tot[(c, d)]:,.0f}" for d in MONTH_ENDS) + " |")
    L.append("")
    titles = {"source": "Source immutability", "referential": "Referential integrity", "portfolio": "Portfolio reconciliation", "historical": "Historical consistency",
              "evidence": "Evidence integrity", "conversation_audit": "Conversation consistency audit", "determinism": "Determinism", "database": "Database"}
    for cat in ("source", "referential", "portfolio", "historical", "evidence", "determinism", "database", "conversation_audit"):
        if cat not in by:
            continue
        L += [f"## {titles[cat]}", "", "| Result | Check | Detail |", "|---|---|---|"]
        for c in by[cat]:
            L.append(f"| {c['status']} | {c['name']} | {c['detail'].replace('|', '/')[:220]} |")
        L.append("")
    if aud:
        for k, title, cols in (("total", "Advisor-quoted portfolio totals vs model", ("client", "date", "stated", "model", "dev_raw", "dev_flow_adjusted")),
                               ("equity", "Advisor-quoted equity share vs model", ("client", "date", "stated", "model_same_day", "model_plus14d", "known_inconsistency")),
                               ("cash", "Advisor-quoted cash and short-term holdings vs model cash & equivalents", ("client", "date", "stated", "model", "dev"))):
            rows = aud[k]
            L += [f"### {title}", "", f"{len(rows)} statements. Worst deviation: " +
                  (f"{max(r.get('abs_best', r.get('best_dev', r.get('abs_dev', 0))) for r in rows):.1%}" if rows else "n/a"), "",
                  "| " + " | ".join(cols) + " | evidence |", "|" + "---|" * (len(cols) + 1)]
            for r in sorted(rows, key=lambda r: (r["client"], r["date"])):
                L.append("| " + " | ".join(str(r.get(c, "")) for c in cols) + f" | {r['interaction']}.{r['turn']} |")
            L.append("")
    path.write_text("\n".join(L), encoding="utf-8")


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    data_dir = Path(argv[argv.index("--data-dir") + 1]) if "--data-dir" in argv else OUT_DIR
    conv = ConvIndex()
    rep = Report()
    ctx = validate(data_dir, conv, rep)
    aud = audit(ctx, conv, rep)
    if "--skip-determinism" not in argv:
        determinism(rep)
    if "--sqlite" in argv:
        from opdata.load_db import load_sqlite
        path = argv[argv.index("--sqlite") + 1]
        load_sqlite(path, data_dir)
        conn = sqlite3.connect(path); conn.execute("PRAGMA foreign_keys=ON")
        sql_checks(conn, "", ctx, rep, "sqlite")
        conn.close()
    if "--dsn" in argv:
        import psycopg
        with psycopg.connect(argv[argv.index("--dsn") + 1]) as conn:
            class C:
                def execute(self, s):
                    cur = conn.cursor(); cur.execute(s); return cur
            sql_checks(C(), "cmf_ops.", ctx, rep, "postgres")
    REPORT_DIR.mkdir(exist_ok=True)
    out = dict(summary=dict(passed=sum(c["status"] == "PASS" for c in rep.checks), failed=len(rep.failed()), total=len(rep.checks)),
               counts=ctx["stats"], checks=rep.checks,
               audit={k: v for k, v in aud.items() if k != "events"} | {"events": aud["events"]})
    (REPORT_DIR / "operational_validation_report.json").write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    render_md(rep, ctx, aud, REPORT_DIR / "operational_validation_report.md")
    for c in rep.checks:
        if c["status"] == "FAIL":
            print("FAIL", c["category"], c["name"], c["detail"], c["items"][:3])
    print(f"{out['summary']['passed']} passed, {out['summary']['failed']} failed of {out['summary']['total']} checks")
    return 1 if rep.failed() else 0


if __name__ == "__main__":
    raise SystemExit(main())
