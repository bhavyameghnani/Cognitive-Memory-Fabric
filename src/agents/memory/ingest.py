"""Ingestion stages 1-4 (and 8 for episodic/procedural rows): salience -> extraction -> validation -> classification/routing -> persistence.

Each stage is a plain function so it can be tested alone.  The LLM only ever sees meeting snippets; this module builds the SQL-free
operations that MemoryStore validates and persists.
"""
from __future__ import annotations

import difflib
import json
from dataclasses import dataclass, field
from typing import Optional

from agents.memory import rules
from agents.memory.config import CERTAINTY_RANK, EXTRACTION_AGENT, FilterConfig, NUMERIC_TOPICS
from agents.memory.prompts import EXTRACTION_SYSTEM, build_user_prompt
from agents.memory.integrity import Principal
from agents.memory.store import MemoryStore, h
from llm.client import LLMClient, LLMError, StructuredOutputError, generate_structured
from orchestration.tracing import NullTracer, Tracer
from schemas.memory import ExtractionResult, MemoryCandidate
from services.conversations import Conversations


@dataclass
class MeetingContext:
    interaction_id: str
    client_id: str
    advisor_id: str
    date: str
    ts: str
    context: str
    rows: list[dict]

    @classmethod
    def load(cls, conv: Conversations, interaction_id: str) -> "MeetingContext":
        rows = conv.meeting(interaction_id)
        r0 = rows[0]
        return cls(interaction_id, r0["client_id"], r0["advisor_id"], r0["interaction_datetime"][:10], r0["interaction_datetime"], r0.get("meeting_context", ""), rows)

    def turn(self, idx: int) -> Optional[dict]:
        return next((r for r in self.rows if r["turn_index"] == idx), None)


@dataclass
class SelectedTurn:
    turn_index: int
    speaker: str
    text: str                       # key sentences only
    score: float
    reasons: list[str] = field(default_factory=list)


@dataclass
class Decision:
    origin: str
    candidate: MemoryCandidate
    decision: str                   # accepted | downgraded | merged | rejected
    reason: str
    final: Optional[MemoryCandidate] = None
    certainty_in: str = ""
    certainty_out: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def kept(self) -> bool:
        return self.decision in ("accepted", "downgraded")


@dataclass
class Classification:
    buckets: list[str]
    status: str
    event_type: str
    procedure_proposal: bool
    semantic_eligible: bool


# ---------------------------------------------------------------------------------------------- stage 1
def select_salient_turns(rows: list[dict], cfg: Optional[FilterConfig] = None) -> list[SelectedTurn]:
    """Keep key sentences only.  Greetings, acknowledgements, scheduling and routine portfolio/spending read-outs score below min_score."""
    cfg = cfg or FilterConfig()
    out: list[SelectedTurn] = []
    for r in rows:
        kept, why, best = [], [], 0.0
        for s in rules.split_sentences(r["utterance"]):
            sc, rs = rules.score_sentence(s, r["speaker"], cfg)
            if sc >= cfg.min_score:
                kept.append(s)
                why += [x for x in rs if x not in why]
                best = max(best, sc)
        if kept:
            text = ""
            for s in kept:
                if text and len(text) + len(s) + 1 > cfg.max_snippet_chars:
                    break
                text = f"{text} {s}".strip()
            out.append(SelectedTurn(r["turn_index"], r["speaker"], text, round(best + 0.1 * len(kept), 2), why))
    if len(out) > cfg.max_turns:
        top = sorted(out, key=lambda t: (-t.score, t.turn_index))[: cfg.max_turns]
        out = sorted(top, key=lambda t: t.turn_index)
    return out


# ---------------------------------------------------------------------------------------------- stage 2
def extract_memory_candidates(llm: LLMClient, ctx: MeetingContext, selected: list[SelectedTurn], client_label: str = "the client",
                              tracer: Optional[Tracer] = None) -> tuple[ExtractionResult, Optional[str]]:
    """One LLM call per meeting.  Returns (result, error).  On failure the result is empty and the rule safety net still runs."""
    tracer = tracer or NullTracer()
    if not selected:
        return ExtractionResult(), None
    shown = {t.turn_index for t in selected}
    user = build_user_prompt(client_label, ctx.date, ctx.interaction_id, ctx.context, [(t.turn_index, t.speaker, t.text) for t in selected])

    def check(res: ExtractionResult) -> None:
        bad = [c.turn_index for c in res.candidates if c.turn_index not in shown]
        if bad:
            raise ValueError(f"turn_index {bad} was not shown; use only turns {sorted(shown)}")

    try:
        res = generate_structured(llm, EXTRACTION_AGENT, EXTRACTION_SYSTEM, user, ExtractionResult, retries=2, tracer=tracer, validator=check)
        tracer.event("memory", "extraction", interaction=ctx.interaction_id, candidates=len(res.candidates))
        return res, None
    except (StructuredOutputError, LLMError) as e:
        tracer.event("memory", "extraction_failed", interaction=ctx.interaction_id, error=str(e)[:300])
        return ExtractionResult(), str(e)[:300]


def rule_candidates(ctx: MeetingContext) -> list[MemoryCandidate]:
    """Recall safety net: recap wording ('You've decided...', 'still weighing... idea and not an instruction', 'I've corrected my own record',
    'The working plan I'm recording is...') is high precision.  These candidates (origin conversation_rule) pass the SAME validation."""
    out = []
    for r in ctx.rows:
        for s in rules.split_sentences(r["utterance"]):
            c = rules.recap_certainty(s)
            if not c or len(s) < 12:
                continue
            topic = rules.infer_topic(s)
            out.append(MemoryCandidate(topic=topic, subject=topic, statement=s[:400], value=rules.infer_value(topic, s, c), certainty=c, speaker_role=r["speaker"],
                                       turn_index=r["turn_index"], excerpt=s[:500]))
    return out


# ---------------------------------------------------------------------------------------------- stage 3
def _repair_excerpt(turn_text: str, excerpt: str) -> Optional[str]:
    best, ratio = None, 0.0
    for s in rules.split_sentences(turn_text):
        q = difflib.SequenceMatcher(None, rules.norm(s), rules.norm(excerpt)).ratio()
        if q > ratio:
            best, ratio = s, q
    return best[:500] if best and ratio >= 0.6 else None


def filter_memory_candidates(cands: list[tuple[MemoryCandidate, str]], ctx: MeetingContext, conv: Conversations, shown: Optional[set[int]] = None,
                             cfg: Optional[FilterConfig] = None) -> list[Decision]:
    """Validate every candidate.  cands = [(candidate, origin)] with LLM candidates first so they win merges."""
    cfg = cfg or FilterConfig()
    decisions: list[Decision] = []
    seen: dict[tuple, Decision] = {}
    accepted_excerpts: list[tuple[int, str]] = []
    for cand, origin in cands:
        def rej(why: str, notes=None) -> Decision:
            return Decision(origin, cand, "rejected", why, None, cand.certainty, cand.certainty, notes or [])

        row = ctx.turn(cand.turn_index)
        if row is None:
            decisions.append(rej("turn does not exist in this meeting"))
            continue
        if origin == "conversation_llm" and shown is not None and cand.turn_index not in shown:
            decisions.append(rej("cited turn was not shown to the model"))
            continue
        notes: list[str] = []
        role = row["speaker"]
        if role != cand.speaker_role:
            notes.append(f"speaker role taken from data: {role} (model said {cand.speaker_role})")
        excerpt = cand.excerpt
        if not conv.verify_excerpt(ctx.client_id, ctx.interaction_id, cand.turn_index, excerpt):
            fixed = _repair_excerpt(row["utterance"], excerpt)
            if not fixed:
                decisions.append(rej("excerpt does not exist in the cited turn", notes))
                continue
            notes.append("excerpt repaired to the closest real sentence")
            excerpt = fixed
        # topic: deterministic override first (retirement timing vs income), then the model's, then the lexicon
        pt = rules.priority_topic(excerpt)
        topic = pt or (cand.topic if rules.valid_topic(cand.topic) else rules.infer_topic(excerpt))
        if topic == "other" or topic not in rules.TOPICS:
            topic = rules.infer_topic(cand.statement)
        if topic == "other":
            topic = rules.infer_topic(ctx.context)        # fallback: the meeting's stated context ("We've decided: 63" in a retirement-age meeting)
            if topic != "other":
                notes.append(f"topic taken from meeting context: {topic}")
        if topic == "other":
            decisions.append(rej("low relevance: no recognisable topic", notes))
            continue
        if topic != cand.topic:
            notes.append(f"topic {cand.topic!r} -> {topic!r}")
        subject = rules.normalise_subject(topic, cand.subject)
        # value: must be short, normalised and supported by the text; infer deterministically when empty
        value = rules.normalise_value(topic, cand.value)
        if cand.value and value is None:
            notes.append("value dropped: not usable for this topic")
        if value is not None and not rules.value_supported(value, excerpt):
            notes.append(f"value {value!r} dropped: unsupported by the excerpt")
            value = None
        if value is None:
            inferred = rules.normalise_value(topic, rules.infer_value(topic, excerpt, cand.certainty))
            if inferred:
                value = inferred
                notes.append(f"value inferred: {inferred}")
        corrects = rules.normalise_value(topic, cand.corrects_value)
        if cand.certainty == "correction" and not corrects and topic == "retirement_age":
            m = rules.RETIRE_AGE_RE.search(excerpt)
            corrects = rules.normalise_value(topic, m.group(1)) if m else None
        if corrects and not rules.value_supported(corrects, excerpt + " " + row["utterance"]):
            corrects = None
        # certainty guard (code, not prompt): downgrade only
        certainty, why = rules.guard_certainty(cand.certainty, excerpt, role)
        # relevance
        rel = (1 if (pt or rules.lexicon_topic_hits(excerpt)) else 0) + (1 if value else 0) \
            + (1 if (rules.has_decision(excerpt) or rules.has_exploratory(excerpt) or rules.has_correction(excerpt) or rules.RECOMMENDATION.search(rules.norm(excerpt))) else 0)
        if rules.is_template(excerpt) and not rel > 1:
            rel -= 2
        if rel < cfg.min_relevance or certainty == "n/a":
            decisions.append(rej(f"low relevance (score {rel})", notes))
            continue
        final = cand.model_copy(update=dict(topic=topic, subject=subject, value=value, corrects_value=corrects, certainty=certainty, speaker_role=role, excerpt=excerpt))
        # duplicates: same slot in the same turn, or an excerpt already covered by an accepted candidate of the same turn
        key = (cand.turn_index, topic, subject)
        covered = next((e for t, e in accepted_excerpts if t == cand.turn_index and (rules.norm(excerpt) in e or e in rules.norm(excerpt))), None)
        if key in seen or covered is not None:
            decisions.append(Decision(origin, cand, "merged", "duplicate of an earlier candidate in the same turn", final, cand.certainty, certainty, notes))
            continue
        d = Decision(origin, cand, "downgraded" if certainty != cand.certainty else "accepted", ("certainty guard: " + why) if why else "valid", final, cand.certainty, certainty, notes)
        seen[key] = d
        accepted_excerpts.append((cand.turn_index, rules.norm(excerpt)))
        decisions.append(d)
    return decisions


# ---------------------------------------------------------------------------------------------- stage 4
def classify_memory(d: Decision) -> Classification:
    c = d.final
    assert c is not None
    proposal = bool(c.reusable_procedure and c.speaker_role == "Advisor" and d.origin == "conversation_llm" and rules.PROCEDURE_CUE.search(rules.norm(c.excerpt)))
    numeric_ok = c.value is not None and (c.topic not in NUMERIC_TOPICS or c.value.replace(".", "", 1).isdigit())
    return Classification(buckets=["episodic"] + (["procedural"] if proposal else []), status="unconfirmed" if c.certainty == "unconfirmed" else "active",
                          event_type=rules.event_type_for(c.certainty), procedure_proposal=proposal,
                          semantic_eligible=c.certainty in ("confirmed", "correction") and numeric_ok)


def route_memory(cls: Classification) -> list[str]:
    """Memory types written at ingestion.  Semantic is never written here: only consolidation creates it.  Working memory is never created from conversations."""
    return list(cls.buckets)


# ---------------------------------------------------------------------------------------------- stage 8 (episodic/procedural)
def persist_decision(store: MemoryStore, ctx: MeetingContext, d: Decision, cls: Classification, model: str, selection_note: str = "", request_id: str = "") -> list[tuple[str, bool]]:
    """Returns [(memory_id, created)]; created is False when the same memory (content hash) was already stored."""
    c = d.final
    assert c is not None
    ev = [dict(source_type="conversation", interaction_id=ctx.interaction_id, turn_index=c.turn_index, speaker=c.speaker_role, excerpt=c.excerpt, role="primary")]
    ids: list[tuple[str, bool]] = []
    who = Principal.service("ingestion", [ctx.client_id], request_id=request_id)      # scope = the client of the verified meeting row
    for mtype in route_memory(cls):
        digest = h(ctx.client_id, mtype, ctx.interaction_id, c.turn_index, c.topic, c.subject, rules.norm(c.value or ""), c.certainty, rules.norm(c.excerpt), n=40)
        base = dict(client_id=ctx.client_id, memory_type=mtype, topic=c.topic, subject=c.subject, title=f"{c.topic}: {c.certainty}", content=c.statement, value_text=c.value,
                    corrects_value=c.corrects_value, certainty=c.certainty if mtype == "episodic" else "recommendation", source_role=c.speaker_role,
                    confidence=rules.confidence_for(c.certainty, d.origin, d.decision == "downgraded"), event_date=ctx.date, origin=d.origin, model=model if d.origin == "conversation_llm" else None,
                    content_hash=digest)
        if mtype == "episodic":
            rec = dict(base, memory_id="MEM-EPI-" + digest[:12], status=cls.status)
            det = dict(interaction_id=ctx.interaction_id, turn_index=c.turn_index, event_ts=ctx.ts, speaker_role=c.speaker_role, event_type=cls.event_type,
                       selection_note=(selection_note + "; " + "; ".join(d.notes))[:400].strip("; "), candidate_id=None)
        else:  # procedural proposal from an advisor turn: never active until a human approves it
            rec = dict(base, memory_id="MEM-PRO-" + digest[:12], status="candidate", valid_from=ctx.date)
            det = dict(procedure_name=c.statement[:80], steps=json.dumps([c.statement]), topics=c.topic, keywords="", approval_status="proposed", approver=None,
                       source_ref=f"{ctx.interaction_id}.{c.turn_index}")
        mid, created = store.insert_memory(rec, det, ev, reason=f"created from {ctx.interaction_id} turn {c.turn_index} ({d.origin})", principal=who)
        ids.append((mid, created))
    return ids
