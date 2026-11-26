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
# B1: the wording of an instruction is never a subject.  "including", "show", "explain", "change" and the like describe the OUTPUT asked for (or are so common that they match every meeting),
# so a search for them returns irrelevant turns and spends tool calls.  The DOMAIN words (retirement, mortgage, vesting ...) are untouched.
_REQUEST_WORDS |= set("""including includes included include explain explains explaining show shows showing change changes changed changing conversation conversations turn turns stated stating states
reported reporting reports inferred infer infers inference uncertain uncertainty unverified verified verify remains remain remaining anything everything each every provide provides describe
describes summarise summarize summary detail details clearly briefly format output write written walk through full complete whole entire""".split())
# the words that REPORT or FRAME a question rather than name what it is about: "says" in "unless the source explicitly says so" and "actually" in "what the source actually establishes" matched
# unrelated turns of eight meetings and took the place of the subject.  (The relation words a question really asks about - agree, speak, propose - are NOT here: they are searched, in their inflections.)
_REQUEST_WORDS |= set("""says say saying said tells telling told mention mentions mentioned mentioning stated noted asks asked actually really cite cited citing original originally unless regarding happen happens
happened happening supposed participate participates participated participant participants establishes establish established""".split())
_STOP = _FUNCTION_WORDS | _REQUEST_WORDS


def _norm(s: str) -> str:
    from services.conversations import norm
    return norm(s)


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z][a-z'\-]*", re.sub(r"'s\b", "", _norm(text)))


def source_search_terms(request_text: str, client_id: str, conv, known_first_names: Optional[set] = None) -> list[str]:
    return [t for t, _ in source_search_plan(request_text, client_id, conv, known_first_names)]


_ADVISOR_INTRO = re.compile(r"\b(?:It's|It is|This is)\s+([A-Z][a-z]+)\b")
_ADVISORS: dict = {}


def advisor_first_names(conv) -> set:
    """The advisors' first names as the DATA gives them: an advisor introduces themselves in the first turns of a meeting ("It's Daniel."), three times or more.  A name that opens eight meetings
    matches eight meetings and says nothing about a subject, so it is never a search phrase (the client's own first name already was not)."""
    key = id(conv)
    if key not in _ADVISORS or _ADVISORS[key][0] is not conv:
        from collections import Counter
        seen: Counter = Counter()
        for r in conv.rows:
            if r["speaker"] == "Advisor" and int(r["turn_index"]) <= 2:
                for m in _ADVISOR_INTRO.finditer(str(r["utterance"])[:100]):
                    seen[m.group(1).lower()] += 1
        _ADVISORS.clear()
        _ADVISORS[key] = (conv, {n for n, k in seen.items() if k >= 3})
    return _ADVISORS[key][1]


def inflections(word: str) -> set:
    """The simple inflections of ONE word, and nothing broader: speak / speaks / speaking / speaked, agree / agrees / agreed / agreeing.  No stemming of meaning, no fuzzy matching, no irregular
    forms (spoke, said): a form that is not in this set is not searched."""
    w = (word or "").lower()
    if not re.fullmatch(r"[a-z]{4,}", w):
        return {w} if w else set()
    bases = {w}
    for suf in ("ing", "ed", "es", "s", "d"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            b = w[: -len(suf)]
            bases |= {b, b + "e"}
    out = set()
    for b in bases:
        out |= {b, b + "s", b + "es", b + "d", b + "ed", b + "ing"}
        if b.endswith("e"):
            out.add(b[:-1] + "ing")
    return out


def source_search_plan(request_text: str, client_id: str, conv, known_first_names: Optional[set] = None) -> list[tuple[str, float]]:
    """The phrases of the request that are the SUBJECT of what the client's conversations discuss, best first (at most MAX_QUERIES).

    Candidates are the 1-3 word phrases of the request (function words and the request's own framing words cannot start or end one; a name already identifying the client, or an advisor's own first name,
    is not a subject).  A candidate must occur in this client's turns.  Its score is how much the client talks about it (log of its turn count) times how rare it is across all conversations (inverse
    document frequency).  A single word counts with its simple inflections (`inflections`), so "speaking" in the request finds "Please speak to Mei Lin" and "agree" finds "agreed"; the phrase that is
    SEARCHED is the form the request used if the client's turns contain it, otherwise the commonest form they do contain.  This needs no capitalisation, so a sentence-initial subject ("Lakeshore letter
    of intent: ...") and a lowercase one ("the sale of the company") are found the same way."""
    names = {n.lower() for n in (known_first_names or set()) if n} | advisor_first_names(conv)
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
        forms = inflections(c) if " " not in c else {c}
        rx = re.compile(r"(?<!\w)(?:" + "|".join(re.escape(f) for f in sorted(forms, key=len, reverse=True)) + r")(?!\w)")
        hits = frozenset(n for n, (cid, t) in enumerate(texts) if cid == client_id and rx.search(t))
        tf = len(hits)
        if not tf:
            continue
        df = sum(1 for _, t in texts if rx.search(t))
        if df / total > MAX_DF_SHARE:
            continue
        surface = c
        if len(forms) > 1:                               # which FORM is searched: the request's own if the client's turns contain it, else the commonest form they do contain
            count = {f: sum(1 for n in hits if re.search(r"(?<!\w)" + re.escape(f) + r"(?!\w)", texts[n][1])) for f in forms}
            if not count.get(c):
                surface = max((f for f in forms if count[f]), key=lambda f: (count[f], -len(f), f))
        scored.append((math.log(1 + tf) * math.log(total / df), surface, hits))
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
    """Which meetings to retrieve with `budget` calls, oldest first, and what is left out.

    Without relevance tiers (a plain search result) the priority is unchanged: the earliest and the latest meeting (a chronology needs both ends), then meetings whose correction cue is on a turn
    about the main subject, then the strongest single match (weight of the searches that found a turn), then the number of matching turns, then the most recent.

    B1: meetings that carry a `tier` (set by `enrich_meetings`) are ranked by it first: 3 = the meeting is ABOUT the subject and about a correction of its record, or a retrieved turn refers to it;
    2 = the meeting's own title is about the subject.  Tiers 2 and 3 are retrieved before any meeting that merely MENTIONS the subject, and the two-ends rule then applies only to what is left, so a
    call is never spent on a passing mention while a meeting about the subject is waiting.  Returns (interaction ids to retrieve, omitted: [{interaction_id, date, turns, reason}])."""
    order = sorted(meetings, key=lambda i: (meetings[i]["date"], i))
    if budget >= len(order):
        return order, []
    top = max((w for m in meetings.values() for w in m["turns"].values()), default=1)

    def cue_of(i: str) -> bool:
        # a correction cue counts when the cued turn matches the MAIN subject (a cue on a turn that matched only a secondary phrase is about something else)
        m = meetings[i]
        return any(m["turns"].get(t, 0) >= 0.9 * top for t in m["cue_turns"]) if "cue_turns" in m else m["cue"]

    def priority(i: str):
        m = meetings[i]
        return (not cue_of(i), -max(m["turns"].values(), default=0), -len(m["turns"]), tuple(-ord(c) for c in m["date"]), i)
    chosen: list[str] = []
    strong = [i for i in order if meetings[i].get("tier", 0) >= 2]
    if strong:
        chosen = sorted(strong, key=lambda i: (-meetings[i]["tier"], not cue_of(i), meetings[i]["date"], i))[: max(budget, 0)]
    else:
        if budget >= 1 and order:
            chosen.append(order[-1])
        if budget >= 2 and order[0] not in chosen:
            chosen.append(order[0])
    rest = sorted((i for i in order if i not in chosen), key=priority)
    chosen += rest[: max(budget - len(chosen), 0)]
    keep = set(chosen)
    omitted = [{"interaction_id": i, "date": meetings[i]["date"], "turns": sorted(meetings[i]["turns"]), "reason": "tool-call budget", "tier": meetings[i].get("tier", 0)}
               for i in order if i not in keep]
    return [i for i in order if i in keep], omitted


def meeting_calls(client_id: str, meetings: dict, chosen: list[str]) -> tuple[list[PlannedCall], list[dict]]:
    calls, cut = [], []
    for i in chosen:
        idx = sorted(meetings[i].get("window") or meetings[i]["turns"])      # B1: the window around the matching turns when `enrich_meetings` computed one
        if len(idx) > MAX_TURNS_PER_CALL:
            cut.append({"interaction_id": i, "date": meetings[i]["date"], "turns": idx[MAX_TURNS_PER_CALL:], "reason": f"more than {MAX_TURNS_PER_CALL} matching turns in one meeting"})
            idx = idx[:MAX_TURNS_PER_CALL]
        calls.append(PlannedCall(tool=SOURCE_TOOL, arguments={"client_id": client_id, "interaction_id": i, "turn_indices": idx},
                                 rationale=f"retrieve the {len(idx)} matching turn(s) of {i} (code-driven)"))
    return calls, cut


def omission_note(o: dict, budget: int) -> str:
    turns = ", ".join(str(t) for t in o["turns"])
    why = f"the tool-call budget of {budget} was used" if o["reason"] == "tool-call budget" else o["reason"]
    what = f"matching turn(s) {turns}" if turns else "its title is about the subject; no search hit"      # B1: a meeting found by its title has no matching turn
    return f"Not retrieved: {o['interaction_id']} ({o['date']}; {what}) because {why}"


# ==================================================================================================================================================
# B1: chronology-aware retrieval.  Everything below only DECIDES which meetings and turns to ask for.  The turns themselves are still fetched by the get_source_evidence tool through the
# normal validator, which re-checks that they belong to the client.  The conversation store is read-only here and only the client's OWN meetings are ever considered.
# An identifier is never invented: it comes from a search result, from the client's own meeting list (title / date), or from text of a turn that was actually retrieved.
# ==================================================================================================================================================
MAX_REFERENCE_CALLS = 2          # calls held back so that a meeting named inside a retrieved turn can still be read
MIN_BUDGET_FOR_RESERVE = 6       # with fewer calls left than this, nothing is held back (the old behaviour)
CORRECTION_WORDS = re.compile(r"\b(?:correct\w*|amend\w*|revis\w*|supersed\w*|clarif\w*|retract\w*|mistak\w*|misstat\w*|overwrit\w*|updat\w*)\b")
_ASKS_CORRECTIONS = re.compile(r"\b(?:corrections?|corrected|correcting|amend\w*|supersed\w*|supersession|revis\w*|chang(?:e|es|ed|ing)|overwrit\w*|evolv\w*)\b")
_MONTHS = {m: i for i, m in enumerate("january february march april may june july august september october november december".split(), 1)}
_MONTHS.update({m[:3]: i for m, i in list(_MONTHS.items())})
_MONTHS["sept"] = 9
_MON = "|".join(sorted(_MONTHS, key=len, reverse=True))
_DATE_REF = re.compile(r"\b(?:(?P<m1>" + _MON + r")\.?\s+(?P<d1>\d{1,2})(?:st|nd|rd|th)?|(?P<d2>\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?(?P<m2>" + _MON + r"))\b(?!\s*,?\s*\d{4})")
_ID_REF = re.compile(r"\b(?:INT|MTG|THR|CHT)-\d{3,4}\b")
# a date only counts as a reference to a MEETING when its sentence talks about one; a booking for a future meeting is never a reference
_REF_CUE = re.compile(r"\b(?:meeting|met|spoke|spoken|call|conversation|discuss\w*|session|talked|when we|we (?:covered|focused|agreed|decided|reviewed|looked|went|corrected)|last time|since|follow(?:ed)?[- ]?up|review(?:ed)?)\b")
_FUTURE = re.compile(r"\b(?:book|booked|schedule|scheduled|reschedule|next meeting|next one|upcoming|calendar|diary|will meet|let's meet)\b")


def wants_corrections(request_text: str) -> bool:
    """Does the request ask about corrections, changes or supersession (rather than a plain list of mentions)?"""
    return bool(_ASKS_CORRECTIONS.search(_norm(request_text)))


def subject_stems(terms: list[str]) -> list[str]:
    """Word stems of the chosen subject phrases, to recognise the subject in a turn or a meeting title ("retirement" -> "retir": retire, retiring, retirement).  Request language is excluded."""
    out: list[str] = []
    for term in terms:
        for w in _tokens(term):
            if w in _STOP or len(w) < 4:
                continue
            s = w[:5] if len(w) >= 7 else w
            if s not in out:
                out.append(s)
    return out


def _stem_rx(stems: list[str]):
    return re.compile(r"(?<!\w)(?:" + "|".join(re.escape(s) for s in stems) + r")\w*") if stems else None


def client_meetings(conv, client_id: str) -> dict:
    """{interaction_id: {date, context (the meeting title), rows}} for ONE client, rows in turn order.  Other clients' meetings never appear."""
    out: dict[str, dict] = {}
    for r in conv.rows:
        if r["client_id"] != client_id:
            continue
        m = out.setdefault(r["interaction_id"], {"date": str(r["interaction_datetime"])[:10], "context": _norm(r.get("meeting_context") or ""), "rows": []})
        m["rows"].append(r)
    for m in out.values():
        m["rows"].sort(key=lambda r: int(r["turn_index"]))
    return out


def expand_turns(rows: list[dict], hits: set, stems: list[str], cue_turns: set = frozenset(), cap: int = MAX_TURNS_PER_CALL, correction: bool = False) -> list[int]:
    """The turns of ONE meeting worth reading: the turns that mention the subject (search hits, any turn containing a subject stem, correction-cued turns) and the turns around them, nearest first,
    so that the reply to a matching question and the sentence that confirms it are not cut off.  Around a meeting about a CORRECTION the window is wider (4 turns), because the decisive turns of
    such a meeting often do not repeat the subject word.  At equal distance the client's own turns come first.  A meeting matched only by its title contributes its leading turns.  Never more than
    `cap` turns; the result is sorted, so the call (and its canonical duplicate key) is deterministic."""
    rx = _stem_rx(stems)
    idx = [int(r["turn_index"]) for r in rows]
    who = {int(r["turn_index"]): r["speaker"] for r in rows}
    core = {i for i in hits if i in who} | {i for i in cue_turns if i in who}
    if rx is not None:
        core |= {int(r["turn_index"]) for r in rows if rx.search(_norm(r["utterance"]))}
    if not core:
        return idx[:cap]
    radius = 4 if correction else 1
    scored = []
    for i in idx:
        d = min(abs(i - c) for c in core)
        if d <= radius:
            scored.append((d, 0 if (d == 0 or who[i] == "Client") else 1, i))
    scored.sort()
    return sorted(i for _, _, i in scored[:cap])


def enrich_meetings(meetings: dict, conv, client_id: str, terms: list[str], request_text: str, notes: Optional[list] = None) -> dict:
    """Add what a phrase search cannot see, in place, and return `meetings`:
      * a meeting whose own TITLE is about the subject although no turn contains the search phrase (a thread titled "Early retirement scenarios - summary ..." that never says "retirement");
      * `tier` (see plan_meetings), `about` / `ctx_correction` (title signals) and `window` (the turns to read, see expand_turns).
    With no subject stem (nothing to recognise) the meetings are returned untouched, so retrieval behaves exactly as before."""
    stems = subject_stems(terms)
    rx = _stem_rx(stems)
    if rx is None:
        return meetings
    allm = client_meetings(conv, client_id)
    asks = wants_corrections(request_text)
    for iid, m in allm.items():
        about = bool(rx.search(m["context"]))
        corr = bool(about and CORRECTION_WORDS.search(m["context"]))
        if about and iid not in meetings:
            meetings[iid] = {"date": m["date"], "turns": {}, "cue": False, "cue_turns": set(), "found_by": "title"}
            if notes is not None:
                notes.append(f"{iid} ({m['date']}) was added because its title is about the subject ({m['context']!r}); no turn of it contains the search phrase")
        if iid in meetings:
            mt = meetings[iid]
            mt.setdefault("found_by", "search")
            mt["about"], mt["ctx_correction"] = about, corr
            mt["tier"] = 3 if (corr and asks) else 2 if about else 0
    for iid, mt in meetings.items():
        if iid in allm:
            mt["window"] = expand_turns(allm[iid]["rows"], set(mt["turns"]), stems, set(mt.get("cue_turns") or ()), correction=bool(mt.get("ctx_correction")))
    _read_short_meetings_whole(meetings, allm)
    return meetings


MAX_FOCUS_MEETINGS = 12      # a request whose searches find at most this many meetings is about something NARROW (a call, a person, one document), not a history of a subject


def _read_short_meetings_whole(meetings: dict, allm: dict) -> None:
    """A narrow question ("what was agreed about a call with her accountant?") is answered by the few meetings where it was discussed, and the sentence that completes the exchange is often more than
    one turn away from the phrase that matched (INT-070: the advisor proposes the call in turn 3, the client answers in turn 6).  When ALL the searches together find at most MAX_FOCUS_MEETINGS meetings
    (a broad subject is found in twice as many), each meeting with a STRONG hit (a turn found by a phrase at least 90% as distinctive as the best one) and MAX_TURNS_PER_CALL turns or fewer is read WHOLE:
    still one call, so the tool-call budget does not change.  A broad subject (a company sale, a retirement age) is never widened: its windows stay as `expand_turns` made them."""
    top = max((w for m in meetings.values() for w in m["turns"].values()), default=0)
    if not top or len(meetings) > MAX_FOCUS_MEETINGS:
        return
    strong = [i for i, m in meetings.items() if any(w >= 0.9 * top for w in m["turns"].values()) and i in allm]
    if not strong:
        return
    for i in strong:
        rows = allm[i]["rows"]
        if len(rows) <= MAX_TURNS_PER_CALL:
            meetings[i]["window"] = sorted(int(r["turn_index"]) for r in rows)
            meetings[i]["whole_meeting"] = True


def find_references(texts: list[dict], conv, client_id: str, exclude: set = frozenset()) -> tuple[list[dict], list[dict]]:
    """Meetings that RETRIEVED turns refer to.  `texts` = [{interaction_id, turn_index, text}].

    A reference is an explicit meeting id ("INT-049") or a date the way an advisor writes one ("Since March 4, when we focused on ...") in a sentence that talks about a meeting.  It is resolved only
    against THIS client's own meetings, using the year of the referring meeting.  It resolves when exactly ONE other meeting of the client falls on that date; zero or several is recorded as
    unresolved (never guessed).  A date later than the referring meeting is a booking, not a reference, and is skipped; so is a sentence about scheduling.  An id that is not one of the client's
    meetings is unresolved.  Returns (resolved: [{interaction_id, date, from, quote, kind, sentence}], unresolved: [{quote, from, kind, why}]); `exclude` ids are not repeated in `resolved`."""
    allm = client_meetings(conv, client_id)
    by_date: dict[str, list[str]] = {}
    for iid, m in allm.items():
        by_date.setdefault(m["date"], []).append(iid)
    resolved: dict[tuple, dict] = {}
    unresolved: dict[tuple, dict] = {}
    for t in sorted(texts, key=lambda t: (t["interaction_id"], int(t["turn_index"]))):
        src, where, body = t["interaction_id"], f"{t['interaction_id']}#{t['turn_index']}", str(t["text"])
        ref_date = allm.get(src, {}).get("date", "")
        for sent in [s.strip() for s in re.split(r"(?<=[.!?])\s+|\n+", body) if s.strip()]:
            low = _norm(sent)
            for m in _ID_REF.finditer(sent):
                rid = m.group(0)
                if rid == src:
                    continue
                if rid in allm:
                    if rid not in exclude:
                        resolved.setdefault((rid,), {"interaction_id": rid, "date": allm[rid]["date"], "from": where, "quote": rid, "kind": "id", "sentence": sent[:200], "clause": low[:200]})
                else:
                    unresolved.setdefault((where, rid), {"quote": rid, "from": where, "kind": "id", "why": "not a meeting of this client"})
            if not ref_date or _FUTURE.search(low) or not _REF_CUE.search(low):
                continue
            for m in _DATE_REF.finditer(low):
                mon, day = _MONTHS.get((m.group("m1") or m.group("m2")).rstrip(".")), int(m.group("d1") or m.group("d2"))
                try:
                    iso = f"{int(ref_date[:4]):04d}-{mon:02d}-{day:02d}"
                except (TypeError, ValueError):
                    continue
                if iso >= ref_date:
                    continue                                    # the referring meeting's own day or a later one: a booking, not a reference to an earlier meeting
                ids = [i for i in by_date.get(iso, []) if i != src]
                quote = m.group(0)
                if len(ids) == 1:
                    if ids[0] not in exclude:
                        resolved.setdefault((ids[0],), {"interaction_id": ids[0], "date": iso, "from": where, "quote": quote, "kind": "date", "sentence": sent[:200],
                                                        "clause": _referred_clause(low, m.start())})
                else:
                    why = "no meeting of this client falls on that date" if not ids else f"ambiguous: {len(ids)} meetings of this client fall on that date ({', '.join(sorted(ids))})"
                    unresolved.setdefault((where, quote), {"quote": quote, "from": where, "kind": "date", "why": why})
    return list(resolved.values()), list(unresolved.values())


def add_referenced_meeting(meetings: dict, ref: dict, conv, client_id: str, stems: list[str]) -> None:
    """Make a resolved, relevant reference a candidate meeting (tier 3: a retrieved turn names it, so it outranks a mere mention).  Its window is the wide one: the sentence that refers to it
    usually says why ("correcting the retirement-age record"), and the decisive turns of such a meeting need not repeat the subject word."""
    iid = ref["interaction_id"]
    m = client_meetings(conv, client_id).get(iid)
    if m is None:
        return
    if iid in meetings:                                       # already a candidate (found by search or title): the reference only raises its rank
        meetings[iid]["tier"] = max(meetings[iid].get("tier", 0), 3)
        meetings[iid].setdefault("referenced_from", ref["from"])
        return
    meetings[iid] = {"date": m["date"], "turns": {}, "cue": False, "cue_turns": set(), "found_by": "reference", "tier": 3, "about": True, "ctx_correction": True,
                     "referenced_from": ref["from"], "window": expand_turns(m["rows"], set(), stems, correction=True)}


_NEXT_TOPIC = re.compile(r"\b(?:the )?next (?:topic|item|thing|point)\b|\btoday\b|\bnow\b")


def _referred_clause(low: str, start: int) -> str:
    """The part of a sentence that describes the REFERRED meeting: from the date up to where the sentence turns to the present meeting ("Since March 4, when we focused on correcting the
    retirement-age record, | the next topic is comfort with portfolio risk").  What the referring meeting itself is about does not make the referred one relevant."""
    rest = low[start:]
    cut = _NEXT_TOPIC.search(rest)
    return (rest[: cut.start()] if cut else rest)[:200]


def reference_is_relevant(ref: dict, conv, client_id: str, stems: list[str]) -> bool:
    """Is a resolved reference worth a tool call?  Only when the clause that describes the referred meeting is about the subject, or the referred meeting's own title is.  ("We last spoke on
    March 2, about share vesting" is a reference too, but not one a retirement chronology needs; nor is a meeting that merely precedes one about retirement.)"""
    rx = _stem_rx(stems)
    if rx is None:
        return False
    if rx.search(_norm(ref.get("clause") if ref.get("clause") is not None else ref.get("sentence") or "")):
        return True
    m = client_meetings(conv, client_id).get(ref["interaction_id"])
    return bool(m and rx.search(m["context"]))
