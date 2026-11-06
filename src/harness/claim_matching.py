"""Deterministic comparison of a StructuredClaim with the facts of the evidence it cites.

Evidence facts come ONLY from sources that can be checked:
  * text evidence: the quoted excerpt, after proving the excerpt is really a substring of the original source turn (the description an agent wrote is never used as proof);
    memory fields (topic / value / certainty / status / validity) refine it but can only make it WEAKER or more specific, never firmer than the quotation says;
  * tool evidence: the typed numeric leaves of the tool result and its arguments, with the unit read from the key name (months, percent, ...).

A claim is judged against every aligned fact in this order and the FIRST failing dimension decides the reason:
  subject -> flags (qualifier / unclear negation) -> value & unit (exact Decimal; months<->years converted, nothing else) -> polarity -> period -> modality
VERIFIED needs all of them.  The claim is VERIFIED if some cited fact verifies it and no firm, current fact of the same client contradicts it; otherwise AMBIGUOUS if some fact
might support it, else CONTRADICTED / UNVERIFIED.  Free-form entailment is deliberately absent: a conservative false negative is acceptable, an unsupported assertion is not.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

from harness.claim_extraction import discriminators, extract_claims, stem
from schemas.claims import ClaimVerification, Modality, Outcome, Period, PeriodKind, Polarity, Reason, StructuredClaim
from schemas.report import EvidenceItem

# strength of assertion.  HYPOTHETICAL / EXPLORATORY / UNCERTAIN are one tier on purpose: "we discussed retiring at 60" is faithfully supported by "what if I retired at 60?".
RANK = {Modality.HYPOTHETICAL: 1, Modality.EXPLORATORY: 1, Modality.UNCERTAIN: 1, Modality.RECOMMENDED: 2, Modality.REPORTED: 3, Modality.ESTIMATED: 3, Modality.CONFIRMED: 4, None: 4}
CERTAINTY_MODALITY = {"confirmed": Modality.CONFIRMED, "correction": Modality.CONFIRMED, "unconfirmed": Modality.REPORTED, "exploratory": Modality.EXPLORATORY,
                      "hypothetical": Modality.HYPOTHETICAL, "recommendation": Modality.RECOMMENDED}
OPEN_FLAGS = {"qualifier": Reason.QUALIFIER_PRESENT, "multi_negation": Reason.AMBIGUOUS_EVIDENCE, "negation_scope": Reason.AMBIGUOUS_EVIDENCE,
              "temporal_conflict": Reason.AMBIGUOUS_EVIDENCE, "unparsed_number": Reason.AMBIGUOUS_EVIDENCE, "alternatives": Reason.AMBIGUOUS_EVIDENCE,
              "multi_subject": Reason.AMBIGUOUS_SUBJECT}
KEY_TO_CANONICAL = {"retirement_age": "retirement_age", "asset_allocation": "allocation"}
FAMILY = {"months": "duration", "years": "duration", "weeks": "duration", "days": "duration", "percent": "percent", "USD": "currency", "INR": "currency", "EUR": "currency",
          "GBP": "currency", "CURRENCY": "currency"}
TO_MONTHS = {"months": Decimal(1), "years": Decimal(12)}
_CURRENCY_KEYS = {"amount", "balance", "gap", "shortfall", "target", "resource", "value", "incom", "expen", "payme", "cost", "total", "worth", "price", "market", "fund"}


# ------------------------------------------------------------------------------------------------------------------ evidence model
@dataclass
class Candidate:
    fact: StructuredClaim
    kind: str                                     # "quote" | "structured" | "tool"
    dates: list = field(default_factory=list)     # explicit dates / years that define the period of this fact
    window: Optional[tuple] = None                # (valid_from, valid_to or None) for memory facts


@dataclass
class Bundle:
    item: EvidenceItem
    client_id: Optional[str]
    source_id: str
    quote: Optional[str] = None
    problems: list = field(default_factory=list)          # [(Reason, detail)] - the evidence cannot be used at all
    candidates: list = field(default_factory=list)
    rendered_values: list = field(default_factory=list)   # every value the evidence really contains (for code-rendered findings)
    rendered_dates: set = field(default_factory=set)


@dataclass
class Context:
    plan_clients: frozenset
    default_client: Optional[str]                 # the report's single client, when there is exactly one


def _norm_quote(s: str) -> str:
    return " ".join(s.replace("’", "'").replace("“", '"').replace("”", '"').split())


def quote_in_source(excerpt: str, source: str) -> bool:
    """The quotation must be real text of the source.  Whitespace and quote style are normalised; case is not.  A trailing ellipsis (truncation marker) is allowed."""
    q = _norm_quote(excerpt).rstrip()
    for tail in ("...", "…"):
        if q.endswith(tail):
            q = q[: -len(tail)].rstrip()
    return bool(q) and q in _norm_quote(source)


def _to_decimal(v: Any) -> Optional[Decimal]:
    try:
        d = Decimal(str(v).replace(",", "").strip())
        return d if d.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def weaker(a: Optional[Modality], b: Optional[Modality]) -> Optional[Modality]:
    if a is None:
        return b
    if b is None:
        return a
    return a if RANK[a] <= RANK[b] else b


def _leaves(obj: Any, path: tuple = ()):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _leaves(v, path + (str(k),))
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _leaves(v, path)               # list positions carry no meaning; the key names do
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        yield path, obj


def _leaf_unit(terms: list[str]) -> Optional[str]:
    t = set(terms)
    if t & {"percent", "pct", "perce"}:
        return "percent"
    if t & {"month", "mo"}:
        return "months"
    if t & {"year", "age"}:
        return "years"
    if t & _CURRENCY_KEYS:
        return "CURRENCY"
    return None


# ------------------------------------------------------------------------------------------------------------------ building the evidence side
def build_bundle(item: EvidenceItem, *, conv: Any, tools: Any, names: dict[str, str]) -> Bundle:
    if item.tool_execution_id:
        return _tool_bundle(item, tools)
    b = Bundle(item=item, client_id=item.client_id, source_id=item.memory_id or (f"{item.interaction_id}#{item.turn_index}" if item.interaction_id else item.ref))
    if item.interaction_id is None or item.turn_index is None or not item.excerpt:
        b.problems.append((Reason.QUOTE_UNAVAILABLE, "the evidence has no quotation from a source turn"))
        return b
    turn = conv.turn(item.interaction_id, item.turn_index)
    if turn is None:
        b.problems.append((Reason.QUOTE_NOT_IN_SOURCE, f"source turn {item.interaction_id}#{item.turn_index} does not exist"))
        return b
    if item.client_id and turn.get("client_id") != item.client_id:
        b.problems.append((Reason.CLIENT_MISMATCH, f"the source turn belongs to {turn.get('client_id')}, not {item.client_id}"))
        return b
    if not quote_in_source(item.excerpt, turn.get("utterance", "")):
        b.problems.append((Reason.QUOTE_NOT_IN_SOURCE, "the quoted excerpt is not text of the original source"))
        return b
    b.quote = item.excerpt
    qfacts = extract_claims(item.excerpt, names, handle=item.ref, source_id=b.source_id)
    status_hist = item.status in ("superseded", "expired") or bool(item.valid_to)
    cert_mod = CERTAINTY_MODALITY.get((item.certainty or "").lower())
    item_dates = [d for d in (item.event_date,) if d]
    value_facts = [f for f in qfacts if f.value is not None]
    for f in qfacts:
        f.evidence_handle = item.ref
        if f.subject_client is None and "multi_subject" not in f.flags:
            f.subject_client = item.client_id                        # an unnamed speaker in this client's own conversation is that client
        if f.topic is None and not f.topic_terms and len(value_facts) == 1 and item.topic:
            f.topic = KEY_TO_CANONICAL.get(item.topic)               # a bare "We've decided: 63." takes the topic of the memory it was extracted for
            f.topic_terms = [stem(w) for w in item.topic.split("_")]
        if f.topic and f.unit is None:
            from harness.claim_extraction import DEFAULT_UNIT
            if DEFAULT_UNIT.get(f.topic):
                f.unit, f.unit_implied = DEFAULT_UNIT[f.topic], True
        f.modality = weaker(f.modality, cert_mod)                    # structured certainty can only make a fact weaker
        if status_hist:
            f.period = Period(kind=PeriodKind.HISTORICAL, dates=list(f.period.dates if f.period else []))
        if f.value is not None:
            b.candidates.append(Candidate(fact=f, kind="quote", dates=list(f.period.dates if f.period else []) + item_dates, window=(item.valid_from, item.valid_to) if item.valid_from else None))
            b.rendered_values.append((f.value, f.unit))
        b.rendered_dates.update(f.period.dates if f.period else [])
    # structured memory fact (value / topic / validity of the memory record the quotation belongs to)
    v = _to_decimal(item.value) if item.value else None
    if v is not None and item.topic:
        canon = KEY_TO_CANONICAL.get(item.topic)
        from harness.claim_extraction import DEFAULT_UNIT
        same_value = [f for f in qfacts if f.value == v]
        neg = any(f.polarity == Polarity.NEGATIVE for f in same_value) or (not same_value and any(f.polarity == Polarity.NEGATIVE for f in qfacts))
        flags = sorted({fl for f in qfacts for fl in f.flags if fl in OPEN_FLAGS and fl != "multi_subject"})
        kind = PeriodKind.HISTORICAL if status_hist else PeriodKind.CURRENT if (item.memory_type == "semantic" and item.status == "active") else PeriodKind.UNSPECIFIED
        sf = StructuredClaim(claim_id=f"S.{item.ref}", text=item.excerpt, subject_client=item.client_id, topic=canon, topic_terms=[stem(w) for w in item.topic.split("_")], value=v,
                             unit=DEFAULT_UNIT.get(canon) if canon else None, unit_implied=bool(canon and DEFAULT_UNIT.get(canon)), polarity=Polarity.NEGATIVE if neg else Polarity.POSITIVE,
                             modality=weaker(cert_mod, same_value[0].modality if same_value else None), period=Period(kind=kind), evidence_handle=item.ref, source_id=b.source_id, flags=flags)
        win = (item.valid_from, item.valid_to) if item.valid_from else None
        b.candidates.append(Candidate(fact=sf, kind="structured", dates=[d for d in (item.valid_from, item.valid_to, item.event_date) if d], window=win))
        b.rendered_values.append((v, sf.unit))
    for d in (item.valid_from, item.valid_to, item.event_date):
        if d:
            b.rendered_dates.add(d)
    return b


def _tool_bundle(item: EvidenceItem, tools: Any) -> Bundle:
    ex = next((e for e in (tools.executions if tools is not None else []) if e.id == item.tool_execution_id), None)
    b = Bundle(item=item, client_id=item.client_id, source_id=item.tool_execution_id or item.ref)
    if ex is None or ex.status != "success" or ex.result is None:
        b.problems.append((Reason.EVIDENCE_NOT_FOUND, "the cited tool execution does not exist or did not succeed"))
        return b
    env = ex.result
    b.client_id = env.client_id or item.client_id
    dates = [env.as_of] if env.as_of else []
    b.rendered_dates.update(dates)
    for source in (env.data, ex.arguments):
        for path, num in _leaves(source):
            terms = [stem(t) for t in re.findall(r"[a-z]+", " ".join(path).lower().replace("_", " ")) if len(t) > 1]
            v = _to_decimal(num)
            if v is None:
                continue
            f = StructuredClaim(claim_id=f"T.{item.ref}.{len(b.candidates)}", text="/".join(path), subject_client=b.client_id, topic_terms=terms, value=v, unit=_leaf_unit(terms),
                                polarity=Polarity.POSITIVE, modality=None, period=Period(kind=PeriodKind.CURRENT, dates=dates), evidence_handle=item.ref, source_id=b.source_id)
            b.candidates.append(Candidate(fact=f, kind="tool", dates=dates))
            b.rendered_values.append((v, f.unit))
    return b


# ------------------------------------------------------------------------------------------------------------------ comparison
def _jaccard(a: list, b: list) -> float:
    sa, sb = set(a), set(b)
    return len(sa & sb) / len(sa | sb) if sa and sb else 0.0


def aligned(c: StructuredClaim, e: StructuredClaim, kind: str) -> bool:
    """Is the evidence fact about the same thing as the claim?  Tool leaves need one shared content word with the claim; text facts need the same canonical topic or near-equal wording.
    Facts that name different allocation classes / amounts (equities vs bonds) are never aligned."""
    if kind == "tool":
        same = bool(set(c.topic_terms) & set(e.topic_terms))
    elif c.topic and e.topic:
        same = c.topic == e.topic
    else:
        same = _jaccard(c.topic_terms, e.topic_terms) >= 0.5
    dc, de = discriminators(c.topic_terms), discriminators(e.topic_terms)
    return same and not (dc and de and not (dc & de))


def compare_values(c: StructuredClaim, e: StructuredClaim) -> str:
    """'ok' | 'value' | 'unit' | 'unknown_unit' - exact Decimal comparison; the only conversion is months <-> years."""
    cu, eu = c.unit, e.unit
    if c.value is None or e.value is None:
        return "value"
    if cu is None and eu is None:
        return "ok" if c.value == e.value else "value"
    if cu is None or eu is None:
        return "unknown_unit"
    fc, fe = FAMILY.get(cu), FAMILY.get(eu)
    if fc is None or fe is None or fc != fe:
        return "unit"
    if fc == "currency":
        if cu != eu and "CURRENCY" not in (cu, eu):
            return "unit"
        return "ok" if c.value == e.value else "value"
    if fc == "duration":
        if cu in TO_MONTHS and eu in TO_MONTHS:
            if c.value * TO_MONTHS[cu] == e.value * TO_MONTHS[eu]:
                return "ok"
            return "unit" if (cu != eu and c.value == e.value) else "value"
        if cu != eu:
            return "unit"
    return "ok" if c.value == e.value else "value"


def date_supported(d: str, cand: Candidate) -> bool:
    if d in cand.dates:
        return True
    if len(d) == 4:
        if any(x[:4] == d for x in cand.dates):
            return True
        if cand.window:
            lo, hi = cand.window[0][:4], (cand.window[1] or "9999")[:4]
            return lo <= d <= hi
        return False
    if cand.window and len(d) == 10:
        return cand.window[0] <= d <= (cand.window[1] or "9999-12-31")
    return False


def _judge(c: StructuredClaim, cand: Candidate, b: Bundle) -> tuple[Outcome, Optional[Reason], str]:
    e = cand.fact
    # 1. subject
    if "multi_subject" in c.flags or "multi_subject" in e.flags or c.subject_client is None or e.subject_client is None:
        return Outcome.AMBIGUOUS, Reason.AMBIGUOUS_SUBJECT, "the subject of the claim or of the evidence cannot be established"
    if c.subject_client != e.subject_client:
        return Outcome.UNVERIFIED, Reason.CLIENT_MISMATCH, f"the claim is about {c.subject_client}, the evidence about {e.subject_client}"
    # 2. things the reader refuses to guess
    for flag in ("qualifier", "multi_negation", "negation_scope", "temporal_conflict", "unparsed_number", "alternatives"):
        if flag in e.flags:
            return Outcome.AMBIGUOUS, OPEN_FLAGS[flag], f"the evidence is {flag.replace('_', ' ')}"
        if flag in c.flags:
            return Outcome.AMBIGUOUS, OPEN_FLAGS[flag], f"the claim is {flag.replace('_', ' ')}"
    # 3. value and unit
    cmp = compare_values(c, e)
    if cmp == "unit":
        return Outcome.UNVERIFIED, Reason.UNIT_MISMATCH, f"claim {c.value} {c.unit} vs evidence {e.value} {e.unit}"
    if cmp == "unknown_unit":
        return Outcome.AMBIGUOUS, Reason.UNIT_MISMATCH, f"the unit cannot be established (claim: {c.unit}, evidence: {e.unit})"
    if cmp == "value":
        firm = RANK[e.modality] >= 4 and e.period is not None and e.period.kind != PeriodKind.HISTORICAL
        return (Outcome.CONTRADICTED if firm else Outcome.UNVERIFIED), Reason.VALUE_MISMATCH, f"claim {c.value} {c.unit or ''} vs evidence {e.value} {e.unit or ''}".replace("  ", " ")
    # 4. polarity
    if c.polarity is None or e.polarity is None:
        return Outcome.AMBIGUOUS, Reason.AMBIGUOUS_EVIDENCE, "polarity cannot be determined"
    if c.polarity != e.polarity:
        return Outcome.CONTRADICTED, Reason.POLARITY_MISMATCH, f"the evidence is {e.polarity.value.lower()}, the claim is {c.polarity.value.lower()}"
    # 5. period
    ck = c.period.kind if c.period else PeriodKind.UNSPECIFIED
    ek = e.period.kind if e.period else PeriodKind.UNSPECIFIED
    if (ck == PeriodKind.HISTORICAL) != (ek == PeriodKind.HISTORICAL):
        return Outcome.UNVERIFIED, Reason.TEMPORAL_MISMATCH, f"the claim is {ck.value.lower()}, the evidence is {ek.value.lower()}"
    for d in (c.period.dates if c.period else []):
        if not date_supported(d, cand):
            return Outcome.UNVERIFIED, Reason.TEMPORAL_MISMATCH, f"the evidence does not support the date {d}"
    if ck == PeriodKind.CURRENT and cand.kind == "quote" and ek != PeriodKind.CURRENT and cand.dates and not (c.period and c.period.dates):
        return Outcome.AMBIGUOUS, Reason.TEMPORAL_MISMATCH, "the claim is current but the evidence is tied to other dates"
    # 6. modality: a claim may not be firmer than its evidence
    rc, re_ = RANK[c.modality], RANK[e.modality]
    if rc > re_:
        return Outcome.UNVERIFIED, Reason.MODALITY_MISMATCH, f"the claim is {c.modality.value.lower() if c.modality else 'a plain assertion'}, the evidence only {e.modality.value.lower() if e.modality else 'asserted'}"
    if rc < re_:
        return Outcome.AMBIGUOUS, Reason.MODALITY_MISMATCH, "the claim is weaker than the evidence (the stronger statement is not reported)"
    return Outcome.VERIFIED, None, ""


def verify_claim(c: StructuredClaim, bundles: list[Bundle], ctx: Context) -> ClaimVerification:
    c = c.model_copy(deep=True)
    if c.subject_client is None and "multi_subject" not in c.flags and ctx.default_client:
        c.subject_client, c.subject_inferred = ctx.default_client, True
    handle = bundles[0].item.ref if bundles else None

    def done(outcome, reasons, detail, b=None, quote=None):
        c.evidence_handle = (b.item.ref if b else handle)
        c.source_id = b.source_id if b else None
        return ClaimVerification(claim=c, outcome=outcome, reasons=reasons, detail=detail, evidence_handle=c.evidence_handle, source_id=c.source_id, quote=quote)

    if "multi_subject" in c.flags or c.subject_client is None:
        return done(Outcome.AMBIGUOUS, [Reason.AMBIGUOUS_SUBJECT], "whom the claim is about cannot be established")
    if c.subject_client not in ctx.plan_clients:
        return done(Outcome.UNVERIFIED, [Reason.CLIENT_MISMATCH], f"{c.subject_client} is not a client of this report")
    verdicts = []                                             # (outcome, reason, detail, bundle, candidate)
    for b in bundles:
        if b.problems:
            verdicts.append((Outcome.UNVERIFIED, b.problems[0][0], b.problems[0][1], b, None))
            continue
        for cand in b.candidates:
            if aligned(c, cand.fact, cand.kind):
                verdicts.append((*_judge(c, cand, b), b, cand))
    comparable = [v for v in verdicts if v[4] is not None]
    if not comparable:
        reason = verdicts[0][1] if verdicts else Reason.NO_COMPARABLE_FACT
        return done(Outcome.UNVERIFIED, [reason], verdicts[0][2] if verdicts else "the cited evidence contains no fact about this topic", verdicts[0][3] if verdicts else None)
    good = [v for v in comparable if v[0] == Outcome.VERIFIED]
    if good:
        # a firm, current, same-client fact that says something else makes the evidence disagree with itself: do not call that verified
        rival = [v for v in comparable if v[0] == Outcome.CONTRADICTED and v[4].fact.period and v[4].fact.period.kind != PeriodKind.HISTORICAL]
        if rival:
            return done(Outcome.AMBIGUOUS, [Reason.AMBIGUOUS_EVIDENCE], "the cited evidence contains a conflicting current value", good[0][3])
        return done(Outcome.VERIFIED, [], "", good[0][3], good[0][3].quote)
    for want in (Outcome.AMBIGUOUS, Outcome.CONTRADICTED, Outcome.UNVERIFIED):
        hit = [v for v in comparable if v[0] == want]
        if hit:
            reasons = list(dict.fromkeys(v[1] for v in hit))
            return done(want, reasons, hit[0][2], hit[0][3], hit[0][3].quote)
    return done(Outcome.UNVERIFIED, [Reason.NO_COMPARABLE_FACT], "")


def verify_rendered(c: StructuredClaim, bundles: list[Bundle], ctx: Context) -> ClaimVerification:
    """Findings assembled by code from the cited evidence item (origin 'rendered' / 'tool_code').  Their wording is a template, so polarity / modality / period are NOT read from the
    prose; what is checked is what a template can get wrong: whom it names, and that every figure and date in it really occurs in the (verified) evidence."""
    c = c.model_copy(deep=True)
    usable = [b for b in bundles if not b.problems]
    handle = bundles[0].item.ref if bundles else None

    def done(outcome, reasons, detail, b=None):
        return ClaimVerification(claim=c, outcome=outcome, reasons=reasons, detail=detail, evidence_handle=(b.item.ref if b else handle), source_id=(b.source_id if b else None))

    if not usable:
        r = bundles[0].problems[0] if bundles and bundles[0].problems else (Reason.EVIDENCE_NOT_FOUND, "no usable evidence")
        return done(Outcome.UNVERIFIED, [r[0]], r[1])
    if "multi_subject" in c.flags:
        return done(Outcome.AMBIGUOUS, [Reason.AMBIGUOUS_SUBJECT], "several clients are named")
    if c.subject_client is not None and all(c.subject_client != b.client_id for b in usable):
        return done(Outcome.UNVERIFIED, [Reason.CLIENT_MISMATCH], f"the text names {c.subject_client} but the evidence belongs to {usable[0].client_id}", usable[0])
    if c.subject_client is None and not any(b.client_id for b in usable):
        return done(Outcome.AMBIGUOUS, [Reason.AMBIGUOUS_SUBJECT], "the evidence has no client")
    for d in (c.period.dates if c.period else []):
        if not any(d in b.rendered_dates or any(date_supported(d, cd) for cd in b.candidates) for b in usable):
            return done(Outcome.UNVERIFIED, [Reason.TEMPORAL_MISMATCH], f"the evidence does not contain the date {d}", usable[0])
    if c.value is None:
        return done(Outcome.AMBIGUOUS, [Reason.NO_QUANTIFIABLE_CLAIM], "no value to check", usable[0])
    for b in usable:
        if any(v == c.value for v, _ in b.rendered_values):
            return done(Outcome.VERIFIED, [], "", b)
    prefix = any(str(v).startswith(str(c.value).rstrip("0").rstrip(".")) and len(str(c.value)) >= 2 for b in usable for v, _ in b.rendered_values)
    if prefix:                                                # the stored description of a tool result is cut at a fixed length: a clipped number is not a wrong number
        return done(Outcome.AMBIGUOUS, [Reason.AMBIGUOUS_EVIDENCE], "the figure may be a truncated rendering of an evidence value", usable[0])
    return done(Outcome.UNVERIFIED, [Reason.VALUE_MISMATCH], f"{c.value} does not occur in the cited evidence", usable[0])
