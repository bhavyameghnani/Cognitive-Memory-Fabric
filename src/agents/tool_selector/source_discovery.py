"""Source discovery for a request about what the conversations record (a chronology of a named subject).

The Tool Selector's model cannot be trusted to find real interaction ids: given a subject such as "the Lakeshore letter of intent" it guessed entity ids and got empty results.  This module does the
discovery in code, through the SAME read-only tool the model would use:

  1. derive the searchable subject from the request, using the client's own conversations (no model, no new dependency, no embeddings);
  2. get_source_evidence(client_id, search_text=...) lists real turn references for that client (with a correction cue);
  3. get_source_evidence(client_id, interaction_id, turn_indices=[...]) retrieves ALL matching turns of ONE meeting in one call (conversation mode: exact text, speaker and date).

One call per meeting, not per turn, is what lets a whole chronology fit the harness tool-call budget.  If the matching meetings still exceed it, the meetings are ranked (earliest and latest always kept,
then corrections, then the strongest matches) and EXACTLY what was left out, and why, is reported.  Nothing is invented: every identifier comes from a tool result.  Pure functions; the loop that
executes the calls is in agent.py.
"""
from __future__ import annotations

import math
import re
from typing import Optional

from schemas.tools import Envelope, PlannedCall, ToolExecution

SOURCE_TOOL = "get_source_evidence"
MAX_QUERIES = 3
MAX_TURNS_PER_CALL = 12      # must equal the tool's own limit (mcp_server.services.MAX_TURNS_PER_CALL)
MIN_RELATIVE_SCORE = 0.5     # a second search term must be at least half as distinctive as the best one
MAX_DF_SHARE = 0.10          # a phrase found in more than 10% of all turns says nothing about a subject

_FUNCTION_WORDS = set("""a about above after again against all also am an and any are as at be because been before being below between both but by can could did do does doing down during each few for from
further had has have having he her here hers herself him himself his how i if in into is it its itself just me more most my myself no nor not now of off on once only or other our ours out over own same she
should so some such than that the their theirs them then there these they this those through to too under until up very was we were what when where which while who whom why will with would you your yours""".split())
# words of the REQUEST'S OWN framing: they describe the report asked for, not its subject
_REQUEST_WORDS = set("""prepare include identify state preserve separate distinguish make please give list show tell report factual explicitly whether source sources record records statement statements earlier
rather infer evidence establish establishes available relevant dates date meeting meetings discussion discussions confirmed decisions decision questions question open items item recommendations
recommendation judgments forecasts transaction transactions investment suitability exploratory chronology timeline corrections correction unresolved authored notes note client advisor something say
happened""".split())
_STOP = _FUNCTION_WORDS | _REQUEST_WORDS


def _norm(s: str) -> str:
    from services.conversations import norm
    return norm(s)


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z][a-z'\-]*", _norm(text))


def source_search_terms(request_text: str, client_id: str, conv, known_first_names: Optional[set] = None) -> list[str]:
    return [t for t, _ in source_search_plan(request_text, client_id, conv, known_first_names)]


def source_search_plan(request_text: str, client_id: str, conv, known_first_names: Optional[set] = None) -> list[tuple[str, float]]:
    """The phrases of the request that are the SUBJECT of what the client's conversations discuss, best first (at most MAX_QUERIES).

    Candidates are the 1-3 word phrases of the request (function words and the request's own framing words cannot start or end one; a name already identifying the client is not a subject).  A candidate
    must occur in this client's turns.  Its score is how much the client talks about it (log of its turn count) times how rare it is across all conversations (inverse document frequency),  This needs no capitalisation, so a sentence-initial subject ("Lakeshore letter of intent: ...") and a lowercase one ("the sale of the company") are found the same way."""
    names = {n.lower() for n in (known_first_names or set()) if n}
    toks = _tokens(request_text)
    cands: dict[str, None] = {}
    for n in (3, 2, 1):
        for i in range(len(toks) - n + 1):
            g = toks[i:i + n]
            if g[0] in _STOP or g[-1] in _STOP or any(w in names for w in g) or len(g[0]) < 3 or len(" ".join(g)) < 4:
                continue
            cands[" ".join(g)] = None
    texts = _corpus(conv)
    total = len(texts)
    scored: list[tuple[float, str, frozenset]] = []
    for c in cands:
        rx = re.compile(r"(?<!\w)" + re.escape(c) + r"(?!\w)")
        hits = frozenset(n for n, (cid, t) in enumerate(texts) if cid == client_id and rx.search(t))
        tf = len(hits)
        if not tf:
            continue
        df = sum(1 for _, t in texts if rx.search(t))
        if df / total > MAX_DF_SHARE:
            continue
        scored.append((math.log(1 + tf) * math.log(total / df), c, hits))
    best: dict[frozenset, tuple] = {}
    for item in scored:                                  # candidates that find exactly the same client turns: keep the most specific wording (same recall, least noise)
        cur = best.get(item[2])
        if cur is None or (len(item[1].split()), item[0]) > (len(cur[1].split()), cur[0]):
            best[item[2]] = item
    scored = list(best.values())
    scored.sort(key=lambda x: (-x[0], len(x[1].split()), x[1]))       # ties go to the shorter, more general phrase: recall matters more than precision for a chronology
    chosen: list[tuple[str, float]] = []
    covered: list[frozenset] = []
    for s, c, hits in scored:
        if chosen and s < MIN_RELATIVE_SCORE * scored[0][0]:
            break
        if any(hits <= h for h in covered):
            continue                                    # every turn it finds is already found by a chosen phrase: another search would add a call and no turn
        covered.append(hits)
        chosen.append((c, round(s, 2)))
        if len(chosen) == MAX_QUERIES:
            break
    sets = dict(zip((c for c, _ in chosen), covered))
    return [(c, s) for c, s in chosen if not any(sets[c] < sets[k] for k, _ in chosen)]      # a phrase chosen before a broader one that finds all its turns (and more) is redundant


_CORPUS: dict = {}


def _corpus(conv) -> list[tuple[str, str]]:
    key = id(conv)
    if key not in _CORPUS or _CORPUS[key][0] is not conv:
        _CORPUS.clear()
        _CORPUS[key] = (conv, [(r["client_id"], _norm(r["utterance"])) for r in conv.rows])
    return _CORPUS[key][1]


def search_calls(client_id: str, terms: list[str]) -> list[PlannedCall]:
    return [PlannedCall(tool=SOURCE_TOOL, arguments={"client_id": client_id, "search_text": t}, rationale=f"discover turns that mention '{t}' (code-driven source discovery)") for t in terms]


# --------------------------------------------------------------------------------------------------------------------------------------------------
def meetings_from(results: list[ToolExecution], weights: Optional[dict] = None) -> tuple[dict, list[str]]:
    """The matching meetings from the SUCCESSFUL search results: {interaction_id: {date, turns: {index: weight of the searches that found it}, cue}}, and a note for every truncated search.
    `weights` maps a search phrase to how distinctive a subject it is, so a turn that matches the main subject outranks one that matches only a secondary phrase (default 1)."""
    meetings: dict[str, dict] = {}
    notes: list[str] = []
    for ex in results:
        env: Optional[Envelope] = ex.result
        search = (env.data or {}).get("search") if env is not None else None
        if ex.status != "success" or not isinstance(search, dict):
            continue
        if search.get("truncated"):
            notes.append(f"the search for {search.get('query')!r} matched {search.get('total')} turns but lists only the first {len(search.get('matches') or [])}")
        for m in search.get("matches") or []:
            if isinstance(m, dict) and isinstance(m.get("interaction_id"), str) and isinstance(m.get("turn_index"), int):
                mt = meetings.setdefault(m["interaction_id"], {"date": str(m.get("date") or ""), "turns": {}, "cue": False, "cue_turns": set()})
                mt["turns"][m["turn_index"]] = mt["turns"].get(m["turn_index"], 0) + (weights or {}).get(search.get("query"), 1)
                if m.get("cue") == "correction":
                    mt["cue"] = True
                    mt["cue_turns"].add(m["turn_index"])
    return meetings, notes


def plan_meetings(meetings: dict, budget: int) -> tuple[list[str], list[dict]]:
    """Which meetings to retrieve with `budget` calls, oldest first, and what is left out.  Priority: the earliest and the latest meeting (a chronology needs both ends), then meetings whose correction cue is on a
    turn about the main subject, then the strongest single match (weight of the searches that found a turn), then the number of matching turns, then the most recent.  Returns (interaction ids to retrieve, omitted: [{interaction_id, date, turns, reason}])."""
    order = sorted(meetings, key=lambda i: (meetings[i]["date"], i))
    if budget >= len(order):
        return order, []
    chosen: list[str] = []
    if budget >= 1 and order:
        chosen.append(order[-1])
    if budget >= 2 and order[0] not in chosen:
        chosen.append(order[0])
    top = max((w for m in meetings.values() for w in m["turns"].values()), default=1)

    def priority(i: str):
        m = meetings[i]
        # a correction cue counts when the cued turn matches the MAIN subject (a cue on a turn that matched only a secondary phrase is about something else)
        cue = any(m["turns"].get(t, 0) >= 0.9 * top for t in m["cue_turns"]) if "cue_turns" in m else m["cue"]
        return (not cue, -max(m["turns"].values()), -len(m["turns"]), tuple(-ord(c) for c in m["date"]), i)
    rest = sorted((i for i in order if i not in chosen), key=priority)
    chosen += rest[: max(budget - len(chosen), 0)]
    keep = set(chosen)
    omitted = [{"interaction_id": i, "date": meetings[i]["date"], "turns": sorted(meetings[i]["turns"]), "reason": "tool-call budget"} for i in order if i not in keep]
    return [i for i in order if i in keep], omitted


def meeting_calls(client_id: str, meetings: dict, chosen: list[str]) -> tuple[list[PlannedCall], list[dict]]:
    calls, cut = [], []
    for i in chosen:
        idx = sorted(meetings[i]["turns"])
        if len(idx) > MAX_TURNS_PER_CALL:
            cut.append({"interaction_id": i, "date": meetings[i]["date"], "turns": idx[MAX_TURNS_PER_CALL:], "reason": f"more than {MAX_TURNS_PER_CALL} matching turns in one meeting"})
            idx = idx[:MAX_TURNS_PER_CALL]
        calls.append(PlannedCall(tool=SOURCE_TOOL, arguments={"client_id": client_id, "interaction_id": i, "turn_indices": idx},
                                 rationale=f"retrieve the {len(idx)} matching turn(s) of {i} (code-driven)"))
    return calls, cut


def omission_note(o: dict, budget: int) -> str:
    turns = ", ".join(str(t) for t in o["turns"])
    why = f"the tool-call budget of {budget} was used" if o["reason"] == "tool-call budget" else o["reason"]
    return f"Not retrieved: {o['interaction_id']} ({o['date']}; matching turn(s) {turns}) because {why}"
