"""Clause-level support check for the REPORT boundary (P1.5.1, audit finding H1).

WHY THIS EXISTS.  The structured claim verifier compares the FIGURES a finding states with the figures of the evidence it cites.  A finding that is supported on its figure and then
EXTENDS itself ("... is 63 and he has a terminal illness", "... which his wife also chose") has no further figure, so the extension was never compared with anything and the whole
finding was presented as VERIFIED.  This module is the second half of the check: every clause of a model-written finding must be accounted for by the cited source text, and the
structure around the figure (who acts, negation, conditions, hearsay, 'and' vs 'or', retraction) must match.  It can only DOWNGRADE a verdict; it never promotes anything.

WHAT THIS IS NOT.  It is deterministic lexical / structural matching, not semantic entailment, and it reuses the building blocks of the memory matcher (agents.memory.support) rather
than a model.  When it cannot establish that a clause is covered it says so (UNVERIFIED, or AMBIGUOUS when exactly one word is not accounted for).  False negatives (a faithful
paraphrase that is downgraded) are accepted on purpose; an unsupported claim presented as verified is not.

Code-rendered findings (origin rendered / tool_code) are assembled from the evidence item's own fields and are not checked here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

from agents.memory import support
from schemas.claims import Outcome, Reason

# words a report may use that describe the finding itself rather than assert a new fact
ENABLED = True            # evaluation only: p151 switches the clause check off to measure the report verifier as it was before P1.5.1
SCAFFOLD = {"curr", "conf", "know", "supe", "vali", "epis", "clie", "valu", "stat", "acti", "chan", "upda", "repl", "revi", "adju", "swit", "deci", "reco", "earl", "prev", "orig",
            # symbols that a source writes differently ("35%" / "35 percent", "$2 million" / "2 million dollars")
            "old", "perc", "doll",
            # copular paraphrase verbs ("makes up", "stands at", "amounts to"): they restate the figure, they do not add a fact
            "make", "stan", "amou", "repr", "tota", "equa"}
_SPLIT = re.compile(r"(, | and | but | or | then | while | although | though | which | who | whose | because | so | with | also )")
# a COMPLETED action in the finding ("has retired", "sold") needs the same completed form in the source: "plans to retire" / "will retire" do not support it
COMPLETED = {"retired", "sold", "bought", "purchased", "completed", "transferred", "closed", "paid", "signed", "married", "divorced", "inherited", "resigned", "quit", "hired", "moved",
             "finished", "cancelled", "canceled", "withdrew", "withdrawn", "liquidated"}
# conditions that a SOURCE attaches to a statement ("only after the sale", "once the house sells", "when the loan closes"): a finding without them states more than the source does
CONDITION = {"only", "after", "before", "once", "when", "whenever", "provided", "assuming", "depending", "contingent"}
# "after tax" is an AMOUNT qualifier ("15,000 a month after tax", "after-tax income"), not a temporal "after".  The tokenizer drops hyphens and punctuation ("after-tax" == "after tax"), so the pair
# alone cannot tell it from "after tax season" / "after tax filing".  An "after <tax>" is therefore exempt ONLY in one of two shapes; anything else stays a condition (conservative: the
# finding is withheld/qualified rather than silently verified):
#   (a) it ENDS the clause and an amount cue (a period unit or a currency token) comes before it: "fifteen thousand a month after tax";
#   (b) the tax word is followed by an amount noun and nothing but filler / figures: "after-tax income", "after-tax income is 15,000".
TAX_WORDS = {"tax", "taxes", "taxation"}
AMOUNT_NOUNS = {"income", "pay", "salary", "wage", "wages", "return", "returns", "proceeds", "amount", "cash", "profit", "profits", "earnings", "pension", "benefit", "yield"}
AMOUNT_FILLER = {"is", "are", "was", "were", "will", "be", "of", "at", "about", "around", "roughly", "approximately", "a", "an", "per", "month", "year", "week", "monthly", "annual", "annually"}
AMOUNT_CUES = {"month", "months", "monthly", "year", "years", "yearly", "annual", "annually", "week", "weekly", "per", "dollars", "usd", "inr", "eur", "gbp", "rupees", "euros", "pounds"}
_FIRST = {"he", "she", "his", "her", "hers", "him", "the client", "client"}


@dataclass
class ClauseResult:
    outcome: Outcome
    reason: Reason
    detail: str


def _names_to_self(text: str, client_names: Iterable[str]) -> str:
    t = (text or "").replace("’", "'")
    for n in sorted({x for x in client_names if x}, key=len, reverse=True):
        t = re.sub(r"(?<![A-Za-z])" + re.escape(n) + r"(?:'s)?(?![A-Za-z])", lambda m: "my" if m.group(0).lower().endswith("'s") else "I", t, flags=re.I)
    return t


def _pronouns_if_alone(sentence: str) -> str:
    """In a source sentence about the client alone ("Kenji saves 20% of his income") he / his are the client; next to a relation noun or another name they are NOT assumed to be."""
    words = {w.lower() for w in re.findall(r"[A-Za-z]+", sentence)}
    others = [w for w in re.findall(r"(?<!^)(?<![.!?] )[A-Z][a-z]+", sentence) if w.lower() not in support._NOT_NAMES and w != "I"]
    if words & support.RELATION or others:
        return sentence
    sentence = re.sub(r"(?<![A-Za-z])(?:he|she)(?![A-Za-z])", "I", sentence, flags=re.I)
    return re.sub(r"(?<![A-Za-z])(?:his|her)(?![A-Za-z])", "my", sentence, flags=re.I)


def _normalise(text: str, client_names: Iterable[str]) -> str:
    """The report talks about 'Kenji' / 'he' / 'his'; the source talks in the first person.  Both become the SAME actor so that actors can be compared."""
    t = (text or "").replace("’", "'")
    for n in sorted({x for x in client_names if x}, key=len, reverse=True):
        t = re.sub(r"(?<![A-Za-z])" + re.escape(n) + r"(?:'s)?(?![A-Za-z])", lambda m: "my" if m.group(0).lower().endswith("'s") else "I", t, flags=re.I)
    t = re.sub(r"(?<![A-Za-z])(?:the client)(?:'s)?(?![A-Za-z])", lambda m: "my" if m.group(0).lower().endswith("'s") else "I", t, flags=re.I)
    t = re.sub(r"(?<![A-Za-z])(?:he|she)(?![A-Za-z])", "I", t, flags=re.I)
    t = re.sub(r"(?<![A-Za-z])(?:his|her)(?![A-Za-z])", "my", t, flags=re.I)
    return t


def _clauses(sentence: str) -> list[dict]:
    parts = _SPLIT.split(" " + sentence.strip() + " ")
    out, conj, prev = [], "", ()
    for part in parts:
        if _SPLIT.fullmatch(part):
            conj = part.strip(" ,;") or "and"
            conj = "and" if conj in ("", ",", ";", "also", "then", "so") else conj
            continue
        if not part.strip():
            continue
        tk = support.tokens(part)
        act = support._actors(part.strip(), not out)
        eff = act if act else prev
        prev = eff or prev
        out.append(dict(text=part.strip(), toks=tk, toks_set=set(tk), conj=conj, actors=eff, neg=support._has(tk, support.NEGATION), hedge=support._has(tk, support.HEDGE),
                        attrib=support._has(tk, support.ATTRIB), nums=set(support.numbers_in(part)), stems=set(support.content_stems(tk))))
        conj = ""
    return out


_TIME_UNITS = {"mont", "week", "days", "hour"}


def _duration_ok(c: dict, best: dict, uncovered: list) -> bool:
    """The only unit-word equivalences allowed: 'N years' for a bare age N ("retire at 63" / "63 years"), and 'N years' <-> '12*N months'.  'a month' versus 'a year' (same figure) is NOT one."""
    if uncovered == ["year"] and not (best["stems"] & _TIME_UNITS):
        return True
    if set(uncovered) <= {"year", "mont"}:
        for n in c["nums"]:
            for m in best["nums"]:
                try:
                    a, b = float(n.replace(",", "")), float(m.replace(",", ""))
                except ValueError:
                    continue
                if a * 12 == b or a == b * 12:
                    return True
    return False


def _is_tax_qualifier(tk: list, i: int) -> bool:
    """True when the 'after' at tk[i] qualifies an amount ('... a month after tax', 'after-tax income') instead of naming a moment."""
    if i + 1 >= len(tk) or tk[i + 1] not in TAX_WORDS:
        return False
    rest = tk[i + 2:]
    if not rest:                                                                       # shape (a): the clause ends with 'after tax' and an amount cue precedes it
        return any(t in AMOUNT_CUES or t.startswith("$") for t in tk[:i])
    return rest[0] in AMOUNT_NOUNS and all(t in AMOUNT_NOUNS or t in AMOUNT_FILLER or support._is_num(t) for t in rest)       # shape (b)


_DETERMINERS = {"the", "a", "an", "my", "his", "her", "our", "their", "this", "that"}


def conditions(tk: list) -> set:
    """Condition markers of a clause, as comparable keys.  Plain markers ('only', 'once', ...) are their own key.  Every 'after' that is not an amount qualifier becomes 'after:<next word>'
    (determiners skipped), so a DIFFERENT 'after' in the claim ('after tax') cannot cover the source's ('after the sale'): the claim must state the same condition, not just any 'after'."""
    found = {t for t in tk if t in CONDITION and t != "after"}
    for i, t in enumerate(tk):
        if t == "after" and not _is_tax_qualifier(tk, i):
            nxt = next((w for w in tk[i + 1:] if w not in _DETERMINERS), "")
            found.add(f"after:{nxt}")
    return found


def _roles(actors: tuple) -> tuple:
    """Who comes first matters when someone else is involved ('my wife retires before me' is not 'I retire before my wife')."""
    return tuple(a for a in actors if a != "you")


def _outsiders(actors: tuple) -> set:
    return {a for a in actors if a not in ("SELF", "you")}


def check(finding: str, sources: list, vocabulary: str, client_names: Iterable[str], *, open_evidence: bool = False) -> list[ClauseResult]:
    """Problems found in `finding` against the verified source texts `sources` (quotations proven to be real).  Empty list = nothing to downgrade."""
    if not ENABLED or not sources:
        return []
    sources = [(s, set()) if isinstance(s, str) else (s[0], set(s[1])) for s in sources]       # (quotation, names of the client who owns it)
    if any(support.retracted(s) for s, _ in sources):
        return [ClauseResult(Outcome.UNVERIFIED, Reason.MODALITY_MISMATCH, "the cited source takes the statement back (retraction)")]
    names = list(client_names)
    src_clauses: list[dict] = []
    for s, owner in sources:
        for sent in support.sentences(_pronouns_if_alone(_names_to_self(s, owner))):          # the owner speaks about himself: "Kenji will retire" in a source about Kenji is the same actor as "I"
            src_clauses += _clauses(sent)
    vocab = {support._stem(t) for t in support.tokens(vocabulary + " " + " ".join(s for s, _ in sources))} | SCAFFOLD
    vocab |= {support._stem(n.lower()) for n in names}
    src_outsiders = set().union(*[_outsiders(e["actors"]) for e in src_clauses]) if src_clauses else set()
    problems: list[ClauseResult] = []
    for sent in support.sentences(_normalise(finding, names)):
        for k, c in enumerate(_clauses(sent)):
            if not c["stems"] and not c["nums"]:
                continue
            stranger = sorted(_outsiders(c["actors"]) - src_outsiders)
            if stranger:                                       # a person (wife, parent, colleague ...) that no cited source clause mentions
                who = "named person" if stranger[0].startswith("NAME:") else stranger[0]        # a relation noun (wife, father ...) comes from a fixed list; a NAME: token is a word of the finding itself, which may be withheld
                problems.append(ClauseResult(Outcome.UNVERIFIED, Reason.ACTOR_MISMATCH, f"a clause brings in a person ({who}) who is not in the cited source"))
                continue
            # the source clause this one is about: the most shared numbers / content words
            best: Optional[dict] = None
            score = 0
            for e in src_clauses:
                sc = 3 * len(c["nums"] & e["nums"]) + len(c["stems"] & e["stems"])
                if sc > score:
                    best, score = e, sc
            uncovered = sorted(s for s in c["stems"] if s not in vocab)
            if uncovered and best is not None and _duration_ok(c, best, uncovered):
                uncovered = []
            if len(uncovered) >= 2:
                problems.append(ClauseResult(Outcome.UNVERIFIED, Reason.UNSUPPORTED_CLAUSE, f"a clause states what the cited source does not ({len(uncovered)} unsupported words)"))
                continue
            if len(uncovered) == 1:
                problems.append(ClauseResult(Outcome.AMBIGUOUS, Reason.UNSUPPORTED_CLAUSE, "a clause contains a word the cited source does not support"))
            if best is None:
                continue
            if c["nums"] and not c["nums"] <= best["nums"]:
                continue                                    # figures are the structured verifier's job (value / unit / period); this module only checks the structure around them
            done = sorted(t for t in c["toks"] if t in COMPLETED and t not in best["toks"])
            if done:
                problems.append(ClauseResult(Outcome.UNVERIFIED, Reason.TEMPORAL_MISMATCH, f"a clause states a completed action ({done[0]}) that the source does not state as done"))
            elif conditions(best["toks"]) - conditions(c["toks"]):
                problems.append(ClauseResult(Outcome.UNVERIFIED, Reason.MODALITY_MISMATCH, "the source attaches a condition (only / after / once / when ...) that the clause leaves out"))
            elif c["neg"] != best["neg"]:
                problems.append(ClauseResult(Outcome.UNVERIFIED, Reason.POLARITY_MISMATCH, f"negation differs between a clause and the source"))
            elif c["attrib"] != best["attrib"]:
                problems.append(ClauseResult(Outcome.UNVERIFIED, Reason.MODALITY_MISMATCH, f"a clause and the source differ on hearsay / a third party's recommendation"))
            elif c["hedge"] != best["hedge"] and not (open_evidence and best["hedge"] and not c["hedge"]):          # exploratory evidence is qualified by the verifier's own soft check
                problems.append(ClauseResult(Outcome.UNVERIFIED, Reason.MODALITY_MISMATCH, f"hedging / conditional wording differs between a clause and the source"))
            elif _outsiders(c["actors"]) != _outsiders(best["actors"]) or (_outsiders(c["actors"]) and _roles(c["actors"]) != _roles(best["actors"])):
                problems.append(ClauseResult(Outcome.UNVERIFIED, Reason.ACTOR_MISMATCH, f"a clause is about a different person than the source clause it rests on"))
            elif k > 0 and c["conj"] and best["conj"] and c["conj"] != best["conj"]:
                problems.append(ClauseResult(Outcome.UNVERIFIED, Reason.MODALITY_MISMATCH, f"'{c['conj']}' versus '{best['conj']}': the clauses are joined differently in the source"))
    return problems
