"""B2B: evidence classification and chronological claim construction.  Pure code (data in, data out): no model, no store access, no retrieval.

WHAT IT DOES.  The report's evidence list holds two kinds of conversation evidence: retrieved SOURCE TURNS (real text, with speaker and date) and stored MEMORY episodes (a model-extracted
paraphrase with a certainty, a verification verdict and, sometimes, a correction link).  This module turns them into dated, classified `TimelineClaim`s and states, in readable sentences,
who said what and when, what the speaker was and was not committing to, what later corrected it, and what the evidence does not establish.

THE RULES THAT KEEP IT HONEST (each is a test in tests/test_report_timeline.py):
  * ATTRIBUTION is the speaker of the actual turn, classified by M1's own function (agents.memory.attribution.classify_attribution).  An advisor turn is never a client statement.
  * A statement about WHAT WAS SAID rests on a retrieved source turn, never on a stored memory.  A memory whose turn was not retrieved is reported as unverified in the uncertainties; it is
    never rendered as a finding (the report's authority split would withhold it anyway, and it must not be able to stand in for the client's own words).
  * MODALITY comes from wording cues (M1's rules module: exploratory, hypothetical, unconfirmed, decision, correction, recommendation, "not final"); hedging beats decisive wording, exactly as
    in M1.  Wording alone NEVER makes a confirmed client decision: that needs a stored memory that is confirmed, verified AND client-attributable (M1 capped it at extraction), or an advisor
    statement that the client's own immediate reply confirmed.
  * VALUES come only from memory records.  Nothing is guessed from wording, so a claim without a memory has no value and takes part in no value-based relation.
  * RELATIONS (corrects / corrected_by / contradicts) are built only from memory structure (the value a correction names, a disputed status, a shared open conflict).  A correction never
    deletes or edits the earlier claim: it stays in the timeline with its original wording and is marked `corrected`.
  * "CURRENT" is given to at most one claim per slot, only when that claim is a verified, confirmed, client-attributable decision that nothing corrects, supersedes or disputes.  Otherwise the
    latest decision-like claim is `unresolved`, and the report says so.
"""
from __future__ import annotations

import re
from typing import Optional

from agents.memory import attribution, rules, support
from agents.report.status import link_decisions
from schemas.report import EvidenceItem, Finding, TimelineClaim, is_source_turn

KIND = {attribution.CLIENT_DIRECT: "client_statement", attribution.ADVISOR_REPORTED: "advisor_reported", attribution.ADVISOR_INTERPRETATION: "advisor_interpretation"}
DECISIVE = ("decision", "correction")
TENTATIVE = ("tentative", "hypothetical")
MEMORY_MODALITY = {"confirmed": "decision", "correction": "correction", "exploratory": "tentative", "hypothetical": "hypothetical", "unconfirmed": "tentative", "recommendation": "recommendation"}
QUOTE_CHARS = 220


# ------------------------------------------------------------------------------------------------------------------------------------ classification
# correction wording beyond M1's lexicon (which is tuned to the extractor's own phrasing); module-local, M1's rules are not changed
_CORRECTION_MORE = re.compile(r"\bcorrection to (?:my|the|an|our)\b|\bcorrect(?:ing|ed)? (?:my|the|an|our) (?:earlier|previous|prior|own)\b|\b(?:earlier|previous|prior) (?:note|statement|record)\b[^.]{0,40}\b(?:wrong|mistaken|incorrect|too strong|overstated)\b|"
                              r"\bmy note overstated\b|\bi (?:misstated|was mistaken)\b|\bstrike that\b|\bscratch that\b", re.I)


def text_cues(text: str, speaker: Optional[str]) -> list[str]:
    """The wording cues present in a statement (M1's own detectors), in a fixed order."""
    cues = []
    if rules.has_exploratory(text):
        cues.append("exploratory")
    if rules.has_hypothetical(text):
        cues.append("hypothetical")
    if rules.has_unconfirmed(text):
        cues.append("unconfirmed")
    if attribution.finality(text).status != "final":
        cues.append("not_final")
    if rules.has_correction(text) or _CORRECTION_MORE.search(text):
        cues.append("correction")
    if support.retracted(text):                              # M1's matcher: "scratch that", "never mind", "I misspoke", "disregard" ... the speaker takes a statement back
        cues.append("withdrawn")
    if rules.has_decision(text):
        cues.append("decision")
    if speaker == "Advisor" and rules.RECOMMENDATION.search(rules.norm(text)):
        cues.append("recommendation")
    return cues


def modality_from_cues(cues: list[str]) -> str:
    """A withdrawn statement is not a decision; hedging beats decisive wording (a hedged 'decision' is not one); an explicit correction cue beats a bare decision; a recommendation is the
    advisor's, not a decision."""
    if "withdrawn" in cues:
        return "withdrawn"
    if "recommendation" in cues and not any(c in cues for c in ("exploratory", "hypothetical", "not_final")):
        return "recommendation"
    if "hypothetical" in cues and "exploratory" not in cues and "not_final" not in cues:
        return "hypothetical"
    if any(c in cues for c in ("exploratory", "unconfirmed", "not_final", "hypothetical")):
        return "tentative"
    if "correction" in cues:
        return "correction"
    if "decision" in cues:
        return "decision"
    return "statement"


def _order(c: TimelineClaim) -> tuple:
    return (c.event_date or "", c.interaction_id or "", c.turn_index if c.turn_index is not None else -1, c.claim_id)


def _slot(c: TimelineClaim) -> Optional[tuple]:
    return (c.topic, c.subject or c.topic) if c.topic else None


def _norm_val(v: Optional[str]) -> str:
    return " ".join((v or "").lower().split())


# ------------------------------------------------------------------------------------------------------------------------------------ construction
def topic_stems(topics) -> list[str]:
    """Word stems of the plan's memory topics ("retirement_age" -> ["retir"]), to recognise a meeting that is about them.  Short words are ignored."""
    out: list[str] = []
    for t in topics or []:
        for w in str(t).lower().split("_"):
            if len(w) >= 4:
                s = w[:5] if len(w) >= 7 else w
                if s not in out:
                    out.append(s)
    return out


def build_timeline(evidence: list[EvidenceItem], topics=None) -> list[TimelineClaim]:
    """Dated, classified claims from the report's evidence, oldest first.  Deterministic.  `topics` (the plan's memory topics) decide which meetings are ABOUT the request: a turn of any
    other meeting stays in the timeline (nothing is dropped) but is marked `relevant=False` and is not narrated."""
    turns = [e for e in evidence if is_source_turn(e)]
    memories = [e for e in evidence if e.memory_id and e.memory_type == "episodic" and not is_source_turn(e)]
    by_key = {(e.interaction_id, e.turn_index): e for e in turns}
    rows: dict[str, list[dict]] = {}
    for e in turns:
        rows.setdefault(e.interaction_id or "", []).append(dict(turn_index=e.turn_index, speaker=e.speaker, utterance=e.excerpt or ""))
    for v in rows.values():
        v.sort(key=lambda r: r["turn_index"])
    linked: dict[tuple, list[EvidenceItem]] = {}
    for m in memories:
        linked.setdefault((m.interaction_id, m.turn_index), []).append(m)

    claims: list[TimelineClaim] = []
    for e in turns:
        text = e.excerpt or ""
        attr = attribution.classify_attribution(e.speaker, text)
        cues = text_cues(text, e.speaker)
        mems = sorted(linked.get((e.interaction_id, e.turn_index), []), key=lambda m: m.memory_id or "")
        best = next((m for m in mems if m.certainty in DECISIVE), mems[0] if mems else None)
        c = TimelineClaim(claim_id="", source="source_turn", wording=text, speaker=e.speaker, speaker_role=e.speaker, attribution=attr, statement_kind=KIND.get(attr, "unattributed"),
                          modality=modality_from_cues(cues), cues=cues, interaction_id=e.interaction_id, turn_index=e.turn_index, event_date=e.event_date, evidence_refs=[e.ref],
                          memory_refs=[m.memory_id for m in mems])
        if best is not None:
            c.topic, c.subject, c.value, c.value_source = best.topic, best.subject or best.topic, best.value, ("memory" if best.value else None)
            c.verification, c.memory_certainty, c.evidence_class = best.verification or "unverified", best.certainty, best.evidence_class
            c.evidence_refs += [m.ref for m in mems if m.ref not in c.evidence_refs]
            if best.status in ("superseded", "expired"):
                c.temporal_status = "superseded"
                if best.status == "expired":
                    c.notes.append("the stored memory has expired and is no longer in force")
            if best.status == "disputed" or best.evidence_class in ("contradicted", "open_conflict"):
                c.temporal_status = "disputed"
            # a CONFIRMED CLIENT DECISION needs the memory layer's verdict AND client-attributable evidence; wording alone never makes one
            if (best.certainty in attribution.STRONG and best.verification == "verified" and best.evidence_class == "verified"
                    and attr in (attribution.CLIENT_DIRECT, attribution.ADVISOR_REPORTED)):
                reported_ok = attr == attribution.CLIENT_DIRECT or attribution.find_client_confirmation(rows.get(e.interaction_id or "", []), e.turn_index) is not None
                c.confirmed_client_decision = reported_ok
        if attr == attribution.ADVISOR_REPORTED or (e.speaker == "Advisor" and c.modality in DECISIVE):
            conf = attribution.find_client_confirmation(rows.get(e.interaction_id or "", []), e.turn_index)
            if conf is not None:
                c.client_confirmation = f"{e.interaction_id}#{conf['turn_index']}"
                other = by_key.get((e.interaction_id, conf["turn_index"]))
                if other is not None and other.ref not in c.evidence_refs:
                    c.evidence_refs.append(other.ref)
            else:
                c.notes.append("no client reply confirming this was retrieved: it is the advisor's account, not the client's own statement")
        if mems and not any(m.verification == "verified" for m in mems):
            c.notes.append("the stored memory of this turn is unverified: the wording is real, its interpretation is not established")
        claims.append(c)

    for m in memories:                                       # memory-only claims: their turn was not retrieved, so what was said cannot be stated; they are kept for the uncertainties
        if (m.interaction_id, m.turn_index) in by_key:
            continue
        attr = attribution.classify_attribution(m.source_role, m.excerpt or "")
        cues = text_cues(m.excerpt or "", m.source_role)
        c = TimelineClaim(claim_id="", source="memory", topic=m.topic, subject=m.subject or m.topic, value=m.value, value_source="memory" if m.value else None, wording=m.excerpt or "",
                          speaker=m.source_role, speaker_role=m.source_role, attribution=attr, statement_kind=KIND.get(attr, "unattributed"),
                          modality=MEMORY_MODALITY.get(m.certainty or "", "statement"), cues=cues, verification=m.verification or "unverified", memory_certainty=m.certainty,
                          evidence_class=m.evidence_class, interaction_id=m.interaction_id, turn_index=m.turn_index, event_date=m.event_date, evidence_refs=[m.ref], memory_refs=[m.memory_id])
        c.confirmed_client_decision = bool(m.certainty in attribution.STRONG and m.verification == "verified" and m.evidence_class == "verified" and attr == attribution.CLIENT_DIRECT)
        if m.status in ("superseded", "expired"):
            c.temporal_status = "superseded"
            if m.status == "expired":
                c.notes.append("the stored memory has expired and is no longer in force")
        if m.status == "disputed" or m.evidence_class in ("contradicted", "open_conflict"):
            c.temporal_status = "disputed"
        if m.evidence_class in ("unverified", "contradicted", "open_conflict") or m.verification != "verified":
            c.notes.append("the stored memory is not verified and its source turn was not retrieved")
        claims.append(c)

    stems = topic_stems(topics)
    if stems:
        about = {e.interaction_id for e in turns if any(s in (e.excerpt or "").lower() for s in stems)} | {m.interaction_id for m in memories if m.topic in set(topics or [])}
        for c in claims:
            c.relevant = c.interaction_id in about
            c.mentions_topic = any(s in c.wording.lower() for s in stems)
    claims.sort(key=_order)
    for i, c in enumerate(claims, 1):
        c.claim_id = f"C{i}"
    link_decisions(claims)                                   # Phase 4A: a decision the advisor restated and the client explicitly confirmed (adjacent retrieved turns of one meeting)
    _relate(claims, memories)
    link_wording_corrections(claims)
    return claims


def _relate(claims: list[TimelineClaim], memories: list[EvidenceItem]) -> None:
    """Corrections, supersession, disputes and the single current claim of each slot.  Only memory structure creates a relation."""
    by_mem = {m.memory_id: m for m in memories}
    slots: dict[tuple, list[TimelineClaim]] = {}
    for c in claims:
        if _slot(c):
            slots.setdefault(_slot(c), []).append(c)
    for slot, items in slots.items():
        for i, c in enumerate(items):
            mem = [by_mem[x] for x in c.memory_refs if x in by_mem]
            names = [m.corrects_value for m in mem if m.corrects_value]
            is_correction = c.memory_certainty == "correction" or bool(names)
            if not is_correction:
                continue
            before = [x for x in items[:i] if (x.interaction_id, x.turn_index) != (c.interaction_id, c.turn_index)]
            if names:
                hits = [x for x in before if _norm_val(x.value) == _norm_val(names[0])]
            else:                                            # a correction that names no value replaces the latest earlier decision-like claim with a different value
                hits = [x for x in before if x.modality in DECISIVE + ("statement",) and x.statement_kind in ("client_statement", "advisor_reported") and _norm_val(x.value) != _norm_val(c.value)]
            if hits:
                target = hits[-1]
                c.relations.append({"type": "corrects", "claim_id": target.claim_id})
                target.relations.append({"type": "corrected_by", "claim_id": c.claim_id})
                if target.temporal_status not in ("superseded", "disputed"):
                    target.temporal_status = "corrected"
            else:
                c.notes.append("this is recorded as a correction, but no earlier claim of this slot with the value it corrects was retrieved")
        shared: dict[str, list[TimelineClaim]] = {}
        for c in items:                                      # claims that share an open conflict contradict each other (the memory layer's own finding)
            for cid in {cid for x in c.memory_refs if x in by_mem for cid in (by_mem[x].conflict_ids or [])}:
                shared.setdefault(cid, []).append(c)
        for group in shared.values():
            for a in group:
                for b in group:
                    if a is not b and {"type": "contradicts", "claim_id": b.claim_id} not in a.relations:
                        a.relations.append({"type": "contradicts", "claim_id": b.claim_id})
                        a.temporal_status = "disputed"
        same_day: dict[tuple, list[TimelineClaim]] = {}
        for c in items:                                      # two decisive claims of one slot on one day with different values, neither corrected, are an unreconciled contradiction
            if c.modality in DECISIVE and c.temporal_status in ("historical", "unresolved") and c.value and c.event_date:
                same_day.setdefault(c.event_date, []).append(c)
        for group in same_day.values():
            if len({_norm_val(x.value) for x in group}) > 1:
                for a in group:
                    for b in group:
                        if a is not b and _norm_val(a.value) != _norm_val(b.value):
                            a.relations.append({"type": "contradicts", "claim_id": b.claim_id})
                    a.temporal_status = "disputed"
        live = [c for c in items if c.modality in DECISIVE and c.statement_kind in ("client_statement", "advisor_reported") and c.temporal_status not in ("corrected", "superseded", "disputed")]
        if live:
            last = live[-1]
            if last.confirmed_client_decision or (last.decision_link or {}).get("role") in ("decision", "restatement"):
                last.temporal_status = "current"             # a verified confirmed memory, OR the client's explicit confirmation of the advisor's restatement in the retrieved turns (status.link_decisions)
            else:
                last.temporal_status = "unresolved"
                last.notes.append("the latest decision-like claim is not a verified, confirmed client decision, so no current position is established")


_LINK_STOP = frozenset("a an the and or but of to in on at for with by is are was were be been it its that this i you your my me we our he she they them his her their do did does not no have has "
                       "had will would can could should may might as if so than then there here from up out about into over just also very".split())
MIN_LINK_RUN = 5                  # a correction is linked to an earlier claim by wording only when they share this many consecutive words ...
MIN_LINK_CONTENT = 3              # ... of which at least this many are not function words


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", (text or "").lower())


def _shared_run(a: list[str], b: list[str]) -> list[str]:
    import difflib
    m = difflib.SequenceMatcher(None, a, b, autojunk=False).find_longest_match(0, len(a), 0, len(b))
    return a[m.a:m.a + m.size]


def link_wording_corrections(claims: list[TimelineClaim]) -> None:
    """A correction the RECORD states in its own words ("my earlier note that you had accepted the Lakeshore offer was wrong") names the position it corrects.  Memory structure links most
    corrections (`_relate`); when it did not, the correction is linked to an earlier retrieved turn by SHARED WORDING, under strict conditions, and the link says so:
      - the correcting claim is a retrieved turn that the wording itself marks as a correction, and nothing has linked it to an earlier claim yet;
      - exactly one earlier retrieved turn shares the longest run of consecutive words with it (at least MIN_LINK_RUN words, MIN_LINK_CONTENT of them not function words); a tie links nothing;
      - the relation carries basis "shared_wording" and the shared phrase, so no reader can mistake it for a memory-derived link.
    The earlier claim keeps its wording and is marked corrected; nothing is edited or deleted."""
    order = {id(c): i for i, c in enumerate(claims)}
    for c in claims:
        if c.source != "source_turn" or not (c.modality == "correction" or "correction" in c.cues) or any(r["type"] == "corrects" for r in c.relations):
            continue
        mine = _tokens(c.wording)
        best: list[tuple[int, TimelineClaim, list[str]]] = []
        for x in claims:
            if x is c or x.source != "source_turn" or order[id(x)] >= order[id(c)] or (x.interaction_id, x.turn_index) == (c.interaction_id, c.turn_index):
                continue
            run = _shared_run(mine, _tokens(x.wording))
            if len(run) >= MIN_LINK_RUN and sum(1 for w in run if w not in _LINK_STOP) >= MIN_LINK_CONTENT:
                best.append((len(run), x, run))
        if not best:
            continue
        top = max(n for n, _, _ in best)
        winners = [(x, run) for n, x, run in best if n == top]
        if len(winners) != 1:
            c.notes.append("this is worded as a correction, but more than one earlier turn matches it equally well, so no earlier claim is named as the one corrected")
            continue
        target, run = winners[0]
        phrase = " ".join(run)
        c.relations.append({"type": "corrects", "claim_id": target.claim_id, "basis": "shared_wording", "phrase": phrase})
        target.relations.append({"type": "corrected_by", "claim_id": c.claim_id, "basis": "shared_wording", "phrase": phrase})
        c.notes.append(f"linked to {target.claim_id} by shared wording (\"{phrase}\"), not by a stored memory")
        if target.temporal_status not in ("superseded", "disputed"):
            target.temporal_status = "corrected"


# ------------------------------------------------------------------------------------------------------------------------------------ rendering
WHO = {"Client": "the client", "Advisor": "the advisor"}
PHRASE = {("client_statement", "tentative"): "said, tentatively,", ("client_statement", "hypothetical"): "said, hypothetically,", ("client_statement", "decision"): "said, in decisive wording,",
          ("client_statement", "correction"): "said, correcting the record,", ("client_statement", "statement"): "said", ("client_statement", "recommendation"): "said",
          ("client_statement", "withdrawn"): "said, withdrawing an earlier statement,", ("advisor_reported", "withdrawn"): "recorded, as the advisor's account of the client,",
          ("advisor_reported", "tentative"): "recorded, as the advisor's account of the client,", ("advisor_reported", "decision"): "recorded, as the advisor's account of the client,",
          ("advisor_reported", "correction"): "recorded, as the advisor's account of the client,", ("advisor_reported", "hypothetical"): "recorded, as the advisor's account of the client,",
          ("advisor_reported", "statement"): "recorded, as the advisor's account of the client,", ("advisor_reported", "recommendation"): "recorded, as the advisor's account of the client,"}
TAG = {"client_statement": "the client's own words", "advisor_reported": "the advisor's account, not a client statement", "advisor_interpretation": "the advisor's interpretation or recommendation",
       "unattributed": "speaker not attributable"}


def _who(c: TimelineClaim) -> str:
    return WHO.get(c.speaker or "", f"the speaker ({c.speaker or 'unknown'})")


def _q(c: TimelineClaim) -> str:
    t = " ".join(c.wording.replace("“", '"').replace("”", '"').split())
    return t if len(t) <= QUOTE_CHARS else t[:QUOTE_CHARS - 3] + "..."


def _where(c: TimelineClaim) -> str:
    return f"{c.interaction_id} turn {c.turn_index}"


def significant(c: TimelineClaim) -> bool:
    """Is this claim worth a sentence of the narrative?  A retrieved turn that a stored memory describes, one whose wording carries a cue (hedge, decision, correction, recommendation), or a
    substantive client turn.  The pleasantries and logistics around them stay in the evidence list and the source records, not in the narrative."""
    if c.source != "source_turn" or not c.relevant:
        return False
    strong = [x for x in c.cues if x != "hypothetical"]                  # a bare "if ... would" is not a statement about the record
    if c.memory_refs or strong:
        return True
    # the client's own substantive words are the primary evidence of a chronology even without a hedge or decision cue ("I have not signed it yet"); greetings, thanks and scheduling are not
    return c.speaker == "Client" and len(c.wording.split()) >= 6 and not rules.TEMPLATE.search(rules.norm(c.wording)) and c.mentions_topic is not False


def timeline_findings(claims: list[TimelineClaim], evidence: list[EvidenceItem]) -> list[Finding]:
    """The chronology as readable paragraphs (B3: agents/report/narrative.py renders them; this module only classifies)."""
    from agents.report.narrative import narrate
    return narrate(claims, evidence)


def timeline_uncertainties(claims: list[TimelineClaim]) -> list[str]:
    """What the chronology does NOT establish, as code-written lines for the report's uncertainties."""
    out: list[str] = []
    by_id = {c.claim_id: c for c in claims}
    for c in claims:
        ref = ", ".join(c.evidence_refs[:1])
        what = f"{c.event_date or 'undated'} {c.statement_kind.replace('_', ' ')} ({c.modality}{', value ' + c.value if c.value else ''})"
        if c.source == "memory":
            out.append(f"Unverified memory without a retrieved source turn [{ref}]: {what}: \"{_q(c)}\" ({_where(c)}); it is not stated as fact.")
        if c.statement_kind == "advisor_reported" and c.modality in DECISIVE + TENTATIVE and not c.client_confirmation and not c.confirmed_client_decision:
            out.append(f"Advisor-reported, not confirmed by the client [{ref}]: {what} ({_where(c)}).")
        if c.temporal_status == "unresolved":
            out.append(f"No current position is established [{ref}]: the latest decision-like statement ({what}, {_where(c)}) is not a verified, confirmed client decision.")
        if c.temporal_status == "disputed":
            others = ", ".join(sorted({by_id[r["claim_id"]].claim_id for r in c.relations if r["type"] == "contradicts" and r["claim_id"] in by_id}))
            out.append(f"Unreconciled contradiction [{ref}]: {what} ({_where(c)}) conflicts with {others or 'another record'}.")
        for n in c.notes:
            if n.startswith("this is recorded as a correction"):
                out.append(f"Correction without its target [{ref}]: {what} ({_where(c)}): {n}.")
    return list(dict.fromkeys(out))


def timeline_summary(claims: list[TimelineClaim], incomplete: bool = False) -> str:
    """A neutral description of the chronology (B3: written by agents/report/narrative.py)."""
    from agents.report.narrative import narrative_summary
    return narrative_summary(claims, incomplete)
