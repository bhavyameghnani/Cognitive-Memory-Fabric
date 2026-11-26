"""Phase 4A (D): what was RECORDED wrongly, what the client ACTUALLY SAID, and how the advisor CORRECTED it are three different things.  Pure code: no model, no store, no retrieval.

THE PROBLEM.  The first correction display paired the correcting turn with "the original position" found by shared wording, and for Rebecca that turn was the advisor's QUESTION "my note says you had accepted
the Lakeshore offer.  Is that correct?": a question that asks about a record is not the record.  The client's answer ("No, ... I said I was inclined to accept ... I haven't signed the letter of intent") was
not shown at all, and the advisor's own acknowledgement a turn later ("my note overstated it") was listed as an unlinked correction.

WHAT THIS DOES, for every correction the timeline links (memory structure or shared wording; those links and their validation are unchanged):
  * the CORRECTION is the correcting turn itself (advisor or client), with its speaker;
  * the advisor's CHECK, when the linked earlier turn is a question or "my note says ...", is shown as the advisor's question, labelled as context and never as the record;
  * the ORIGINAL record is an earlier retrieved turn that itself states the corrected position (not a question, not a correction, not the check).  If none is retrieved the original is marked `described`
    (the correcting turn says what the earlier note said: that is the advisor's later description, shown as such, with its exact words) or `unavailable`, and `missing` says what evidence is absent.
    Nothing is reconstructed from memory, from the CRM or from the model;
  * what the CLIENT SAID is the client's own turn that answers the check, or failing that the nearest client turn of the same meeting before the correction that shares its content;
  * the ACKNOWLEDGEMENT is an advisor turn between the client's answer and the correction that already words itself as a correction ("my note overstated it").
Every part carries its turn, speaker and exact wording."""
from __future__ import annotations

import re
from typing import Optional

from agents.report.status import content_roots, evidence_of, fmt_date, sentences
from schemas.report import CorrectionPart, CorrectionRecord, StatusEvidence, TimelineClaim

_CHECK = re.compile(r"\b(?:my|our|the) (?:notes?|records?|file|email|summary|entry)\b[^.?!]{0,40}\b(?:say|says|show|shows|has|have|read|reads|state|states|record|records|recorded)\b|\bis that (?:correct|right|accurate)\b|"
                    r"\bcan you confirm\b|\bjust to confirm\b|\bi want to confirm\b|\bi have you down\b|\bam i right\b|\bdid i (?:get|have) (?:that|it) right\b", re.I)


def is_check(c: TimelineClaim) -> bool:
    """An advisor turn that ASKS the client whether a record is right ("my note says ... Is that correct?"): it quotes the record, it is not the record."""
    if c.speaker != "Advisor" or c.source != "source_turn":
        return False
    parts = sentences(c.wording)
    return bool(_CHECK.search(c.wording)) and (bool(parts) and parts[-1].rstrip().endswith("?") or bool(re.search(r"\bis that (?:correct|right|accurate)\b", c.wording, re.I)))


def _order(c: TimelineClaim) -> tuple:
    return (c.event_date or "", c.interaction_id or "", c.turn_index if c.turn_index is not None else -1)


def _is_question(c: TimelineClaim) -> bool:
    parts = sentences(c.wording)
    return bool(parts) and parts[-1].rstrip().endswith("?") and len(parts) <= 2


def _description(correcting: TimelineClaim, phrase: Optional[str]) -> Optional[str]:
    """The clause of the correcting turn that says what the earlier record said ("my earlier note that you had accepted the Lakeshore offer was wrong"), as an exact substring, or None."""
    low = " ".join((phrase or "").split()).lower()
    for s in sentences(correcting.wording):
        for part in re.split(r"(?<=[;:])\s+", s):
            seg = part.strip().rstrip(";:").strip()
            if re.search(r"\b(?:earlier|previous|prior|original|my)\b[^.;]{0,20}\b(?:note|record|email|entry|summary)\b", seg, re.I) and re.search(r"\b(?:wrong|mistaken|incorrect|overstated|too strong)\b", seg, re.I):
                if not low or any(w in seg.lower() for w in low.split()[:3]):
                    return seg
    return None


_STOPW = frozenset("the and for with that this from have has had was were are been you your they them their there what when which will would could should about into than then also not but its our".split())


def _find_original(correcting: TimelineClaim, target: TimelineClaim, claims: list[TimelineClaim], phrase: Optional[str]) -> Optional[TimelineClaim]:
    """An earlier retrieved turn that itself STATES the corrected position: it is not a question, not a correction, not the check, not hedged or a recommendation, and it contains EVERY content word of the
    position the correction names, in the same form ("accepted", not "accepting": "you're still weighing accepting the offer" is not the record that said she had accepted)."""
    need = [w for w in re.findall(r"[a-z']+", (phrase or "").lower()) if len(w) >= 4 and w not in _STOPW]
    if len(need) < 2:
        return None
    best: Optional[TimelineClaim] = None
    for x in claims:
        if x.source != "source_turn" or x is target or x is correcting or _order(x) >= _order(target):
            continue
        if _is_question(x) or x.modality in ("correction", "withdrawn", "tentative", "hypothetical", "recommendation") or is_check(x) or any(r["type"] in ("corrects", "corrected_by") for r in x.relations):
            continue
        toks = set(re.findall(r"[a-z']+", x.wording.lower().replace("’", "'")))
        toks |= {t[:-2] for t in toks if t.endswith("'s")}
        if all(w in toks for w in need) and (best is None or _order(x) > _order(best)):
            best = x
    return best


def correction_records(claims: list[TimelineClaim]) -> list[CorrectionRecord]:
    """One record per linked correction, in date order (see the module doc)."""
    by_id = {c.claim_id: c for c in claims}
    out: list[CorrectionRecord] = []
    for c in sorted((x for x in claims if x.source == "source_turn"), key=_order):
        for rel in c.relations:
            if rel["type"] != "corrects" or rel["claim_id"] not in by_id:
                continue
            t = by_id[rel["claim_id"]]
            if t.source != "source_turn":
                continue
            phrase = rel.get("phrase")
            same = [x for x in claims if x.source == "source_turn" and x.interaction_id == c.interaction_id and x.turn_index is not None]
            check_part = None
            if is_check(t):
                check_part = CorrectionPart(role="check", status="retrieved", label="The advisor's question to the client about the advisor's own note (context: a question about a record is not the record)", evidence=evidence_of(t, "check"))
                found = _find_original(c, t, claims, phrase)
            else:
                found = t
            missing: Optional[str] = None
            if found is not None:
                kind = "the client's own earlier words" if found.statement_kind == "client_statement" else "an earlier record by the advisor"
                original = CorrectionPart(role="original", status="retrieved", label=f"What was originally recorded ({kind})", evidence=evidence_of(found, "original"))
            else:
                desc = _description(c, phrase)
                missing = ("The earlier record itself (its own wording, its author and its date) was not among the retrieved evidence, so it is not reconstructed here; what it said is known only from the advisor's later "
                           "description in the correcting turn." if desc else "The earlier record itself was not among the retrieved evidence, and no turn says what it contained, so the original recorded position is unavailable.")
                if desc:
                    original = CorrectionPart(role="original", status="described", label="The earlier record was not retrieved; the advisor's later description of it",
                                              evidence=evidence_of(c, "original", excerpt=desc), note="This is the advisor's own account of what the earlier note said, not the note.")
                else:
                    original = CorrectionPart(role="original", status="unavailable", label="The earlier record was not retrieved", note=missing)
            # what the client actually said: the client's own turn that answers the check, else the nearest earlier client turn of the same meeting that shares the correction's content
            client_part = None
            lo = t.turn_index if check_part is not None else None
            mine_roots = content_roots(c.wording)
            cands = [x for x in same if x.statement_kind == "client_statement" and x.turn_index < c.turn_index and (lo is None or x.turn_index > lo)]
            pick = None
            if check_part is not None:
                pick = next((x for x in sorted(cands, key=lambda y: y.turn_index) if x.turn_index == t.turn_index + 1), None)
            if pick is None:
                shared = [(len(content_roots(x.wording) & mine_roots), x) for x in cands]
                shared = [s for s in shared if s[0] >= 2]
                pick = max(shared, key=lambda s: (s[0], s[1].turn_index))[1] if shared else None
            if pick is not None:
                client_part = CorrectionPart(role="client_said", status="retrieved", label="What the client actually said", evidence=evidence_of(pick, "client_said"))
            ack = next((x for x in sorted(same, key=lambda y: y.turn_index) if x.speaker == "Advisor" and x is not c and x is not t and x.turn_index < c.turn_index
                        and (pick is None or x.turn_index > pick.turn_index) and (x.modality == "correction" or "correction" in x.cues)), None)
            ack_part = CorrectionPart(role="acknowledgement", status="retrieved", label="The advisor's acknowledgement in the same meeting", evidence=evidence_of(ack, "acknowledgement")) if ack is not None else None
            out.append(CorrectionRecord(date=c.event_date, basis=rel.get("basis") or "memory", phrase=phrase, original=original, client_said=client_part, check=check_part, acknowledgement=ack_part,
                                        correction=CorrectionPart(role="correction", status="retrieved", label=f"The correction ({fmt_date(c.event_date)})", evidence=evidence_of(c, "correction")), missing=missing))
    return out
