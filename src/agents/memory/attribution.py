"""Evidence attribution policy and certainty ceilings (Milestone M1).

ONE deterministic policy, applied wherever a memory's certainty is set or relied on: rule extraction, LLM-first extraction, the harness write validator, ingestion persistence,
consolidation (semantic eligibility) and the integrity boundary.  It never calls a model and it can only LOWER a certainty: a confident LLM answer cannot override it.

Attribution classes (who is the SOURCE of the statement, as established by the data - the cited turn's speaker - never by the model):
  client_direct             the client's own turn.
  advisor_reported_client   the advisor reports what the client said, decided or wants ("you've decided ...", "your decision to ...", "as you told me ...").
  advisor_interpretation    any other advisor statement: recommendations, analysis, the advisor's own view.
  system_inference          not taken from a turn: consolidation, orchestrator, tool or curated output.
  ambiguous                 the speaker is neither Client nor Advisor, or is not stated.

Ceilings.  Only client-attributable evidence supports `confirmed` / `correction` (the two certainties of rank 5):
  * client_direct                                                            -> no ceiling from attribution.
  * an ADVISOR statement (advisor_reported_client or advisor_interpretation) WITH client confirmation (a Client-spoken evidence row that affirms the content or states it,
    cited as evidence: the LLM-first path cites the client's agreeing reply, and `find_client_confirmation` finds the client's immediate, unhedged affirmation) -> no ceiling;
    the memory cites BOTH rows and keeps `source_role = Advisor` on the primary row.
  * an advisor statement WITHOUT client confirmation, system_inference, ambiguous -> at most `unconfirmed`.
  An advisor's own `recommendation`, `exploratory` and `hypothetical` claims are already weaker than the ceiling and are left unchanged.

Finality.  An explicit statement that the matter is NOT final ("don't treat it as final yet", "I haven't decided", "nothing has been decided", "I may change my mind", "this is only a
possibility", "I'll confirm on Wednesday") caps a would-be confirmed claim at `unconfirmed` (or `exploratory` for "still considering / only a possibility / may change my mind").
  * A negation in the CITED sentence decides.
  * A negation in ANOTHER sentence of the same turn decides only when it is anaphoric (it / this / that / the decision) or generic ("nothing has been decided"), because a negation about
    something else (the mortgage, the house) must not veto an unrelated decision.  When the scope cannot be resolved the claim is capped at `unconfirmed` and flagged `needs_review`:
    abstain, never guess.
  * A negation and an explicit decision in the same sentence is unresolved, not final.

These are deterministic MECHANISM rules over wording.  They are not a semantic evaluation: they cannot read meaning, so they are deliberately conservative, and their accuracy on real
conversations has NOT been measured.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Optional

CLIENT_DIRECT = "client_direct"
ADVISOR_REPORTED = "advisor_reported_client"
ADVISOR_INTERPRETATION = "advisor_interpretation"
SYSTEM_INFERENCE = "system_inference"
AMBIGUOUS = "ambiguous"
CLASSES = (CLIENT_DIRECT, ADVISOR_REPORTED, ADVISOR_INTERPRETATION, SYSTEM_INFERENCE, AMBIGUOUS)

STRONG = ("confirmed", "correction")
RANK = {"confirmed": 5, "correction": 5, "recommendation": 3, "exploratory": 2, "hypothetical": 2, "unconfirmed": 1, "n/a": 0}     # = config.CERTAINTY_RANK (checked by a test)

# episodic event types that record an attribution the certainty alone cannot carry (episodic_details.event_type is free text)
EVENT_REPORTED = "reported_decision"
EVENT_INTERPRETATION = "advisor_interpretation"
NON_CLIENT_EVENTS = (EVENT_REPORTED, EVENT_INTERPRETATION)


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')).strip().lower()


def _sentences(text: str) -> list[str]:
    return [p.strip() for p in re.split(r"(?<=[.!?])\s+|\n+", (text or "").replace("\r", " ")) if p and p.strip()]


# ------------------------------------------------------------------------------------------------------------------------------- attribution
_REPORTED = re.compile(
    r"\b(?:you(?:'ve| have)\s+(?:both\s+)?(?:decided|chosen|agreed|settled|told me|said|mentioned|confirmed|asked)|you\s+(?:decided|chose|agreed|told me|said|mentioned|confirmed|asked)|"
    r"your\s+(?:decision|choice|plan|preference|instruction|request)\b|as\s+you\s+(?:said|told|decided|mentioned|asked)|per\s+your\s+(?:decision|instruction|request)|"
    r"you(?:'d| would)\s+(?:like|prefer|want)|you\s+want\s+to|you(?:'re| are)\s+(?:going to|planning to|leaning)|we(?:'ve| have)\s+(?:both\s+)?(?:agreed|decided|settled)|"
    r"(?:he|she|they|the client)\s+(?:has |have )?(?:decided|chosen|agreed)|\w+ has decided|"
    r"the working (?:plan|assumption) (?:i'm|i am) recording|i'?ll record that as your|for the record,? (?:here is|here's) what we|here's where we landed|here's what i'm taking away|"
    r"my notes (?:from today )?read as follows)\b")
_AFFIRM = re.compile(r"^(?:yes|yeah|yep|yup|correct|right|exactly|agreed|absolutely|definitely|sure|ok(?:ay)?|that(?:'s| is) (?:all )?(?:right|correct|accurate|what we decided|the plan|fine|good)|"
                     r"(?:that )?sounds (?:good|right|fine)|let(?:'s| us) do (?:that|it)|go ahead|please (?:go ahead|proceed)|i agree|that works|"
                     r"(?:that )?makes sense|(?:that )?looks (?:about )?right|all correct|spot on|"
                     r"that(?:'s| is) (?:what i (?:understand|understood|remember)|the story|it)|that matches|perfect|good(?! question)|(?:that )?sounds (?:sensible|reasonable))\b")
# The natural-wording forms above were added after inspecting the policy's misses on the evaluation sets (INT-036, INT-146 paraphrased; INT-0xx scripted):
# "That makes sense to me.", "That's all accurate.", "Perfect. That matches what I remember.", "That's what I understood, yes.", "Good. That's it.".
# Hedges and negations (_CONFIRM_BLOCK, _hit) still veto every one of them.


def is_bare_affirmation(text: str) -> bool:
    """A short reply that only agrees ("Yes, let's do that.", "That's right.") and carries no content of its own: the only kind of client sentence whose content may be taken from
    an earlier turn."""
    n = _norm(text or "")
    return bool(n) and len(n.split()) <= 20 and bool(_AFFIRM.match(n)) and not _hit(n) and not _CONFIRM_BLOCK.search(n)


def classify_attribution(speaker_role: Optional[str], excerpt: str = "", *, origin: str = "conversation_llm") -> str:
    """The source class of a statement.  `speaker_role` must come from the DATA (the cited turn), never from a model."""
    if origin in ("consolidation", "orchestrator", "tool_summary", "curated_file") or speaker_role == "System":
        return SYSTEM_INFERENCE
    if speaker_role == "Client":
        return CLIENT_DIRECT
    if speaker_role == "Advisor":
        return ADVISOR_REPORTED if _REPORTED.search(_norm(excerpt)) else ADVISOR_INTERPRETATION
    return AMBIGUOUS


def client_confirms(evidence: Iterable[dict], value: Optional[str] = None) -> bool:
    """True when a CLIENT-spoken evidence row affirms the cited content (a short agreement such as 'Yes, that's right') or states the same value, and is not itself hedged or negated."""
    for e in evidence or ():
        if (e.get("speaker") or "") != "Client":
            continue
        t = _norm(e.get("excerpt") or "")
        if _hit(t) or re.search(r"\b(?:no|not|don't|won't|rather|instead)\b", t) and not _AFFIRM.match(t):
            continue
        if _AFFIRM.match(t) or (value and re.search(rf"(?<![\w.]){re.escape(_norm(value))}(?![\w.])", t)):
            return True
    return False


_CONFIRM_BLOCK = re.compile(r"\b(?:but|however|rather|instead|maybe|probably|perhaps|still|unless|though|actually|depends|i guess|i suppose|not (?:sure|certain|convinced|yet|ready)|"
                            r"don't (?:know|think)|do not (?:know|think)|won't|wouldn't)\b")


def find_client_confirmation(rows: list[dict], turn_index: int) -> Optional[dict]:
    """The client's IMMEDIATE confirmation of the cited (advisor) turn: the very next turn, spoken by the Client, a short affirmation with no hedge or negation ("That's right.",
    "Correct. Move on it.", "That sounds right to me.").  Returned as a supporting evidence row (the verbatim text of that turn), or None.  It never looks further than the next
    turn: a later "yes" may answer a different question."""
    order = [r["turn_index"] for r in rows]
    if turn_index not in order:
        return None
    k = order.index(turn_index)
    if k + 1 >= len(rows):
        return None
    nxt = rows[k + 1]
    text = (nxt.get("utterance") or "").strip()
    n = _norm(text)
    if nxt.get("speaker") != "Client" or not n or len(n.split()) > 20 or not _AFFIRM.match(n) or _hit(n) or _CONFIRM_BLOCK.search(n):
        return None
    return dict(turn_index=nxt["turn_index"], speaker="Client", excerpt=text, role="supporting")


# ------------------------------------------------------------------------------------------------------------------------------- finality
_NOT_FINAL = [
    ("unconfirmed", re.compile(r"\b(?:do not|don't|dont|never|please don't|not to)\s+(?:yet\s+)?(?:treat|consider|regard|take|record|log|count|read)\s+(?:this|it|that|them|these)?\s*(?:as\s+)?(?:being\s+)?"
                               r"(?:final|settled|decided|a decision|confirmed|binding|definite|fixed)\b")),
    ("unconfirmed", re.compile(r"\b(?:isn't|aren't|wasn't|is not|are not|was not|not)\s+(?:yet\s+|quite\s+)?(?:final|settled|decided|confirmed|definite|binding|certain)\b")),
    ("unconfirmed", re.compile(r"\b(?:haven't|have not|hasn't|has not|hadn't|had not|didn't|did not)\s+(?:yet\s+)?(?:decided|settled|chosen|committed|agreed|made (?:up )?(?:my|our|a|the|any)\s*(?:mind|decision|choice)?|made it final)\b")),
    ("unconfirmed", re.compile(r"\b(?:nothing|no (?:final )?decision|no choice)\s+(?:has been|is|was|'s been|had been|yet)?\s*(?:been\s+)?(?:decided|made|settled|final|confirmed|chosen)\b")),
    ("unconfirmed", re.compile(r"\b(?:i'll|i will|we'll|we will|let me)\s+(?:confirm|decide|finali[sz]e|let you know)\b[^.?!]*\b(?:later|tomorrow|next|on\s+\w+day|monday|tuesday|wednesday|thursday|friday|after|once|when)\b")),
    ("unconfirmed", re.compile(r"\b(?:tentative(?:ly)?|provisional(?:ly)?|subject to)\b")),
    ("exploratory", re.compile(r"\b(?:still|currently|just|only)\s+(?:considering|deciding|thinking|weighing|undecided|open|not sure|mulling|on the fence|exploring|looking at)\b")),
    ("exploratory", re.compile(r"\b(?:only|just|merely)\s+(?:a\s+|an\s+)?(?:possibility|option|idea|thought|maybe)\b|\b(?:is|it's|that's|this is)\s+(?:only\s+|just\s+)?(?:a\s+)?possib(?:le|ility)\b")),
    ("exploratory", re.compile(r"\b(?:may|might|could|can)\s+(?:still\s+|well\s+)?change\s+(?:my|our|the|your)\s+(?:mind|plan|decision|view)\b")),
]
_ANAPHORIC = re.compile(r"\b(?:it|this|that|these|those|the decision|my decision|our decision|the plan|the choice)\b")
_GENERIC = re.compile(r"\b(?:nothing|no decision|no final decision|no choice)\b|\bhaven't (?:yet )?decided\b|\bnot (?:yet )?(?:final|decided|settled|confirmed)\b")
_POSITIVE = re.compile(r"\b(?:we|you|i)(?:'ve| have)\s+(?:both\s+)?(?:decided|chosen|agreed|settled)\b|\b(?:decided|going with|let's go with|go ahead with|final decision)\b")


@dataclass(frozen=True)
class Finality:
    status: str                    # final | not_final | unresolved
    label: Optional[str] = None    # unconfirmed | exploratory: the weaker certainty to apply when not final / unresolved
    reason: str = ""


_OWN_SENTENCE_ONLY = (4, 5)      # "I'll confirm ... later/when/after" and "tentative / subject to": in ANOTHER sentence these are usually about logistics ("I'll confirm when it arrives"), so they count only in the cited sentence


def _hit(text: str, sibling: bool = False) -> Optional[tuple[str, str]]:
    t = _norm(text)
    for i, (label, rx) in enumerate(_NOT_FINAL):
        if sibling and i in _OWN_SENTENCE_ONLY:
            continue
        m = rx.search(t)
        if m and sibling and re.match(r"(?:hadn't|had not|didn't|did not)\b", m.group(0)):
            continue                                   # a past-perfect negation in ANOTHER sentence is a read-back of history ("in March you hadn't decided"), not a statement that the matter is open now
        if m:
            return label, m.group(0)
    return None


def finality(excerpt: str, turn_text: str = "") -> Finality:
    """Is the cited statement stated as final?  See the module docstring for the scoping rules."""
    own = _hit(excerpt)
    if own:
        label, phrase = own
        if _POSITIVE.search(_norm(excerpt).replace(phrase, " ")):                 # a positive decision cue OUTSIDE the negated phrase itself
            return Finality("unresolved", "unconfirmed", f"a decision and a finality negation ({phrase!r}) in the same sentence")
        return Finality("not_final", label, f"explicitly not final: {phrase!r}")
    ex = _norm(excerpt)
    for sent in _sentences(turn_text):
        ns = _norm(sent)
        if not ns or ns == ex or (len(ex) >= 12 and (ex in ns or ns in ex)):
            continue                                    # the cited sentence itself (already judged above)
        h = _hit(ns, sibling=True)
        if h and (_ANAPHORIC.search(ns) or _GENERIC.search(ns)):
            return Finality("unresolved", "unconfirmed", f"another sentence of the same turn says the matter is not final ({h[1]!r}); its scope cannot be resolved")
    return Finality("final")


# ------------------------------------------------------------------------------------------------------------------------------- the policy
@dataclass(frozen=True)
class Verdict:
    attribution: str
    certainty: str                 # never higher than the input
    notes: tuple = ()
    needs_review: bool = False
    finality: str = "final"

    @property
    def lowered(self) -> bool:
        return bool(self.notes)


def _lower(current: str, ceiling: str) -> str:
    return ceiling if RANK.get(ceiling, 0) < RANK.get(current, 0) else current


_ATTR_NOTE = {ADVISOR_REPORTED: "attribution: an advisor's account of what the client said or decided, without client-spoken confirmation, is at most unconfirmed",
              ADVISOR_INTERPRETATION: "attribution: an advisor's statement or interpretation is at most unconfirmed as a client decision",
              SYSTEM_INFERENCE: "attribution: a system inference is at most unconfirmed",
              AMBIGUOUS: "attribution: the speaker cannot be attributed to the client or the advisor"}


def assess(certainty: str, speaker_role: Optional[str], excerpt: str, turn_text: str = "", evidence: Iterable[dict] = (), origin: str = "conversation_llm",
           value: Optional[str] = None) -> Verdict:
    """The certainty a statement may carry, given WHO said it and whether it is stated as final.  Downgrade only; `evidence` are the OTHER cited rows (dicts with `speaker`, `excerpt`)."""
    ev = list(evidence or ())
    attr = classify_attribution(speaker_role, excerpt, origin=origin)
    if certainty not in STRONG:
        return Verdict(attr, certainty)
    out, notes, review = certainty, [], False
    confirmed_by_client = attr in (ADVISOR_REPORTED, ADVISOR_INTERPRETATION) and client_confirms(ev, value)
    if attr != CLIENT_DIRECT and not confirmed_by_client:
        out = _lower(out, "unconfirmed")
        notes.append(_ATTR_NOTE[attr])
        review = attr in (ADVISOR_REPORTED, AMBIGUOUS)
    fin = finality(excerpt, turn_text)
    if fin.status != "final":
        out = _lower(out, fin.label or "unconfirmed")
        notes.append("finality: " + fin.reason)
        review = review or fin.status == "unresolved"
    return Verdict(attr, out, tuple(notes), review, fin.status)


def event_type_for(attribution: str, certainty: str, default: str) -> str:
    """The episodic event type that records the attribution when the certainty alone cannot."""
    if attribution == ADVISOR_REPORTED and certainty in ("unconfirmed", "exploratory", "hypothetical", "recommendation"):
        return EVENT_REPORTED
    if attribution == ADVISOR_INTERPRETATION and default == "statement":
        return EVENT_INTERPRETATION
    return default
