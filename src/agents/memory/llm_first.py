"""LLM-first ingestion (CMF_INGEST_MODE=llm_first): the model reads the WHOLE conversation; code checks facts; a second, blind model votes on certainty.

    ① extract   one LLM call sees every turn (no regex pre-selection) and proposes MemoryCandidates
    ② verify    hard, deterministic FACT checks only: the turn exists, the excerpt is really in it, the speaker comes from the data, the value appears in the
                excerpt.  No certainty or relevance regexes here: whether something is a decision is the models' call.
    ③ vote      a verifier LLM labels each candidate's certainty BLIND (it never sees the extractor's label) and says whether the statement is supported
                by the text and worth remembering.  The existing regex cues give a third vote, which can only veto on hedging wording.
    ④ combine   downgrade only: the final certainty is the LOWER of the extractor's and the verifier's.  Agreement sets a confidence bucket;
                'unsupported' rejects; disagreement is recorded on the memory for review.
    ⑤ value     every confirmed number that could become a current value must be the client's own figure (not an unaccepted recommendation, an
                advisor's read-out of balances or spending, a what-if or a corrected-away value), and its kind of quantity becomes part of the slot, so
                dollars a month never replace a number of days.
    ⑥ slot      before a confirmed value enters a topic that already has current values of the same kind, the verifier says which of them (if any) it
                is about: a match reuses that subject; otherwise the memory keeps its own (no unrelated value overwrites a current one, and one fact is
                not split across several subjects).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, Optional

from pydantic import BaseModel, Field

from agents.memory import rules
from agents.memory.config import CERTAINTY_RANK, EXTRACTION_AGENT, FIXED_SUBJECT_TOPICS
from agents.memory.ingest import Decision, MeetingContext, _repair_excerpt, enforce_policy
from agents.planner.topics import TOPICS
from llm.client import LLMClient, LLMError, OpenAILLM, StructuredOutputError, generate_structured
from orchestration.tracing import NullTracer, Tracer
from schemas.memory import ExtractionResult, MemoryCandidate
from services.conversations import Conversations

VERIFIER_AGENT = "memory_verifier"
CONTEXT_BEFORE, CONTEXT_AFTER = 2, 1
MAX_TRANSCRIPT_CHARS = 40_000          # a meeting is ~3-6k characters; anything far larger is refused, not truncated silently

# Agreement bucket -> confidence stored on the memory = MEASURED certainty accuracy of that bucket (scripts/eval_llm_first.py, gpt-4o-mini extractor +
# gpt-4.1-mini verifier, 150 original + 60 paraphrased conversations, after the harness write validator; n per bucket in brackets).  Re-measure when models or
# prompts change.  Disagreement buckets are small samples, and in them the downgraded label was usually the WRONG one: downgrade-only is kept on purpose
# (over-confidence is the costlier error), so these memories carry a low confidence and are flagged for review instead.
CONFIDENCE = {"agree_all": 0.96,         # [101]
              "agree_llms": 0.82,        # [44]
              "verifier_higher": 0.86,   # [7]  verifier rated it higher; the extractor's lower label is kept
              "verifier_lower": 0.2,     # [5]  verifier rated it lower; the lower label is kept
              "regex_hedge": 0.3,        # [3]  hedge-wording veto; 0/3 measured, kept as a safety prior
              "not_target": 0.4}         # [-]  not measured: downgraded by the value check, a safety prior
HEDGES = ("exploratory", "hypothetical")

EXTRACT_SYSTEM = f"""You extract durable client memory candidates from ONE wealth-advisory conversation. You never invent facts.
You are shown the WHOLE conversation (a meeting or call transcript, an email thread or a chat), every turn or message numbered and labelled with its speaker.
Never cite a message from a Third party (e.g. the client's accountant) as the turn_index: only Client or Advisor turns can be evidence. Return JSON with a list "candidates" (at most 12; usually 1 to 5).
Only include something worth remembering at the next meeting: a decision or agreed plan, a decision to wait or defer, an option the client is still weighing,
a correction of an earlier record, an advisor recommendation, a completed action (something was signed, paid, transferred, submitted or executed: certainty
confirmed), or a stated fact or request about the client's situation.
Do not include greetings, scheduling or routine pleasantries.
Do not include figures the advisor reads out from portfolio or banking systems (current balances, the current allocation, returns, spending averages): those are
system data, not something the client decided or said. A target the CLIENT agrees to change TO is a decision; a read-out of where the portfolio IS is not.
Follow-up tasks ("I'll send...", "I'll model...", "bring the forms next time") are not decisions: include one only if it matters for the plan, with certainty
unconfirmed and value null.
Read across turns: a short reply such as "Yes, let's do that" can turn the previous turn's proposal into a decision.

For each candidate:
- topic: exactly one of {", ".join(TOPICS)}. Use retirement_age for when someone retires and retirement_income for how much income they need. Avoid "other".
- subject: a short slot name; for retirement_age and asset_allocation use the topic name itself. For every other topic the subject must tell apart
  different things on the same topic: name the person, account, property or purpose (e.g. "daughter_tuition" vs "home_renovation"). Two memories
  share a subject ONLY if a later value replaces the earlier one.
- statement: one sentence saying who decided / is considering / corrected what. Stay close to the words of the excerpt and reuse its key words and figures;
  the statement must be checkable against the excerpt alone.
- value: a short normalised value taken from the text (for example "63" or "35"), or null. Never a sentence.
- certainty: confirmed (the client decided or agreed a plan, including a decision to defer), exploratory (still weighing, leaning, an idea not an instruction),
  hypothetical (an if-scenario), recommendation (the advisor proposes and the client has not accepted), correction (an earlier record is being corrected),
  unconfirmed (unclear or not yet verified). If unsure choose the weaker one.
  Two rules that override everything else:
  * If anyone says an earlier note, record, summary or file entry was wrong, too strong, imprecise or needs correcting, certainty is correction
    (whatever the client's position now is). Put the corrected value in value and the old one in corrects_value, and say the current position in the statement.
  * "Inclined to", "leaning toward", "hasn't signed", "hasn't decided", "nothing is final" are never confirmed: use exploratory.
- speaker_role: Client or Advisor, as labelled. turn_index: the turn whose text STATES the content (the proposal with its figures), not a later
  "yes" or "okay" that only agrees to it.
- excerpt: ONE sentence copied EXACTLY, character for character, from that turn, containing the key content.
- corrects_value: for a correction, the value being corrected, else null.
- reusable_procedure: true only when an advisor states a reusable way of working.
Output JSON only."""

VERIFY_SYSTEM = """You are an independent reviewer of memory records extracted from a wealth-advisory conversation. Judge each item ONLY from the quoted text and its context.
For every item return:
- id: the item id you were given.
- certainty: your own label for what the quoted text shows about the client's position: confirmed (decided / agreed / decided to defer), exploratory (still weighing,
  leaning, an idea), hypothetical (an if-scenario), recommendation (advisor proposal not yet accepted), correction (an earlier record is corrected),
  unconfirmed (unclear or not verified). If the text is ambiguous choose the weaker label.
  If the text says an earlier note, record or summary was wrong, too strong or imprecise, the label is correction. "Inclined to", "leaning toward",
  "hasn't signed", "hasn't decided" are never confirmed.
- supported: true only if the statement is fully supported by the quoted text and context (no invented details, no stronger claim than the text makes).
- worth_remembering: true if an advisor would want this at the next meeting; false for pleasantries, scheduling, and figures read out from portfolio or
  banking systems (current balances, the current allocation, returns, spending averages), which are system data rather than client memory.
A follow-up task ("I'll send...", "I'll model...") is not a decision: label it unconfirmed.
Output JSON only: {"items": [...]}."""


class VerifiedItem(BaseModel):
    id: int
    certainty: Literal["confirmed", "unconfirmed", "recommendation", "exploratory", "hypothetical", "correction"]
    supported: bool
    worth_remembering: bool


class VerifyResult(BaseModel):
    items: list[VerifiedItem] = Field(default_factory=list)


@dataclass
class Vote:
    """Everything the combine step decided, kept for the audit trail and the evaluation."""
    extractor: str
    verifier: Optional[str]
    regex: str
    supported: Optional[bool]
    worthy: Optional[bool]
    bucket: str
    final: str
    confidence: float


DEFAULT_OPENAI_VERIFIER = "gpt-4.1-mini"    # a different model than the default extractor (gpt-4o-mini): measured +5 points certainty accuracy over a same-model verifier


def make_verifier_llm(default: LLMClient) -> LLMClient:
    """The verifier should be a DIFFERENT model than the extractor: two copies of one model share blind spots, so their agreement says less.
    Only a plain OpenAI extractor gets a separate verifier: CMF_VERIFIER_MODEL (default gpt-4.1-mini; 'same' reuses the extractor).  Any other client
    (Ollama, a test MockLLM, a harness wrapper) is reused as is, with the verifier's own prompt, so nothing silently bypasses what the caller passed in."""
    if type(default) is not OpenAILLM:
        return default
    model = os.environ.get("CMF_VERIFIER_MODEL", "").strip() or DEFAULT_OPENAI_VERIFIER
    return default if model == "same" or model == getattr(default, "model", None) else OpenAILLM(model=model)


# ---------------------------------------------------------------------------------------------- ① extract
def transcript(ctx: MeetingContext) -> str:
    """Meetings and calls: '[turn n | Advisor] text'.  Email threads and chats also show who sent each message and when (a thread spans weeks)."""
    def head(r):
        if r.get("channel") in ("email", "chat"):
            return f"[turn {r['turn_index']} | {r['speaker']}: {r.get('speaker_name', '')} | {r['interaction_datetime'][:16].replace('T', ' ')}]"
        return f"[turn {r['turn_index']} | {r['speaker']}]"
    return "\n".join(f"{head(r)} {r['utterance']}" for r in ctx.rows)


def extract(llm: LLMClient, ctx: MeetingContext, client_label: str, tracer: Optional[Tracer] = None) -> tuple[list[MemoryCandidate], Optional[str]]:
    tracer = tracer or NullTracer()
    body = transcript(ctx)
    if len(body) > MAX_TRANSCRIPT_CHARS:
        return [], f"transcript too long for one call ({len(body)} chars)"
    kind = {"email": "Email thread", "chat": "Chat conversation"}.get(ctx.rows[0].get("channel", ""), "Conversation")
    user = f"Conversation {ctx.interaction_id} ({kind}) starting {ctx.date} with {client_label}. Subject / context: {ctx.context}\n\n{body}"
    turns = {r["turn_index"] for r in ctx.rows}

    def check(res: ExtractionResult) -> None:
        bad = [c.turn_index for c in res.candidates if c.turn_index not in turns]
        if bad:
            raise ValueError(f"turn_index {bad} does not exist; valid turns are {min(turns)}..{max(turns)}")

    try:
        res = generate_structured(llm, EXTRACTION_AGENT, EXTRACT_SYSTEM, user, ExtractionResult, retries=2, tracer=tracer, validator=check)
        tracer.event("memory", "llm_first_extraction", interaction=ctx.interaction_id, candidates=len(res.candidates))
        return list(res.candidates), None
    except (StructuredOutputError, LLMError) as e:
        tracer.event("memory", "extraction_failed", interaction=ctx.interaction_id, error=str(e)[:300])
        return [], str(e)[:300]


# ---------------------------------------------------------------------------------------------- ② hard fact checks
REANCHOR_BACK = 3           # how many earlier turns may hold the content a short reply confirms
REANCHOR_BELOW = 0.6        # try re-anchoring when the cited sentence carries less than this share of the statement's content words ...
REANCHOR_MIN = 0.3          # ... onto an earlier sentence that carries at least this share ...
REANCHOR_MARGIN = 0.15      # ... and clearly more than the cited one
_STOP = set("the and for that with this you your are was were have has had not but from will would can could about than then they their its his her she "
            "who what when which there here been also only just more some any all into over".split())


def _words(t: str) -> set[str]:
    return {w[:6] for w in rules.norm(t).replace("'", "").split() if len(w) > 2 and w.isalpha() and w not in _STOP}


def supports(statement: str, value: Optional[str], sentence: str) -> float:
    """How well ONE source sentence carries the statement: 1.0 if it contains the value, else the share of the statement's content words it contains."""
    if value and rules.value_supported(value, sentence):
        return 1.0
    s = _words(statement)
    return len(s & _words(sentence)) / len(s) if s else 0.0


def reanchor(cand: MemoryCandidate, ctx: MeetingContext) -> Optional[tuple[dict, str, float]]:
    """The model may cite a short reply ("Yes, let's do that") for content stated earlier.  Find the earlier sentence that states it:
    (row, sentence, score) from the cited turn's REANCHOR_BACK predecessors, or None."""
    order = [r["turn_index"] for r in ctx.rows]
    k = order.index(cand.turn_index)
    best = None
    for r in ctx.rows[max(0, k - REANCHOR_BACK): k]:
        if r["speaker"] not in ("Client", "Advisor"):          # never re-anchor onto a third party's message
            continue
        for sent in rules.split_sentences(r["utterance"]):
            sc = supports(cand.statement, cand.value, sent)
            if best is None or sc > best[2]:
                best = (r, sent[:500], sc)
    return best


def fact_check(cand: MemoryCandidate, ctx: MeetingContext, conv: Conversations) -> tuple[Optional[MemoryCandidate], str, list[str], list[dict]]:
    """(checked candidate or None, rejection reason, notes, extra evidence).  Facts only: never judges certainty or relevance."""
    row = ctx.turn(cand.turn_index)
    if row is None:
        return None, "turn does not exist in this conversation", [], []
    notes: list[str] = []
    extra: list[dict] = []
    role = row["speaker"]
    if role not in ("Client", "Advisor"):
        return None, "a third-party message cannot be cited as client memory evidence", [], []
    if role != cand.speaker_role:
        notes.append(f"speaker role taken from data: {role} (model said {cand.speaker_role})")
    excerpt = cand.excerpt
    if not conv.verify_excerpt(ctx.client_id, ctx.interaction_id, cand.turn_index, excerpt):
        fixed = _repair_excerpt(row["utterance"], excerpt)
        if not fixed:
            return None, "excerpt does not exist in the cited turn", notes, []
        notes.append("excerpt repaired to the closest real sentence")
        excerpt = fixed
    # evidence must SHOW the content: a bare confirmation ("Yes, I think so.") is re-anchored to the earlier sentence that states it,
    # and the confirmation is kept as supporting evidence (so the memory cites both the proposal and the agreement)
    # Re-anchoring never rejects: the harness MemoryWriteValidator stays the one authority on whether evidence supports a memory.
    here = supports(cand.statement, cand.value, excerpt)
    found = reanchor(cand.model_copy(update=dict(excerpt=excerpt)), ctx) if here < REANCHOR_BELOW else None
    if found is not None and found[2] >= REANCHOR_MIN and found[2] >= here + REANCHOR_MARGIN:
        prev, sent, _ = found
        extra.append(dict(turn_index=row["turn_index"], speaker=role, excerpt=excerpt, role="supporting"))
        notes.append(f"evidence re-anchored to turn {prev['turn_index']} (content); turn {row['turn_index']} kept as supporting evidence")
        cand = cand.model_copy(update=dict(turn_index=prev["turn_index"]))
        row, role, excerpt = prev, prev["speaker"], sent
    topic = cand.topic if rules.valid_topic(cand.topic) else rules.infer_topic(cand.statement + " " + excerpt)
    if topic == "other" or topic not in rules.TOPICS:
        topic = rules.infer_topic(ctx.context)
    if topic == "other":
        return None, "no recognisable topic", notes, []
    if topic != cand.topic:
        notes.append(f"topic {cand.topic!r} -> {topic!r}")
    value = rules.normalise_value(topic, cand.value)
    if value is not None and not rules.value_supported(value, excerpt):
        notes.append(f"value {value!r} dropped: not in the excerpt")
        value = None
    if value is None and topic in ("retirement_age", "asset_allocation"):      # numeric slots: parse the number deterministically from the excerpt
        value = rules.normalise_value(topic, rules.infer_value(topic, excerpt, cand.certainty))
    corrects = rules.normalise_value(topic, cand.corrects_value)
    if corrects and not rules.value_supported(corrects, excerpt + " " + row["utterance"]):
        corrects = None
    return cand.model_copy(update=dict(topic=topic, subject=rules.normalise_subject(topic, cand.subject), value=value, corrects_value=corrects,
                                       speaker_role=role, excerpt=excerpt, turn_index=row["turn_index"])), "", notes, extra


# ---------------------------------------------------------------------------------------------- ③ blind verifier
def verify(llm: LLMClient, ctx: MeetingContext, cands: list[MemoryCandidate], tracer: Optional[Tracer] = None) -> tuple[dict[int, VerifiedItem], Optional[str]]:
    """One call per conversation for all candidates.  The verifier sees the quote, the surrounding turns and the extractor's statement, never its certainty."""
    tracer = tracer or NullTracer()
    if not cands:
        return {}, None
    order = [r["turn_index"] for r in ctx.rows]
    blocks = []
    for i, c in enumerate(cands):
        k = order.index(c.turn_index)
        around = ctx.rows[max(0, k - CONTEXT_BEFORE): k + CONTEXT_AFTER + 1]
        ctx_txt = "\n".join(f"    [turn {r['turn_index']} | {r['speaker']}] {r['utterance']}" for r in around)
        blocks.append(f"Item {i}\n  statement: {c.statement}\n  value: {c.value}\n  quoted ({c.speaker_role}, turn {c.turn_index}): \"{c.excerpt}\"\n  context:\n{ctx_txt}")
    user = f"Conversation {ctx.interaction_id} ({ctx.date}). Context: {ctx.context}\n\n" + "\n\n".join(blocks)

    def check(res: VerifyResult) -> None:
        missing = set(range(len(cands))) - {x.id for x in res.items}
        if missing:
            raise ValueError(f"items {sorted(missing)} have no verdict; return one entry per item id")

    try:
        res = generate_structured(llm, VERIFIER_AGENT, VERIFY_SYSTEM, user, VerifyResult, retries=2, tracer=tracer, validator=check)
        return {x.id: x for x in res.items}, None
    except (StructuredOutputError, LLMError) as e:
        tracer.event("memory", "verification_failed", interaction=ctx.interaction_id, error=str(e)[:300])
        return {}, str(e)[:300]


# ---------------------------------------------------------------------------------------------- ④ combine (downgrade only)
def _lower(a: str, b: str) -> str:
    return b if CERTAINTY_RANK[b] < CERTAINTY_RANK[a] else a


def combine(c: MemoryCandidate, v: Optional[VerifiedItem]) -> Vote:
    regex, _ = rules.guard_certainty(c.certainty, c.excerpt, c.speaker_role)     # advisory only: affects confidence, never the label
    if v is None:                                                               # verifier unavailable: fail safe to the weakest stored claim
        final = _lower(c.certainty, "unconfirmed")
        return Vote(c.certainty, None, regex, None, None, "unverified", final, 0.4)
    final = _lower(c.certainty, v.certainty)
    if regex in HEDGES and CERTAINTY_RANK[regex] < CERTAINTY_RANK[final]:
        # the regexes are a poor DECISION detector but a precise HEDGE detector ("maybe", "leaning", "what if"): when they find hedging both models
        # missed, that veto wins (two agreeing models can share the same blind spot)
        return Vote(c.certainty, v.certainty, regex, v.supported, v.worth_remembering, "regex_hedge", regex, CONFIDENCE["regex_hedge"])
    if v.certainty == c.certainty:
        bucket = "agree_all" if regex == c.certainty else "agree_llms"
    elif CERTAINTY_RANK[v.certainty] < CERTAINTY_RANK[c.certainty]:
        bucket = "verifier_lower"
    else:
        bucket = "verifier_higher"
    return Vote(c.certainty, v.certainty, regex, v.supported, v.worth_remembering, bucket, final, CONFIDENCE.get(bucket, 0.5))


# ---------------------------------------------------------------------------------------------- ⑥ slot resolution
SLOT_SYSTEM = """You decide which existing client memory, if any, a NEW memory is about. All share one topic. Decide from the quoted text only.
match: the number of the existing memory that is about THE SAME person, account, property, goal or purpose as the new one (the new one then confirms or
replaces its value), or -1 if the new memory is about something different from all of them (a different child, account, goal or purpose).
new_subject: when match is -1, a short snake_case name for what the NEW memory is about (e.g. "son_tuition", "rental_property"); otherwise "".
Output JSON only: {"match": 0, "new_subject": ""}."""


class SlotVerdict(BaseModel):
    match: int
    new_subject: str = ""


def check_slot(verifier: LLMClient, store, ctx: MeetingContext, c: MemoryCandidate, tracer: Optional[Tracer] = None,
               quantity: Optional[str] = None) -> tuple[MemoryCandidate, Optional[str]]:
    """Consolidation folds the confirmed values of one (topic, subject) slot into versions, so the subject decides what replaces what.  The extractor's
    subject is unreliable in both directions: one shared subject lets an unrelated value 'supersede' a current one (one child's one-off cost replacing
    another child's tuition budget), and a fresh subject per mention splits one fact into several current values.  So before a confirmed value enters a topic
    that already has current values, the verifier says which of them (if any) it is about: a match reuses that subject; no match keeps (or, on a name clash,
    replaces) the extractor's subject.  Single-valued topics (retirement_age, asset_allocation) have one slot per client and are skipped."""
    if store is None or c.topic in FIXED_SUBJECT_TOPICS or c.certainty not in ("confirmed", "correction") or not c.value:
        return c, None
    cur = store.records(ctx.client_id, "semantic", statuses=["active"], topics=[c.topic])
    if quantity:                                             # only values of the same kind can be the same thing (see check_value)
        cur = [r for r in cur if (r["subject"] or r["topic"]).endswith("_" + quantity)]
    if not cur:
        return c, None
    same = [r for r in cur if (r["subject"] or r["topic"]) == c.subject]
    if same and rules.norm(same[0]["value_text"] or "") == rules.norm(c.value):
        return c, None                                       # same slot, same value: a reinforcement, nothing to decide
    evs = store.evidence_map([r["memory_id"] for r in cur])
    lines = []
    for i, r in enumerate(cur):
        quote = next((e.excerpt for e in evs.get(r["memory_id"]) or [] if getattr(e, "excerpt", None)), r["content"])
        lines.append(f"{i}. ({(r['subject'] or r['topic']).replace('_', ' ')}, value {r['value_text']}) \"{quote}\"")
    user = f"Topic: {c.topic}\nExisting memories:\n" + "\n".join(lines) + f"\n\nNew memory (value {c.value}): {c.statement}\n  quoted: \"{c.excerpt}\""

    def check(v: SlotVerdict) -> None:
        if not -1 <= v.match < len(cur):
            raise ValueError(f"match must be -1 or 0..{len(cur) - 1}")
    try:
        v = generate_structured(verifier, VERIFIER_AGENT, SLOT_SYSTEM, user, SlotVerdict, retries=1, tracer=tracer, validator=check)
    except (StructuredOutputError, LLMError) as e:
        return c, f"slot check unavailable ({str(e)[:80]}); kept on slot {c.subject}"
    if v.match >= 0:
        hit = cur[v.match]
        subject = hit["subject"] or hit["topic"]
        verb = "confirms" if rules.norm(hit["value_text"] or "") == rules.norm(c.value) else "replaces"
        return c.model_copy(update=dict(subject=subject)), f"slot check: same thing as the current {subject} value {hit['value_text']} ({verb} it)"
    if not same:
        return c, f"slot check: about something new; own subject {c.subject!r}"
    new = rules.normalise_subject(c.topic, v.new_subject)
    if new in ("", c.topic) or any((r["subject"] or r["topic"]) == new for r in cur):
        new = rules.normalise_subject(c.topic, f"{c.subject}_{ctx.interaction_id}")
    if quantity and not new.endswith("_" + quantity):
        new = rules.normalise_subject(c.topic, f"{new}_{quantity}")
    return c.model_copy(update=dict(subject=new)), f"slot check: different from the current {c.subject} value {same[0]['value_text']}; own subject {new!r}"


# ---------------------------------------------------------------------------------------------- ⑤ value check (before a number can become a current value)
QUANTITIES = ("age", "years", "months", "percent", "usd_amount", "usd_per_month", "usd_per_year", "count", "date", "other")
TARGET_MEANING = {"retirement_age": ("the age at which the client plans to retire", "age"),
                  "asset_allocation": ("the share of the client's investable portfolio held in equities (stocks)", "percent")}
VALUE_SYSTEM = f"""You check one memory before its number can become a CURRENT value in the client's profile. Decide from the quoted text only.
client_figure: true if the text shows this figure is the client's own: something the client decided or agreed, a completed action, or a fact about the
client's situation. It does not matter who says it: an advisor recording the plan or decision the client agreed ("The working plan I'm recording is
..."), an agreed working assumption for planning, an advisor correcting the record ("my note was wrong; the form says sixty percent"), the client
stating their own income, costs or spending, and figures from the client's own documents, accounts or professionals (a pension form, a loan balance
the client owes, the client's accountant's estimate) are all the client's figure.
false only for: an advisor's suggestion or recommendation the client has not accepted; the ADVISOR reading out figures from statements or systems
(current balances, holdings weights, average spending "I'm seeing", returns); a scenario or what-if; the old value that a correction says was wrong;
or a number about something other than the memory's subject.
quantity: what kind of number the value is, exactly one of {", ".join(QUANTITIES)} (usd_amount = a one-off or total dollar amount).
Output JSON only: {{"client_figure": true, "quantity": "usd_per_month"}}."""


class ValueVerdict(BaseModel):
    client_figure: bool
    quantity: Literal["age", "years", "months", "percent", "usd_amount", "usd_per_month", "usd_per_year", "count", "date", "other"]


def check_value(verifier: LLMClient, c: MemoryCandidate, tracer: Optional[Tracer] = None) -> tuple[MemoryCandidate, Optional[str], Optional[str]]:
    """(candidate, note, quantity).  Consolidation turns every confirmed number on a slot into its current value, so before that can happen the verifier
    says (1) whether the figure is the client's own (an unaccepted recommendation, a balance or spending read-out, a what-if or a corrected-away claim is
    not: kept as unconfirmed) and (2) what kind of quantity it is.  The quantity becomes part of the slot, so dollars a month can never replace a number of
    days or a one-off amount.  Single-valued topics must also have their own quantity (retirement_age: an age; asset_allocation: a percent).
    If the check cannot run, the memory is kept as unconfirmed (fail safe)."""
    if c.certainty not in ("confirmed", "correction") or not c.value:
        return c, None, None
    meaning = TARGET_MEANING.get(c.topic)
    target = f"Single-valued target: {c.topic} = {meaning[0]}\n" if meaning else ""
    user = (f"Topic: {c.topic}\nSubject: {c.subject.replace('_', ' ')}\n{target}Proposed value: {c.value}\nMemory: {c.statement}\n"
            f"  quoted ({c.speaker_role}): \"{c.excerpt}\"")
    try:
        v = generate_structured(verifier, VERIFIER_AGENT, VALUE_SYSTEM, user, ValueVerdict, retries=1, tracer=tracer)
    except (StructuredOutputError, LLMError) as e:
        return c.model_copy(update=dict(certainty="unconfirmed")), f"value check unavailable ({str(e)[:60]}); kept as unconfirmed", None
    if not v.client_figure:
        return c.model_copy(update=dict(certainty="unconfirmed")), f"value check: {c.value} is not the client's own figure; kept as unconfirmed", v.quantity
    if meaning:
        if v.quantity != meaning[1]:
            return (c.model_copy(update=dict(certainty="unconfirmed")),
                    f"value check: {c.value} is a {v.quantity}, not the client's {c.topic} ({meaning[1]}); kept as unconfirmed", v.quantity)
        return c, f"value check: the client's agreed {c.topic}", v.quantity
    subject = c.subject if c.subject.endswith("_" + v.quantity) else rules.normalise_subject(c.topic, f"{c.subject}_{v.quantity}")
    return c.model_copy(update=dict(subject=subject)), f"value check: the client's own figure ({v.quantity})", v.quantity


# ---------------------------------------------------------------------------------------------- the whole stage
def run(llm: LLMClient, verifier: LLMClient, ctx: MeetingContext, conv: Conversations, client_label: str,
        tracer: Optional[Tracer] = None, store=None) -> tuple[list[Decision], dict[int, Vote], Optional[str]]:
    """Returns (decisions, votes by decision index, error).  Same Decision contract as ingest.filter_memory_candidates."""
    cands, err = extract(llm, ctx, client_label, tracer)
    decisions: list[Decision] = []
    checked: list[tuple[int, MemoryCandidate, list[str]]] = []
    extras: dict[int, list[dict]] = {}
    seen: set[tuple] = set()
    for cand in cands:
        final, why, notes, extra = fact_check(cand, ctx, conv)
        if final is None:
            decisions.append(Decision("conversation_llm", cand, "rejected", why, None, cand.certainty, cand.certainty, notes))
            continue
        key = (final.turn_index, final.topic, final.subject, rules.norm(final.value or ""))
        if key in seen:
            decisions.append(Decision("conversation_llm", cand, "merged", "duplicate of an earlier candidate in the same turn", final, cand.certainty, cand.certainty, notes))
            continue
        seen.add(key)
        decisions.append(Decision("conversation_llm", cand, "accepted", "valid", final, cand.certainty, cand.certainty, notes))
        checked.append((len(decisions) - 1, final, notes))
        extras[len(decisions) - 1] = extra
    verdicts, verr = verify(verifier, ctx, [c for _, c, _ in checked], tracer)
    votes: dict[int, Vote] = {}
    for j, (di, c, notes) in enumerate(checked):
        vote = combine(c, verdicts.get(j))
        votes[di] = vote
        d = decisions[di]
        tag = f"votes: extractor={vote.extractor} verifier={vote.verifier} regex={vote.regex} -> {vote.final} ({vote.bucket}, confidence {vote.confidence})"
        if vote.supported is False:
            decisions[di] = Decision(d.origin, d.candidate, "rejected", "verifier: statement not supported by the text", d.final, d.certainty_in, d.certainty_in, notes + [tag])
            continue
        if vote.worthy is False:
            decisions[di] = Decision(d.origin, d.candidate, "rejected", "verifier: not worth remembering", d.final, d.certainty_in, d.certainty_in, notes + [tag])
            continue
        if vote.bucket in ("verifier_lower", "verifier_higher", "regex_hedge", "unverified"):
            tag += "; needs review"
        out, value_note, quantity = check_value(verifier, c.model_copy(update=dict(certainty=vote.final)), tracer)
        out, slot_note = check_slot(verifier, store, ctx, out, tracer, quantity)
        notes = notes + [n for n in (value_note, slot_note) if n]
        if out.certainty != vote.final:                       # the value check may only lower certainty
            vote = Vote(vote.extractor, vote.verifier, vote.regex, vote.supported, vote.worthy, "not_target", out.certainty, CONFIDENCE["not_target"])
            votes[di] = vote
            tag = tag + f"; value check -> {out.certainty} (confidence {vote.confidence}); needs review"
        decisions[di] = Decision(d.origin, d.candidate, "downgraded" if vote.final != c.certainty else "accepted",
                                 ("verifier vote: " + vote.bucket) if vote.final != c.certainty else "valid", out, d.certainty_in, vote.final, notes + [tag],
                                 confidence=vote.confidence, extra_evidence=extras.get(di, []))
    for d in decisions:
        if d.kept:
            enforce_policy(d, ctx)     # M1: a confident extractor/verifier vote can never exceed the evidence ceiling (speaker attribution, finality)
    return decisions, votes, err or verr
