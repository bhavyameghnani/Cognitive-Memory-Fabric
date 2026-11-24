"""B3: the chronology as a readable, evidence-grounded account.  Pure code (data in, data out): no model, no store, no retrieval.  It RENDERS B2B's `TimelineClaim`s; it never classifies, never
changes a status, never upgrades a certainty and never adds a fact.

WHAT "COHERENT" MEANS HERE, AND WHAT IT DOES NOT.  The account is told meeting by meeting, in date order, and inside a meeting turn by turn.  The connectives are FACTS of the record, not
interpretation: "later" (a later date), "on the same day" (the same date), "in the next turn" (the very next turn index, spoken by someone else), "then, in turn N" (a later turn of the same
meeting).  How earlier and later statements relate is told only where B2B recorded a relation (corrects / corrected_by / contradicts).  WHY a position changed is told only in the speakers' OWN
quoted words: no sentence supplies a reason, a cause or a motive, and none contains "because", "therefore", "as a result" or the like (a test checks this).

EVERY SENTENCE CARRIES ITS PROVENANCE: meeting id, date, turn, who spoke and in what capacity (the client's own words / the advisor's account of the client / the advisor's interpretation), the
ORIGINAL wording in quotation marks and the evidence handle ([E4]).  A statement about what was said rests on a retrieved source turn; a stored memory that was never retrieved as a turn is not narrated
(B2B puts it in the uncertainties).

WHY THE FINDINGS LOOK THE WAY THEY DO.  The report's verifier cannot check a sentence that has a date and no figure, and would mark the whole paragraph "qualified".  So every dated sentence also carries
the quotation (and its figures), relations are written without dates (they point at "INT-049 turn 4"), and the standing of the record (what is held, corrected, disputed, unresolved, incomplete) is stated
in the SUMMARY and the uncertainties, not in findings of its own.
"""
from __future__ import annotations

import re
from typing import Optional

from agents.report.timeline import PHRASE, TAG, _norm_val, _q, _where, _who, significant
from schemas.report import EvidenceItem, Finding, TimelineClaim, is_source_turn

MAX_PER_PARAGRAPH = 6
STORY = re.compile(r"^(?:In meeting |Later, in meeting |On the same day, in meeting |Continuing in meeting )")
CAUSAL = re.compile(r"\b(?:because|therefore|hence|thus|consequently|as a result|due to|owing to|led to|so that|in order to|which is why)\b", re.I)


def is_narrative(f: Finding) -> bool:
    """Is this finding one of the code-written chronology paragraphs?"""
    return f.origin == "rendered" and bool(STORY.match(f.text))


def _verb(c: TimelineClaim) -> str:
    if c.statement_kind == "advisor_reported":                                  # the parenthesis already says "the advisor's account, not a client statement": the verb need not repeat it
        return "recorded"
    verb = PHRASE.get((c.statement_kind, c.modality)) or ("noted" if c.statement_kind == "advisor_interpretation" else "said")
    if "correction" in c.cues and c.modality != "withdrawn":
        if c.statement_kind == "client_statement":
            verb = "said, correcting the record" + (" tentatively," if c.modality == "tentative" else ",")
        elif c.statement_kind == "advisor_interpretation":
            verb = "noted, acknowledging a correction,"
    return verb


def _named(t: TimelineClaim) -> str:
    """How a RELATED claim is named: by meeting and turn when it is a retrieved source turn (the paragraph then cites that turn), otherwise without any meeting or turn number - a stored record whose
    turn was not retrieved is not cited, and a figure naming it would be one the cited evidence does not contain."""
    return _where(t) if t.source == "source_turn" else "a stored record whose source turn was not retrieved"


def _relations(c: TimelineClaim, by_id: dict) -> list[str]:
    """How this claim relates to others, in words, pointing at the other claim's meeting and turn (no dates: see the module docstring).  A decision corrected more than once says so."""
    out: list[str] = []
    later = [by_id[r["claim_id"]] for r in c.relations if r["type"] == "corrected_by" and r["claim_id"] in by_id]
    for i, t in enumerate(later):
        out.append(f"later corrected in {_named(t)}" if i == 0 else f"and then corrected again in {_named(t)}")
    for r in c.relations:
        t = by_id.get(r["claim_id"])
        if t is None:
            continue
        if r["type"] == "corrects":
            out.append(f"corrects the record in {_named(t)}, which stays unchanged")
        elif r["type"] == "contradicts":
            out.append(f"conflicts with the record in {_named(t)}, unresolved")
    return out


def _clause(c: TimelineClaim, by_id: dict) -> str:
    inside = [TAG[c.statement_kind]] + _relations(c, by_id)
    return f"{_who(c)} {_verb(c)} ({'; '.join(inside)}): \"{_q(c)}\" [{c.evidence_refs[0]}]."


def _after(c: TimelineClaim) -> str:
    """What the record itself says about the standing of a statement, immediately after it (no dates, no figures)."""
    s = ""
    if c.client_confirmation:
        s += f" The client's own reply ({c.client_confirmation}) was retrieved."
    if c.confirmed_client_decision:
        mem = [r for r in c.evidence_refs[1:]]
        cite = f" [{mem[-1]}]" if mem else ""
        if c.temporal_status in ("corrected", "superseded", "disputed"):         # the sentence follows B2B's status: a confirmed decision that was corrected, superseded or disputed is not the position
            s += f" The memory layer recorded it as a verified, confirmed client decision{cite}, but it is {c.temporal_status} and is not held as the current position."
        else:
            s += f" The memory layer holds it as a verified, confirmed client decision{cite}."
    elif c.temporal_status == "unresolved":
        s += " Whether this is the current position is unresolved: no verified, confirmed client decision supports it."
    elif c.temporal_status == "superseded":
        s += " The memory layer marks this record as superseded; it stays in the record."
    elif c.temporal_status == "disputed" and not any(r["type"] == "contradicts" for r in c.relations):
        s += " The memory layer marks this record as disputed."
    return s


def narrate(claims: list[TimelineClaim], evidence: list[EvidenceItem]) -> list[Finding]:
    """The chronology as paragraphs, one per meeting (a long meeting continues in a second paragraph), oldest first.  Each paragraph cites the turns it tells and, for a confirmed decision, the
    verified memory it rests on; it never cites an unverified memory."""
    by_id = {c.claim_id: c for c in claims}
    ref_of = {e.ref: e for e in evidence}
    told = [c for c in claims if significant(c)]
    meetings: list[list[TimelineClaim]] = []
    for c in told:
        if meetings and (meetings[-1][0].interaction_id, meetings[-1][0].event_date) == (c.interaction_id, c.event_date):
            meetings[-1].append(c)
        else:
            meetings.append([c])
    out: list[Finding] = []
    prev_date: Optional[str] = None
    for mi, grp in enumerate(meetings):
        iid, date = grp[0].interaction_id, grp[0].event_date or "an unrecorded date"
        for ci in range(0, len(grp), MAX_PER_PARAGRAPH):
            chunk = grp[ci:ci + MAX_PER_PARAGRAPH]
            sents: list[str] = []
            refs: list[str] = []
            for j, c in enumerate(chunk):
                if j == 0:
                    if ci > 0:
                        lead = f"Continuing in meeting {iid} ({date}), in turn {c.turn_index},"
                    elif mi == 0:
                        lead = f"In meeting {iid} ({date}), in turn {c.turn_index},"
                    elif prev_date == c.event_date:
                        lead = f"On the same day, in meeting {iid} ({date}), in turn {c.turn_index},"
                    else:
                        lead = f"Later, in meeting {iid} ({date}), in turn {c.turn_index},"
                else:
                    p = chunk[j - 1]
                    adjacent = c.turn_index is not None and p.turn_index is not None and c.turn_index == p.turn_index + 1 and c.speaker != p.speaker
                    lead = f"In the next turn (turn {c.turn_index})," if adjacent else f"Then, in turn {c.turn_index},"
                sents.append(f"{lead} {_clause(c, by_id)}{_after(c)}")
                for r in [c.evidence_refs[0]] + [x for x in c.evidence_refs[1:] if x in ref_of and is_source_turn(ref_of[x])]:
                    if r not in refs:
                        refs.append(r)
                if c.confirmed_client_decision:
                    refs += [r for r in c.evidence_refs if r in ref_of and ref_of[r].evidence_class == "verified" and not is_source_turn(ref_of[r]) and r not in refs]
                for rel in c.relations:
                    t = by_id.get(rel["claim_id"])
                    if t is not None and t.source == "source_turn" and t.evidence_refs[0] not in refs:
                        refs.append(t.evidence_refs[0])
            out.append(Finding(text=" ".join(sents), refs=refs, origin="rendered"))
        prev_date = grp[0].event_date
    return out


# ------------------------------------------------------------------------------------------------------------------------------------ the summary
def narrative_summary(claims: list[TimelineClaim], incomplete: bool = False) -> str:
    """A neutral, code-written description of the account, built only from B2B's classifications and statuses.  No counts and no verdict words ('decided', 'agreed', 'confirmed', 'current',
    'verified', 'established', 'final'): the report's verifier treats those in a summary that rests on source turns as claims it must be able to prove."""
    told = [c for c in claims if significant(c) and c.event_date]
    if not told:
        return ""
    kinds = [lab for key, lab in (("client_statement", "the client's own words"), ("advisor_reported", "the advisor's account of the client"), ("advisor_interpretation", "advisor interpretation"))
             if key in {c.statement_kind for c in told}]
    s = f"The retrieved record contains dated statements from {told[0].event_date} to {told[-1].event_date}"
    if kinds:
        s += ", classified as " + (", ".join(kinds[:-1]) + " and " + kinds[-1] if len(kinds) > 1 else kinds[0])
    s += ". The account follows the meetings in date order and keeps each speaker's own wording."
    if any(c.statement_kind == "client_statement" and c.modality in ("tentative", "hypothetical") for c in told):
        s += " Some of the client's statements were tentative."
    if any(c.statement_kind == "advisor_reported" and not c.client_confirmation and not c.confirmed_client_decision for c in told):
        s += " Some statements are the advisor's account of the client and no confirming reply from the client was retrieved."
    if any(sum(r["type"] == "corrected_by" for r in c.relations) > 1 for c in claims):
        s += " At least one record was corrected more than once."
    if any(c.temporal_status == "corrected" for c in claims):
        s += " At least one earlier record was later corrected; the earlier wording is kept."
    if any(c.modality == "withdrawn" for c in claims):
        s += " At least one statement was taken back by its speaker; it stays in the record."
    if any(c.temporal_status in ("current", "unresolved") for c in claims):
        s += " The key findings and the uncertainties say what the memory layer holds and what remains open."
    if any(c.temporal_status == "disputed" for c in claims):
        s += " Some records conflict and the conflict is not resolved."
    if incomplete:
        s += " The account may be incomplete: some relevant meetings or turns were not retrieved or not written out, and the uncertainties list them."
    return s


_PAST = re.compile(r"\b(?:was|were|until|previously|formerly|former|earlier|originally|replaced|superseded|superseding|corrected|corrects|exploring|explored|considered|considering|tentative|tentatively|"
                   r"no longer|instead of|not|changed|moved|shifted|revised|updated|from)\b", re.I)
_ASSERT = re.compile(r"\b(?:is|are|has been|have been|now|currently|set at|settled|decided|confirmed|clarified|changed to|final|finalised|finalized)\b", re.I)


def summary_conflict(summary: str, claims: list[TimelineClaim], held=frozenset()) -> Optional[str]:
    """Why a (model-written) summary may not stand beside this timeline, or None.  Two cases, both about VALUES (the only thing a summary can get wrong that the timeline can check):
      * it names a value that nothing holds as current, and says it in no past or hedged way;
      * nothing is current at all, yet it states a value as the position.
    What counts as "held as current": a timeline claim that B2B marked `current`, and `held` - the values of VERIFIED, ACTIVE semantic memories (the report's own current knowledge, which is
    not a conversation turn and so is not on the timeline).  A summary that states a held value, or that speaks of earlier values in the past tense, is left alone."""
    vals = {_norm_val(c.value) for c in claims if c.value}
    cur = {_norm_val(c.value) for c in claims if c.temporal_status == "current" and c.value} | {_norm_val(v) for v in held}
    named = {v for v in vals if v and re.search(rf"(?<![\d.]){re.escape(v)}(?![\d])", summary)}
    if not named:
        return None
    if named - cur and not _PAST.search(summary):
        return f"the summary states {sorted(named - cur)[0]} as the position, but no verified, confirmed claim of the timeline holds it as current"
    if not cur and _ASSERT.search(summary) and not _PAST.search(summary):
        return "the summary states a value as the position, but the timeline holds no verified, confirmed current claim"
    return None
