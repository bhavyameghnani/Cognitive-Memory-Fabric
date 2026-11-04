"""Beat library: building blocks a storyline step is composed from.

S(...)      hand-authored scripted segment (with optional logged outcomes)
Check(...)  advisor/client follow-up on an earlier action item
G(name, ..) parameterised generic segment (portfolio review, doc request ...)
"""
from __future__ import annotations

from .engine import (MARKET_NOTE, cap, market_index, money, parse, pick, lower_first, render, vpick)
from .explain import EXPLAIN, LEAD


# ---------------------------------------------------------------- scripted
def S(text, log=(), open=(), close=(), set=None, setF=None, tags=()):
    turns = parse(text)

    def run(ctx):
        for kind, t in log:
            ctx.log.append((kind, t))
        for aid, owner, desc in open:
            ctx.actions[aid] = dict(owner=owner, desc=desc, opened=ctx.date.date(),
                                    status="open", deferrals=0, closed=None)
            ctx.actions[aid]["mentioned_today"] = True
        for aid in close:
            a = ctx.actions[aid]
            a["status"] = "done"
            a["closed"] = ctx.date.date()
            a["mentioned_today"] = True
        if set:
            ctx.S.update(set)
        if setF:
            ctx.F.update(setF)
        ctx.tags.update(tags)
        return [(spk, render(ctx, t)) for spk, t in turns]
    return run


# ------------------------------------------------------------ action checks
def Check(aid, outcome, text, tags=()):
    assert outcome in ("done", "pending")

    def run(ctx):
        a = ctx.actions[aid]
        d = a["desc"]
        txt = render(ctx, text)
        a["mentioned_today"] = True
        ctx.tags.update(tags)
        ctx.tags.add("follow_up")
        if a["owner"] == "A":
            if outcome == "done":
                a["status"], a["closed"] = "done", ctx.date.date()
                a1 = pick(ctx, "chkA_done", [
                    f"Before we go further, I owe you something from last time. I said I would {d}. {txt}",
                    f"Let me close out an item from our last conversation first. I had taken the action to {d}. {txt}",
                ])
                c1 = pick(ctx, "chkA_done_c", [
                    "Thanks, that helps. It's clearer than I expected.",
                    "Good, I'm glad that's done. That was on my mind.",
                    "Okay. That gives me something concrete to react to.",
                ])
                return [("Advisor", a1), ("Client", c1)]
            a["deferrals"] += 1
            ctx.log.append(("a_action", f"finish the work to {d} and bring it to our next meeting"))
            a1 = pick(ctx, "chkA_pend", [
                f"I need to be upfront with you. I committed to {d}, and I haven't finished it. {txt} I'll have it for you at our next meeting.",
                f"One honest update: the item where I said I'd {d} isn't complete yet. {txt} I'd rather tell you than hand over something rushed.",
            ])
            c1 = pick(ctx, "chkA_pend_c", [
                "Okay, thanks for telling me. Please don't let it slip, though.",
                "I appreciate the honesty. Just let me know if the timing changes again.",
            ])
            return [("Advisor", a1), ("Client", c1)]
        # client-owned
        if outcome == "done":
            a["status"], a["closed"] = "done", ctx.date.date()
            a1 = pick(ctx, "chkC_done", [
                f"On the item from last time, did you manage to {d}?",
                f"Quick follow-up from our last meeting: were you able to {d}?",
            ])
            a2 = pick(ctx, "chkC_done_a", ctx.AV["received"])
            return [("Advisor", a1), ("Client", txt), ("Advisor", a2)]
        a["deferrals"] += 1
        ctx.log.append(("c_action", d))
        a1 = pick(ctx, "chkC_pend", [
            f"On the item from last time, you were going to {d}. Where does that stand?",
            f"Let me check in on something. Last time we agreed you would {d}. How is that going?",
        ])
        a2 = pick(ctx, "chkC_pend_a", ctx.AV["chase"])
        c2 = vpick(ctx, "commit")
        return [("Advisor", a1), ("Client", txt), ("Advisor", a2), ("Client", c2)]
    return run


# ------------------------------------------------------------------ generic
def _ret_phrase(ctx):
    r = ctx.S.get("_ret", 0.0) * 100
    if abs(r) < 0.3:
        return "roughly flat since we last looked"
    return f"{'up' if r > 0 else 'down'} about {abs(r):.1f} percent since we last looked"


def g_market(ctx):
    m = MARKET_NOTE[ctx.date.month]
    lvl = ctx.S["anxiety"]
    eq = ctx.S["equity"]
    drawdown = money(ctx.S["value"] * eq / 100 * 0.20)
    lead = pick(ctx, "mk_lead", [
        f"Let's check in on the markets. The month has brought {m}. How is that sitting with you?",
        f"A quick word on the markets, since there has been {m}. How are you feeling about it?",
        f"I want to ask about the markets, because there has been {m}. What's your reaction?",
    ])
    if lvl == "low":
        q = pick(ctx, "mk_q_low", [
            "Honestly, I'm not losing sleep over it. If anything, I'm tempted to see it as an opportunity.",
            "It doesn't bother me much. Is there any reason it should?",
            "I feel oddly calm about it. Am I missing something?",
        ])
        ans = pick(ctx, "mk_a_low", [
            f"I like the calm, and your history supports it. To keep it grounded: at {eq} percent equities, a twenty percent market fall would take roughly {drawdown} off the portfolio. If you're comfortable with that number, then we're consistent.",
            f"That steadiness is useful. The caution I'd add is that it's easy to be calm when markets are kind. A twenty percent fall at your current allocation is about {drawdown}, so let's keep checking that this still feels acceptable.",
            f"You may not be missing anything. Being relaxed in a mild pullback is different from being relaxed in a deep one, which at your mix would cost around {drawdown}. We'll keep an eye on how you feel as it develops.",
        ])
        cr = pick(ctx, "mk_c_low", [
            "I can live with that number. Let's not change anything.",
            "That's fair. I'll tell you if my feelings change.",
            "Understood. As long as the plan holds, I'm fine.",
        ])
    elif lvl == "mid":
        q = vpick(ctx, "mk_q_mid")
        ans = pick(ctx, "mk_a_mid", [
            f"It's understandable. My suggestion is not to act on a month of headlines. Your allocation was designed for periods like this, and a twenty percent equity decline would be about {drawdown}. What I do want to confirm is that your cash reserve is enough that you'd never have to sell into a weak market.",
            f"Feeling uneasy is reasonable and doesn't mean something is wrong. We have a policy so that decisions aren't made in the middle of a storm. A twenty percent fall in equities would cost about {drawdown} at your mix, and we can walk through what that would do to your plan.",
            f"Checking more often is a common reaction, and it isn't a bad sign by itself. It becomes one if it leads to trades you'd regret. For perspective, a twenty percent drop in stocks is roughly {drawdown} for you, and the plan is built to absorb that.",
        ])
        cr = vpick(ctx, "mk_c_mid")
    else:
        q = pick(ctx, "mk_q_high", [
            "Honestly, I've been checking my balance almost daily. It's affecting my sleep.",
            "I need to be honest: it has been stressful for me. I keep wondering whether I'm taking too much risk.",
        ])
        ans = pick(ctx, "mk_a_high", [
            "Thank you for saying so plainly. Let me ask a question. If the portfolio fell another ten percent, what would you actually do? Your answer matters more than the questionnaire you completed last year.",
            "That's important information, and I'm glad you told me. How you feel in a downturn is part of your real risk tolerance, and it's something I have to document. If we fell another ten percent, what would you want to do?",
        ])
        cr = pick(ctx, "mk_c_high", [
            "Probably sell something. I know that's not what you want to hear.",
            "I think I'd want to get out of some of it, yes.",
        ])
    if lvl == "high":
        fol = ("It's not a failure to say so. I won't push you to act today, because moves made in a stress spike are often regretted. "
               "But I'll record that your comfort level has changed, and we'll revisit the allocation deliberately.")
        return [("Advisor", lead), ("Client", q), ("Advisor", ans), ("Client", cr), ("Advisor", fol)]
    return [("Advisor", lead), ("Client", q), ("Advisor", ans), ("Client", cr)]


def g_portfolio(ctx):
    pool = [x for x in ctx.client.p["holdings"] if len(x) < 3 or x[2][0] <= ctx.step <= x[2][1]]
    h, hc = pick(ctx, "holding", pool)[:2]
    val = money(ctx.S["value"])
    snap = pick(ctx, "snap", [
        f"Here's the snapshot. Your investable portfolio is about {val}, {_ret_phrase(ctx)}, before any contributions or withdrawals. Equities are around {ctx.S['equity']} percent, which matches your current target.",
        f"Let me give you the headline numbers. The portfolio is about {val} today, {_ret_phrase(ctx)}. The equity share is about {ctx.S['equity']} percent, in line with what we agreed.",
        f"On the portfolio: roughly {val}, {_ret_phrase(ctx)}, and the asset mix is close to its target with equities near {ctx.S['equity']} percent.",
    ])
    q = pick(ctx, "holdq", [
        f"I noticed {h}. What is that doing for me?",
        f"Can you remind me why we hold {h}?",
        f"I wanted to ask about {h}. Is it still earning its place?",
    ])
    return [("Advisor", snap), ("Client", q), ("Advisor", f"Good question. {hc[0].upper() + hc[1:]}."),
            ("Client", vpick(ctx, "q_action")),
            ("Advisor", pick(ctx, "pf_a", [
                "Nothing from you today. If I think a trade is warranted, I'll bring it to you first, and nothing happens without your agreement.",
                f"No action needed on your side. With the portfolio at {money(ctx.S['value'])}, I'd only propose changes if the mix drifts well away from target, and I'd ask you first.",
                "Not today. I'm comfortable with where it sits, and any proposal will come to you in writing before I trade.",
                "Nothing urgent. I'll keep monitoring it, and you'll hear from me with a recommendation before anything is executed.",
            ]))]


def g_cashflow(ctx):
    spend = ctx.F["spend"]
    p = pick(ctx, "pressure", ctx.client.p["pressures"])
    a1 = pick(ctx, "cf_a", [
        f"Let's glance at your cash flow. Over the past few months your spending has averaged around ${spend:,.0f} a month, measured against {ctx.F['income_desc']}. Does that feel right to you?",
        f"I'd like to check your spending against what we assumed in the plan. I'm seeing about ${spend:,.0f} a month on average, set against {ctx.F['income_desc']}. Is that how it feels?",
    ])
    return [("Advisor", a1), ("Client", vpick(ctx, "cf", p=p, P2=cap(p))),
            ("Advisor", pick(ctx, "cf_a2", ctx.AV["cf_note"])),
            ("Client", vpick(ctx, "ack"))]


def g_liquidity(ctx):
    cash, spend, target = ctx.S["cash"], ctx.F["spend"], ctx.F["buffer_target"]
    months = cash / spend
    low = months < target
    a1 = (f"On liquidity: you hold about {money(cash)} in cash and short-term holdings, which covers roughly "
          f"{months:.0f} months of spending at the current level.")
    a2 = (f"For you, given {ctx.F['liq_reason']}, I'd want closer to {target} months, so that you're never forced to sell investments in a bad market to pay bills."
          if low else
          f"For you, given {ctx.F['liq_reason']}, I'd aim for around {target} months, so you're at or above that. I don't see a reason to hold more cash than this, because it has a cost in lost growth.")
    c2 = vpick(ctx, "ack") if ctx.rng.random() < 0.5 else pick(ctx, "lq_c_low" if low else "lq_c_ok", [
        "I hadn't thought of it as a number of months. That's a helpful way to see it.",
        "That makes sense. Counting in months is more tangible than a dollar figure.",
        "Fair point. I'd rather have the cushion than be forced to sell something at a bad time.",
    ] if low else [
        "Good, I wasn't sure whether I was holding too much.",
        "That's a relief. I worried the cash was just sitting there doing nothing.",
        "Okay, so I can stop worrying about that piece.",
    ])
    return [("Advisor", a1), ("Client", vpick(ctx, "q_enough")), ("Advisor", a2), ("Client", c2)]


def g_doc(ctx, doc, why, id, due="before our next meeting"):
    ctx.actions[id] = dict(owner="C", desc=f"send over {doc}", opened=ctx.date.date(),
                           status="open", deferrals=0, closed=None, mentioned_today=True)
    ctx.log.append(("c_action", f"send over {doc}"))
    ctx.tags.update(["document_request", "procedure"])
    a1 = pick(ctx, "doc_a", [
        f"I'm going to need {doc}, {why}. Could you send it {due}?",
        f"Can I ask you for {doc}? It will help {why.replace('so I can', 'me')}, and without it I'd be estimating.",
        f"On documentation: please get me {doc}. I need it {why}, and I'd rather work from the paper than from memory.",
    ])
    c1 = vpick(ctx, "doc_ok")
    return [("Advisor", a1), ("Client", c1),
            ("Advisor", pick(ctx, "doc_a2", [
                "A clear PDF or scan is fine. Please upload it through the secure client portal rather than emailing it, and I'll confirm when it arrives.",
                "Use the secure portal for that, not regular email, because it contains personal information. I'll send you a confirmation once it's in.",
                "A phone photo is acceptable if every page is legible. Upload it to the secure portal and I'll acknowledge it.",
                "Scans work. Please use the secure upload link in your portal, and tell me if any page is missing so I can note it.",
            ])),
            ]


def g_suit(ctx, topic):
    ctx.tags.update(["suitability", "procedure"])
    v = ctx.rng.randrange(3)
    if v == 0:
        t = [("Advisor", f"Before I can recommend anything about {topic}, I have to complete a suitability assessment. That means documenting your objectives, your time horizon, how much loss you can absorb, and what you hold elsewhere."),
             ("Client", "Is that mostly paperwork?"),
             ("Advisor", "It's partly paperwork, but it's also a check on me. If your answers don't support a recommendation, I shouldn't make it, even if you'd like me to."),
             ("Client", "That's fair. What do you need from me?"),
             ("Advisor", "Honest answers. Where your comfort and your capacity differ, I record the more cautious of the two.")]
    elif v == 1:
        t = [("Advisor", f"Regarding {topic}, there's a step I have to take first: a suitability review. I compare what you're asking for with your goals, your capacity for loss and your liquidity needs."),
             ("Client", "And if it doesn't pass?"),
             ("Advisor", "Then I tell you why, and we look at alternatives that do. It isn't a verdict on you. It's how I show that any advice I give was appropriate for your circumstances at the time."),
             ("Client", "Okay, that makes sense. Let's do it properly.")]
    else:
        t = [("Advisor", f"Since {topic} would change your risk profile, I need to update your suitability assessment before we act. It covers your objectives, horizon and tolerance for loss."),
             ("Client", "Haven't we done that already?"),
             ("Advisor", "We did, and your answers then were reasonable. But they're a snapshot, and your situation has moved. I'd rather update the record than rely on an outdated one."),
             ("Client", "Understood. Ask me what you need.")]
    return t


def g_delay(ctx, ask, reason, until, item):
    ctx.log.append(("deferred", f"{item} until {until}"))
    ctx.tags.update(["deferred_decision", "advisor_advice_delay"])
    return [("Client", ask),
            ("Advisor", f"I understand the pull to move quickly. My recommendation is to wait, though. {reason}"),
            ("Client", "How long would we be waiting?"),
            ("Advisor", f"Until {until}. It's your decision in the end, but I'd be failing at my job if I didn't advise a pause."),
            ("Client", "All right. Let's hold off for now.")]


def g_scenario(ctx, id, desc):
    ctx.actions[id] = dict(owner="A", desc=desc, opened=ctx.date.date(), status="open",
                           deferrals=0, closed=None, mentioned_today=True)
    ctx.log.append(("a_action", desc))
    ctx.tags.update(["scenario_calculation", "advisor_commitment"])
    return [("Advisor", f"I think the next step is for me to {desc}. I'll use conservative return assumptions, not the optimistic ones."),
            ("Client", "How long will that take?"),
            ("Advisor", "I can have it for our next meeting. I'll show you the assumptions as well as the answer, so you can challenge them."),
            ("Client", "Good. Thank you.")]


def g_explain(ctx, concept):
    seen = ctx.client.used.setdefault("explained", set())
    if concept in seen:  # never lecture the same client twice on the same concept
        return []
    seen.add(concept)
    ctx.tags.add("concept_explained")
    return [("Advisor", LEAD[concept])] + parse(EXPLAIN[concept])


def g_circumstances(ctx):
    """Routine suitability check: has anything material changed?"""
    ctx.tags.add("circumstances_check")
    return [("Advisor", pick(ctx, "circ_q", ctx.AV["circ_q"])), ("Client", vpick(ctx, "status")),
            ("Advisor", pick(ctx, "circ_a", ctx.AV["circ_a"]))]


_REG = dict(circumstances=g_circumstances, market=g_market, portfolio=g_portfolio, cashflow=g_cashflow,
            liquidity=g_liquidity, doc=g_doc, suit=g_suit, delay=g_delay,
            scenario=g_scenario, explain=g_explain)


LABELS = dict(doc="some paperwork", suit="suitability", scenario="a calculation I should run for you")


def G(name, **kw):
    fn = _REG[name]

    def run(ctx):
        ctx.gnames = getattr(ctx, "gnames", set())
        ctx.gnames.add(name)
        return fn(ctx, **kw)
    run.gname = name
    run.label = LABELS.get(name)
    if name == "explain":
        run.label = "how " + kw["concept"].replace("_", " ") + " works"
    return run
