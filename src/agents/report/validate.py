"""Step 5: deterministic validation of model prose (refs, numbers, citations of interactions, status wording) and the
deterministic fallback report built straight from the evidence."""
from __future__ import annotations

import re
from typing import Optional

from schemas.plan import ExecutionPlan
from schemas.report import EvidenceItem, Finding

NUM = re.compile(r"(?<![\w.])(\$?)(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?\s*(%|million\b|billion\b|thousand\b|[kKmMbB]\b)?")
ID_RX = re.compile(r"\b[A-Za-z]+(?:-[A-Za-z0-9]+)+\b")  # INT-090, CLIENT-001, EX-REQ-ab12-03
REF_RX = re.compile(r"\bE\d+\b")
MULT = {"k": 1e3, "thousand": 1e3, "m": 1e6, "million": 1e6, "b": 1e9, "billion": 1e9}


def _strip(text: str) -> str:
    return REF_RX.sub(" ", ID_RX.sub(" ", text))


def numbers(text: str, scaled: bool = True) -> list[tuple[float, float, int, str, bool]]:
    """(value, raw_number, decimals, suffix, has_currency) for every number in text."""
    out = []
    for m in NUM.finditer(_strip(text)):
        cur, whole, dec, suf = m.group(1), m.group(2).replace(",", ""), m.group(3) or "", (m.group(4) or "").lower()
        raw = float(whole + dec)
        mult = MULT.get(suf, 1.0) if scaled else 1.0
        out.append((raw * mult, raw, len(dec) - 1 if dec else 0, suf, bool(cur)))
    return out


def evidence_text(evidence: list[EvidenceItem], extra: str = "") -> str:
    parts = [extra]
    for e in evidence:
        parts += [e.description, e.excerpt or "", e.value or "", e.valid_from or "", e.valid_to or "", e.event_date or ""]
    return " \n ".join(parts)


class NumberChecker:
    def __init__(self, haystack: str) -> None:
        self.have = [n[0] for n in numbers(haystack)]
        self.have_plain = [n[1] for n in numbers(haystack, scaled=False)]

    def ok(self, text: str) -> list[str]:
        """Return the numbers in text that do not occur in the evidence ([] means all fine)."""
        bad = []
        for val, raw, dec, suf, cur in numbers(text):
            if not suf and not cur and raw == int(raw) and dec == 0 and (raw <= 12 or 1900 <= raw <= 2100):
                continue  # small counts and years
            tol = 10 ** -dec if dec else 1.0
            scale = val / raw if raw else 1.0
            hit = any(abs(h - val) <= tol * scale for h in self.have)
            if not hit and suf == "%":
                hit = any(abs(h * 100 - val) <= tol for h in self.have)
            if not hit and not suf:
                hit = any(abs(h - raw) <= tol for h in self.have_plain)
            if not hit:
                bad.append(f"{raw:g}{suf}")
        return bad


def clean_refs(refs: list[str], valid: set[str]) -> tuple[list[str], int]:
    good = [r for r in dict.fromkeys(refs) if r in valid]
    return good, len(set(refs)) - len(good)


def strip_bad_inline_refs(text: str, valid: set[str]) -> tuple[str, int]:
    n = 0

    def sub(m: re.Match) -> str:
        nonlocal n
        if m.group(1) in valid:
            return m.group(0)
        n += 1
        return ""
    return re.sub(r"\s?\b(E\d+)\b", sub, text), n


def check_interaction_cites(text: str, evidence: list[EvidenceItem]) -> Optional[str]:
    """Never cite an interaction/turn that is not in the evidence list."""
    pairs = {(e.interaction_id, e.turn_index) for e in evidence if e.interaction_id}
    ids = {i for i, _ in pairs}
    turns = {t for _, t in pairs if t is not None}
    for m in re.finditer(r"\b(INT-\d+)(?:[,\s(]*turns?\s*(\d+))?", text):
        iid, turn = m.group(1), m.group(2)
        if iid not in ids:
            return f"cites {iid}, which is not in the evidence"
        if turn is not None and (iid, int(turn)) not in pairs:
            return f"cites {iid} turn {turn}, which is not in the evidence"
    for m in re.finditer(r"\bturns?\s*(\d+)", text):
        if int(m.group(1)) not in turns:
            return f"cites turn {m.group(1)}, which is not in the evidence"
    return None


# ---- meaning guard: wording that contradicts the status of a cited value ----------------------------------------------
PAST_MARK = re.compile(r"\b(was|were|until|previously|former|formerly|earlier|replaced|replacing|superseded|prior|original|instead of|not|no longer|explored|considered|considering)\b", re.I)


def _has(sentence: str, v: str) -> bool:
    return re.search(rf"(?<![\d.]){re.escape(v)}(?![\d])", sentence) is not None


def value_sets(evidence: list[EvidenceItem]) -> tuple[set[str], set[str]]:
    cur = {e.value for e in evidence if e.value and e.status == "active" and e.memory_type == "semantic" and not e.valid_to}
    other = {e.value for e in evidence if e.value and (e.status == "superseded" or e.valid_to or e.certainty in ("exploratory", "hypothetical"))}
    return cur, other - cur


def status_conflict(text: str, evidence: list[EvidenceItem]) -> Optional[str]:
    cur, non_cur = value_sets(evidence)
    for s in re.split(r"(?<=[.;!?])\s+", text):
        low = s.lower()
        for v in non_cur:
            if _has(s, v) and re.search(r"\b(current|currently|now|today|present)\b", low) and not PAST_MARK.search(low):
                return f"presents non-current value {v} as current"
        for v in cur:
            if _has(s, v) and re.search(r"\b(still|currently|remains?|is|are)\s+(exploratory|hypothetical|unconfirmed|only being considered)\b|\bexploratory\s+(at|value|target)\b", low):
                return f"presents the current value {v} as exploratory"
    return None


def check_prose(text: str, evidence: list[EvidenceItem], nc: NumberChecker) -> Optional[str]:
    bad = nc.ok(text)
    if bad:
        return "number(s) not in evidence: " + ", ".join(bad)
    return check_interaction_cites(text, evidence) or status_conflict(text, evidence)


def filter_findings(items: list[Finding], evidence: list[EvidenceItem], nc: NumberChecker, rec: dict) -> list[Finding]:
    valid = {e.ref for e in evidence}
    out = []
    for f in items:
        refs, n_bad = clean_refs(f.refs, valid)
        rec["invalid_refs_removed"] += n_bad
        text, n_in = strip_bad_inline_refs(f.text, valid)
        rec["invalid_refs_removed"] += n_in
        if not refs:
            rec["findings_dropped"].append({"text": f.text[:120], "reason": "no valid evidence reference"})
            continue
        why = check_prose(text, evidence, nc)
        if why:
            rec["findings_dropped"].append({"text": f.text[:120], "reason": why})
            continue
        out.append(Finding(text=text.strip(), refs=refs))
    return out


# ---- deterministic fallback ---------------------------------------------------------------------------------------------
def _label(e: EvidenceItem) -> str:
    return e.topic or "item"


# ---- P1.4.3: evidence authority.  Presence in the retrieval context is not proof of truth ---------------------------------------
def is_definitive(e: EvidenceItem) -> bool:
    """Tool results (evidence_class None) and VERIFIED memories may support a definitive statement; unverified / contradicted / ambiguous / open-conflict memories may not."""
    return e.evidence_class in (None, "verified")


def authority_notes(evidence: list[EvidenceItem]) -> list[str]:
    """One code-written line per memory that is shown but may NOT be relied on, so the reader (and the model) sees the review requirement."""
    out = []
    for e in evidence:
        if e.evidence_class in (None, "verified"):
            continue
        what = {"unverified": "unverified - needs confirmation, not an established fact", "contradicted": "contradicted / disputed - not used as support",
                "ambiguous": "exploratory or unconfirmed - not a decision", "open_conflict": "part of an open conflict - review required"}[e.evidence_class]
        out.append(f"[{e.ref}] {e.topic or 'item'}{(' ' + e.value) if e.value else ''}: {what}" + (f" (conflict {', '.join(e.conflict_ids)})" if e.conflict_ids else ""))
    return out


def authority_split(items: list[Finding], evidence: list[EvidenceItem], rec: dict) -> tuple[list[Finding], list[str]]:
    """A finding that cites ANY unverified / contradicted / open-conflict memory is not a definitive finding: it is withdrawn and a code-written 'needs confirmation' line replaces it
    (the withheld text is NOT echoed - it may be exactly the fabricated statement).  AMBIGUOUS (exploratory) evidence may still be cited by a finding that says so: the evidence
    verifier checks the modality of the wording against it, and claims about it are limited to exploratory / recommendation status."""
    by = {e.ref: e for e in evidence}
    kept, moved = [], []
    for f in items:
        bad = [by[r] for r in f.refs if r in by and by[r].evidence_class in ("unverified", "contradicted", "open_conflict")]
        if bad:
            moved.append("Withheld pending confirmation: a statement rested on " + ", ".join(f"{b.ref} ({b.evidence_class})" for b in bad) + ", which is not verified evidence.")
            rec.setdefault("findings_demoted", []).append({"text": f.text[:120], "refs": [b.ref for b in bad], "classes": sorted({str(b.evidence_class) for b in bad})})
        else:
            kept.append(f)
    return kept, moved


def deterministic_sections(evidence: list[EvidenceItem]) -> dict:
    key, hist, fin = [], [], []
    for e in evidence:
        v = f" is {e.value}" if e.value else ""
        if e.evidence_class in ("unverified", "contradicted", "open_conflict"):
            continue                                   # never a finding: authority_notes() lists it under uncertainties
        if e.evidence_class == "ambiguous":
            if len(hist) < 4:
                hist.append(Finding(text=f"{_label(e)} (ambiguous: {e.certainty or e.status}): {e.description.split(': ', 1)[-1]}", refs=[e.ref], origin="rendered"))
            continue
        if e.source_class in ("operational_data", "tool_calculation"):
            if len(fin) < 4:
                fin.append(Finding(text=("Synthetic " if e.synthetic else "") + e.description, refs=[e.ref], origin="rendered"))
        elif e.status == "active" and e.memory_type == "semantic" and not e.valid_to and len(key) < 4:
            key.append(Finding(text=f"Current {_label(e)}{v}" + (f" (valid from {e.valid_from})" if e.valid_from else "") + f": {e.description.split(': ', 1)[-1]}", refs=[e.ref], origin="rendered"))
        elif e.status == "superseded" or e.valid_to:
            if len(hist) < 4:
                hist.append(Finding(text=f"Earlier {_label(e)}{v} was superseded (valid {e.valid_from or '?'} to {e.valid_to or 'open'}).", refs=[e.ref], origin="rendered"))
        elif len(hist) < 4:
            lab = f" ({e.certainty})" if e.certainty and e.certainty != "n/a" else ""
            hist.append(Finding(text=f"{_label(e)}{lab}: {e.description.split(': ', 1)[-1]}", refs=[e.ref], origin="rendered"))
    return {"key_findings": key, "historical_context": hist, "financial_analysis": fin}


def deterministic_summary(evidence: list[EvidenceItem], plan: ExecutionPlan) -> str:
    cur = [e for e in evidence if e.status == "active" and e.memory_type == "semantic" and not e.valid_to and e.value and is_definitive(e)]
    if cur:
        parts = [f"{e.topic} is {e.value}" + (f" since {e.valid_from}" if e.valid_from else "") + f" [{e.ref}]" for e in cur[:3]]
        return "Based on the available memory: " + "; ".join(parts) + "."
    tools = [e for e in evidence if e.synthetic]
    if tools:
        return f"Synthetic tool results are available [{tools[0].ref}]; see the financial analysis for the exact figures and their as-of dates."
    return f"Evidence was found ({len(evidence)} items) but it does not state a definite answer; see the findings and uncertainties."


def no_evidence_summary() -> str:
    return "This question cannot be answered reliably: no memory, conversation or tool evidence was available."
