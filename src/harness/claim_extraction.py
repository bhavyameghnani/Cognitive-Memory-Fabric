"""Deterministic extraction of StructuredClaims from text (claims AND evidence quotations use the same reader, so they are compared like for like).

This is a conservative, lexicon-and-grammar reader, not natural-language understanding.  Its contract is asymmetric on purpose:
  * when it reads something it reads it exactly (numbers as Decimal, never as substrings: 6 is not 63; 8% is not 80%; months are not years);
  * when it cannot read something it says so (None / UNSPECIFIED / a flag) and the verifier turns that into AMBIGUOUS, never into VERIFIED.
Anything it does not recognise therefore costs recall (a conservative false negative), never safety.

What is read, per quantitative assertion (one StructuredClaim per quantity):
  value + unit      12 months | 1 year | 8% | 35 percent | $1,200.50 | Rs 5 lakh | fifteen thousand | a bare number (unit None; a topic's default unit, e.g. years for a
                    retirement age, is attached as `unit` with unit_implied=True)
  topic             a canonical topic when the wording identifies one (retirement_age, cash_runway, emergency_fund_target, allocation, ...) plus the content words around the number
  subject_client    a client NAMED in the sentence (capitalised first name from the client directory); none named -> None (the caller applies the report's single client)
  polarity          NEGATIVE for not / never / no longer / cannot / without ...; two negations or negation next to several figures is flagged, not guessed
  modality          HYPOTHETICAL (if, would, what if) > EXPLORATORY (could, consider, discussed) > UNCERTAIN (may, might, nothing is settled) > RECOMMENDED > REPORTED (says, wants,
                    prefers) > ESTIMATED (about, roughly, directly before the number) > CONFIRMED (decided, confirmed, agreed); no cue -> None (a plain assertion)
  period            HISTORICAL (was, previously, earlier, no longer, superseded) | CURRENT (current, now) | PLANNED (will, plans to) | UNSPECIFIED, plus explicit ISO dates / years
Code-rendered status annotations ("the current confirmed knowledge", "it has been superseded and is no longer current", "exploratory, not a decision") are read as modality /
period and are removed before negation is scanned, so "not a decision" is not mistaken for a negated value.
"""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Optional

from schemas.claims import Modality, Period, PeriodKind, Polarity, StructuredClaim

# ------------------------------------------------------------------------------------------------------------------ lexicon
# canonical topic -> (regex over the words around the number, default unit when the text states none)
TOPICS: list[tuple[str, re.Pattern, Optional[str]]] = [
    ("emergency_fund_target", re.compile(r"emergency[\s-]fund\b.*\btarget\b|\btarget\b.*\bemergency[\s-]fund\b"), "months"),
    ("liquidity_target", re.compile(r"liquidity\s+(?:target|reserve)\b|\btarget\b.*\bliquidity\b"), "months"),
    ("cash_runway", re.compile(r"\bcash\s+(?:covers?|runway|lasts?|cover)\b|\bmonths?\s+of\s+(?:expenses|spending)\b|\brunway\b"), "months"),
    ("retirement_age", re.compile(r"\bretir\w*\s+(?:at|by|age)\b|\bretirement\s+age\b|\bretire\s+(?:at|by)\b"), "years"),
    ("target_retirement_date", re.compile(r"\b(?:target\s+)?retirement\s+date\b"), None),
    ("monthly_expenses", re.compile(r"\b(?:monthly\s+)?(?:expenses|spending|outgoings)\b"), None),
    ("allocation", re.compile(r"\ballocat\w*"), "percent"),
]
# words that distinguish WHICH allocation / amount is meant: two facts with disjoint, non-empty discriminators are never the same fact
DISCRIMINATORS = {"equit": "equities", "stock": "equities", "bond": "bonds", "cash": "cash", "gold": "gold", "propert": "property", "estate": "property", "mortg": "mortgage",
                  "incom": "income", "expen": "expenses", "spend": "expenses", "salar": "income", "pensi": "pension"}

STOP = set("""a an the and or of to in on for with at by from is are was were be been being it its as this that these those there their his her he she they them we you i me my our
your has have had will would shall should can could may might do does did not no never so than then also just only about around roughly approximately approx up make makes made
make covers cover covered per after before into over under again very more most some any each which who whom what when where while but if unless except since until during
total available current currently now today earlier previously formerly decided decide confirmed agreed settled planned plan plans planning wants want prefers prefer consider
considering considered discussed said says""".split())

NUMBER_WORDS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split())}
TENS = {w: 20 + 10 * i for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())}
SCALE = {"hundred": 100, "thousand": 1000, "million": 1_000_000}
_NW = "|".join(sorted([*NUMBER_WORDS, *TENS, *SCALE], key=len, reverse=True))
_NUMBER_WORD_RUN = re.compile(rf"\b(?:{_NW})(?:[\s-]+(?:{_NW}))*\b")
_UNIT_AFTER = re.compile(r"\s*(?:-\s*)?(?:months?|years?|yrs?|weeks?|days?|percent|per\s?cent|%|dollars?|rupees?)\b")

NUM = r"\d[\d,]*(?:\.\d+)?"
CUR_SYM = r"(?:₹|\$|€|£|(?<![a-z])(?:rs\.?|inr|us\$|usd|eur|gbp))"     # letters need a word start: 'rs' inside 'covers 15 months' is not rupees
MULT = r"(?:thousand|million|billion|lakhs?|crores?|mn|bn|k|m)"
_QUANT = re.compile(
    rf"(?P<date>\b\d{{4}}-\d{{2}}-\d{{2}}\b)"
    rf"|(?P<cp>{CUR_SYM})\s?(?P<cpn>{NUM})(?:\s?(?P<cpm>{MULT})\b)?"
    rf"|(?P<cs>{NUM})(?:\s?(?P<csm>{MULT})\b)?\s?(?P<csw>dollars?|usd|rupees?|inr|euros?|eur|pounds?|gbp)\b"
    rf"|(?P<pn>{NUM})\s?(?:%|percent\b|per\s?cent\b|pct\b)"
    rf"|(?P<dn>{NUM})[\s-]?(?P<du>months?|mos?\b|years?|yrs?|weeks?|days?)\b"
    rf"|\b(?:in|by|since|until|during|from|of|before|after|through|for|circa)\s+(?P<year>(?:19|20)\d{{2}})\b(?![\d%]|-\d)"
    rf"|(?<![\w.])(?P<bn>{NUM})(?:\s?(?P<bm>{MULT})\b)?(?![\w%])")
_MULT_VALUE = {"k": 1_000, "thousand": 1_000, "m": 1_000_000, "mn": 1_000_000, "million": 1_000_000, "bn": 1_000_000_000, "billion": 1_000_000_000,
               "lakh": 100_000, "lakhs": 100_000, "crore": 10_000_000, "crores": 10_000_000}
_CUR_CODE = {"₹": "INR", "rs": "INR", "rs.": "INR", "inr": "INR", "rupee": "INR", "rupees": "INR", "$": "USD", "us$": "USD", "usd": "USD", "dollar": "USD", "dollars": "USD",
             "€": "EUR", "eur": "EUR", "euro": "EUR", "euros": "EUR", "£": "GBP", "gbp": "GBP", "pound": "GBP", "pounds": "GBP"}
_DUR_UNIT = {"month": "months", "months": "months", "mo": "months", "mos": "months", "year": "years", "years": "years", "yr": "years", "yrs": "years", "week": "weeks", "weeks": "weeks",
             "day": "days", "days": "days"}

# ---- cues
_NEG = re.compile(r"\b(?:not|never|no longer|no|cannot|without|neither|nor|none|nobody)\b")
_NOT_ONLY = re.compile(r"\bnot only\b|\bwhether or not\b|\bif not\b")
_ALTERNATIVES = re.compile(r"\d\s*(?:,\s+|/|\s+or\s+)(?:[$₹€£]\s?)?\d")   # "63, 65 or 67", "60/63": thousands separators ("1,200") have no space and do not match
_QUALIFIER = re.compile(r"\b(?:except|unless|apart from|other than|only if|provided that|subject to|depends? on|depending on|dependent on|conditional(?: on)?|contingent)\b")
_HYPO = re.compile(r"\b(?:if|suppose|supposing|what if|hypothetical\w*|scenario|imagine|would)\b")
_WOULD_DESIRE = re.compile(r"\bwould (?:like|prefer|rather)\b")
_EXPLORE = re.compile(r"\b(?:could|consider\w*|thinking|think about|weigh\w*|explor\w*|idea|leaning|maybe|perhaps|possibly|tentative\w*|discuss\w*|talked about|raised|mentioned|floated)\b")
_UNCERTAIN = re.compile(r"\b(?:may(?!\s+\d)|might|unsure|unclear|undecided)\b")   # 'may 2026' is a month, not a modal
_RECOMMEND = re.compile(r"\b(?:recommend\w*|suggest\w*|propos\w*)\b")
_REPORTED = re.compile(r"\b(?:says?|said|told|reports?|reported|claims?|believes?|prefer\w*|wants?|wanted|hopes?|likes?|would like|would prefer|would rather)\b")
_CONFIRMED = re.compile(r"\b(?:decided|decide|confirmed?|agreed?|settled|committed|chosen|finali[sz]ed|resolved)\b")
_ESTIMATE_BEFORE = re.compile(r"(?:\b(?:about|around|roughly|approximately|approx|circa|estimated?)|~)\W*$")
_PAST = re.compile(r"\b(?:was|were|had been|used to|previously|formerly|earlier|until|ago|no longer|superseded|replaced by|has been replaced)\b")
_CURRENT = re.compile(r"\b(?:current|currently|now|today|at present|present)\b")
_PLANNED = re.compile(r"\b(?:will|plans?|planning|planned|intends?|going to|aims? to|target date)\b")

# code-rendered status annotations: (pattern, modality, period).  They are removed before negation / hypothetical scanning.
_ANNOTATIONS = [
    (re.compile(r"the current confirmed knowledge|current confirmed"), Modality.CONFIRMED, PeriodKind.CURRENT),
    (re.compile(r"(?:it )?has been superseded and is no longer current|is no longer (?:current|valid)|no longer (?:current|valid)"), None, PeriodKind.HISTORICAL),
    (re.compile(r"this was exploratory, not a decision|exploratory, not a decision"), Modality.EXPLORATORY, None),
    (re.compile(r"this is an advisor recommendation, not a client decision|advisor recommendation, not a client decision"), Modality.RECOMMENDED, None),
    (re.compile(r"\bnot (?:yet )?(?:settled|decided|final|confirmed)\b|\bnothing is settled\b"), Modality.UNCERTAIN, None),
    (re.compile(r"\bnot an? (?:client )?(?:decision|instruction|verified fact)\b"), None, None),
]

_PROVENANCE = re.compile(r"\((?:said by [^)]*|[A-Za-z]+-\d+ turn \d+|synthetic[^)]*|[A-Z]+-\d+(?:-[A-Za-z0-9]+)*)\)", re.I)
_QUALIFIED_LABEL = re.compile(r"^\s*\[qualified:[^\]]*\]\s*", re.I)
_HANDLE = re.compile(r"\[?\bE\d+\b\]?")
_IDENT = re.compile(r"\b[A-Za-z]{2,}-\d[\w-]*\b")
_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])|[\n;]+")
_CLAUSE = re.compile(r"[,;:()]|\b(?:but|while|whereas|against|although|which|however)\b")
_WORD = re.compile(r"[a-z][a-z']*")

DEFAULT_UNIT = {name: unit for name, _, unit in TOPICS}


def stem(w: str) -> str:
    """Crude, deterministic stem used only to align topic words (retire/retiring/retirement -> 'retir'; holdings/holding -> 'holdi')."""
    w = w.lower().strip("'")
    if w.endswith("ies") and len(w) > 4:
        w = w[:-3] + "y"
    elif w.endswith("s") and len(w) > 3 and not w.endswith("ss"):
        w = w[:-1]
    return w[:5] if len(w) > 5 else w


# ------------------------------------------------------------------------------------------------------------------ normalisation
def _words_to_number(run: str) -> Optional[int]:
    total, current, seen = 0, 0, False
    for tok in re.split(r"[\s-]+", run):
        if tok in NUMBER_WORDS:
            current += NUMBER_WORDS[tok]
        elif tok in TENS:
            current += TENS[tok]
        elif tok in SCALE:
            current = max(current, 1) * SCALE[tok]
            if SCALE[tok] >= 1000:
                total, current = total + current, 0
        else:
            return None
        seen = True
    return total + current if seen else None


def words_to_digits(text: str) -> str:
    """'sixty-three' -> '63', 'fifteen thousand' -> '15000', 'six months' -> '6 months'.  A lone word 'one'..'ten' is only converted next to a unit ('one' alone is a pronoun)."""
    def sub(m: re.Match) -> str:
        n = _words_to_number(m.group(0))
        if n is None:
            return m.group(0)
        single = " " not in m.group(0) and "-" not in m.group(0)
        if single and m.group(0) in SCALE:              # '5 thousand': the digit reader owns the multiplier; a lone scale word is not a number
            return m.group(0)
        if single and n <= 10 and not _UNIT_AFTER.match(text, m.end()):
            return m.group(0)
        return str(n)
    return _NUMBER_WORD_RUN.sub(sub, text)


def normalize(text: str) -> str:
    """Strip everything that is not a statement about the world: provenance tags, evidence handles, ids, a leading [Qualified: ...] label."""
    t = _QUALIFIED_LABEL.sub("", text or "")
    t = t.replace("’", "'").replace("“", '"').replace("”", '"').replace("_", " ")
    t = _PROVENANCE.sub(" ", t)
    t = _HANDLE.sub(" ", t)
    t = _IDENT.sub(" ", t)
    t = re.sub(r"\brs\.\s*(?=\d)", "Rs ", t, flags=re.I)        # 'Rs. 5 lakh' must not look like a sentence end
    for contraction, full in (("can't", "cannot"), ("won't", "will not"), ("shan't", "shall not")):
        t = re.sub(contraction, full, t, flags=re.I)
    t = re.sub(r"\b(\w+)n't\b", r"\1 not", t, flags=re.I)
    return " ".join(t.split())


# ------------------------------------------------------------------------------------------------------------------ extraction
def _amount(raw: str, mult: Optional[str] = None) -> Optional[Decimal]:
    try:
        d = Decimal(raw.replace(",", ""))
    except InvalidOperation:
        return None
    return d * _MULT_VALUE[mult.lower()] if mult else d


def _find_subjects(original: str, names: dict[str, str]) -> list[str]:
    """Clients named in the sentence.  Capitalised first names only (so 'grace period' is not the client Grace), possessives included."""
    found: list[str] = []
    for name, cid in names.items():
        if re.search(rf"\b{re.escape(name.capitalize())}\b", original) and cid not in found:
            found.append(cid)
    return found


def _strip_annotations(s: str) -> tuple[str, Optional[Modality], Optional[PeriodKind]]:
    mod, per = None, None
    for rx, m, p in _ANNOTATIONS:
        if rx.search(s):
            s = rx.sub(" ", s)
            mod, per = mod or m, per or p
    return s, mod, per


def _topic_for(window: str) -> tuple[Optional[str], Optional[str]]:
    for name, rx, unit in TOPICS:
        if rx.search(window):
            return name, unit
    return None, None


_PRONOUN_CONTRACTION = re.compile(r"^(?:i|we|you|they|he|she|it|there|that)'")


def _terms(window: str, skip: frozenset = frozenset()) -> list[str]:
    """Content words around a number.  Client names, stop words and 'I'm / we've' style words never count as topic words."""
    seen: list[str] = []
    for w in _WORD.findall(window):
        if w in STOP or len(w) < 3 or _PRONOUN_CONTRACTION.match(w):
            continue
        s = stem(w)
        if s in skip or s in seen:
            continue
        seen.append(s)
    return seen


def discriminators(terms: list[str]) -> set[str]:
    """Words that say WHICH allocation / amount is meant (terms are already stemmed to <= 5 characters, which is how DISCRIMINATORS is keyed)."""
    return {DISCRIMINATORS[t] for t in terms if t in DISCRIMINATORS}


def extract_claims(text: str, names: Optional[dict[str, str]] = None, handle: Optional[str] = None, source_id: Optional[str] = None) -> list[StructuredClaim]:
    """All quantitative assertions in `text`; one qualitative StructuredClaim (value None) for a sentence that states no quantity."""
    names = names or {}
    out: list[StructuredClaim] = []
    cleaned = normalize(text)
    for n, sent in enumerate([s for s in _SPLIT.split(cleaned) if s and s.strip()], 1):
        out.extend(_extract_sentence(sent.strip(), names, f"C{len(out) + 1}", handle, source_id))
    return out


def _extract_sentence(original: str, names: dict[str, str], first_id: str, handle: Optional[str], source_id: Optional[str]) -> list[StructuredClaim]:
    subjects = _find_subjects(original, names)
    skip = frozenset(stem(n) for n in names)
    s = words_to_digits(original.lower())
    s, ann_modality, ann_period = _strip_annotations(s)
    flags: list[str] = []
    if len(subjects) > 1:
        flags.append("multi_subject")

    # ---- quantities, dates, years
    quants: list[dict] = []
    dates: list[str] = []
    consumed: list[tuple[int, int]] = []
    for m in _QUANT.finditer(s):
        if m.group("date"):
            dates.append(m.group("date"))
        elif m.group("year"):
            dates.append(m.group("year"))
        elif m.group("cp"):
            quants.append(dict(span=m.span(), amount=_amount(m.group("cpn"), m.group("cpm")), unit=_CUR_CODE.get(m.group("cp").lower())))
        elif m.group("csw"):
            quants.append(dict(span=m.span(), amount=_amount(m.group("cs"), m.group("csm")), unit=_CUR_CODE.get(m.group("csw").lower())))
        elif m.group("pn"):
            quants.append(dict(span=m.span(), amount=_amount(m.group("pn")), unit="percent"))
        elif m.group("dn"):
            quants.append(dict(span=m.span(), amount=_amount(m.group("dn")), unit=_DUR_UNIT.get(m.group("du").lower().rstrip("."), None)))
        elif m.group("bn"):
            quants.append(dict(span=m.span(), amount=_amount(m.group("bn"), m.group("bm")), unit=None))
        consumed.append(m.span())

    # ---- sentence-level cues
    scan = s
    neg_hits = [m for m in _NEG.finditer(_NOT_ONLY.sub(" ", scan))]
    polarity: Optional[Polarity] = Polarity.POSITIVE
    if len(neg_hits) == 1:
        polarity = Polarity.NEGATIVE
    elif len(neg_hits) > 1:
        polarity = None
        flags.append("multi_negation")
    if neg_hits and len([q for q in quants if q["amount"] is not None]) > 1:
        flags.append("negation_scope")
    if _QUALIFIER.search(s):
        flags.append("qualifier")
    if _ALTERNATIVES.search(_QUANT.sub(lambda m: " DATE " if m.group("date") else m.group(0), s)):
        flags.append("alternatives")

    if ann_modality:
        modality: Optional[Modality] = ann_modality
    elif _WOULD_DESIRE.search(s):
        modality = Modality.REPORTED
    elif _HYPO.search(s):
        modality = Modality.HYPOTHETICAL
    elif _EXPLORE.search(s):
        modality = Modality.EXPLORATORY
    elif _UNCERTAIN.search(s):
        modality = Modality.UNCERTAIN
    elif _RECOMMEND.search(s):
        modality = Modality.RECOMMENDED
    elif _REPORTED.search(s):
        modality = Modality.REPORTED
    elif _CONFIRMED.search(s):
        modality = Modality.CONFIRMED
    else:
        modality = None

    past, current, planned = bool(_PAST.search(s)), bool(_CURRENT.search(s)), bool(_PLANNED.search(s))
    if ann_period:
        kind = ann_period
    elif past and current:
        kind = PeriodKind.UNSPECIFIED
        flags.append("temporal_conflict")
    elif past:
        kind = PeriodKind.HISTORICAL
    elif current:
        kind = PeriodKind.CURRENT
    elif planned:
        kind = PeriodKind.PLANNED
    else:
        kind = PeriodKind.UNSPECIFIED
    period = Period(kind=kind, dates=dates)

    # digits nobody accounted for (a malformed date, a number glued to a word): never silently ignored
    rest = s
    for a, b in sorted(consumed, reverse=True):
        rest = rest[:a] + " " + rest[b:]
    if re.search(r"\d", rest):
        flags.append("unparsed_number")

    base = dict(subject_client=subjects[0] if len(subjects) == 1 else None, polarity=polarity, period=period, evidence_handle=handle, source_id=source_id)
    real = [q for q in quants if q["amount"] is not None]
    if not real:
        return [StructuredClaim(claim_id=first_id, text=original, modality=modality, flags=list(flags), topic_terms=_terms(s, skip), **base)]

    claims: list[StructuredClaim] = []
    clause_bounds = [0] + [m.end() for m in _CLAUSE.finditer(s)] + [len(s)]
    for i, q in enumerate(real):
        a, b = q["span"]
        c_start = max(x for x in clause_bounds if x <= a)
        c_end = min([x for x in clause_bounds if x >= b] or [len(s)])
        prev_end = max([r["span"][1] for r in real[:i] if r["span"][1] <= a] + [c_start])
        next_start = min([r["span"][0] for r in real[i + 1:] if r["span"][0] >= b] + [c_end])
        before, after = s[prev_end:a], s[b:next_start]
        if not before.strip() and c_start > 0 and s[c_start - 1:c_start] == ":":
            earlier = [x for x in clause_bounds if x < c_start - 1]
            label = s[(max(earlier) if earlier else 0):c_start - 1]
            if label.strip() and not any(r["span"][0] >= (max(earlier) if earlier else 0) and r["span"][1] <= c_start for r in real):
                before = label                                  # a short label ahead of "label: value" belongs to the value
        # mask other quantities / dates so they are never read as topic words
        window = f"{before} {' '.join(after.split()[:3])}"
        topic, default_unit = _topic_for(window)
        terms = _terms(before, skip) or _terms(" ".join(after.split()[:3]), skip)
        only_one_in_clause = sum(1 for r in real if c_start <= r["span"][0] and r["span"][1] <= c_end) == 1
        if topic is None and only_one_in_clause:            # the whole clause may name the topic ('the target for the emergency fund is 6 months') - but never when several figures share it
            topic, default_unit = _topic_for(s[c_start:c_end])
        unit, implied = q["unit"], False
        if unit is None and default_unit is not None:
            unit, implied = default_unit, True
        qmod = modality
        if _ESTIMATE_BEFORE.search(before.strip()) and (modality is None or modality == Modality.CONFIRMED):
            qmod = Modality.ESTIMATED
        claims.append(StructuredClaim(claim_id=f"{first_id}.{i + 1}", text=original, topic=topic, topic_terms=terms, value=q["amount"], unit=unit, unit_implied=implied,
                                      modality=qmod, flags=list(flags), **base))
    return claims
