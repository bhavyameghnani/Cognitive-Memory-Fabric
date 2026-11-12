"""Evidence Verifier: deterministic verification of every finding in a drafted AdvisorReport.

Two modes (HarnessConfig.structured_claim_verification, on by default and not switchable in the production profile):

STRUCTURED (default)   Each finding is read into StructuredClaims (claim_extraction) and each claim is compared with the facts of the evidence it cites (claim_matching):
    client identity -> unresolved qualifiers / negation scope -> exact value and unit -> polarity -> period -> modality.
    The facts of the evidence come ONLY from a quotation that is proven to be real text of the original source turn (or from typed tool values); the description an agent wrote about
    the evidence is never proof.  Outcomes: VERIFIED / UNVERIFIED / CONTRADICTED / AMBIGUOUS, each with structured reasons (schemas.claims).
    Mapping to the report: VERIFIED -> kept; AMBIGUOUS -> kept, labelled "[Qualified: ...]"; UNVERIFIED / CONTRADICTED -> removed and listed under uncertainties.
    An optional entailment checker (an NLI model, an LLM) may only DOWNGRADE a verdict; it cannot promote anything and cannot override a deterministic failure.
LEXICAL (legacy)       Word overlap plus "does this figure occur anywhere in the evidence".  Kept ONLY so the evaluation can measure what the structured mode fixed.
    It is known to verify negated claims, claims about the wrong person, 6-for-63, 12-months-for-12-years and similar (see tests/test_structured_claims.py).

Checks that decide before any text is compared (both modes): the finding cites evidence; the handle exists; its client is in the plan AND in the actor's scope; the kind of evidence may support
the kind of statement; memory evidence is re-read from the store (exists, same client, superseded memory cannot support a "current" statement).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Optional, Protocol

from harness import clause_support
from harness.claim_extraction import extract_claims
from harness.claim_matching import Bundle, Context, build_bundle, verify_claim, verify_rendered
from schemas.claims import ClaimVerification, Outcome, Reason
from schemas.report import AdvisorReport, EvidenceItem, Finding

SECTIONS = ("key_findings", "historical_context", "financial_analysis")
TOOL_CLASSES = ("operational_data", "tool_calculation")
OPEN_CERTAINTY = ("exploratory", "hypothetical", "unconfirmed", "recommendation")
NUM = re.compile(r"(?<![\w.])\$?\d[\d,]*(?:\.\d+)?%?")
DATE = re.compile(r"\b(20\d{2}-\d{2}-\d{2})\b")
STOP = set("the a an and or of to in on for with at by from is are was were be been this that these those it its as not no than then there their his her they them has have had".split())
WORD = re.compile(r"[a-z]{3,}")


# ---- legacy lexical helpers (used by structured=False and by the summary check) ---------------------------------------------------------------------
def _num_key(tok: str) -> Optional[str]:
    t = tok.replace("$", "").replace(",", "").rstrip("%")
    try:
        f = float(t)
    except ValueError:
        return None
    return f"{f:.6f}".rstrip("0").rstrip(".")


def numbers_in(text: str) -> list:
    """(key, raw) for amounts / percentages / counts in text; ISO dates are handled separately and four-digit years inside them are skipped."""
    scrub = DATE.sub(" ", text or "")
    scrub = re.sub(r"\bE\d+\b", " ", scrub)    # evidence handles are not figures
    out = []
    for m in NUM.finditer(scrub):
        k = _num_key(m.group(0))
        if k is not None:
            out.append((k, m.group(0)))
    return out


def _leaves(o: Any) -> Iterable[Any]:
    if isinstance(o, dict):
        for v in o.values():
            yield from _leaves(v)
    elif isinstance(o, (list, tuple)):
        for v in o:
            yield from _leaves(v)
    else:
        yield o


class EntailmentChecker(Protocol):
    def check(self, text: str, evidence_texts: list) -> tuple: ...   # -> ("supported" | "uncertain" | "contradicted", detail)


class OverlapEntailment:
    """Lexical overlap.  NOT semantic entailment: 'uncertain' just means 'a human or a stronger verifier should look'."""

    def __init__(self, supported: float = 0.5) -> None:
        self.supported = supported

    def check(self, text: str, evidence_texts: list) -> tuple:
        words = {w for w in WORD.findall(text.lower()) if w not in STOP}
        if not words:
            return "uncertain", "no content words"
        ev = set()
        for t in evidence_texts:
            ev |= set(WORD.findall((t or "").lower()))
        ratio = len(words & ev) / len(words)
        return ("supported" if ratio >= self.supported else "uncertain"), f"content-word overlap {ratio:.2f}"


# ---- results -----------------------------------------------------------------------------------------------------------------------------------
# What the PUBLIC response says about why a finding was withheld.  The detailed reason (`FindingVerdict.reasons`, from claim_matching / clause_support) quotes the claimed value ("claim 60
# years vs evidence 63 years"); that is kept for the server-side trace only.  Here: the reason CODE plus a description written in code, containing nothing taken from the finding.
PUBLIC_REASON = {
    "CLIENT_MISMATCH": "the statement concerns a client other than the one the cited evidence is about",
    "AMBIGUOUS_SUBJECT": "it cannot be established whom the statement is about",
    "POLARITY_MISMATCH": "the statement affirms or denies something the cited evidence does not",
    "VALUE_MISMATCH": "a figure in the statement does not match the cited evidence",
    "UNIT_MISMATCH": "the unit of a figure does not match the cited evidence",
    "TEMPORAL_MISMATCH": "the period or date of the statement is not supported by the cited evidence",
    "MODALITY_MISMATCH": "the statement is firmer than, or differs in kind from, the cited evidence",
    "EVIDENCE_NOT_FOUND": "the cited evidence reference does not exist",
    "EVIDENCE_INACCESSIBLE": "the cited evidence is outside the authorised scope",
    "QUOTE_NOT_IN_SOURCE": "the quoted text is not text of the original source",
    "QUOTE_UNAVAILABLE": "there is no source text to check the statement against",
    "NO_COMPARABLE_FACT": "the cited evidence contains no fact about this topic",
    "NO_QUANTIFIABLE_CLAIM": "the statement states no checkable value",
    "QUALIFIER_PRESENT": "the statement depends on a qualifier that cannot be compared safely",
    "AMBIGUOUS_EVIDENCE": "the cited evidence is itself unclear",
    "SOURCE_NOT_PERMITTED": "this kind of evidence cannot support this kind of statement",
    "EVIDENCE_NOT_FINAL": "the cited evidence is exploratory, unconfirmed or superseded",
    "UNSUPPORTED_CLAUSE": "a part of the statement is not stated by the cited source",
    "ACTOR_MISMATCH": "the statement is about a different person than the cited source",
    "EVIDENCE_NOT_VERIFIED": "the cited memory is not verified evidence",
}
PUBLIC_REASON_FALLBACK = "it could not be verified against the cited evidence"


def public_reason(code: str) -> str:
    return f"{code} - {PUBLIC_REASON.get(code, PUBLIC_REASON_FALLBACK)}"


@dataclass
class FindingVerdict:
    section: str
    text: str
    refs: list
    verdict: str            # verified | qualified | unsupported   (the report-facing label; kept for backward compatibility)
    reasons: list = field(default_factory=list)          # human-readable
    memory_ids: list = field(default_factory=list)
    outcome: str = Outcome.VERIFIED.value                # VERIFIED | UNVERIFIED | CONTRADICTED | AMBIGUOUS
    reason_codes: list = field(default_factory=list)     # schemas.claims.Reason values
    claims: list = field(default_factory=list)           # ClaimVerification dumps (no quotations)

    def as_dict(self) -> dict:
        return dict(section=self.section, text=self.text[:200], refs=self.refs, verdict=self.verdict, reasons=self.reasons, memory_ids=self.memory_ids, outcome=self.outcome,
                    reason_codes=self.reason_codes)

    def public_reasons(self) -> list:
        """The reasons as the API shows them: codes with code-written descriptions, never a value or a word taken from the withheld finding."""
        return [public_reason(c) for c in self.reason_codes] or [PUBLIC_REASON_FALLBACK]

    def public_dict(self) -> dict:
        """as_dict without the finding's text and without the detailed reasons (which quote the claimed value): what may leave the process in the API response."""
        d = self.as_dict()
        d.pop("text")
        d["reasons"] = self.public_reasons()
        return d


@dataclass
class VerificationResult:
    verdicts: list = field(default_factory=list)
    summary_replaced: Optional[str] = None

    def counts(self) -> dict:
        c = {"verified": 0, "qualified": 0, "unsupported": 0}
        for v in self.verdicts:
            c[v.verdict] += 1
        return c

    def outcome_counts(self) -> dict:
        c = {o.value: 0 for o in Outcome}
        for v in self.verdicts:
            c[v.outcome] += 1
        return c

    def claim_evidence(self) -> list:
        return [dict(section=v.section, text=v.text[:160], refs=v.refs, memory_ids=v.memory_ids, verdict=v.verdict, outcome=v.outcome, reason_codes=v.reason_codes)
                for v in self.verdicts if v.verdict != "unsupported"]

    def findings_summary(self) -> list:
        """Per finding: label, outcome, reasons and the structured claims.  No quotations and no evidence text; for a WITHHELD finding not even the sentence the model wrote."""
        return [dict(section=v.section, refs=v.refs, verdict=v.verdict, outcome=v.outcome, reason_codes=v.reason_codes, claims=_redact_claims(v.claims) if v.verdict == "unsupported" else v.claims)
                for v in self.verdicts]

    def withheld_public(self) -> list:
        """The withheld findings as returned by the API: section, refs, outcome and reason codes - never the text (`FindingVerdict.as_dict` keeps it for the server-side trace)."""
        return [v.public_dict() for v in self.verdicts if v.verdict == "unsupported"]


def _worst(outcomes: list) -> Outcome:
    if all(o == Outcome.VERIFIED for o in outcomes):
        return Outcome.VERIFIED
    for o in (Outcome.CONTRADICTED, Outcome.UNVERIFIED, Outcome.AMBIGUOUS):
        if o in outcomes:
            return o
    return Outcome.AMBIGUOUS


class EvidenceVerifier:
    def __init__(self, conv, store=None, entailment: Optional[EntailmentChecker] = None, owns: Optional[Callable[[Optional[str]], bool]] = None, *,
                 structured: bool = True, client_names: Optional[dict] = None) -> None:
        self.conv, self.store = conv, store
        self.structured = structured
        # lexical overlap is only the default in the legacy mode; the structured mode needs no word-overlap crutch and never lets one promote a claim
        self.entailment = entailment if entailment is not None else (None if structured else OverlapEntailment())
        self.owns = owns or (lambda cid: True)
        self._names = client_names

    # ------------------------------------------------------------------ client directory (names of clients, from the data)
    def names(self) -> dict:
        if self._names is None:
            try:
                from services.conversations import shared
                self._names = {str(v["first_name"]).lower(): cid for cid, v in shared().directory().items() if v.get("first_name")}
            except Exception:       # no directory -> no name can be resolved -> subjects stay unresolved (AMBIGUOUS), never guessed
                self._names = {}
        return self._names

    # ------------------------------------------------------------------ single finding
    def verify_finding(self, section: str, f: Finding, evidence: dict, plan_clients: set, tools) -> FindingVerdict:
        v = FindingVerdict(section, f.text, list(f.refs), "verified")
        bad, soft, codes = v.reasons, [], v.reason_codes
        if not f.refs:
            return self._fail(v, "no evidence reference", Reason.EVIDENCE_NOT_FOUND)
        items = []
        for r in f.refs:
            e = evidence.get(r)
            if e is None:
                bad.append(f"unknown evidence handle {r}")
                codes.append(Reason.EVIDENCE_NOT_FOUND.value)
            else:
                items.append(e)
        if bad:
            v.verdict, v.outcome = "unsupported", Outcome.UNVERIFIED.value
            return v
        v.memory_ids = [e.memory_id for e in items if e.memory_id]
        # ownership + permission (never skipped, in either mode)
        for e in items:
            if e.client_id and e.client_id not in plan_clients:
                bad.append(f"{e.ref}: evidence belongs to {e.client_id}, outside the plan/authorised scope")
                codes.append(Reason.CLIENT_MISMATCH.value)
            elif e.client_id and not self.owns(e.client_id):
                bad.append(f"{e.ref}: evidence belongs to {e.client_id}, outside the plan/authorised scope")
                codes.append(Reason.EVIDENCE_INACCESSIBLE.value)
            if not e.client_id and e.source_class not in TOOL_CLASSES and e.source_class != "assumption":
                bad.append(f"{e.ref}: evidence has no client ownership")
                codes.append(Reason.AMBIGUOUS_SUBJECT.value)
        if section in ("key_findings", "historical_context") and all(e.source_class in TOOL_CLASSES for e in items):
            bad.append("a client-state statement cannot rest only on tool/operational results")
            codes.append(Reason.SOURCE_NOT_PERMITTED.value)
        for e in items:
            if e.evidence_class in ("unverified", "contradicted", "open_conflict"):        # P1.5.1: the memory layer's verdict is binding at the report boundary
                bad.append(f"{e.ref}: the cited memory is {e.evidence_class} and cannot support a finding")
                codes.append(Reason.EVIDENCE_NOT_VERIFIED.value)
        for e in items:
            self._provenance(e, section, f, bad, soft, codes)
        if bad:
            v.verdict, v.outcome = "unsupported", (Outcome.CONTRADICTED.value if Reason.POLARITY_MISMATCH.value in codes else Outcome.UNVERIFIED.value)
            v.reason_codes = list(dict.fromkeys(codes))
            return v
        return self._structured(v, f, items, plan_clients, tools, soft) if self.structured else self._lexical(v, f, items, tools, soft)

    @staticmethod
    def _fail(v: FindingVerdict, why: str, code: Reason) -> FindingVerdict:
        v.verdict, v.outcome, v.reasons, v.reason_codes = "unsupported", Outcome.UNVERIFIED.value, [why], [code.value]
        return v

    # ------------------------------------------------------------------ structured path
    def _structured(self, v: FindingVerdict, f: Finding, items: list, plan_clients: set, tools, soft: list) -> FindingVerdict:
        names = self.names()
        ctx = Context(plan_clients=frozenset(plan_clients), default_client=next(iter(plan_clients)) if len(plan_clients) == 1 else None)
        bundles: list[Bundle] = [build_bundle(e, conv=self.conv, tools=tools, names=names) for e in items]
        claims = extract_claims(f.text, names, handle=f.refs[0] if f.refs else None)
        rendered = f.origin in ("rendered", "tool_code")
        valued = [c for c in claims if c.value is not None]
        extras = [c for c in claims if c.value is None and (c.period and c.period.dates or (c.topic_terms and not rendered))]
        results: list[ClaimVerification] = []
        for c in valued:
            results.append(verify_rendered(c, bundles, ctx) if rendered else verify_claim(c, bundles, ctx))
        for c in extras:                                       # a statement with no figure: its dates must still be supported; its wording can never be VERIFIED
            if c.period and c.period.dates:
                unsupported = [d for d in c.period.dates if not any(any(_date_ok(d, cd) for cd in b.candidates) or d in b.rendered_dates for b in bundles if not b.problems)]
                if unsupported:
                    results.append(ClaimVerification(claim=c, outcome=Outcome.UNVERIFIED, reasons=[Reason.TEMPORAL_MISMATCH], detail=f"the evidence does not support the date {unsupported[0]}",
                                                     evidence_handle=f.refs[0]))
                    continue
            results.append(ClaimVerification(claim=c, outcome=Outcome.AMBIGUOUS, reasons=[Reason.NO_QUANTIFIABLE_CLAIM], detail="no checkable value in this statement", evidence_handle=f.refs[0]))
        if not results:
            results.append(ClaimVerification(claim=(claims[0] if claims else _blank(f)), outcome=Outcome.AMBIGUOUS, reasons=[Reason.NO_QUANTIFIABLE_CLAIM],
                                             detail="the finding states no checkable value", evidence_handle=f.refs[0]))
        # P1.5.1 (audit H1): every CLAUSE of a model-written finding must be accounted for by the verified source text, and the structure around the figure must match.  Downgrade only.
        if not rendered:
            quotes = [(b.quote, {n for n, cid in names.items() if cid == b.item.client_id}) for b in bundles if b.quote]
            vocab = " ".join(" ".join(x for x in (e.topic or "", (e.topic or "").replace("_", " "), e.value or "", e.certainty or "", e.status or "") if x) for e in items)
            own = {n for n, cid in names.items() if cid in plan_clients}
            for pr in clause_support.check(f.text, quotes, vocab, own, open_evidence=any(e.certainty in OPEN_CERTAINTY for e in items)):
                results.append(ClaimVerification(claim=(claims[0] if claims else _blank(f)), outcome=pr.outcome, reasons=[pr.reason], detail=pr.detail, evidence_handle=f.refs[0]))
        # P1.5: a statement that NAMES a client outside the plan is never presented (not even "qualified"): its evidence cannot be about that client (ownership is checked above), so the
        # sentence can only be the model's own words about someone this request is not authorised for.  Matched as a capitalised whole word (a lower-case "grace period" is not Grace).
        if not any(Reason.CLIENT_MISMATCH in r.reasons for r in results) and any(
                cid not in plan_clients and re.search(r"\b" + re.escape(n.capitalize()) + r"\b", f.text) for n, cid in names.items()):
            results.append(ClaimVerification(claim=(claims[0] if claims else _blank(f)), outcome=Outcome.UNVERIFIED, reasons=[Reason.CLIENT_MISMATCH],
                                             detail="the statement names a client outside the plan", evidence_handle=f.refs[0]))
        outcome = _worst([r.outcome for r in results])
        for r in results:
            r.quote_verified = r.quote is not None
        v.claims = [r.model_dump(mode="json") for r in results]
        v.outcome = outcome.value
        v.reason_codes = list(dict.fromkeys(rc.value for r in results if r.outcome != Outcome.VERIFIED for rc in r.reasons))
        v.reasons = [f"{r.claim.value if r.claim.value is not None else 'statement'}: {', '.join(x.value for x in r.reasons)}" + (f" - {r.detail}" if r.detail else "")
                     for r in results if r.outcome != Outcome.VERIFIED]
        if outcome in (Outcome.UNVERIFIED, Outcome.CONTRADICTED):
            v.verdict = "unsupported"
            return v
        if outcome == Outcome.AMBIGUOUS:
            v.verdict = "qualified"
            for e in items:                                           # the evidence's own status is part of the qualification, whatever else made the finding ambiguous
                if e.certainty in OPEN_CERTAINTY or e.status in ("unconfirmed", "disputed"):
                    v.reasons.insert(0, f"{e.ref}: evidence is {e.certainty or e.status}, not a verified fact")
            return v
        # VERIFIED by structure.  Evidence that is not a final fact can support only a qualified statement; entailment checkers can only lower the verdict further.
        for e in items:
            if e.certainty in OPEN_CERTAINTY or e.status in ("unconfirmed", "disputed"):
                soft.append(f"{e.ref}: evidence is {e.certainty or e.status}, not a verified fact")
        soft_final = [s for s in soft]
        if self.entailment is not None:
            verdict, detail = self.entailment.check(f.text, [b.quote or "" for b in bundles])
            if verdict == "contradicted":
                v.verdict, v.outcome, v.reasons = "unsupported", Outcome.CONTRADICTED.value, [f"entailment: {detail}"]
                return v
            if verdict == "uncertain":
                soft_final.append(f"semantic support not established ({detail})")
        if soft_final:
            v.verdict, v.outcome, v.reasons = "qualified", Outcome.AMBIGUOUS.value, soft_final
            v.reason_codes = [Reason.EVIDENCE_NOT_FINAL.value]
        return v

    # ------------------------------------------------------------------ legacy lexical path (evaluation baseline only)
    def _lexical(self, v: FindingVerdict, f: Finding, items: list, tools, soft: list) -> FindingVerdict:
        bad = v.reasons
        src = self._source_text(items, tools)
        src_nums = {_num_key(t) for t in NUM.findall(src)}
        for k, raw in numbers_in(f.text):
            if raw.strip("$%,") and not (float(k) < 13 and "." not in raw and "$" not in raw and "%" not in raw):   # tiny bare counts were never checked (the audited hole)
                if k not in src_nums:
                    bad.append(f"figure {raw} does not occur in the cited evidence")
        for d in DATE.findall(f.text):
            if d not in src:
                bad.append(f"date {d} does not occur in the cited evidence")
        if bad:
            v.verdict, v.outcome = "unsupported", Outcome.UNVERIFIED.value
            return v
        for e in items:
            if e.certainty in OPEN_CERTAINTY or (e.status in ("unconfirmed", "disputed")):
                soft.append(f"{e.ref}: evidence is {e.certainty or e.status}, not a verified fact")
        verdict, detail = (self.entailment or OverlapEntailment()).check(f.text, [src])
        if verdict == "contradicted":
            v.verdict, v.outcome, v.reasons = "unsupported", Outcome.CONTRADICTED.value, [f"entailment: {detail}"]
            return v
        if verdict == "uncertain":
            soft.append(f"semantic support not established ({detail})")
        v.reasons = soft
        v.verdict = "qualified" if soft else "verified"
        v.outcome = Outcome.AMBIGUOUS.value if soft else Outcome.VERIFIED.value
        return v

    def _provenance(self, e: EvidenceItem, section: str, f: Finding, bad: list, soft: list, codes: list) -> None:
        if e.source_class in TOOL_CLASSES:
            return
        if e.memory_id and self.store is not None:
            row = self.store.get(e.memory_id)
            if row is None:
                bad.append(f"{e.ref}: memory {e.memory_id} does not exist")
                codes.append(Reason.EVIDENCE_NOT_FOUND.value)
                return
            if row["client_id"] not in (e.client_id, "GLOBAL"):
                bad.append(f"{e.ref}: memory {e.memory_id} belongs to {row['client_id']}")
                codes.append(Reason.CLIENT_MISMATCH.value)
                return
            if row["status"] in ("superseded", "expired") or row.get("valid_to"):
                if section == "key_findings" or re.search(r"\b(current|currently|now|today)\b", f.text, re.I):
                    bad.append(f"{e.ref}: memory {e.memory_id} is {row['status']} and cannot support a current statement")
                    codes.append(Reason.TEMPORAL_MISMATCH.value)
                else:
                    soft.append(f"{e.ref}: describes superseded knowledge")
        if e.interaction_id is not None and e.turn_index is not None and e.excerpt and e.client_id:
            if not self.conv.verify_excerpt(e.client_id, e.interaction_id, e.turn_index, e.excerpt.rstrip(".").rstrip()) and \
                    not self.conv.verify_excerpt(e.client_id, e.interaction_id, e.turn_index, e.excerpt.replace("...", "").strip()):
                bad.append(f"{e.ref}: excerpt not found in {e.interaction_id} turn {e.turn_index} of {e.client_id}")
                codes.append(Reason.QUOTE_NOT_IN_SOURCE.value)

    @staticmethod
    def _source_text(items: list, tools) -> str:
        parts = []
        by_id = {x.id: x for x in (tools.executions if tools is not None else [])}
        for e in items:
            parts += [e.description or "", e.excerpt or "", e.value or "", e.valid_from or "", e.valid_to or "", e.event_date or ""]
            ex = by_id.get(e.tool_execution_id) if e.tool_execution_id else None
            if ex is not None and ex.result is not None:
                parts += [str(x) for x in _leaves(ex.result.data)] + [str(x) for x in _leaves(ex.arguments)] + [ex.result.as_of or ""]
        return " ".join(parts)

    # ------------------------------------------------------------------ whole report
    def verify_report(self, report: AdvisorReport, plan_clients: Iterable[str], tools, fallback_summary: Optional[Callable[[], str]] = None) -> tuple:
        evidence = {e.ref: e for e in report.evidence}
        pc = set(plan_clients)
        result = VerificationResult()
        new_sections: dict = {}
        withheld: list = []
        for sec in SECTIONS:
            kept = []
            for f in getattr(report, sec):
                v = self.verify_finding(sec, f, evidence, pc, tools)
                result.verdicts.append(v)
                if v.verdict == "unsupported":
                    if {Reason.CLIENT_MISMATCH.value, Reason.EVIDENCE_INACCESSIBLE.value} & set(v.reason_codes):      # P1.5: a statement about a client / evidence the caller may not use is not echoed
                        withheld.append(f"Withheld unsupported statement ({sec.replace('_', ' ')}): [text not shown - it refers to a client or evidence outside the authorised scope] [{'; '.join(v.public_reasons())[:300]}]")
                    else:
                        withheld.append(f"Withheld unsupported statement ({sec.replace('_', ' ')}): [text not shown - it could not be verified against the cited evidence] [{'; '.join(v.public_reasons())[:300]}]")
                elif v.verdict == "qualified":
                    kept.append(Finding(text=f"[Qualified: {'; '.join(v.reasons)[:160]}] {f.text}", refs=f.refs, origin=f.origin))
                else:
                    kept.append(f)
            new_sections[sec] = kept
        summary = report.summary
        # The summary is still checked by figure/date presence only (NOT by structured claims): documented limitation, see docs/harness.md.
        s_nums = numbers_in(summary)
        allsrc = " ".join(self._source_text(list(evidence.values()), tools).split())
        keys = {_num_key(t) for t in NUM.findall(allsrc)}
        bad_s = [raw for k, raw in s_nums if not (float(k) < 13 and raw.strip("$%,").isdigit()) and k not in keys and raw.strip("$%,")]
        bad_s += [d for d in DATE.findall(summary) if d not in allsrc]
        if bad_s and fallback_summary is not None:
            result.summary_replaced = "summary cited figures/dates not in any evidence"          # the figures themselves are not repeated in the response
            summary = fallback_summary()
        validation = dict(report.validation)
        validation["harness_evidence"] = dict(counts=result.counts(), outcomes=result.outcome_counts(), mode="structured" if self.structured else "lexical",
                                              summary_replaced=result.summary_replaced, claim_evidence=result.claim_evidence(), findings=result.findings_summary(),
                                              withheld=result.withheld_public())
        new = report.model_copy(update=dict(summary=summary, uncertainties=list(report.uncertainties) + withheld, validation=validation, **new_sections))
        return new, result


def _redact_claims(claims: list) -> list:
    """ClaimVerification dumps of a WITHHELD finding.  Kept: topic, unit, polarity, modality, handles, outcome and reason CODES.  Removed: the sentence, the extracted VALUE, the extracted
    dates and the detailed reason text (it quotes the value).  The unredacted form exists only in the server-side trace (`FindingVerdict.as_dict`)."""
    out = []
    # decided per FINDING: one sentence yields several claims (each figure), and only one of them need carry the scope reason
    out_of_scope = any({"CLIENT_MISMATCH", "EVIDENCE_INACCESSIBLE"} & {str(r) for r in (c.get("reasons") or [])} for c in claims or [])
    for c in claims or []:
        c = dict(c)
        codes = [str(r) for r in (c.get("reasons") or [])]
        if isinstance(c.get("claim"), dict):
            claim = {**c["claim"], "text": None, "value": None}
            if isinstance(claim.get("period"), dict):
                claim["period"] = {**claim["period"], "dates": []}
            if out_of_scope:
                claim["subject_client"] = None                                      # the client a withheld statement was ABOUT is the thing outside the caller's scope: not an id the response may carry
            c["claim"] = claim
        c["detail"] = "; ".join(PUBLIC_REASON.get(r, PUBLIC_REASON_FALLBACK) for r in codes) if codes else ""
        out.append(c)
    return out


def _date_ok(d: str, cand) -> bool:
    from harness.claim_matching import date_supported
    return date_supported(d, cand)


def _blank(f: Finding):
    from schemas.claims import StructuredClaim
    return StructuredClaim(claim_id="C0", text=f.text)
