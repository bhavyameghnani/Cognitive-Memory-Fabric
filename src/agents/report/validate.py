"""Step 5: deterministic validation of model prose (refs, numbers, citations of interactions, status wording) and the
deterministic fallback report built straight from the evidence."""
from __future__ import annotations

import hashlib
import re
from typing import Optional

from schemas.plan import ExecutionPlan
from schemas.report import EvidenceItem, Finding, is_source_turn

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


_FIGURES = re.compile(r"^(number\(s\) not in evidence):.*$")
_NON_CURRENT = re.compile(r"^presents non-current value .+ as current$")
_CURRENT_AS_EXPLORATORY = re.compile(r"^presents the current value .+ as exploratory$")


def public_reason(why: str) -> str:
    """A report-layer refusal reason as the API shows it: the category, without the figures / values of the refused text (the reasons quote them)."""
    why = _FIGURES.sub(r"\1", why or "")
    why = _NON_CURRENT.sub("presents a non-current value as current", why)
    return _CURRENT_AS_EXPLORATORY.sub("presents the current value as exploratory", why)


def withheld_ref(text: str) -> dict:
    """How a withheld / dropped finding is recorded in `validation`, which is returned by the API: a fingerprint and a length, NEVER the text (it may be exactly the fabricated
    statement that was refused).  The fingerprint lets a reviewer match the record to the model output kept in the server-side audit trail."""
    return {"finding_sha": hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:12], "chars": len(text or "")}


def filter_findings(items: list[Finding], evidence: list[EvidenceItem], nc: NumberChecker, rec: dict) -> list[Finding]:
    valid = {e.ref for e in evidence}
    out = []
    for f in items:
        refs, n_bad = clean_refs(f.refs, valid)
        rec["invalid_refs_removed"] += n_bad
        text, n_in = strip_bad_inline_refs(f.text, valid)
        rec["invalid_refs_removed"] += n_in
        if not refs:
            rec["findings_dropped"].append({**withheld_ref(f.text), "reason": "no valid evidence reference"})
            continue
        why = check_prose(text, evidence, nc)
        if why:
            rec["findings_dropped"].append({**withheld_ref(f.text), "reason": public_reason(why)})
            continue
        out.append(Finding(text=text.strip(), refs=refs))
    return out


# ---- source-record statements (retrieved conversation turns) -----------------------------------------------------------------
# A source turn proves that a turn CONTAINS a statement.  A model-written finding about it must name the recorded speaker and the turn, and may not turn the statement into a fact, a decision,
# an agreement, an authorisation or a current state (the exact words of the turn may of course be quoted).
_OVERREACH = re.compile(r"\b(decided|decides|decision|agreed|agrees|agreement|accepted|accepts|signed|signs|authori[sz]\w*|instruct\w*|approved|approves|consent\w*|confirmed|confirms|"
                        r"currently|current|final|finalised|finalized|committed)\b", re.I)
_NEG = re.compile(r"\b(?:not|never|no|without|nor|neither|cannot)\b|n['’]t\b", re.I)
_CLAUSE_BREAK = re.compile(r"[.;:!?,\n]|\b(?:but|and|yet|however|although|though|whereas|while|then|because|so)\b", re.I)
_HARD_BREAK = re.compile(r"[.;:!?\n]")
# A negation is accepted as covering an overreach term ONLY in one of two explicit shapes, and only when it is the single negation of its clause:
#   direct:    "... has not signed", "... never agreed", "... no decision", "... no longer current"  (the negator sits right before the term, adverbs aside)
#   epistemic: "... do not establish / prove / show that Rebecca agreed"  (the negator governs a verb of evidence, the term is inside its complement)
# Everything else ("no one doubts that X signed", "do not doubt X agreed", "not hesitating about the offer she accepted", "not true that X has not signed") is treated as an assertion.
_DIRECT_NEG = re.compile(r"(?:\b(?:not|never|no)|n['’]t)\s+(?:(?:yet|been|fully|formally|actually|really|ever|any|longer)\s+)*$", re.I)
_EPISTEMIC_NEG = re.compile(r"(?:\b(?:not|never)|n['’]t)\s+(?:establish|prove|show|demonstrate|indicate|imply|mean|record|state|say)\w*\s+(?:that\s+)?(?:\w+\s+){0,3}$", re.I)
_QUOTED = re.compile(r"\"([^\"“”]{3,})\"|“([^\"“”]{3,})”")          # delimiters must match: straight-straight or curly-curly
QUOTE_MIN_WORDS = 4          # a shorter quoted fragment is never exempt: it is too small to prove it was not lifted out of context
_QUALIFIER = re.compile(r"\b(?:not|never|no|without|nor|neither|cannot|wrong|incorrect|incorrectly|mistake\w*|error|erroneous|correct(?:ed|ion|ing)|retract\w*|overstat\w+|but|however|"
                        r"although|though|unless|except|until|rather|instead)\b|n['’]t\b", re.I)
_LEFT_QUALIFIER = re.compile(r"\b(?:if|whether|suppose|imagine)\b", re.I)


def _clause_before(text: str, pos: int) -> str:
    """The part of the clause containing `pos` that comes before it.  A clause ends at punctuation or a contrast / addition conjunction, so a negation in an EARLIER assertion cannot reach it."""
    start = 0
    for m in _CLAUSE_BREAK.finditer(text, 0, pos):
        start = m.end()
    return text[start:pos]


def _negated(prefix: str) -> bool:
    """Is the overreach term that follows `prefix` (the earlier part of its clause) explicitly negated?  Conservative: exactly one negator in the clause, in a recognised shape; otherwise no."""
    if len(_NEG.findall(prefix)) != 1:
        return False
    return bool(_DIRECT_NEG.search(prefix) or _EPISTEMIC_NEG.search(prefix))


def _unnegated(text: str, rx: "re.Pattern") -> Optional[str]:
    """The first match of `rx` that is not explicitly negated within its own clause, or None.  When negation scope cannot be established the match counts."""
    for m in rx.finditer(text):
        if not _negated(_clause_before(text, m.start())):
            return m.group(0)
    return None


# Words that make a clause speculation or a hedge rather than an assertion; the QUESTION test is separate (the sentence ends with "?").
_HEDGE = re.compile(r"\b(?:think|thinks|thought|might|may(?!\s+\d)|maybe|perhaps|possibly|possible|probably|likely|unlikely|could|suppose\w*|guess\w*|seems?|seemed|appears?|apparently|"
                    r"presumably|unclear|unsure|uncertain|wonder\w*|whether|if|hope\w*|expect\w*|assum\w*|believe\w*|reckon\w*|gather)\b", re.I)


def _term_qualified(xn: str, s: int, e: int) -> bool:
    """True when an overreach term INSIDE the quoted span xn[s:e] sits, in the source, in a hedged / speculative clause or in a question.  Only the clause (and sentence) that holds the term is
    assessed: conversational uncertainty elsewhere in the turn does not matter, and a quote without such a term is unaffected."""
    for t in _OVERREACH.finditer(xn[s:e]):
        a, b = s + t.start(), s + t.end()
        cs = 0
        for m in _CLAUSE_BREAK.finditer(xn, 0, a):
            cs = m.end()
        nb = _CLAUSE_BREAK.search(xn, b)
        if _HEDGE.search(xn[cs: nb.start() if nb else len(xn)]):
            return True
        end = re.search(r"[.!?]", xn[b:])
        if end and xn[b + end.start()] == "?":
            return True
    return False


def _genuine_quote(q: str, excerpts: list) -> bool:
    """A quotation is exempt only when it is an exact, whole-word, case/whitespace-insensitive run of at least QUOTE_MIN_WORDS words of a cited turn AND it is a complete source clause:
    it ends where the source clause ends, nothing before it in the clause negates or conditions it, and nothing after it (to the end of the source sentence part) retracts, negates or
    qualifies it ('... accepted the offer' followed by 'was wrong' is a lifted claim, not a quotation of what the turn says)."""
    norm = lambda t: " ".join(t.replace("’", "'").lower().split())
    qn = norm(q).strip(" .,;:!?…")
    if len(qn.split()) < QUOTE_MIN_WORDS:
        return False
    for x in excerpts:
        xn = norm(x or "")
        for m in re.finditer(r"(?<![\w'])" + re.escape(qn) + r"(?![\w'])", xn):
            left = _clause_before(xn, m.start())
            if _NEG.search(left) or _LEFT_QUALIFIER.search(left):
                continue
            after = xn[m.end():].lstrip()
            if after and not (re.match(r"[.;:!?,]", after) or re.match(r"(?:but|and|yet|however|although|though|whereas|while|then|because|so)\b", after)):
                continue                                                      # the quote stops in the middle of a source clause
            h = _HARD_BREAK.search(after)
            if _QUALIFIER.search(after[: h.start()] if h else after):
                continue                                                      # the source goes on to negate / retract / qualify it
            if _term_qualified(xn, m.start(), m.end()):
                continue                                                      # the quoted proposition is a question or a hedge in the source: quoting it does not license asserting it
            return True
    return False


def _outside_quotes(text: str, excerpts: list) -> str:
    """The finding with every quotation that genuinely is text of a cited turn removed."""
    return _QUOTED.sub(lambda m: " " if _genuine_quote(m.group(1) or m.group(2), excerpts) else m.group(0), text)


def source_record_problem(text: str, items: list) -> Optional[str]:
    """Why a MODEL-written finding may not rest on these source-turn items, or None.  `items` are the cited items that are source turns."""
    for e in items:
        low = text.lower()
        if (e.speaker or "").lower() not in low:
            return f"{e.ref}: the statement does not attribute the turn to its recorded speaker"
        if (e.interaction_id or "").lower() not in low or not re.search(rf"\bturns?\s*#?\s*{e.turn_index}\b", low):
            return f"{e.ref}: the statement does not cite the interaction id and turn index"
    rest = _outside_quotes(text, [e.excerpt for e in items])
    hit = _unnegated(rest, _OVERREACH)
    if hit:
        return f"a source turn shows what was said, not that it is true, agreed, decided, authorised or current ('{hit}')"
    return None


_PROOF = re.compile(r"\b(?:prov(?:e|es|ed|ing)|demonstrat\w+|establish(?:es|ed)|verified)\b", re.I)


def source_summary_problem(text: str, items: list) -> Optional[str]:
    """Why a SUMMARY may not stand while source turns are in the evidence, or None.  A summary names no single turn, so only the overreach vocabulary is checked (outside quotations
    of the turns themselves): it may not turn a recorded statement into a fact, decision, agreement, signature, authorisation, instruction or current state."""
    rest = _outside_quotes(text, [e.excerpt for e in items])
    for rx in (_OVERREACH, _PROOF):
        hit = _unnegated(rest, rx)
        if hit:
            return f"a summary may not present a source turn as true, agreed, decided, signed, authorised or current ('{hit}')"
    return None


SOURCE_LIMITATION = "Conversation text is synthetic; only the referenced turn is returned."      # the get_source_evidence tool's own limitation, repeated so a replaced summary keeps it
SOURCE_LISTED = 6


def source_summary(items: list, others_omitted: bool = False) -> str:
    """Deterministic, provenance-aware summary of retrieved source turns; states only what the records contain.  It lists at most SOURCE_LISTED turns and says so when there are more;
    it does not point at other report sections (they are not guaranteed to survive verification).  `others_omitted`: memory / calculation evidence exists but is not summarised here."""
    items = [e for e in items if is_source_turn(e)]
    n, shown = len(items), items[:SOURCE_LISTED]
    kind = "synthetic source record" if all(e.synthetic for e in items) else "source record"
    where = ", ".join(f"{e.interaction_id} turn {e.turn_index} (recorded speaker {e.speaker})" for e in shown)
    count = f"{n} {kind}{'s' if n != 1 else ''} retrieved" + (f"; first {len(shown)} of {n} listed" if n > len(shown) else "")
    out = (f"{count}: {where}. These show only what each turn records, not that any statement is true; they show no decision, no agreement and no present client state. "
           f"{SOURCE_LIMITATION} " + " ".join(f"[{e.ref}]" for e in shown))
    if others_omitted:
        out += " Other evidence (memory or calculations) is not summarised here; see the evidence list."          # the evidence list is always in the report; a rendered finding is not guaranteed
    return out


def _quote(e: EvidenceItem) -> str:
    return (e.excerpt or "").replace("“", '"').replace("”", '"').strip()


SOURCE_FINDINGS_LIMIT = 24      # a chronology can run to many turns; every retrieved turn up to this many is rendered, and the report says so when there are more


def source_record_findings(evidence: list[EvidenceItem], limit: int = SOURCE_FINDINGS_LIMIT) -> list[Finding]:
    """Code-written statements of what each retrieved source turn records.  Assembled only from the evidence item's own fields; they never state more than the record."""
    out = []
    for e in evidence:
        if is_source_turn(e) and len(out) < limit:
            when = f" ({e.event_date})" if e.event_date else ""
            kind = "synthetic source record" if e.synthetic else "source record"
            out.append(Finding(text=(f"{kind.capitalize()}: {e.interaction_id} turn {e.turn_index}{when}, recorded speaker {e.speaker}, contains: “{_quote(e)}”. "
                                     f"This shows what the turn records; it does not establish that the statement is true, that the client agreed to it, or any decision."),
                               refs=[e.ref], origin="rendered"))
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
            rec.setdefault("findings_demoted", []).append({**withheld_ref(f.text), "refs": [b.ref for b in bad], "classes": sorted({str(b.evidence_class) for b in bad})})
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
        if is_source_turn(e):
            continue                                   # rendered once, as a source record, below
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
    return {"key_findings": key, "historical_context": source_record_findings(evidence) + hist, "financial_analysis": fin}


def deterministic_summary(evidence: list[EvidenceItem], plan: ExecutionPlan) -> str:
    cur = [e for e in evidence if e.status == "active" and e.memory_type == "semantic" and not e.valid_to and e.value and is_definitive(e) and not is_source_turn(e)]
    mem = ("Based on the available memory: " + "; ".join(f"{e.topic} is {e.value}" + (f" since {e.valid_from}" if e.valid_from else "") + f" [{e.ref}]" for e in cur[:3]) + ".") if cur else ""
    tools = [e for e in evidence if e.synthetic and not is_source_turn(e)]
    turns = [e for e in evidence if is_source_turn(e)]
    where = "the evidence list" if turns else "the financial analysis"      # with source turns a financial-analysis finding is only guaranteed for the four calculate_* tools, the evidence list always exists
    tool = (f"Synthetic tool results are available [{tools[0].ref}]; see {where} for the exact figures and their as-of dates.") if tools else ""
    if turns:       # every category present is kept; the source turns are described as records only and never back the memory / calculation parts
        return " ".join(p for p in (mem, tool, source_summary(turns)) if p)
    if mem:
        return mem
    if tool:
        return tool
    return f"Evidence was found ({len(evidence)} items) but it does not state a definite answer; see the findings and uncertainties."


def no_evidence_summary() -> str:
    return "This question cannot be answered reliably: no memory, conversation or tool evidence was available."
