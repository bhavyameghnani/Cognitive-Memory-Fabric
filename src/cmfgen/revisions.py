"""Revision 2 overlay.

Each entry replaces or extends ONE storyline step of the v1 modules (src/cmfgen/clients/cN.py), so the original
storyline stays readable next to what changed.  No entry opens or closes an action item, so the v1 ledger
(48 opened / 40 completed / 8 outstanding) is unchanged.

REPLACE[(client_idx, step)] = (context, [beats], channel)   - rewrite a thin meeting
APPEND[(client_idx, step)]  = [beats]                       - add content after the existing beats
"""
from .beats import Check, G, S

REPLACE = {}
APPEND = {}

# QA-only, written to the manifest and never to the CSV.  These do not change the action ledger counts.
BEYOND_WINDOW = [dict(
    id="g_jul1_call", client_idx=3, step=23, owner_role="advisor", owner_id="ADVISOR-001",
    description="call Grace before the July 1 purchase if the stock holding is down more than ten percent since the first purchase",
    condition="only if the holding is down more than 10 percent since the 1 June purchase", due="2026-07-01",
    status="outstanding_beyond_observation_window", counted_in_action_ledger=False)]
LEDGER_ANNOTATIONS = {
    "CLIENT-005:l_ins": ("Open client item. The original request (send current health and disability policy summaries) evolved in "
                         "dialogue: on 2026-05-29 Lucas said he may only have health cover, and on 2026-06-18 he said the broker needed "
                         "about three weeks to produce quotes. The underlying need (evidence of, or quotes for, disability cover) is still "
                         "open at 2026-06-29 (INT-148). No advisor commitment is attached to it."),
    "CLIENT-003:s_offer": ("Counted as outstanding in the v1 ledger and kept so that counts stay 48/40/8. The dialogue has since made it moot: "
                           "on the last check Sofia said Mateo decided not to interview, so there are no offer details to send. "
                           "It should be read as withdrawn-by-circumstance, not as a live obligation."),
}


def apply_revisions(idx, steps):
    from . import rev_c1, rev_c2, rev_c3, rev_c4, rev_c5, rev_c6  # noqa: F401  (populate REPLACE/APPEND)
    out = list(steps)
    for (ci, st), v in REPLACE.items():
        if ci == idx:
            ctx_, beats, ch = v
            out[st - 1] = (ctx_, beats, ch or out[st - 1][2])
    for (ci, st), extra in APPEND.items():
        if ci == idx:
            ctx_, beats, ch = out[st - 1]
            out[st - 1] = (ctx_, list(beats) + list(extra), ch)
    return out

