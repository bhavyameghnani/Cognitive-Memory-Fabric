"""Deterministic scenario engine.

Turns hand-authored client storylines into full meeting transcripts while
keeping per-client state (preferences, open action items, prior-meeting log)
so that later meetings can refer back to earlier ones.

No LLM is used anywhere in this package: all dialogue is authored text,
assembled and varied by seeded Python logic.
"""
from __future__ import annotations

import datetime as dt
import random
import re

from .voice import ADVISOR as VA, CLIENT as VC

SPEAKER = {"A": "Advisor", "C": "Client"}

# Cumulative equity-market index (percent vs 1 Jan 2026) used so portfolio
# commentary is consistent across all clients on a given date.  Fictional.
_MARKET_KEYS = [
    (dt.date(2026, 1, 1), 0.0), (dt.date(2026, 1, 31), 2.5),
    (dt.date(2026, 2, 15), 1.0), (dt.date(2026, 2, 28), -2.5),
    (dt.date(2026, 3, 15), -7.0), (dt.date(2026, 3, 31), -5.0),
    (dt.date(2026, 4, 30), -1.0), (dt.date(2026, 5, 31), 2.0),
    (dt.date(2026, 6, 30), 3.0),
]

MARKET_NOTE = {
    1: "a strong start to the year for equities, led by large technology companies",
    2: "a sharp technology-led sell-off late in the month",
    3: "continued volatility, with a few days when the index fell more than two percent",
    4: "a partial recovery, although bond yields swung around on inflation data",
    5: "central bank talk about possible rate cuts, which helped both stocks and bonds",
    6: "choppy trading that still left markets modestly higher for the half-year",
}


def market_index(d: dt.date) -> float:
    for (d0, v0), (d1, v1) in zip(_MARKET_KEYS, _MARKET_KEYS[1:]):
        if d0 <= d <= d1:
            span = (d1 - d0).days or 1
            return v0 + (v1 - v0) * (d - d0).days / span
    return _MARKET_KEYS[-1][1]


def parse(text: str):
    turns = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        tag, _, body = line.partition(": ")
        if tag not in SPEAKER or not body:
            raise ValueError(f"Bad script line: {line!r}")
        turns.append((SPEAKER[tag], body.strip()))
    return turns


def money(x: float) -> str:
    if x >= 1_000_000:
        return f"${x / 1_000_000:.2f} million"
    return f"${round(x, -3):,.0f}"


def oxford(items):
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def cap(s: str) -> str:
    return s[:1].upper() + s[1:]


_PROPER = {"Yuki", "Ren", "Tobi", "Folake", "Frank", "Brian", "Mateo", "Amélie", "Lucia", "Hugo", "Maplewood",
           "Social", "Medicare", "Vantora", "Lakeshore", "Hannah", "Mariko", "Kenji", "Northgate", "Lisbon", "Moreau",
           "Holloway", "Samuel", "Dorothy", "Colleen", "Camille", "Sofia", "Rebecca", "Grace", "Peg", "Lucas",
           "Ethan", "Dennis", "Luis", "Mei", "Daniel", "Priya", "Elena", "David", "Mateo's"}


def lower_first(s: str) -> str:
    word = s.split(" ")[0].split("'")[0].rstrip(",.?")
    if word in _PROPER:
        return s
    return s[:1].lower() + s[1:]


def date_text(d) -> str:
    return f"{d:%B} {d.day}"


class ClientRun:
    """Persistent per-client state across the six months."""

    def __init__(self, profile: dict, rng: random.Random):
        self.p = profile
        self.rng = rng
        self.F = dict(profile["facts"])
        self.S = dict(profile["state"])
        self.actions: dict = {}
        self.used: dict = {}
        self.casual_used: set = set()
        self.history: list = []  # one record per completed meeting


class Ctx:
    def __init__(self, client: ClientRun, date: dt.datetime, channel: str,
                 context: str, next_dt, step: int = 0):
        self.client = client
        self.rng = client.rng
        self.date = date
        self.channel = channel
        self.context = context
        self.next_dt = next_dt
        self.step = step
        self.prev = client.history[-1] if client.history else None
        self.log: list = []
        self.tags: set = set()
        self.F = client.F
        self.S = client.S
        self.actions = client.actions
        self.V = VC[client.p["cstyle"]]
        self.AV = VA[client.p["astyle"]]


TOKEN = re.compile(r"<<(\w+)(?::(-?\d+))?>>")


def render(ctx: Ctx, text: str) -> str:
    """Resolve <<ago:N>> (explicit date N days before the meeting), <<prev>> (previous meeting date)."""
    def sub(m):
        name, n = m.group(1), m.group(2)
        if name == "ago":
            d = ctx.date.date() - dt.timedelta(days=int(n))
            return f"{d:%A}, {date_text(d)}"
        if name == "prev":
            return date_text(ctx.prev["date"])
        if name == "today":
            return f"{ctx.date:%A}, {date_text(ctx.date)}"
        raise ValueError(m.group(0))
    return TOKEN.sub(sub, text)


def pick(ctx: Ctx, key: str, options):
    used = ctx.client.used.setdefault(key, set())
    local = ctx.__dict__.setdefault("_local_used", {}).setdefault(key, set())
    avail = [i for i in range(len(options)) if i not in used and i not in local]
    if not avail:
        used.clear()
        avail = [i for i in range(len(options)) if i not in local] or list(range(len(options)))
    i = ctx.rng.choice(avail)
    used.add(i)
    local.add(i)
    return options[i]


def vpick(ctx: Ctx, key: str, **kw):
    """Pick a client-voice line for `key`, avoiding recent repeats for this client."""
    line = pick(ctx, "v_" + key, ctx.V[key])
    return line.format(**kw) if kw else line


# --------------------------------------------------------------------------
# Opening and closing
# --------------------------------------------------------------------------

def _daypart(d: dt.datetime) -> str:
    return "morning" if d.hour < 12 else "afternoon"


def opening(ctx: Ctx):
    f = ctx.F
    first, adv = f["first"], f["adv_first"]
    part = _daypart(ctx.date)
    ctxl = lower_first(ctx.context).rstrip("?. ")
    ctxc = cap(ctxl)
    prevtxt = date_text(ctx.prev["date"]) if ctx.prev else "the new year"
    g = pick(ctx, "greet", ctx.AV["greet"]).format(part=part, first=first, topic=ctxl, prev=prevtxt)
    if ctx.channel == "telephone":
        g = f"It's {adv}. " + g
    if ctx.prev:
        pc = lower_first(ctx.prev["context"]).rstrip("?. ")
        pd = date_text(ctx.prev["date"])
        ag = pick(ctx, "agenda", [
            f"Last time, on {pd}, we focused on {pc}. Today's focus is {ctxl}.",
            f"We last spoke on {pd}, about {pc}. For today I have {ctxl}.",
            f"On {pd} we covered {pc}; today the topic is {ctxl}.",
            f"Since {pd}, when we focused on {pc}, the next topic is {ctxl}.",
        ])
        if ctxl in g or pd in g:
            ag = f"Today the topic is {ctxl}." if pd in g else ag.split(". ")[0].rstrip(".") + "."
        a1 = f"{g} {ag}"
    else:
        a1 = (f"{g} Since this is the start of our review cycle, I'd like to take stock of where things are and what you "
              f"want from this year. The agenda I have is {ctxl}.")
    c1 = vpick(ctx, "agenda", topic=ctxl, Topic=ctxc)
    return [("Advisor", a1), ("Client", c1)]


def closing(ctx: Ctx):
    L = ctx.log
    by = {k: [t for kk, t in L if kk == k] for k in
          ("decision", "plan", "tentative", "deferred", "correction", "a_action", "c_action")}
    first = ctx.F["first"]
    s1 = []
    for t in by["correction"]:
        s1.append(f"I've corrected my own record: {t}.")
    for t in by["decision"]:
        s1.append(f"You've decided {t}.")
    for t in by["plan"]:
        s1.append(f"The working plan I'm recording is {t}.")
    for t in by["tentative"]:
        s1.append(f"You're still weighing {t}, which I've recorded as an idea and not an instruction.")
    for t in by["deferred"]:
        s1.append(f"We agreed to hold off on {t}.")
    nothing = not s1
    if nothing:
        cl = lower_first(ctx.context)
        s1.append(pick(ctx, "nochange", [
            f"Nothing in your plan changes today; {cl} was a check that nothing is drifting.",
            f"No decisions were needed today. We went through {cl} and everything is on track.",
            f"The plan stays as it is. Today was about {cl}, and nothing we saw requires action.",
        ]))
    acts = []
    if by["a_action"]:
        acts.append("From my side, I'll " + oxford(by["a_action"]) + ".")
    if by["c_action"]:
        acts.append("From your side, you'll " + oxford(by["c_action"]) + ".")
    if ctx.next_dt:
        nxt = pick(ctx, "nxt", [
            f"Let's pencil in {ctx.next_dt:%A}, {date_text(ctx.next_dt)} for our next meeting.",
            f"Does {ctx.next_dt:%A}, {date_text(ctx.next_dt)} work for the next one?",
            f"I'll book us for {ctx.next_dt:%A}, {date_text(ctx.next_dt)}, if that suits you.",
        ])
    else:
        nxt = ("That brings us to the end of this review period. My assistant will contact you in July "
               "to schedule the next full review.")
    portal = pick(ctx, "portal", ["", "", "I'll put a written summary in the portal today.",
                                  "A written recap will be in the portal shortly; tell me if anything looks off.",
                                  "You'll have my notes in the portal this afternoon."])
    nuance = bool(by["tentative"] or by["deferred"])
    intro = pick(ctx, "sum_intro", ctx.AV["sum_intro"])
    conf = vpick(ctx, "confirm_n" if nuance else "confirm")
    turns = []
    if len(s1) + len(acts) <= 3:
        body = " ".join(s1 + acts + [nxt, portal]).strip()
        turns.append(("Advisor", f"{intro} {body}"))
        turns.append(("Client", conf))
    else:
        turns.append(("Advisor", f"{intro} " + " ".join(s1)))
        turns.append(("Client", conf))
        turns.append(("Advisor", " ".join(acts + [nxt, portal]).strip()))
        if ctx.rng.random() < 0.5:
            turns.append(("Client", vpick(ctx, "thanks")))
    if ctx.rng.random() < 0.12 and turns[-1][0] == "Client":
        turns.append(("Advisor", pick(ctx, "signoff", ctx.AV["signoff"]).format(first=first)))
    return turns
