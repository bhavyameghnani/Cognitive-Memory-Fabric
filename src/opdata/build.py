"""Deterministic builder: specs -> coherent entities -> every table.

Order of derivation (nothing is generated independently per table):
  opening positions (Jan-31)  ->  ledger of dated transactions  ->  quantities at each month-end
  ->  prices from the shared market path  ->  market values  ->  snapshots / current holdings / totals.
"""
from __future__ import annotations

import calendar
import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from . import market
from .config import (BASE_DATE, CURRENCY, MONTH_ENDS, PROFILE_AS_OF, PROV_ANCHORED, PROV_SYNTH, SEED,
                     ConvIndex)
from .market import INSTRUMENTS, price_on, q2, q4, series
from .specs import CLIENTS, D, X

ZERO = Decimal("0")
EXTERNAL_TYPES = {"contribution", "vest_receipt", "tax_payment", "expense_payment", "qcd", "withdrawal",
                  "secondary_sale_proceeds"}
GROWTH = {"Equity": 0.075, "Multi-Asset": 0.05, "Fixed Income": 0.010, "Commodities": 0.060, "Alternatives": 0.10,
          "Cash & Equivalents": 0.0, "Other Illiquid": 0.25}

ACCOUNT_EV = {
    ("CLIENT-001", "dc"): X("INT-001", 11, "Northgate deferred compensation account", "implied", "account_name"),
    ("CLIENT-002", "c529"): X("INT-038", 4, "David started a college account when Ethan was born", "implied", "account_name"),
    ("CLIENT-002", "biz"): X("INT-032", 4, "nineteen to twenty-three million", "implied", "account_name"),
    ("CLIENT-003", "k401"): X("INT-019", 10, "401(k)", "implied", "account_type"),
    ("CLIENT-003", "house"): X("INT-139", 4, "That forty thousand is house money", "implied", "account_name"),
    ("CLIENT-004", "inh"): X("INT-059", 10, "the account you inherited from Samuel", "implied", "account_name"),
    ("CLIENT-004", "roll"): X("INT-117", 6, "the retirement account you rolled into your own name", "implied", "account_name"),
    ("CLIENT-004", "k401"): X("INT-007", 8, "your own 401(k) at the hospital", "implied", "account_name"),
    ("CLIENT-004", "tuit"): X("INT-036", 11, "dedicated tuition bucket", "implied", "account_name"),
    ("CLIENT-005", "priv"): X("INT-034", 2, "founders' shares", "implied", "account_name"),
    ("CLIENT-006", "ira"): X("INT-085", 6, "from the IRA", "implied", "account_type"),
}
ACCOUNT_EV = {k: v for k, v in ACCOUNT_EV.items() if v}
ANCHORED_INSTRUMENTS = {"northgate", "vantora", "founders", "dcomp", "holloway"}


def cnum(cid: str) -> str:
    return cid[-3:]


def eom(y: int, m: int) -> date:
    return date(y, m, calendar.monthrange(y, m)[1])


@dataclass
class Txn:
    client: str
    account: str
    hkey: str
    d: date
    ttype: str
    qty: Decimal
    price: Decimal
    external: bool
    group: str
    note: str
    ev: dict | None


@dataclass
class ClientBuild:
    cid: str
    spec: dict
    qty: dict = field(default_factory=dict)
    cost: dict = field(default_factory=dict)       # total cost of acquired units
    acquired: dict = field(default_factory=dict)   # total units acquired
    first_buy: dict = field(default_factory=dict)
    closed: dict = field(default_factory=dict)
    txns: list = field(default_factory=list)
    snaps: dict = field(default_factory=dict)      # date -> {hkey: qty}
    open_qty: dict = field(default_factory=dict)
    warnings: list = field(default_factory=list)
    gseq: int = 0


def build_all(conv: ConvIndex) -> dict[str, list[dict]]:
    tables: dict[str, list[dict]] = defaultdict(list)
    evid: list[dict] = []
    used_inst: set[str] = set()
    builds: dict[str, ClientBuild] = {}

    for cid, spec in CLIENTS.items():
        b = ClientBuild(cid, spec)
        builds[cid] = b
        _opening(b)
        _run_events(b)
        for h in spec["holdings"]:
            used_inst.add(h["inst"])

    inst_ids = {k: f"INS-{i:03d}" for i, k in enumerate([k for k in INSTRUMENTS if k in used_inst], 1)}
    _ref_tables(tables, inst_ids)

    for cid, b in builds.items():
        n = cnum(cid)
        spec = b.spec
        acct_id = {a["key"]: f"ACC-{n}-{i:02d}" for i, a in enumerate(spec["accounts"], 1)}
        hold_id = {h["key"]: f"HLD-{n}-{i:02d}" for i, h in enumerate(spec["holdings"], 1)}
        hdef = {h["key"]: h for h in spec["holdings"]}
        adef = {a["key"]: a for a in spec["accounts"]}
        ev_rows: list[tuple[str, str, str, dict]] = []  # (entity_type, entity_id, default_field, ev)

        # ---- accounts
        for a in spec["accounts"]:
            aid = acct_id[a["key"]]
            ev = ACCOUNT_EV.get((cid, a["key"]))
            if ev:
                ev_rows.append(("account", aid, "account_name", ev))
            tables["portfolio_accounts"].append(dict(
                account_id=aid, client_id=cid, account_type=a["type"], account_name=a["name"], currency=CURRENCY,
                account_status=a["status"], opened_date=a["opened"], closed_date=a["closed"],
                in_investable_portfolio=a["investable"], access_restriction=a["restriction"],
                data_provenance=PROV_ANCHORED if ev else PROV_SYNTH,
                provenance_note=("Existence/name anchored to the cited turn; opened_date and all other fields synthetic."
                                 if ev else "Entirely synthetic account created for the POC.")))

        # ---- profile
        p = spec["profile"]
        mi = 6_300 if cid == "CLIENT-006" else round(p["annual_income"] / 12)
        tables["client_financial_profiles"].append(dict(
            client_id=cid, age=p["age"], employment_status=p["employment_status"], occupation=p["occupation"],
            annual_income=p["annual_income"], monthly_income=mi, monthly_expenses=p["monthly_expenses"],
            dependants=p["dependants"], retirement_target_age=p["retirement_target_age"],
            investment_horizon=p["investment_horizon"], investment_horizon_years=p["investment_horizon_years"],
            risk_category=p["risk_category"], liquidity_requirements=p["liquidity_requirements"],
            liquidity_target_months=p["liquidity_target_months"], currency=CURRENCY, profile_as_of_date=PROFILE_AS_OF,
            data_provenance=PROV_ANCHORED, synthetic_fields=p["synth_fields"]))
        for e in spec["profile_ev"]:
            ev_rows.append(("profile", cid, e["field"], e))
        for i, (frm, to, amt) in enumerate(spec["expense_hist"], 1):
            tables["client_expense_history"].append(dict(
                expense_period_id=f"EXP-{n}-{i:02d}", client_id=cid, effective_from=D(frm), effective_to=D(to) if to else None,
                monthly_expenses=amt, currency=CURRENCY, data_provenance=PROV_ANCHORED,
                provenance_note="Monthly spending as stated by the advisor in the conversations."))

        # ---- transactions
        txn_rows = []
        for i, t in enumerate(sorted(b.txns, key=lambda t: t.d), 1):
            tid = f"TXN-{n}-{i:04d}"
            amount = q2(t.qty * t.price)
            row = dict(transaction_id=tid, client_id=cid, account_id=acct_id[t.account], holding_id=hold_id[t.hkey],
                       instrument_id=inst_ids[hdef[t.hkey]["inst"]], transaction_date=t.d, transaction_type=t.ttype,
                       quantity=t.qty, price=t.price, amount=amount, currency=CURRENCY, is_external_flow=t.external,
                       group_id=f"{n}-{t.group}", description=t.note,
                       data_provenance=PROV_ANCHORED if t.ev else PROV_SYNTH,
                       provenance_note=("Decision/instruction anchored to the cited turn unless evidence_kind says 'explicit'; execution date, quantity and price are synthetic."
                                        if t.ev else "Synthetic transaction needed to make the ledger reconcile."))
            if t.ttype == "opening_balance":
                row["provenance_note"] = "Opening position at 2026-01-31 sized by the generator (synthetic) unless the holding has its own evidence."
            txn_rows.append(row)
            if t.ev:
                ev_rows.append(("transaction", tid, "transaction_context", t.ev))
        tables["portfolio_transactions"].extend(txn_rows)

        # ---- holdings (current, as of last month-end)
        last = MONTH_ENDS[-1]
        for h in spec["holdings"]:
            k = h["key"]
            inst = INSTRUMENTS[h["inst"]]
            q_end = b.snaps[last].get(k, ZERO)
            price = series(h["inst"])[6]
            avg = (b.cost[k] / b.acquired[k]) if b.acquired.get(k) else price
            hid = hold_id[k]
            anchored = bool(h["ev"])
            fields = sorted({_hfield(x) for x in h["ev"]})
            tables["portfolio_holdings"].append(dict(
                holding_id=hid, account_id=acct_id[h["acct"]], client_id=cid, instrument_id=inst_ids[h["inst"]],
                instrument_name=inst[0], instrument_type=inst[1], asset_class=inst[2], sector=inst[3], issuer=inst[4],
                quantity=q_end, purchase_price=q4(avg), purchase_date=b.first_buy[k], current_price=price,
                market_value=q2(q_end * price), currency=CURRENCY, valuation_date=last, liquidity_category=inst[5],
                holding_status="active" if q_end > 0 else "closed", closed_date=b.closed.get(k),
                data_provenance=PROV_ANCHORED if anchored else PROV_SYNTH,
                provenance_note=((f"Anchored fields: {', '.join(fields)}. " if anchored else "") +
                                 "Quantity, prices, cost basis and purchase date are synthetic; purchase_price is an average cost across lots (no tax-lot detail)."
                                 + (" " + h["note"] if h["note"] else ""))))
            for x in h["ev"]:
                ev_rows.append(("holding", hid, _hfield(x), x))

        # ---- snapshots
        sn = 0
        for d in MONTH_ENDS:
            for h in spec["holdings"]:
                k = h["key"]
                q = b.snaps[d].get(k, ZERO)
                if q <= 0:
                    continue
                sn += 1
                pr = price_on(h["inst"], d)
                tables["portfolio_snapshots"].append(dict(
                    snapshot_id=f"SNP-{n}-{d.strftime('%Y%m')}-{sn:03d}", client_id=cid, snapshot_date=d,
                    holding_id=hold_id[k], instrument_id=inst_ids[h["inst"]], account_id=acct_id[h["acct"]],
                    quantity=q, market_price=pr, market_value=q2(q * pr), currency=CURRENCY, data_provenance=PROV_SYNTH))

        # ---- goals
        for g in spec["goals"]:
            gid = f"GOL-{n}-{g['key']}{g['ver']}"
            anchored = bool(g["ev"])
            tables["financial_goals"].append(dict(
                goal_id=gid, goal_key=f"GK-{n}-{g['key']}", client_id=cid, goal_type=g["gtype"], goal_description=g["desc"],
                target_amount=g["amount"], target_date=g["tdate"], priority=g["prio"], confidence_level=g["conf"],
                current_status=g["status"], currency=CURRENCY, amount_provenance=g["aprov"],
                data_provenance=PROV_ANCHORED if anchored else PROV_SYNTH,
                provenance_note=(g["note"] + " " if g["note"] else "") + ("See evidence links; fields not listed there are synthetic." if anchored else "Synthetic goal."),
                valid_from=g["vfrom"], valid_to=g["vto"]))
            for x in g["ev"]:
                ev_rows.append(("goal", gid, x["field"] or "goal_description", x))

        # ---- liabilities
        for i, l in enumerate(spec["liabilities"], 1):
            lid = f"LIA-{n}-{i:02d}"
            row = _liability(cid, lid, l)
            tables["liabilities"].append(row)
            for x in l["ev"]:
                ev_rows.append(("liability", lid, x["field"] or "outstanding_amount", x))

        # ---- evidence rows (verified against the CSV)
        for etype, eid, dfield, e in ev_rows:
            evid.append(_evidence(conv, cid, etype, eid, dfield, e))

    for i, r in enumerate(sorted(evid, key=lambda r: (r["client_id"], r["entity_type"], r["entity_id"], r["field_name"], r["interaction_id"] or "", r["turn_index"] or 0)), 1):
        r["evidence_id"] = f"EVD-{i:04d}"
        tables["data_evidence_links"].append(r)
    tables["_warnings"] = [w for b in builds.values() for w in b.warnings]
    tables["_builds"] = builds
    return tables


# ---------------------------------------------------------------------------------------------- helpers
def _hfield(x: dict) -> str:
    if x.get("field"):
        return x["field"]
    return "market_value" if x["kind"] == "explicit" and "$" in (x["ex"] or "") else "instrument_name"


def _evidence(conv: ConvIndex, cid: str, etype: str, eid: str, dfield: str, e: dict) -> dict:
    if e["kind"] == "design_doc":
        return dict(client_id=cid, entity_type=etype, entity_id=eid, field_name=e["field"], source_file="docs/dataset_design.md",
                    interaction_id=None, turn_index=None, interaction_date=None, speaker=None, evidence_kind="design_doc",
                    excerpt=None, note=e["note"])
    iid = e["i"]
    r = conv.verify(cid, iid, e["t"], e["ex"])
    default_note = {"explicit": "Stated in the cited turn.", "derived": "Computed from the cited statement(s).",
                    "decision": "The cited turn records the decision/instruction this record implements; execution details are synthetic unless stated.",
                    "implied": "The cited turn only implies this; value details are synthetic."}[e["kind"]]
    return dict(client_id=cid, entity_type=etype, entity_id=eid, field_name=e.get("field") or dfield,
                source_file="data/conversations.csv", interaction_id=iid, turn_index=e["t"],
                interaction_date=r["interaction_datetime"][:10], speaker=r["speaker"], evidence_kind=e["kind"],
                excerpt=e["ex"], note=e.get("note") or default_note)


def _opening(b: ClientBuild) -> None:
    spec, cid = b.spec, b.cid
    d0 = MONTH_ENDS[0]
    fixed = ZERO
    qopen: dict[str, Decimal] = {}
    pct_total = 0.0
    for h in spec["holdings"]:
        inv = next(a for a in spec["accounts"] if a["key"] == h["acct"])["investable"]
        px = price_on(h["inst"], d0)
        if h["qty"] is not None:
            qopen[h["key"]] = q4(h["qty"])
            if inv:
                fixed += q2(qopen[h["key"]] * px)
        elif h["usd"] is not None:
            qopen[h["key"]] = q4(Decimal(h["usd"]) / px)
            if inv:
                fixed += Decimal(h["usd"])
        elif h["pct"] is not None:
            pct_total += h["pct"]
    remaining = Decimal(spec["t0"]) - fixed
    for h in spec["holdings"]:
        if h["pct"] is not None:
            qopen[h["key"]] = q4(remaining * Decimal(str(h["pct"] / pct_total)) / price_on(h["inst"], d0))
    rng = random.Random(f"{SEED}-{cid}-cost")
    for h in spec["holdings"]:
        k = h["key"]
        b.qty[k] = qopen.get(k, ZERO)
        b.cost[k] = ZERO
        b.acquired[k] = ZERO
        if k in qopen:
            ac = INSTRUMENTS[h["inst"]][2]
            model = INSTRUMENTS[h["inst"]][6]
            yrs = max((d0 - h["bought"]).days / 365.25, 0.05)
            px = float(price_on(h["inst"], d0))
            if model in ("cash",) or h["inst"] in ("stable", "dcomp"):
                cp = Decimal(str(px))
            else:
                g = GROWTH.get(ac, 0.03)
                cp = Decimal(str(round(px / ((1 + g) ** yrs) * (1 + rng.uniform(-0.04, 0.04)), 4)))
                if h["inst"] in ("tb6", "tn1", "tn2"):
                    cp = Decimal(str(round(px * (1 - 0.004 * rng.random()), 4)))
            b.cost[k] = qopen[k] * cp
            b.acquired[k] = qopen[k]
            b.first_buy[k] = h["bought"]
            b.open_qty[k] = qopen[k]
            b.txns.append(Txn(cid, h["acct"], k, d0, "opening_balance", qopen[k], price_on(h["inst"], d0), False, "OPEN",
                              "Opening position at the first snapshot date.", None))


def _run_events(b: ClientBuild) -> None:
    spec, cid = b.spec, b.cid
    hdef = {h["key"]: h for h in spec["holdings"]}
    evs: list[tuple[date, int, dict]] = []
    order = 0
    for e in spec["events"]:
        order += 1
        if e["t"] == "monthly":
            for d in MONTH_ENDS:
                if D(e["start"]) <= d <= D(e["end"]):
                    evs.append((d, order, dict(e, t="ext_in", d=d.isoformat())))
        elif e["t"] == "monthly_draw":
            for m in range(D(e["start"]).month, D(e["end"]).month + 1):
                d = date(2026, m, D(e["start"]).day)
                evs.append((d, order, dict(e, t="draw", d=d.isoformat())))
        else:
            evs.append((D(e["d"]), order, e))
    evs.sort(key=lambda x: (x[0], x[1]))
    pending = list(MONTH_ENDS)
    for d, _, e in evs:
        while pending and pending[0] < d:
            _snap(b, pending.pop(0))
        _apply(b, hdef, d, e)
    while pending:
        _snap(b, pending.pop(0))


def _snap(b: ClientBuild, d: date) -> None:
    b.snaps[d] = {k: v for k, v in b.qty.items()}


def _g(b: ClientBuild, tag: str) -> str:
    b.gseq += 1
    return f"{tag}-{b.gseq:03d}"


def _px(b, hdef, k, d):
    return price_on(hdef[k]["inst"], d)


def _post(b, hdef, d, k, ttype, qty, ext, grp, note, ev, price=None):
    px = price if price is not None else _px(b, hdef, k, d)
    if b.qty[k] + qty < 0:
        raise ValueError(f"{b.cid} {k} would go negative on {d}")
    b.qty[k] += qty
    if qty > 0 and ttype != "transfer_in":
        b.cost[k] += qty * px
        b.acquired[k] += qty
        b.first_buy.setdefault(k, d)
    b.txns.append(Txn(b.cid, hdef[k]["acct"], k, d, ttype, qty, px, ext, grp, note, ev))
    if b.qty[k] == 0:
        b.closed[k] = d
    else:
        b.closed.pop(k, None)


def _sell_amount(b, hdef, d, k, amount, grp, note, ev, ttype="sell", ext=False):
    px = _px(b, hdef, k, d)
    qty = q4(Decimal(amount) / px)
    if qty > b.qty[k]:
        b.warnings.append(f"{b.cid} {d} {k}: requested {amount} exceeds holding; sold all")
        qty = b.qty[k]
    _post(b, hdef, d, k, ttype, -qty, ext, grp, note, ev)
    return q2(qty * px)


def _buy_amount(b, hdef, d, k, amount, grp, note, ev, ttype="buy", ext=False):
    px = _px(b, hdef, k, d)
    qty = q4(Decimal(amount) / px)
    _post(b, hdef, d, k, ttype, qty, ext, grp, note, ev)
    return q2(qty * px)


def _apply(b: ClientBuild, hdef: dict, d: date, e: dict) -> None:
    t, ev, note = e["t"], e.get("ev"), e.get("note", "")
    if t == "xfer":
        grp = _g(b, "XF")
        proceeds = _sell_amount(b, hdef, d, e["frm"], e["amount"], grp, note, ev)
        _buy_amount(b, hdef, d, e["to"], proceeds, grp, note, ev)
    elif t == "ext_in":
        _buy_amount(b, hdef, d, e["key"], e["amount"], _g(b, "EX"), note, ev, ttype=e["etype"], ext=True)
    elif t == "ext_out":
        _sell_amount(b, hdef, d, e["key"], e["amount"], _g(b, "EX"), note, ev, ttype=e["etype"], ext=True)
    elif t == "ext_in_qty":
        _post(b, hdef, d, e["key"], e["etype"], q4(e["qty"]), True, _g(b, "EX"), note, ev)
    elif t == "sell_qty_to":
        grp = _g(b, "SQ")
        px = _px(b, hdef, e["key"], d)
        _post(b, hdef, d, e["key"], "sell", -q4(e["qty"]), False, grp, note, ev)
        _buy_amount(b, hdef, d, e["to"], q2(q4(e["qty"]) * px), grp, note, ev)
    elif t == "sell_frac_to":
        grp = _g(b, "SF")
        px = _px(b, hdef, e["key"], d)
        qty = q4(b.open_qty[e["base"]] * Decimal(str(e["frac"])))
        _post(b, hdef, d, e["key"], "sell", -qty, False, grp, note, ev)
        _buy_amount(b, hdef, d, e["to"], q2(qty * px), grp, note, ev)
    elif t == "sell_all_to":
        grp = _g(b, "SA")
        px = _px(b, hdef, e["key"], d)
        qty = b.qty[e["key"]]
        _post(b, hdef, d, e["key"], "sell", -qty, False, grp, note, ev)
        _buy_amount(b, hdef, d, e["to"], q2(qty * px), grp, note, ev)
    elif t == "move_all":
        grp = _g(b, "MV")
        k, to = e["frm"], e["to"]
        qty = b.qty[k]
        px_f, px_t = _px(b, hdef, k, d), _px(b, hdef, to, d)
        avg = b.cost[k] / b.acquired[k]
        _post(b, hdef, d, k, "transfer_out", -qty, False, grp, note, ev)
        b.qty[to] += qty
        b.cost[to] += qty * avg
        b.acquired[to] += qty
        b.first_buy.setdefault(to, b.first_buy[k])
        b.txns.append(Txn(b.cid, hdef[to]["acct"], to, d, "transfer_in", qty, px_t, False, grp, note, ev))
    elif t == "sweep":
        grp = _g(b, "SW")
        val = q2(b.qty[e["frm"]] * _px(b, hdef, e["frm"], d))
        amt = val - Decimal(e["keep"])
        if amt > 0:
            proceeds = _sell_amount(b, hdef, d, e["frm"], amt, grp, note, ev)
            _buy_amount(b, hdef, d, e["to"], proceeds, grp, note, ev)
    elif t == "draw":
        grp = _g(b, "DR")
        proceeds = _sell_amount(b, hdef, d, e["frm"], e["amount"], grp, note, ev)
        _buy_amount(b, hdef, d, e["cash"], proceeds, grp, note, ev)
        _sell_amount(b, hdef, d, e["cash"], proceeds, grp, note, ev, ttype="withdrawal", ext=True)
    elif t == "secondary":
        grp = _g(b, "SE")
        k = e["key"]
        px = _px(b, hdef, k, d)
        qty = q4(Decimal(e["gross"]) / px)
        _post(b, hdef, d, k, "sell", -qty, False, grp, note, ev)
        gross = q2(qty * px)
        spent = ZERO
        plan = []
        for key, how in e["parts"]:
            if how == "rest":
                continue
            if isinstance(how, tuple):
                cur = q2(b.qty[key] * _px(b, hdef, key, d))
                amt = max(Decimal(how[1]) - cur, ZERO)
            else:
                amt = Decimal(how)
            plan.append((key, amt))
            spent += amt
        for key, how in e["parts"]:
            if how == "rest":
                plan.append((key, gross - spent))
        for key, amt in plan:
            _buy_amount(b, hdef, d, key, amt, grp, note + " (proceeds allocation)", ev, ttype="secondary_sale_proceeds", ext=True)
    else:
        raise ValueError(f"unknown event type {t}")


def _add_months(d: date, n: int) -> date:
    y, m = divmod(d.year * 12 + d.month - 1 + n, 12)
    return eom(y, m + 1)


def _liability(cid: str, lid: str, l: dict) -> dict:
    as_of = PROFILE_AS_OF
    bal, rate, pay = l["ref_balance"], l["rate"], l["payment"]
    maturity = D(l["maturity"]) if l["maturity"] else None
    note = []
    if bal is not None:
        r = rate / 12
        ref = D(l["ref_date"])
        k = (as_of.year - ref.year) * 12 + as_of.month - ref.month
        if pay is None:  # derive payment from balance, rate and stated maturity
            n = (maturity.year - as_of.year) * 12 + maturity.month - as_of.month
            pay = round(bal * r / (1 - (1 + r) ** -n)) if r else round(bal / n)
            note.append("monthly_payment derived from balance, rate and stated maturity (synthetic amortisation).")
        b_now = bal
        for _ in range(max(k, 0)):
            b_now = b_now * (1 + r) - pay
        bal_out = round(b_now, 2)
        if maturity is None:
            n = _nper(bal_out, r, pay)
            maturity = _add_months(as_of, n)
            note.append("maturity_date derived from balance, rate and monthly_payment.")
        if k > 0:
            note.append(f"Balance {bal:,.0f} stated at {l['ref_date']} and amortised forward {k} months to {as_of}.")
    else:
        bal_out = None
    return dict(liability_id=lid, client_id=cid, liability_type=l["type"], description=l["desc"], outstanding_amount=bal_out,
                interest_rate=rate, monthly_payment=pay, maturity_date=maturity, as_of_date=as_of, currency=CURRENCY,
                data_provenance=PROV_ANCHORED if l["ev"] else PROV_SYNTH,
                provenance_note=" ".join(note) + (" monthly_payment is synthetic." if l.get("pay_synth") and bal is not None else "") +
                                " Fields not in the evidence links are synthetic.")


def _nper(bal: float, r: float, pay: float) -> int:
    import math
    if r == 0:
        return math.ceil(bal / pay)
    return math.ceil(-math.log(1 - r * bal / pay) / math.log(1 + r))


def _ref_tables(tables, inst_ids):
    from .market import ASSET_CLASSES, LIQUIDITY_CATEGORIES
    for k, v in ASSET_CLASSES.items():
        tables["ref_asset_classes"].append(dict(asset_class=k, description=v))
    for k, (desc, rank) in LIQUIDITY_CATEGORIES.items():
        tables["ref_liquidity_categories"].append(dict(liquidity_category=k, description=desc, liquidity_rank=rank))
    for k, iid in inst_ids.items():
        n = INSTRUMENTS[k]
        anchored = k in ANCHORED_INSTRUMENTS
        tables["ref_instruments"].append(dict(
            instrument_id=iid, instrument_name=n[0], instrument_type=n[1], asset_class=n[2], sector=n[3], issuer=n[4],
            currency=CURRENCY, liquidity_category=n[5], data_provenance=PROV_ANCHORED if anchored else PROV_SYNTH,
            provenance_note=("Issuer/company name appears in conversations.csv; the instrument itself and all prices are fictional/synthetic."
                             if anchored else "Fictional instrument and fictional issuer; prices from the shared synthetic market path.")))
    for i, d in enumerate(MONTH_ENDS):
        tables["ref_market_path"].append(dict(month_end=d, equity_return=market.EQUITY[i], bond_return=market.BOND[i],
                                              note="Driver for instrument price paths; instruments apply a class beta plus seeded noise."))
