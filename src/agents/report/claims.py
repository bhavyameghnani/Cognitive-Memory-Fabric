"""Structured claim validation: the model emits claims {subject, topic, value, status, evidence_ref}; code checks each against the
cited evidence item's certainty/status/validity window; prose is rendered from the validated claims only."""
from __future__ import annotations

import re
from typing import Optional

from schemas.plan import ExecutionPlan
from schemas.report import Claim, EvidenceItem, Finding

EXPLORATORY = ("exploratory", "hypothetical")
CURRENT_OK = (None, "confirmed", "n/a", "correction")
DATE_RX = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")
_EXPL_WORDS = re.compile(r"\b(explor\w*|consider\w*|hypothetic\w*|scenario)\b", re.I)
_DECIDED_WORDS = re.compile(r"\b(decided|decision|confirmed|current|currently|final)\b", re.I)
_CURRENT_WORDS = re.compile(r"\b(current|currently|now|today)\b", re.I)


def _norm(v: Optional[str]) -> str:
    return re.sub(r"[\s$,]", "", (v or "").lower())


def is_current_item(e: EvidenceItem) -> bool:
    return e.source_class == "memory" and e.memory_type == "semantic" and e.status == "active" and not e.valid_to and e.certainty in CURRENT_OK


def backs_current(e: EvidenceItem, evidence: list[EvidenceItem]) -> bool:
    """A confirmed/correction episode of the same client+topic+value, dated on/after the active semantic version began, backs that current value."""
    if e.memory_type != "episodic" or e.status != "active" or e.certainty not in ("confirmed", "correction") or not e.value:
        return False
    for s in evidence:
        if is_current_item(s) and s.client_id == e.client_id and s.topic == e.topic and _norm(s.value) == _norm(e.value) \
                and (not s.valid_from or (e.event_date or "") >= s.valid_from):
            return True
    return False


def _value_ok(c: Claim, e: EvidenceItem) -> bool:
    if not c.value:
        return True
    v = _norm(c.value)
    if e.value:
        return _norm(e.value) == v
    return v in _norm(e.description + " " + (e.excerpt or ""))


def _dates_ok(c: Claim, e: EvidenceItem) -> Optional[str]:
    haystack = f"{e.description} {e.excerpt or ''}"
    for d in DATE_RX.findall(c.text):
        if e.valid_from and d < e.valid_from:
            return f"date {d} is before the validity window start {e.valid_from}"
        if e.valid_to and d > e.valid_to:
            return f"date {d} is after the validity window end {e.valid_to}"
        if not e.valid_from and not e.valid_to and d not in haystack and d != e.event_date:
            return f"date {d} does not occur in the cited evidence"
    return None


def check_claim(c: Claim, evidence: list[EvidenceItem], allowed_clients: set[str]) -> Optional[str]:
    e = next((x for x in evidence if x.ref == c.evidence_ref), None)
    if e is None:
        return f"unknown evidence ref {c.evidence_ref}"
    if e.client_id and allowed_clients and e.client_id not in allowed_clients:
        return "evidence belongs to a client outside the plan"
    if e.source_class in ("operational_data", "tool_calculation"):
        return "tool values belong in the financial analysis, not in client-state claims"
    if c.status == "current":
        if not (is_current_item(e) or backs_current(e, evidence)):
            return f"a current claim must cite the active semantic memory; {e.ref} is {e.status}/{e.certainty}/{e.memory_type}"
        if _EXPL_WORDS.search(c.text) and not re.search(r"\b(not|before|earlier)\b", c.text, re.I):
            return "claim marked current but its text calls it exploratory"
    elif c.status == "superseded":
        if not (e.status == "superseded" or e.valid_to):
            return f"a superseded claim must cite a superseded memory; {e.ref} is {e.status}"
        if _CURRENT_WORDS.search(c.text) and not re.search(r"\b(was|until|previously|former|earlier|replaced|no longer)\b", c.text, re.I):
            return "claim marked superseded but its text presents it as current"
    elif c.status == "exploratory":
        if e.certainty not in EXPLORATORY:
            return f"an exploratory claim must cite exploratory episodes; {e.ref} is certainty {e.certainty}"
        if _DECIDED_WORDS.search(c.text) and not re.search(r"\b(not|never|no)\b", c.text, re.I):
            return "claim marked exploratory but its text presents it as a decision/current"
    elif c.status == "recommendation":
        if e.certainty != "recommendation":
            return f"a recommendation claim must cite a recommendation; {e.ref} is certainty {e.certainty}"
    if not _value_ok(c, e):
        return f"value {c.value!r} does not match the cited evidence value {e.value!r}"
    return _dates_ok(c, e)


def validate_claims(claims: list[Claim], evidence: list[EvidenceItem], plan: ExecutionPlan) -> tuple[list[Claim], list[dict]]:
    allowed = set(plan.client_ids)
    ok, rejected = [], []
    current_by_topic: dict[tuple, str] = {}
    for c in claims:
        why = check_claim(c, evidence, allowed)
        e = next((x for x in evidence if x.ref == c.evidence_ref), None)
        if not why and c.status == "current" and e is not None:
            key = (e.client_id, _norm(e.topic or c.topic))
            prev = current_by_topic.get(key)
            if prev is not None and _norm(prev) != _norm(e.value or c.value):
                why = f"more than one current value for topic {key[1]!r} ({prev} vs {e.value or c.value})"
            else:
                current_by_topic[key] = e.value or c.value or ""
        if why:
            rejected.append({"claim": c.model_dump(), "reason": why})
        else:
            ok.append(c)
    return ok, rejected


# ---- prose rendered from validated claims only ------------------------------------------------------------------------
def _subject(c: Claim, e: EvidenceItem) -> str:
    s = c.subject.strip()
    return s if s and len(s) <= 40 and not re.search(r"\d", s) else (e.client_id or "The client")


def _src(e: EvidenceItem) -> str:
    return f" ({e.interaction_id} turn {e.turn_index})" if e.interaction_id and e.turn_index is not None else ""


def render_claim(c: Claim, e: EvidenceItem) -> tuple[str, Finding]:
    """Return (section, Finding). section in key_findings / historical_context."""
    who, topic = _subject(c, e), (e.topic or c.topic).replace("_", " ")
    val = e.value or c.value or "the value in the cited memory"
    ref = [e.ref]
    if c.status == "current":
        return "key_findings", Finding(text=f"{who}'s {topic} is {val}, the current confirmed knowledge" + (f" (valid from {e.valid_from})" if e.valid_from else "") + f"{_src(e)}.", refs=ref)
    if c.status == "superseded":
        win = f"valid {e.valid_from or 'earlier'} until {e.valid_to}" if e.valid_to else f"valid from {e.valid_from}" if e.valid_from else "no longer valid"
        return "historical_context", Finding(text=f"{who}'s {topic} was {val} ({win}); it has been superseded and is no longer current{_src(e)}.", refs=ref)
    if c.status == "exploratory":
        when = f" on {e.event_date}" if e.event_date else ""
        return "historical_context", Finding(text=f"{who} explored {topic} {val}{when}; this was exploratory, not a decision{_src(e)}.", refs=ref)
    when = f" on {e.event_date}" if e.event_date else ""
    return "historical_context", Finding(text=f"The advisor recommended {topic} {val}{when}; this is an advisor recommendation, not a client decision{_src(e)}.", refs=ref)


def render_claims(claims: list[Claim], evidence: list[EvidenceItem]) -> dict[str, list[Finding]]:
    out: dict[str, list[Finding]] = {"key_findings": [], "historical_context": []}
    for c in claims:
        e = next(x for x in evidence if x.ref == c.evidence_ref)
        sec, f = render_claim(c, e)
        out[sec].append(f)
    return out


def summary_from_claims(claims: list[Claim], evidence: list[EvidenceItem]) -> Optional[str]:
    cur = [(c, next(x for x in evidence if x.ref == c.evidence_ref)) for c in claims if c.status == "current"]
    if not cur:
        return None
    parts = []
    for c, e in cur[:3]:
        parts.append(f"{_subject(c, e)}'s {(e.topic or c.topic).replace('_', ' ')} is {e.value or c.value}" + (f" (valid from {e.valid_from})" if e.valid_from else "") + f" [{e.ref}]")
    s = "; ".join(parts) + "."
    old = [x for x in evidence if x.status == "superseded" and x.value and any(_norm(x.topic) == _norm(e.topic) for _, e in cur)]
    if old:
        s += f" It replaced {old[0].value} [{old[0].ref}], which is no longer current."
    expl = [x for x in evidence if x.certainty in EXPLORATORY and x.value and x.source_class != "tool_calculation"]
    if expl:
        s += " Other values were explored but not decided."
    return s
