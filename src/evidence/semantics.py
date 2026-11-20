"""Deterministic semantic layer: topic segmentation, dialogue-act / cue annotation and derived turn relations.

Everything produced here is labelled rule_generated with its method and a confidence; none of it is source metadata and none of it changes
source text.  No LLM, no embeddings.  The lexicon is small and reviewable on purpose: it is a baseline, not a classifier.
Rule: a suggestion, exploration or hypothetical is never labelled a decision."""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional

from .model import ParsedContainer, ParsedTurn

SEGMENT_METHOD = "keyword_lexicon_v1"
ACT_METHOD = "rule_v1"

TOPICS: dict[str, list[str]] = {
    "retirement_planning": ["retire", "retirement", "pension", "social security", "annuity", "lump sum", "installment", "rollover", "401", "ira", "retention bonus"],
    "portfolio_allocation": ["portfolio", "allocation", "equity", "equities", "bond", "bonds", "stock", "shares", "fund", "index", "diversif", "concentrat", "rebalanc", "risk profile", "volatility"],
    "liquidity_and_cash": ["cash", "liquid", "reserve", "emergency fund", "buffer", "cash flow", "months of", "savings account"],
    "estate_and_trusts": ["estate", "will ", "trust", "beneficiar", "inherit", "power of attorney", "elder-law", "executor", "probate", "death certificate"],
    "tax": ["tax", "withhold", "bracket", "deduction", "capital gain", "estimated payment", "cpa"],
    "insurance_and_care": ["insurance", "medicare", "disability", "long-term care", "care cost", "aide", "premium", "claim"],
    "housing_and_debt": ["mortgage", "home", "house", "down payment", "loan", "student loan", "rent", "closing"],
    "business_and_equity_events": ["tender offer", "sale of the company", "letter of intent", "valuation", "business", "startup", "acquisition", "proceeds", "vesting", "stock option"],
    "family_and_education": ["college", "tuition", "529", "childcare", "parental leave", "daughter", "son ", "grandchild", "school"],
    "goals_and_planning": ["goal", "budget", "spending", "income need", "scenario", "plan "],
    "follow_up_commitments": ["next step", "follow up", "follow-up", "i'll send", "i will send", "by friday", "next week", "next call", "send me", "schedule"],
    "scheduling_and_admin": ["reschedul", "appointment", "calendar", "scanned", "received the", "portal", "document request"],
}
_WORD = re.compile(r"[a-z0-9'\-]+")


def topic_scores(text: str) -> dict[str, int]:
    t = " " + text.lower() + " "
    return {k: sum(t.count(w) for w in ws) for k, ws in TOPICS.items() if any(w in t for w in ws)}


# ---- dialogue acts ----------------------------------------------------------------------------------------------------------------------
_EXPLORATORY = re.compile(r"\b(thinking about|might|maybe|perhaps|leaning|considering|what if|not decided|undecided|haven't decided|not sure|i wonder|could be|no decision|decide nothing|nothing (?:is )?final|not final|not yet)\b", re.I)
_DECISION_FIRST = re.compile(r"\b(i(?:'ve| have) decided|we(?:'ve| have) decided|i(?:'ll| will) go with|we(?:'ll| will) go with|let's go with|let's do it|go ahead and|i(?:'m| am) going to (?:go|elect|take|keep|sell|buy|move)|that's the decision|my decision is|we(?:'ve| have) agreed to)\b", re.I)
_DECISION_RECORDED = re.compile(r"\b(decision[^.?!]{0,80}\b(?:is|was) recorded|you(?:'ve| have)? decided|as you decided|your decision (?:to|is|was)|decision (?:to|is)|confirmed? (?:that )?you(?:'ll| will)?)\b", re.I)
_NOT_DECISION = re.compile(r"(?:isn't|is not|wasn't|not|no) (?:your |a |the )?decision", re.I)
_COMMIT = re.compile(r"\b(i(?:'ll| will) (?:send|call|get|prepare|check|review|update|follow|email|have|bring|forward|confirm|arrange|draft|look|set)|let me (?:check|send|get|look|confirm|find)|i can (?:send|get|have)|we(?:'ll| will) (?:send|prepare|review|talk|speak|meet|revisit)|i(?:'m| am) going to send|by (?:monday|tuesday|wednesday|thursday|friday|next week|the end of))\b", re.I)
_SUGGEST = re.compile(r"\b(i suggest|you could|you might|one option|i(?:'d| would) recommend|i(?:'d| would) suggest|consider|how about|why don't you|it may be worth|worth (?:considering|looking))\b", re.I)
_PROPOSE = re.compile(r"\b(i propose|let's (?!go with|do it)|shall we|we could|what do you think about|would you like me to)\b", re.I)
_AGREE = re.compile(r"^\W*(yes|yeah|yep|agreed|that works|sounds good|okay|ok|sure|i agree|exactly|right|good|fine by me|that's fine|understood)\b", re.I)
_REJECT = re.compile(r"(^\W*no\b|\bi don't want\b|\bi'd rather not\b|\bnot comfortable\b|\bi(?:'m| am) not going to\b|\bi disagree\b|\bi don't think so\b|\bthat's not what i\b|\brule(?:d)? out\b)", re.I)
_CORRECT = re.compile(r"\b(i misspoke|correction|let me correct|i need to correct|that was wrong|that's wrong|i was wrong|my mistake|i made a mistake|i meant|i said [^.]{1,60} but|actually,? (?:it|that|the|it's|that's)|not [^.]{1,30}, (?:it's|it is)|should (?:have )?read|should be [^.]{0,30} not|i got that wrong|sorry,? (?:i|that))\b", re.I)
_DEFER = re.compile(r"\b(revisit|come back to|park (?:that|this|it)|need to think|let me think|haven't (?:decided|settled)|undecided|decide later|next time|wait until|not yet|leave it open|still open|open question|hold off|table (?:that|this)|think about it)\b", re.I)
_UNCERT = re.compile(r"\b(maybe|perhaps|might|probably|i think|i guess|not sure|roughly|around|approximately|about \$|leaning|possibly|i believe|i suppose)\b", re.I)
_SOCIAL = re.compile(r"^\W*(thanks|thank you|good (?:morning|afternoon|evening)|hello|hi\b|hey\b|bye|goodbye|have a (?:good|great|nice)|take care|talk soon|speak soon|see you|welcome)\b", re.I)
_TEMPORAL = re.compile(r"\b((?:mon|tues|wednes|thurs|fri|satur|sun)day|tomorrow|yesterday|today|next (?:week|month|year|call|meeting)|last (?:week|month|year)|this (?:week|month|year|morning|afternoon)|"
                       r"(?:january|february|march|april|may|june|july|august|september|october|november|december)(?: \d{1,2})?(?:, \d{4})?|\d{4}-\d{2}-\d{2}|\b20\d{2}\b|"
                       r"(?:within|in|after|by) (?:\d+|one|two|three|four|five|six|a few|several) (?:days?|weeks?|months?|years?)|week of [A-Z][a-z]+ \d{1,2})\b", re.I)
_AMOUNT = re.compile(r"\$\s?\d[\d,]*(?:\.\d+)?(?:\s?(?:k|m|million|thousand))?|\b\d{1,3}(?:\.\d+)?\s?%|\b\d[\d,]{3,}\b")


def dialogue_acts(text: str, role: Optional[str]) -> list[tuple[str, float]]:
    """Cue-based acts for one turn: list of (act, confidence).  Multi-label.  An exploratory cue blocks `decision`."""
    acts: list[tuple[str, float]] = []
    exploratory = bool(_EXPLORATORY.search(text))
    if "?" in text:
        acts.append(("question", 0.7))
    if _CORRECT.search(text):
        acts.append(("correction", 0.55))
    if not exploratory and not _NOT_DECISION.search(text) and (_DECISION_FIRST.search(text) and role != "Advisor" or _DECISION_RECORDED.search(text) and role in ("Advisor", None)):
        acts.append(("decision", 0.5))
    if _COMMIT.search(text):
        acts.append(("commitment", 0.55))
    if _SUGGEST.search(text):
        acts.append(("suggestion", 0.55))
    if _PROPOSE.search(text):
        acts.append(("proposal", 0.5))
    if _AGREE.search(text):
        acts.append(("agreement", 0.5))
    if _REJECT.search(text):
        acts.append(("rejection", 0.45))
    if _DEFER.search(text):
        acts.append(("deferral_unresolved", 0.5))
    words = len(_WORD.findall(text.lower()))
    if _SOCIAL.search(text) and words <= 14 and not any(a in ("question", "decision", "commitment", "correction") for a, _ in acts):
        acts.append(("social", 0.6))
    if not acts:
        acts.append(("information", 0.4))
    return acts


def cues(text: str) -> list[tuple[str, str, float]]:
    """(kind, value, confidence) for uncertainty cues, temporal references and amounts, from the exact text."""
    out = [("uncertainty_cue", m.group(0).lower(), 0.5) for m in _UNCERT.finditer(text)]
    out += [("temporal_ref", m.group(0), 0.6) for m in _TEMPORAL.finditer(text)]
    out += [("amount_ref", m.group(0).strip(), 0.7) for m in _AMOUNT.finditer(text)]
    seen, uniq = set(), []
    for k, v, c in out:
        if (k, v.lower()) not in seen:
            seen.add((k, v.lower()))
            uniq.append((k, v, c))
    return uniq


def entity_refs(text: str, names: dict[str, str]) -> list[tuple[str, str]]:
    """Registry people mentioned by name; names maps a first or full name to a character id.  Exact whole-word match only."""
    out = []
    for n, cid in names.items():
        if re.search(r"(?<!\w)" + re.escape(n) + r"(?!\w)", text):
            out.append((cid, n))
    return out


# ---- relations ----------------------------------------------------------------------------------------------------------------------------
def _toks(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if len(w) >= 5 or any(c.isdigit() for c in w)}


def relations(c: ParsedContainer, acts_by_turn: dict[str, list[str]]) -> list[tuple[str, str, str, str, str]]:
    """(from_turn, to_turn, rel_type, basis, provenance).  Observed links come from source fields; others are cue rules and say so."""
    out, seen = [], set()

    def add(a, b, t, basis, prov):
        if a != b and (a, b, t) not in seen:
            seen.add((a, b, t))
            out.append((a, b, t, basis, prov))

    ts = c.turns
    by_id = {t.turn_id: t for t in ts}
    for t in ts:
        if t.email and t.email["in_reply_to_turn_id"]:
            add(t.turn_id, t.email["in_reply_to_turn_id"], "replies_to", "in_reply_to_header", "observed")
        if t.chat and t.chat["reply_to_turn_id"]:
            add(t.turn_id, t.chat["reply_to_turn_id"], "replies_to", "reply_to_message_id", "observed")
    for i, t in enumerate(ts):
        a = acts_by_turn[t.turn_id]
        if "question" in a:
            nxt = next((u for u in ts[i + 1:i + 3] if u.speaker_id != t.speaker_id), None)       # first reply by someone else, at most two turns on
            if nxt is not None and acts_by_turn[nxt.turn_id] != ["question"]:
                add(nxt.turn_id, t.turn_id, "answers", f"{ACT_METHOD}:adjacent_question", "rule_generated")
        if "correction" in a:
            mine = _toks(t.original_text)
            for u in reversed(ts[:i]):                                                           # nearest earlier turn that shares a distinctive token
                if mine & _toks(u.original_text) & {w for w in mine if any(ch.isdigit() for ch in w) or len(w) >= 6}:
                    add(t.turn_id, u.turn_id, "corrects", f"{ACT_METHOD}:correction_cue+shared_token", "rule_generated")
                    break
    return [r for r in out if r[0] in by_id and r[1] in by_id]


# ---- segmentation ---------------------------------------------------------------------------------------------------------------------------
CHAT_GAP_HOURS = 6.0


def _gap_hours(a: Optional[str], b: Optional[str]) -> float:
    if not a or not b:
        return 0.0
    return abs((datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds()) / 3600


def segment(c: ParsedContainer) -> list[dict]:
    """Contiguous topic segments over a container's turns.  A window of three turns smooths the topic score; the topic only changes when a new topic
    beats the current one by 2+ cue hits (hysteresis), a turn with no cue joins the surrounding segment, and in chat a long time gap or no explicit reply
    link starts a new context.  Returns dicts with start_seq/end_seq/topic/confidence/turn_ids."""
    ts = c.turns
    per = [topic_scores(t.original_text) for t in ts]
    labels: list[Optional[str]] = []
    cur: Optional[str] = None
    for i in range(len(ts)):
        win: dict[str, int] = {}
        for j in range(max(0, i - 1), min(len(ts), i + 2)):
            for k, v in per[j].items():
                win[k] = win.get(k, 0) + (v * 2 if j == i else v)
        if not win:
            labels.append(cur)
            continue
        best = max(win, key=lambda k: (win[k], k))
        if cur is None or (best != cur and win[best] >= win.get(cur, 0) + 2):
            cur = best
        labels.append(cur)
    first = next((x for x in labels if x), None)
    labels = [x or first or "general_discussion" for x in labels]

    cuts = {0}
    for i in range(1, len(ts)):
        if labels[i] != labels[i - 1]:
            cuts.add(i)
        elif c.channel == "Chat" and _gap_hours(ts[i - 1].ts, ts[i].ts) > CHAT_GAP_HOURS and not (ts[i].chat or {}).get("reply_to_turn_id"):
            cuts.add(i)
    starts = sorted(cuts)
    segs = []
    for n, s in enumerate(starts):
        e = (starts[n + 1] if n + 1 < len(starts) else len(ts))
        hits = sum(per[k].get(labels[s], 0) for k in range(s, e))
        segs.append(dict(seg_index=n + 1, topic=labels[s], start_seq=ts[s].seq, end_seq=ts[e - 1].seq, turn_ids=[t.turn_id for t in ts[s:e]],
                         confidence=round(min(0.85, 0.3 + 0.1 * hits), 2) if labels[s] != "general_discussion" else 0.3))
    return segs


def enrich(c: ParsedContainer, names: dict[str, str]) -> None:
    """Fill c.segments, c.annotations and c.relations in place.  Annotations: (target_type, target_id, kind, value, provenance, method, confidence)."""
    acts_by_turn: dict[str, list[str]] = {}
    ann = []
    for t in c.turns:
        text = (t.email["authored_text"] if t.email else t.original_text)       # never annotate quoted earlier text as this author's words
        acts = dialogue_acts(text, t.speaker_role)
        acts_by_turn[t.turn_id] = [a for a, _ in acts]
        ann += [("turn", t.turn_id, "dialogue_act", a, "rule_generated", ACT_METHOD, conf) for a, conf in acts]
        ann += [("turn", t.turn_id, k, v, "rule_generated", ACT_METHOD, conf) for k, v, conf in cues(text)]
        ann += [("turn", t.turn_id, "entity_ref", f"{cid}|{n}", "rule_generated", "registry_name_match_v1", 0.6) for cid, n in entity_refs(text, names)]
        if t.speaker_role:
            ann.append(("turn", t.turn_id, "speaker_role", t.speaker_role, "observed", "source_field", None))
    c.segments = segment(c)
    for s in c.segments:
        sid = f"SEG-{c.container_id}-{s['seg_index']:02d}"
        ann.append(("segment", sid, "topic", s["topic"], "rule_generated", SEGMENT_METHOD, s["confidence"]))
    c.annotations = ann
    c.relations = relations(c, acts_by_turn)
