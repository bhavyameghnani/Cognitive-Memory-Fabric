"""Deterministic rules: sentence salience, cue detection, topic/value inference, value support and the certainty guard.

Everything here is pure (no database, no LLM) so each rule is unit-testable.  The certainty guard lives here, in code,
not only in the prompt: it may only DOWNGRADE certainty, never upgrade it.
"""
from __future__ import annotations

import re
from typing import Optional

from agents.memory.config import CERTAINTY_RANK, NUMERIC_TOPICS, FilterConfig
from agents.planner.topics import TOPIC_KEYWORDS, TOPICS, infer_topics, topic_hits


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("’", "'").replace("“", '"').replace("”", '"')).strip().lower()


def split_sentences(text: str) -> list[str]:
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'$])", text.strip()) if s.strip()]


# ------------------------------------------------------------------------------------------------ cues
def _rx(*pats: str) -> re.Pattern:
    return re.compile("|".join(f"(?:{p})" for p in pats), re.I)


_NEGATED_DECISION = _rx(r"\b(?:haven't|have not|hasn't|hadn't|not|never|yet to)\s+(?:yet\s+)?(?:fully\s+)?(?:decided|made (?:that|a|the) decision|committed|chosen|agreed)\b",
                        r"\bnothing (?:has been |is |was )?(?:decided|settled)\b")
DECISION = _rx(r"\b(?:we|you|i)(?:'ve| have)\s+(?:both\s+)?(?:decided|chosen|agreed|settled)\b", r"\b(?:we|you|i) (?:decided|chose)\b",
               r"\bdecided (?:to|against|not|on)\b", r"\bgo ahead\b", r"\bthe working plan i'?m recording is\b", r"\blet'?s (?:go with|do it|proceed|lock)\b",
               r"\byes\b[^.]*\.\s*(?:put|set) aside\b", r"\bmove on it\b")
EXPLORATORY = _rx(r"still weighing", r"idea and not an instruction", r"\bmaybe\b", r"what if", r"\bleaning\b", r"thinking (?:about|out loud|of)", r"\bconsidering\b",
                  r"\bexploring\b", r"\btoying\b", r"\bmight\b", r"interest and nothing more", r"\bperhaps\b", r"wondering", r"not a (?:fixed )?decision",
                  r"haven't (?:decided|made)", r"as something you'?re (?:considering|exploring)")
HYPOTHETICAL = _rx(r"\bsuppose\b", r"\bhypothetical", r"\bscenarios?\b", r"\bif (?:we|you|i|the|it|she|he)\b[^.]*\b(?:were|was|would|could)\b", r"\bwould it work if\b")
CORRECTION = _rx(r"corrected my own record", r"\bi'?ve corrected\b", r"was too strong", r"\bi should clarify\b", r"\bimprecise\b", r"wording of my file note",
                 r"\bi misstated\b", r"\bi was wrong\b", r"\bto correct (?:the|my)\b")
RECOMMENDATION = _rx(r"\bi(?:'d| would) (?:recommend|suggest|propose|want closer)\b", r"\bi (?:recommend|suggest|propose)\b", r"\bmy recommendation\b")
UNCONFIRMED = _rx(r"\bunconfirmed\b", r"not (?:yet )?(?:confirmed|verified|checked)", r"until you have checked", r"your own estimate", r"\bbelieving\b")
PROCEDURE_CUE = _rx(r"\b(?:standard|our) (?:process|procedure|practice)\b", r"\bgoing forward\b", r"\bfrom now on\b", r"\bevery time\b",
                    r"\balways (?:check|record|use|ask|confirm|retrieve)\b", r"\bchecklist\b", r"\bworkflow\b", r"\bwhenever\b")
# routine templates that carry operational data or housekeeping, not client memory
TEMPLATE = _rx(r"^(?:good (?:morning|afternoon)|hi|hello|hey|thanks|thank you|okay|alright|sure|yes|no|correct|agreed|good|fine|understood|that's fair|that's right)\b[^.]{0,30}[.!]?$",
               r"investable portfolio is about", r"\bportfolio (?:is|at) (?:about|roughly|\$)", r"equit(?:y|ies) (?:share|are|is) (?:is |are )?(?:about|around|near)",
               r"asset mix is close", r"here's the snapshot", r"up about [\d.]+ percent since", r"spending against what we assumed", r"i'?m seeing about \$",
               r"set against (?:your )?salary", r"no action needed on your side", r"keep monitoring", r"talk soon", r"written (?:recap|summary)", r"notes in the portal",
               r"pencil in", r"book us for", r"does \w+day,? \w+ \d+ work", r"good question", r"shall we begin", r"i'?ve put the numbers", r"nothing urgent",
               r"i'?d only propose changes", r"^from (?:my|your) side\b", r"let me (?:say it back|read it back)", r"here's what i'?m taking away", r"stop me if i'?ve got anything wrong",
               r"for the record, here is what we established", r"(?:here's|here is) where we landed", r"make sure we'?re on the same page", r"let me check i'?ve understood", r"\bwe last spoke\b", r"^since [a-z]+ \d+, when we focused", r"today's focus is", r"for today i have", r"^last time, on ")
RECAP_LEAD = _rx(r"for the record", r"read it back", r"say it back", r"where we landed", r"taking away", r"let me record", r"same page", r"to summari[sz]e")
FIGURE = re.compile(r"\d|\bpercent\b|\b(?:thousand|million|hundred)\b", re.I)
# high-precision recap wording used by the recall safety net (rule candidates)
RECAP_CONFIRMED = _rx(r"\b(?:you've|you have|we've|we have) decided\b", r"\bthe working plan i'?m recording is\b")
RECAP_EXPLORATORY = _rx(r"still weighing", r"idea and not an instruction")
RECAP_CORRECTION = _rx(r"corrected my own record", r"\bi'?ve corrected\b")
CONDITIONAL_ON_CLOSING = re.compile(r"\b(?:at|upon|after) closing\b|\bonce the sale closes\b|\bconditional on\b", re.I)


def has_decision(text: str) -> bool:
    t = norm(text)
    return bool(DECISION.search(_NEGATED_DECISION.sub(" ", t)))


def has_exploratory(text: str) -> bool:
    return bool(EXPLORATORY.search(norm(text)))


def has_hypothetical(text: str) -> bool:
    return bool(HYPOTHETICAL.search(norm(text)))


def has_unconfirmed(text: str) -> bool:
    return bool(UNCONFIRMED.search(norm(text)))


def has_correction(text: str) -> bool:
    return bool(CORRECTION.search(norm(text)))


def is_template(sentence: str) -> bool:
    return bool(TEMPLATE.search(norm(sentence)))


def recap_certainty(sentence: str) -> Optional[str]:
    """Recall safety net: the recap wording the advisor uses is high precision.  Order matters: correction first."""
    t = norm(sentence)
    if RECAP_CORRECTION.search(t):
        return "correction"
    if has_unconfirmed(t) and (RECAP_CONFIRMED.search(t) or RECAP_EXPLORATORY.search(t)):
        return "unconfirmed"
    if RECAP_EXPLORATORY.search(t) and not has_decision(t):
        return "exploratory"
    if RECAP_CONFIRMED.search(_NEGATED_DECISION.sub(" ", t)):
        return "confirmed"
    return None


# ------------------------------------------------------------------------------------------------ stage 1: salience
def score_sentence(sentence: str, speaker: str, cfg: FilterConfig) -> tuple[float, list[str]]:
    t = norm(sentence)
    s, why = 0.0, []
    cue = False
    for name, ok, w in (("decision", has_decision(t), cfg.w_decision), ("correction", has_correction(t), cfg.w_correction),
                        ("exploratory", has_exploratory(t), cfg.w_exploratory), ("recommendation", bool(RECOMMENDATION.search(t)), cfg.w_recommendation),
                        ("unconfirmed", has_unconfirmed(t), cfg.w_exploratory),
                        ("procedure_wording", speaker == "Advisor" and bool(PROCEDURE_CUE.search(t)), cfg.w_procedure)):
        if ok:
            s, cue = s + w, True
            why.append(name)
    if cue and RECAP_LEAD.search(t):
        s += cfg.w_recap
        why.append("recap")
    hits = topic_hits(sentence)
    if hits:
        s += cfg.w_topic * min(2, len(hits))
        why.append("topic:" + hits[0])
    if FIGURE.search(sentence):
        s += cfg.w_figure
        why.append("figure")
    if speaker == "Client":
        s += cfg.w_client_turn
    if len(sentence) < cfg.min_sentence_chars and not cue:
        s -= cfg.p_template
        why.append("too_short")
    elif (sentence.rstrip().endswith("?") or re.match(r"i wanted to ask\b", t)) and not cue:    # a bare question is not a statement of fact or intent
        s -= cfg.p_template
        why.append("question")
    elif is_template(sentence) and not cue:
        s -= cfg.p_template
        why.append("template")
    return s, why


# ------------------------------------------------------------------------------------------------ numbers
_UNITS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_TENS = {w: 20 + 10 * i for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())}
_MULT = {"thousand": 1000, "million": 1_000_000, "k": 1000}
_NUM_TOKEN = re.compile(r"[a-z]+|\d[\d,]*(?:\.\d+)?")


def _fmt(x: float) -> str:
    return str(int(x)) if float(x).is_integer() else str(round(x, 4)).rstrip("0").rstrip(".")


def extract_numbers(text: str) -> list[str]:
    """Ordered numbers in text, from digits ('$220,000', '2.11 million', '35%') and spelled out ('six hundred thousand', 'thirty-five')."""
    toks = _NUM_TOKEN.findall(text.lower().replace("-", " ").replace("’", "'"))
    out: list[str] = []
    total, cur, active = 0.0, 0.0, False

    def flush() -> None:
        nonlocal total, cur, active
        if active:
            out.append(_fmt(total + cur))
        total, cur, active = 0.0, 0.0, False

    i = 0
    while i < len(toks):
        w = toks[i]
        if w[0].isdigit():
            flush()
            v = float(w.replace(",", ""))
            if i + 1 < len(toks) and toks[i + 1] in _MULT:
                out.append(_fmt(v * _MULT[toks[i + 1]]))
                out.append(_fmt(v))
                i += 1
            else:
                out.append(_fmt(v))
        elif w in _UNITS or w in _TENS:
            cur += _UNITS.get(w, _TENS.get(w, 0))
            active = True
        elif w == "hundred" and active:
            cur = max(cur, 1) * 100
        elif w in ("thousand", "million") and active:
            total += max(cur, 1) * _MULT[w]
            cur = 0
        elif w == "and" and active:
            pass
        else:
            flush()
        i += 1
    flush()
    return out


_STOP = {"percent", "months", "month", "years", "year", "about", "around", "roughly", "dollars", "dollar", "thousand", "million", "with", "from", "that", "this", "the", "and", "for"}


def _prefixes(text: str) -> set[str]:
    return {w[:4] for w in re.findall(r"[a-z]+", norm(text).replace("-", " ")) if len(w) >= 3}


def value_supported(value: str, excerpt: str) -> bool:
    """A value is kept only if the excerpt supports it: its numbers appear in the excerpt and most of its words do too."""
    if not value or not value.strip():
        return False
    nums_v = set(extract_numbers(value))
    if nums_v and not nums_v <= set(extract_numbers(excerpt)):
        return False
    words = [w for w in re.findall(r"[a-z]+", norm(value).replace("-", " ").replace("_", " ")) if len(w) >= 4 and w not in _STOP]
    if words:
        ex = _prefixes(excerpt)
        return sum(w[:4] in ex for w in words) * 2 >= len(words)
    return bool(nums_v) or norm(value) in norm(excerpt)


# ------------------------------------------------------------------------------------------------ topic / value inference
_AGE = r"(?:\d{2}|(?:fifty|sixty|seventy)(?:[- ](?:one|two|three|four|five|six|seven|eight|nine))?)"
RETIRE_AGE_RE = re.compile(rf"\bretir(?:e|es|ed|ing|ement)\b[^.;:]{{0,30}}?\b(?:at|age|date|until|by|of|to)\s+(?:age\s+)?({_AGE})\b", re.I)
_RETIRE_WORDS = re.compile(r"\bretirement[- ](?:age|date)\b|\bplanning date\b|\bretire(?:ment)? (?:at|by) ", re.I)
_PRIORITY = [(re.compile(r"survivor (?:amount|benefit|payment|income|option|election)", re.I), "insurance_estate"), (re.compile(r"employee[- ]owner|seller note|\besop\b", re.I), "business_sale"), (re.compile(r"care reserve", re.I), "care_costs"),
             (re.compile(r"maplewood", re.I), "housing_property"),
             (re.compile(r"\bequit(?:y|ies)\b[^.]{0,60}?(?:\d+|percent)|(?:percent|%) (?:in |of )?(?:the )?equit|equity allocation", re.I), "asset_allocation")]


def priority_topic(text: str) -> Optional[str]:
    """High-precision overrides.  Retirement timing beats retirement income: the earlier build filed Kenji's January plan of 65 under
    retirement_income because 'a month after tax' outscored 'retire' in the keyword lexicon."""
    if RETIRE_AGE_RE.search(text) or _RETIRE_WORDS.search(text):
        return "retirement_age"
    for rx, topic in _PRIORITY:
        if rx.search(text):
            return topic
    return None


def infer_topic(text: str) -> str:
    return priority_topic(text) or (infer_topics(text, 1) or ["other"])[0]


def _age(s: str) -> Optional[str]:
    for n in extract_numbers(s):
        if n.isdigit() and 45 <= int(n) <= 80:
            return n
    return None


def infer_value(topic: str, text: str, certainty: Optional[str] = None) -> Optional[str]:
    """Deterministic fallback when the model returns an empty value."""
    if topic == "retirement_age":
        m = re.search(rf"\b({_AGE})\s+(?:remains|is still|stays)\b", text, re.I)
        if m and (certainty == "correction" or has_correction(text)):
            return _age(m.group(1))
        m = re.search(rf"\bfrom ({_AGE}) to ({_AGE})\b", text, re.I)
        if m and re.search(r"retire|planning date", text, re.I):
            return _age(m.group(2))
        m = RETIRE_AGE_RE.search(text)
        if m:
            return _age(m.group(1))
        ages = [n for n in extract_numbers(text) if n.isdigit() and 45 <= int(n) <= 80]   # "We've decided: 63" (topic known from the meeting)
        return ages[0] if len(ages) == 1 and has_decision(text) else None
    if topic == "asset_allocation":
        pct = r"((?:\d+(?:\.\d+)?)|(?:[a-z]+(?:[- ][a-z]+)?))\s*(?:%|percent)"
        m = re.search(rf"\bfrom [^.]*?\bto (?:about |around |roughly )?{pct}", text, re.I)
        m = m or re.search(pct, text, re.I)
        return _num_only(m.group(1)) if m else None
    return None


def _num_only(s: str) -> Optional[str]:
    n = extract_numbers(s)
    return n[0] if n else None


def normalise_value(topic: str, value: Optional[str]) -> Optional[str]:
    """Short normalised values only: numeric topics keep a bare number, others a lower-cased short phrase."""
    if value is None or not value.strip():
        return None
    if topic in NUMERIC_TOPICS:
        return _num_only(value)
    return re.sub(r"\s+", " ", value.strip().lower())[:60] or None


def normalise_subject(topic: str, subject: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "_", (subject or "").lower()).strip("_")[:60]
    return topic if (topic in {"retirement_age", "asset_allocation"} or not s) else s


def lexicon_topic_hits(text: str) -> int:
    return len(topic_hits(text))


# ------------------------------------------------------------------------------------------------ certainty guard
def guard_certainty(certainty: str, excerpt: str, source_role: str) -> tuple[str, Optional[str]]:
    """Re-classify certainty from the excerpt.  Returns (new_certainty, reason or None).  Downgrade only; upgrades are impossible by construction."""
    dec, exp, hyp, cor = has_decision(excerpt), has_exploratory(excerpt), has_hypothetical(excerpt), has_correction(excerpt)
    new, why = certainty, None
    if certainty == "correction" and not cor:
        new, why = ("exploratory", "correction without correction wording; exploratory wording") if exp and not dec else ("unconfirmed", "correction without correction wording")
    elif certainty == "confirmed":
        if has_unconfirmed(excerpt):
            new, why = "unconfirmed", "the text itself treats the item as unconfirmed"
        elif (exp or hyp) and not dec:
            new, why = "exploratory", "exploratory/hypothetical wording and no decision wording"
        elif not dec:
            new, why = "unconfirmed", "no decision wording in the excerpt"
    elif certainty == "recommendation":
        if source_role != "Advisor":
            new, why = "unconfirmed", "recommendation stated by a non-advisor turn"
        elif (exp or hyp) and not dec and not RECOMMENDATION.search(norm(excerpt)):
            new, why = "exploratory", "exploratory wording and no recommendation wording"
    if CERTAINTY_RANK[new] > CERTAINTY_RANK[certainty]:  # defensive: can never happen, but upgrades are forbidden
        return certainty, None
    return new, why


def confidence_for(certainty: str, origin: str, downgraded: bool) -> float:
    base = {"confirmed": 0.85, "correction": 0.8, "recommendation": 0.65, "exploratory": 0.6, "hypothetical": 0.5, "unconfirmed": 0.4}.get(certainty, 0.4)
    base += 0.03 if origin == "conversation_rule" else 0.0
    return round(max(0.05, min(1.0, base - (0.1 if downgraded else 0.0))), 2)


def event_type_for(certainty: str) -> str:
    return {"confirmed": "decision", "correction": "correction", "recommendation": "recommendation", "exploratory": "exploration",
            "hypothetical": "hypothetical"}.get(certainty, "statement")


def valid_topic(t: str) -> bool:
    return t in TOPICS and t != "other"
