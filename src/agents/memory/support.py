"""Deterministic claim-to-source support checks (P1.4.2).  Pure functions, no model, no I/O.

WHAT THIS IS: a conservative lexical / numeric matcher.  A claim is "supported" only when (1) every sentence of the claim is matched by ONE sentence of the source, (2) negation and hedging
agree between claim and source, and (3) every number the claim states occurs in that source sentence in a CURRENT, same-unit, same-context role.
WHAT THIS IS NOT: semantic entailment.  It can miss paraphrases (those stay `unverified`) and it can still be fooled by wording it has no pattern for - it is a filter, not a proof.
Whenever it cannot decide it answers False.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from agents.memory import rules

_STOP = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "at", "for", "with", "that", "this", "it", "its", "is", "are", "was", "were", "be", "been", "i", "you", "we", "he", "she", "they",
         "my", "your", "our", "his", "her", "their", "me", "us", "them", "have", "has", "had", "do", "does", "did", "will", "as", "by", "from", "so", "but", "if", "than", "then", "about",
         "ve", "ll", "re", "s", "d", "m", "t", "just", "also", "very", "which", "who", "what", "when", "where", "there", "here", "into", "out", "up", "can", "all", "any", "some"}
NEGATION = {"not", "no", "never", "cannot", "without", "neither", "nor", "isn't", "wasn't", "don't", "didn't", "doesn't", "won't", "hasn't", "haven't", "hadn't", "aren't", "weren't", "can't",
            "couldn't", "wouldn't", "shouldn't", "none",
            # P1.4.3: rejection / refusal / retraction are negations of the statement they sit in
            "rejected", "reject", "rejects", "rejecting", "declined", "decline", "declines", "refused", "refuse", "refuses", "abandoned", "cancelled", "canceled", "dropped", "kidding", "joking"}
HEDGE = {"maybe", "might", "perhaps", "possibly", "probably", "could", "would", "considering", "weighing", "exploring", "thinking", "wondering", "whether", "if", "hypothetically",
         "suppose", "imagine", "may", "should", "idea", "option", "scenario", "potentially", "likely", "unsure", "tentatively", "consider", "explore", "weigh",
         # P1.4.3: conditions and alternatives
         "unless", "except", "otherwise", "either", "considered", "hypothetical"}
# P1.4.3: somebody ELSE said / recommended / asked it: hearsay and third-party advice are not the client's own statement
ATTRIB = {"recommend", "recommends", "recommended", "recommending", "advise", "advises", "advised", "advising", "suggest", "suggests", "suggested", "suggesting", "told", "tell", "tells",
          "urged", "urge", "urges", "asked", "ask", "asks", "instructed", "insist", "insists", "insisted", "requested", "request", "requests", "encouraged", "encourages", "said"}
# the audited source takes the statement back somewhere (even in ANOTHER sentence of the same excerpt): nothing in that excerpt can verify anything
RETRACTION = ("kidding", "joking", "scratch that", "never mind", "nevermind", "ignore that", "disregard", "forget that", "take that back", "changed my mind", "i misspoke", "strike that", "no longer believe", "no longer true", "was cancelled", "was canceled", "has been cancelled", "has been canceled")
RELATION = {"wife", "husband", "spouse", "partner", "father", "mother", "dad", "mom", "parent", "parents", "son", "daughter", "child", "children", "kids", "brother", "sister", "sibling",
            "colleague", "coworker", "boss", "manager", "friend", "neighbor", "neighbour", "advisor", "adviser", "accountant", "lawyer", "doctor", "employer", "uncle", "aunt", "cousin",
            "grandfather", "grandmother", "grandson", "granddaughter", "banker", "broker", "planner"}
_SELF = {"i", "me", "we", "us", "myself", "ourselves"}
_THIRD = {"he", "she", "they", "him", "her", "them", "his", "hers", "their", "theirs"}
_NOT_NAMES = {"i", "january", "february", "march", "april", "may", "june", "july", "august", "september", "october", "november", "december", "monday", "tuesday", "wednesday", "thursday",
              "friday", "saturday", "sunday", "rs", "usd", "inr", "ok", "okay", "yes", "no", "mr", "mrs", "ms", "dr", "ira", "sip", "etf", "epf", "ppf", "nps", "hdfc", "sbi", "icici"}
REPLACED = {"earlier", "previous", "previously", "former", "formerly", "old", "original", "originally", "replacing", "replaced", "replace", "instead", "rather", "prior", "superseded", "outdated",
            "used", "once", "initially", "formerly", "from"}
REPLACED_AFTER = {"was", "were", "previously", "formerly", "originally", "instead"}
# P1.5: a verb of INTENT or a past-tense copula ("portfolio WAS worth ...") next to a number is not a current commitment / value.  Its presence in the number's own window ("plans to", "wants to", "proposes to") is weaker than a decision.  Its presence next to a number must agree between claim and source, in BOTH directions
# must AGREE between claim and source, in BOTH directions (a claim must not firm up "wants to retire at 63" into "will retire at 63", turn "was 65" into "is 65", nor soften a decision).
WEAK = {"plans", "intends", "intend", "intending", "propose", "proposes", "proposed", "proposing", "wants", "want", "wanted", "hopes", "hope", "hoping", "aims", "prefers", "prefer",
        "wishes", "wish", "expects", "expect"}
MODAL = WEAK | {"was", "were"}
# P1.5 rules are on by default.  The switches exist ONLY so that harness.evaluation.p15 can re-run the shipped P1.4.2 behaviour on the same corpus (reproducible before/after).
FLAGS = {"modal_parity": True, "magnitude_units": True, "structure": True, "context_window": True}
_MAG = {"thousand": "thousand", "lakh": "lakh", "lakhs": "lakh", "lac": "lakh", "crore": "crore", "crores": "crore", "million": "million", "billion": "billion", "k": "thousand", "hundred": "hundred"}
_UNIT_PCT = {"%", "percent", "pct", "per"}
_UNIT_MONEY = {"$", "dollar", "dollars", "usd", "grand", "rupee", "rupees", "rs", "inr"}
_UNIT_MONTH = {"month", "months"}
_NUMWORDS = set(rules._UNITS) | set(rules._TENS) | {"hundred", "thousand", "million"}

_TOKEN = re.compile(r"\$?\d[\d,]*(?:\.\d+)?%?|[a-z]+(?:'[a-z]+)?", re.I)


def _digitize(text: str) -> str:
    """Spelled-out numbers become digits ('sixty-three' -> '63') so that both sides are compared in one form."""
    words = re.findall(r"\S+", (text or "").replace("’", "'").replace("-", " "))
    out: list[str] = []
    run: list[str] = []

    def flush() -> None:
        if run:
            nums = rules.extract_numbers(" ".join(run))
            out.append(nums[0] if len(nums) == 1 else " ".join(run))
            run.clear()
    for w in words:
        plain = re.sub(r"[^a-z]", "", w.lower())
        if plain in _NUMWORDS and plain == w.lower().strip(".,;:!?"):
            run.append(plain)
        else:
            flush()
            out.append(w)
    flush()
    return " ".join(out)


def sentences(text: str) -> list[str]:
    parts = [s.strip() for s in re.split(r"(?<=[.!?])\s+", (text or "").replace("’", "'")) if s.strip()]
    return parts or ([text.strip()] if (text or "").strip() else [])


def tokens(sentence: str) -> list[str]:
    toks = [t.lower() for t in _TOKEN.findall(_digitize(sentence))]
    return [t.split("'")[0] if "'" in t and not t.endswith("n't") else t for t in toks]        # "you've" -> "you", "we're" -> "we"; "don't" stays (it is a negation)


def _is_num(tok: str) -> bool:
    return bool(re.fullmatch(r"\$?\d[\d,]*(?:\.\d+)?%?", tok))


def _num_value(tok: str) -> str:
    n = rules.extract_numbers(tok.replace("$", "").replace("%", ""))
    return n[0] if n else tok


def _stem(w: str) -> str:
    return w[:4]


def content_stems(toks: list[str]) -> list[str]:
    return [_stem(t) for t in toks if not _is_num(t) and t not in _STOP and t not in NEGATION and t not in HEDGE and t not in REPLACED and len(t) >= 3]


def _has(toks: list[str], vocab: set) -> bool:
    return any(t in vocab for t in toks)


def unit_class(toks: list[str], i: int) -> str:
    """pct | money | months | age (a bare number or years), plus the magnitude word that follows the number ('money:million', 'age:crore'): a lakh is not a crore, a thousand is not a million."""
    t = toks[i]
    j, mag = i + 1, ""
    if FLAGS["magnitude_units"] and j < len(toks) and toks[j] in _MAG:
        mag, j = _MAG[toks[j]], j + 1
    nxt = toks[j] if j < len(toks) else ""
    if t.endswith("%") or nxt in _UNIT_PCT:
        base = "pct"
    elif t.startswith("$") or (i > 0 and toks[i - 1] in ("$", "usd")) or nxt in _UNIT_MONEY or (not FLAGS["magnitude_units"] and i + 1 < len(toks) and toks[i + 1] in ("k", "thousand", "million")):
        base = "money"
    elif nxt in _UNIT_MONTH:
        base = "months"
    else:
        base = "age"
    return base + (":" + mag if mag else "")


def _value_unit(value: str) -> Optional[str]:
    toks = tokens(value or "")
    idx = next((i for i, t in enumerate(toks) if _is_num(t)), None)
    return unit_class(toks, idx) if idx is not None else None


def _occurrences(toks: list[str], num: str) -> list[int]:
    return [i for i, t in enumerate(toks) if _is_num(t) and _num_value(t) == num]


def _window(toks: list[str], i: int) -> tuple[list[str], list[str]]:
    return toks[max(0, i - 4):i], toks[i + 1:i + 3]


def occurrence_is_current(toks: list[str], i: int) -> bool:
    """False when the number sits in a replaced / hypothetical / negated context ('earlier plan of 65', 'weighing 60', 'not 63', '65 was the plan')."""
    before, after = _window(toks, i)
    if _has(before, NEGATION) or _has(before, HEDGE) or _has(before, REPLACED):
        return False
    return not (after[:1] and after[0] in REPLACED_AFTER)


def numbers_in(text: str) -> list[str]:
    return [_num_value(t) for t in tokens(text) if _is_num(t)]


@dataclass(frozen=True)
class SourceContext:
    """The ONLY thing a cited CLIENT sentence may borrow from its surroundings (P1.5.x): the topic words that the immediately preceding ADVISOR turn names, and the client's own first
    name (a client who says "we've decided: 63" is the subject the statement calls "Kenji").  Nothing else is borrowed: negation, hedging, modality, attribution, structure and the
    value itself are still judged on the cited sentence alone."""
    topic_stems: frozenset
    client_name: str


def source_context(prior_turn: Optional[str], client_name: Optional[str], topic_context: str) -> Optional[SourceContext]:
    """A SourceContext for a cited client turn, or None.  None unless the preceding advisor turn is not a retraction and names the memory's own topic (shares a content stem with
    `topic_context` = topic + subject): a turn about "the college fund" never licenses a statement about retirement."""
    if not FLAGS["context_window"] or not (prior_turn or "").strip() or not (client_name or "").strip() or retracted(prior_turn):
        return None
    shared = set(content_stems(tokens(topic_context))) & set(content_stems(tokens(prior_turn)))
    return SourceContext(frozenset(shared), client_name.strip().lower()) if shared else None


def _sentence_match(claim: list[str], source: list[str], extra: frozenset = frozenset()) -> tuple[float, int]:
    """(share of the claim's content words found in the source sentence [or in `extra`, the borrowed context stems], number of claim content words NOT found)."""
    cs = content_stems(claim)
    if not cs:
        return 0.0, 0
    ss = {_stem(t) for t in source} | set(extra)
    hit = sum(1 for c in cs if c in ss or any(s.startswith(c) for s in ss if len(c) < 4))
    return hit / len(cs), len(cs) - hit


_SEP = re.compile(r"(, |; | and | but | or | then | while | although | though )")


def _actors(text: str, first: bool) -> tuple:
    """Who the clause is about, in order of mention: SELF (the speaker), a relation noun (wife, advisor ...), THIRD (he / she / they) or a capitalised name."""
    out: list[str] = []
    words = re.findall(r"[A-Za-z][A-Za-z'-]*", text.replace("\u2019", "'"))
    for k, w in enumerate(words):
        lw = w.lower().split("'")[0]                                                  # "we've" -> "we"
        a = "SELF" if lw in _SELF else ("THIRD" if lw in _THIRD else (lw if lw in RELATION else ""))
        named = w[0].isupper() and lw not in _NOT_NAMES and lw not in _STOP and lw not in NEGATION and lw not in HEDGE
        if not a and named and (k > 0 or not first or w.lower().endswith("'s") or (len(words) > 1 and words[1].lower() in ("will", "is", "has", "was", "plans", "wants", "intends", "retires", "says", "said", "thinks", "may", "might", "would"))):
            a = "NAME:" + lw
        if a and (not out or out[-1] != a):
            out.append(a)
    return tuple(out)


def _structure(sentence: str) -> list[dict]:
    """The sentence as a list of clauses, each with its conjunction, actors, negation / hedge / attribution flags and numbers.  A clause without an actor inherits the previous clause's."""
    parts = _SEP.split(" " + (sentence or "").replace("\u2019", "'").strip() + " ")
    out: list[dict] = []
    conj = ""
    prev: tuple = ()
    for part in parts:
        if _SEP.fullmatch(part):
            conj = part.strip(" ,;")
            conj = "and" if conj in ("", ",", ";") else conj
            continue
        if not part.strip():
            continue
        tk = tokens(part)
        act = _actors(part.strip(), not out)
        eff = act if act else prev
        prev = eff if eff else prev
        out.append(dict(conj=conj, actors=eff, neg=_has(tk, NEGATION), hedge=_has(tk, HEDGE), attrib=_has(tk, ATTRIB), nums=set(numbers_in(part))))
        conj = ""
    return out


def _clause_ok(c: dict, s: dict, first: bool) -> bool:
    if not first and c["conj"] != s["conj"]:
        return False
    if (c["neg"], c["hedge"], c["attrib"]) != (s["neg"], s["hedge"], s["attrib"]) or not c["nums"] <= s["nums"]:
        return False
    return c["actors"] == s["actors"] or (c["actors"] == () and s["actors"] in ((), ("SELF",)))


def structure_ok(claim_text: str, source_text: str, client_name: Optional[str] = None) -> bool:
    """Every clause of the claim must be matched, in order, by a clause of the source with the same actor(s), the same negation / hedge / attribution flags, the same connective
    (and / or / but) and the numbers it states.  The source may have MORE clauses; the claim may not state a clause the source does not state in that form."""
    if not FLAGS["structure"]:
        return True
    cl, sl = _structure(claim_text), _structure(source_text)
    if client_name:                                          # a client's own turn: "we/I" IS the client, so the statement may name them; any OTHER name still mismatches
        me = "NAME:" + client_name.lower()
        cl = [dict(c, actors=tuple("SELF" if a == me else a for a in c["actors"])) for c in cl]
    j = 0
    for k, c in enumerate(cl):
        while j < len(sl) and not _clause_ok(c, sl[j], k == 0):
            j += 1
        if j >= len(sl):
            return False
        j += 1
    return True


def retracted(excerpt: str) -> bool:
    low = (excerpt or "").lower().replace("\u2019", "'")
    return FLAGS["structure"] and any(p in low for p in RETRACTION)


def _ctx_stems(toks: list[str], i: int) -> set:
    return set(content_stems(toks[max(0, i - 6):i] + toks[i + 1:i + 4]))


def _number_ok(src: list[str], n: str, unit: str, stems: set, allow_noncurrent: bool, claim_weak: bool = False) -> bool:
    """Does the number n occur in the source sentence in the right unit, in a current role (unless a hedged / replaced claim is being made), and - when the sentence holds other numbers
    of the same unit, so that the role is ambiguous - next to a word the claim (or the corrected subject) shares?"""
    for i in _occurrences(src, n):
        if unit_class(src, i) != unit or not (allow_noncurrent or occurrence_is_current(src, i)) or (FLAGS["modal_parity"] and _has(src[max(0, i - 4):i], MODAL) != claim_weak):
            continue
        rivals = any(j != i and _is_num(src[j]) and _num_value(src[j]) != n and unit_class(src, j) == unit for j in range(len(src)))
        if not rivals or (stems & _ctx_stems(src, i)):
            return True
    return False


def _claim_sentence_supported(claim_text: str, value_nums: list[tuple[str, str]], source_sents: list[str], allow_noncurrent: bool = False, context: str = "", verbatim: bool = False,
                              ctx: Optional[SourceContext] = None) -> tuple[bool, str]:
    ct = tokens(claim_text)
    if len(ct) < 3 or len(content_stems(ct)) + sum(1 for t in ct if _is_num(t)) < 2:
        return False, "the claim is too short to express a proposition"
    best, best_cov, best_miss, best_raw = None, 0.0, 0, ""
    extra = (ctx.topic_stems | {_stem(ctx.client_name)}) if ctx else frozenset()
    for s in source_sents:
        st = tokens(s)
        cov, miss = _sentence_match(ct, st, extra)
        if ctx and _sentence_match(ct, st)[1] >= len(content_stems(ct)):      # borrowed words may fill gaps; they can never be the ONLY match: the cited sentence itself must share a content word
            continue
        if cov > best_cov:
            best, best_cov, best_miss, best_raw = st, cov, miss, s
    if best is None or best_cov < 0.75 or best_miss > 1:         # a long sentence must not hide appended words behind its own coverage
        return False, "part of the claim is not found in any single source sentence"
    if not structure_ok(claim_text, best_raw, ctx.client_name if ctx else None):
        return False, "who acts, what is negated / conditional / attributed to someone else, or how the clauses are joined differs between the claim and the source"
    if _has(ct, NEGATION) != _has(best, NEGATION):
        return False, "negation differs between the claim and the source"
    if _has(ct, HEDGE) != _has(best, HEDGE):
        return False, "hedging / hypothetical wording differs between the claim and the source"
    cstems = set(content_stems(ct))
    marked = _has(ct, REPLACED) or _has(ct, HEDGE) or _has(ct, NEGATION)
    for i, t in enumerate(ct):
        if _is_num(t) and not _number_ok(best, _num_value(t), unit_class(ct, i), cstems, marked, _has(ct[max(0, i - 4):i], MODAL)):
            return False, f"the number {_num_value(t)} is not stated in the source in the same role, unit or context as the claim"
    # an LLM paraphrase may use its own words as context; a verbatim sentence contains EVERY word of its source, so only the corrected / extracted SUBJECT counts for it
    vstems = set(content_stems(tokens(context))) | (set() if verbatim else cstems)
    for n, u in value_nums:
        pos = [i for i, t in enumerate(ct) if _is_num(t) and _num_value(t) == n]          # the value's own role in the claim: its own window, not the whole sentence
        if not _number_ok(best, n, u, vstems, allow_noncurrent, any(_has(ct[max(0, i - 4):i], MODAL) for i in pos) if pos else _has(ct, MODAL)):
            return False, "the extracted value is not stated in the source as a current value of the same unit"
    return True, "supported"


def claim_supported(content: str, value: Optional[str], excerpts: list[str], *, verbatim: bool, allow_noncurrent: bool = False, context: str = "",
                    ctxs: Optional[list] = None) -> tuple[bool, str]:
    """Is the memory's statement (and its extracted value) supported by ONE cited excerpt?
    verbatim=True (rule-extracted): the statement must BE one source sentence.  verbatim=False (LLM paraphrase): see the module docstring."""
    if not (content or "").strip():
        return False, "no statement"
    vu = _value_unit(value or "")
    vnums = [(n, vu or "age") for n in numbers_in(value or "")]
    why = "the statement is not supported by the cited source"
    for idx, ex in enumerate(excerpts):
        if retracted(ex):
            why = "the cited source takes the statement back (retraction)"
            continue
        src = sentences(ex)
        if verbatim:
            nc = " ".join(tokens(content))
            hit = next((s for s in src if " ".join(tokens(s)) == nc or (len(content) >= 400 and " ".join(tokens(s)).startswith(nc))), None)
            if hit is None:
                why = "the stored statement is not one of the cited sentences"
                continue
            ok, why = _claim_sentence_supported(hit, vnums, src, allow_noncurrent, context, True)
            if ok:
                return True, "supported"
            continue
        csents = sentences(content)
        results = [_claim_sentence_supported(c, vnums if k == 0 else [], src, allow_noncurrent, context) for k, c in enumerate(csents)]
        if all(ok for ok, _ in results):
            return True, "supported"
        why = next(w for ok, w in results if not ok)
        ctx = ctxs[idx] if ctxs and idx < len(ctxs) else None
        if ctx is not None:                      # second, strictly additive attempt: only for a cited client turn whose preceding advisor turn names the topic (see SourceContext)
            again = [_claim_sentence_supported(c, vnums if k == 0 else [], src, allow_noncurrent, context, False, ctx) for k, c in enumerate(csents)]
            if all(ok for ok, _ in again):
                return True, "supported_with_context"
    return False, why


def value_current_in(value: Optional[str], excerpts: list[str], context: str = "") -> bool:
    """Human corrections: the new value must occur in ONE cited source sentence in a CURRENT role (not replaced / hypothetical / negated), with the same unit class, and
    next to a word of the corrected subject (`context`: topic, subject and the corrected statement) - 'Mariko teaches until 62' does not support 'retirement age 62'."""
    if not value or not value.strip():
        return False
    nums = numbers_in(value)
    if not nums:
        return rules.value_supported(value, " ".join(excerpts))
    unit = _value_unit(value) or "age"
    ctx = set(content_stems(tokens(context)))
    return any(all(_number_ok(tokens(snt), n, unit, ctx, False) for n in nums) for ex in excerpts if not retracted(ex) for snt in sentences(ex))


def value_numbers_equal(a: Optional[str], b: Optional[str]) -> bool:
    return numbers_in(a or "") == numbers_in(b or "") and bool(numbers_in(a or ""))
