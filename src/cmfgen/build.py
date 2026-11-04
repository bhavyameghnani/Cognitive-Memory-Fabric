"""Scheduling and transcript assembly for all clients.

Revision 2: scheduling is unchanged from v1 (separate seeds), so dates, times, channels and interaction IDs are
identical.  Content is assembled from the storyline modules plus the overlay in revisions.py.
"""
from __future__ import annotations

import datetime as dt
import random

from . import engine
from .beats import G
from .clients import c1, c2, c3, c4, c5, c6
from .engine import ClientRun, Ctx, closing, market_index, opening, parse
from .revisions import apply_revisions

MODULES = [c1, c2, c3, c4, c5, c6]
CSTYLE = ["K", "R", "S", "G", "L", "P"]
ASTYLE = {"ADVISOR-001": "analytical", "ADVISOR-002": "direct", "ADVISOR-003": "warm"}
HOLIDAYS = {dt.date(2026, 1, 1), dt.date(2026, 1, 19), dt.date(2026, 2, 16),
            dt.date(2026, 5, 25), dt.date(2026, 6, 19)}
MIN_TURNS, SOFT_MAX = 12, 25


def _business_days(month):
    d = dt.date(2026, month, 1)
    out = []
    while d.month == month:
        if d.weekday() < 5 and d not in HOLIDAYS:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def schedule_dates(months, rng):
    """Pick distinct business days per month with irregular spacing (>= 2 days apart)."""
    for _ in range(500):
        chosen = []
        for m in sorted(set(months)):
            n = months.count(m)
            days = _business_days(m)
            picks = []
            if m == 1:
                picks.append(rng.choice([d for d in days if d.day <= 14]))
            if m == 6:
                picks.append(rng.choice([d for d in days if d.day >= 18]))
            pool = [d for d in days if d not in picks]
            while len(picks) < n:
                picks.append(rng.choice(pool))
                pool = [d for d in pool if d not in picks]
            chosen += picks
        chosen.sort()
        if len(set(chosen)) == len(chosen) and all((b - a).days >= 2 for a, b in zip(chosen, chosen[1:])):
            return chosen
    raise RuntimeError("could not schedule")


def assign_times(meetings, rng):
    taken = {}
    for m in sorted(meetings, key=lambda x: (x["date"], x["client_idx"])):
        pool = list(m["hours"]) + [9, 10, 11, 13, 14, 15, 16]
        rng.shuffle(pool[:len(m["hours"])])
        chosen = None
        for h in pool:
            minute = rng.choice([0, 0, 30, 15])
            t = h * 60 + minute
            if all(abs(t - o) >= 90 for o in taken.get((m["advisor_id"], m["date"]), [])):
                chosen = t
                break
        if chosen is None:
            raise RuntimeError("no free slot")
        taken.setdefault((m["advisor_id"], m["date"]), []).append(chosen)
        m["dt"] = dt.datetime.combine(m["date"], dt.time(chosen // 60, chosen % 60))


def _update_value(run: ClientRun, date: dt.date):
    prev_idx = run.S.get("_idx", 0.0)
    now = market_index(date)
    gap_months = ((date - run.S.get("_date", dt.date(2026, 1, 1))).days) / 30.0
    eq = run.S["equity"] / 100
    mret = (1 + now / 100) / (1 + prev_idx / 100) - 1
    ret = eq * run.p.get("beta", 1.0) * mret + (1 - eq) * 0.0025 * gap_months
    run.S["_ret"] = ret
    run.S["value"] = run.S["value"] * (1 + ret)
    run.S["_idx"], run.S["_date"] = now, date


# ---- joining segments --------------------------------------------------------------------------

C_CONNECT = ["Also, ", "And separately, ", "One more thing. "]


def join(ctx, turns, new, label=None, plain=False):
    """Concatenate segments.  Same-speaker neighbours are merged into one turn using a transition that
    names the new topic; no stand-alone filler turns are ever inserted."""
    if not new:
        return turns
    if turns and turns[-1][0] == new[0][0]:
        spk, prev_text = turns[-1]
        if spk == "Advisor" and plain:
            merged = (spk, prev_text + " " + new[0][1])
        elif spk == "Advisor":
            tr = (engine.pick(ctx, "trans", ctx.AV["trans"]).format(label=label) if label
                  else engine.pick(ctx, "trans0", ctx.AV["trans_nolabel"]))
            merged = (spk, prev_text + " " + tr + " " + new[0][1])
        else:
            merged = (spk, prev_text + " " + new[0][1])  # verbatim: scripted client lines are never rewritten
        return turns[:-1] + [merged] + new[1:]
    return turns + new


def merge_same_speaker(turns):
    out = []
    for spk, text in turns:
        if out and out[-1][0] == spk:
            out[-1] = (spk, out[-1][1] + " " + text)
        else:
            out.append((spk, text))
    return out


def casual_candidate(run, step, date):
    items, wins = run.p["casual"], run.p["casual_windows"]
    for i, (w0, w1) in enumerate(wins):
        if i in run.casual_used:
            continue
        ok = (w0 <= step <= w1) if isinstance(w0, int) else (w0 <= date.isoformat() <= w1)
        if ok:
            return i
    return None


def build(seed: int = 20260101):
    runs, sched = [], []
    for i, mod in enumerate(MODULES):
        p = mod.PROFILE
        p["cstyle"], p["astyle"] = CSTYLE[i], ASTYLE[p["advisor_id"]]
        steps = apply_revisions(i, mod.STEPS)
        assert len(steps) == len(p["months"]) == 25, (p["client_id"], len(steps), len(p["months"]))
        rng = random.Random(seed * 100 + i)
        run = ClientRun(p, rng)
        run.steps = steps
        dates = schedule_dates(p["months"], random.Random(seed * 1000 + i))
        runs.append(run)
        for k, d in enumerate(dates):
            sched.append(dict(date=d, client_idx=i, step=k, advisor_id=p["advisor_id"], hours=p["hours"]))
    assign_times(sched, random.Random(seed + 7))
    by_client = {i: sorted([s for s in sched if s["client_idx"] == i], key=lambda s: s["step"]) for i in range(6)}

    meetings = []
    for i, run in enumerate(runs):
        for k, slot in enumerate(by_client[i]):
            context, beats, channel = run.steps[k]
            nxt = by_client[i][k + 1]["dt"] if k + 1 < 25 else None
            ctx = Ctx(run, slot["dt"], channel, context, nxt, step=k + 1)
            _update_value(run, slot["date"])
            segs = []
            op = list(opening(ctx))
            for bi, b in enumerate(beats):
                bt = b(ctx)
                if bi == 0 and ctx.prev and bt and bt[0][0] == "Client":
                    op = op[:-1]  # the client's own story answers the advisor's opening directly
                segs.append((bt, getattr(b, "label", None)))
            used = getattr(ctx, "gnames", set())
            extras = [G(n) for n in ("portfolio", "cashflow", "circumstances") if n not in used and (n != "circumstances" or ctx.prev)]
            # casual remark: dated/step-windowed, each used once, placed right after an advisor-ended segment
            ci = casual_candidate(run, k + 1, slot["date"])
            casual_seg = None
            est = len(op) + sum(len(s) for s, _ in segs) + 2
            if ci is not None and est <= SOFT_MAX and run.rng.random() < 0.7:
                casual_seg = parse(run.p["casual"][ci])
                run.casual_used.add(ci)

            def assemble(segs_):
                sg = list(segs_)
                if casual_seg:
                    spots = [j for j in range(1, len(sg) + 1) if sg[j - 1][0][-1][0] == "Advisor"]
                    spots = [j for j in spots if j < len(sg)] or spots
                    if spots:
                        sg.insert(spots[(k * 7 + ci) % len(spots)] if ci is not None else spots[0], (casual_seg, None))
                t = op
                for bt, label in sg:
                    t = join(ctx, t, bt, label)
                t = join(ctx, t, closing(ctx), plain=True)
                return merge_same_speaker(t)

            turns = assemble(segs)
            while len(turns) < MIN_TURNS and extras:
                e = extras.pop(0)
                segs.append((e(ctx), e.label))
                turns = assemble(segs)
            meetings.append(dict(client=run.p, dt=slot["dt"], channel=channel, context=context,
                                 turns=turns, tags=sorted(ctx.tags), log=list(ctx.log), step=k + 1))
            run.history.append(dict(date=slot["dt"].date(), context=context, log=list(ctx.log)))
    meetings.sort(key=lambda m: (m["dt"], m["client"]["client_id"]))
    for n, m in enumerate(meetings, 1):
        m["interaction_id"] = f"INT-{n:03d}"
    ledger = {}
    for run in runs:
        ledger[run.p["client_id"]] = {
            aid: dict(owner=a["owner"], description=a["desc"], opened=str(a["opened"]),
                      status=a["status"], closed=str(a["closed"]) if a["closed"] else None,
                      deferrals=a["deferrals"])
            for aid, a in run.actions.items()}
    return meetings, ledger
