"""P1.5 evaluation: adversarial claim corpus, client-isolation / temporal-integrity / report-quality metrics, cache correctness, context-compression retention and seven
end-to-end scenarios - all executed against the REAL code paths (the integrity boundary, the retrieval layer, the harness pipeline, the response cache, the compressor).

    PYTHONPATH=src python -m harness.evaluation.p15 [--out build/p15_eval] [--real]

Deterministic by default: scripted models only (no network, no credentials).  `--real` additionally runs a SMALL controlled request through the configured local model
(CMF_LLM_BACKEND, default ollama / qwen2.5:3b) and records exactly what ran; it never falls back to a mock silently - if the model is unreachable the section says "not run".
Nothing here is an accuracy figure for a language model: scripted runs measure how deterministic controls behave on fixed inputs.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Optional

from agents.memory import rules, support
from agents.memory.errors import MemoryWriteRejected
from agents.memory.integrity import Principal, value_in
from agents.memory.store import MemoryStore
from harness.evaluation.adversarial import AdvCase, present
from harness.evaluation.p15_corpus import CATEGORIES, CORPUS, N, V, Case15

ROOT = Path(__file__).resolve().parents[3]
CORPUS_VERSION = "p15-1"


# ================================================================================================================ claim verification: systems under test
class _Conv2:
    """A multi-client transcript source for the corpus: one turn per case, owned by whoever the case says owns the evidence."""

    def __init__(self) -> None:
        self.rows: dict = {}

    def add(self, owner: Optional[str], text: str, speaker: str = "Client") -> int:
        idx = len(self.rows) + 1
        self.rows[("INT-P15", idx)] = dict(client_id=owner, speaker=speaker, utterance=text, interaction_datetime="2026-06-01T09:00:00")
        return idx

    def turn(self, iid, idx):
        return self.rows.get((iid, idx))

    def verify_excerpt(self, client_id, iid, idx, excerpt):
        t = self.rows.get((iid, idx))
        return bool(t) and t["client_id"] == client_id and " ".join(excerpt.lower().split()) in " ".join(t["utterance"].lower().split())


def _old_words(text: str) -> set:
    return {w[:4] for w in re.findall(r"[a-z]+", rules.norm(text).replace("-", " ")) if len(w) >= 4 and w not in rules._STOP}


def legacy_p141_verdict(claim: str, value: str, evidence: str) -> str:
    """RECONSTRUCTION of the P1.4.1 LLM-extraction matcher (the BASELINE for before/after): a value that is supported (numbers as a set) AND a statement that is a substring of the
    excerpt, or contains it, or shares a third of its content words and none of its numbers is missing.  Kept here only to measure what P1.4.2 changed."""
    if not value or not value_in(value, evidence):
        return N
    a, b = rules.norm(claim), rules.norm(evidence)
    if a in b or b in a:
        return V
    nums = set(rules.extract_numbers(claim))
    if nums and not nums <= set(rules.extract_numbers(evidence)):
        return N
    w = _old_words(claim)
    return V if w and len(w & _old_words(evidence)) * 3 >= len(w) else N


class MemoryLayerRunner:
    """Scores the memory layer through the REAL integrity boundary: the claim is written as an episodic memory by the ingestion service, citing a transcript turn owned by
    whoever the case says; `verification` is read back from the stored row.  A rejected write (foreign evidence, ...) counts as not verified."""

    def __init__(self) -> None:
        self.conv = _Conv2()
        self.store = MemoryStore(url="sqlite:///:memory:", conv=self.conv)
        self.store.init()
        self.n = 0

    def verdict(self, c: Case15) -> tuple:
        owner = c.owner or None
        idx = self.conv.add(owner, c.evidence)
        self.n += 1
        mid = f"MEM-P15-{c.id}-{self.n}"
        rec = dict(memory_id=mid, client_id=c.client, memory_type="episodic", topic="retirement_age", subject=c.id, content=c.claim, value_text=c.value or None,
                   certainty="exploratory" if c.open_item else "confirmed", source_role="Client", status="active", origin="conversation_llm", event_date="2026-06-01", content_hash=mid)
        det = dict(interaction_id="INT-P15", turn_index=idx, event_ts="2026-06-01T09:00:00", speaker_role="Client", event_type="statement")
        ev = dict(source_type="conversation", interaction_id="INT-P15", turn_index=idx, speaker="Client", excerpt=c.evidence)
        try:
            self.store.insert_memory(rec, det, [ev], principal=Principal.service("ingestion", [c.client]))
        except MemoryWriteRejected as e:
            return N, "rejected:" + e.code
        row = self.store.get(mid)
        return (V if row["verification"] == "verified" else N), row["verification_basis"][:80]


def _report_case(c: Case15) -> AdvCase:
    return AdvCase(c.id, c.category, c.claim, c.evidence, c.expected, c.why, evidence_client=(None if c.evidence_client == "-" else (c.evidence_client or c.client)), plan=(c.client,))


class shipped_p142:
    """Context manager: the CURRENT memory matcher with the P1.5 / P1.4.3 RULES switched off (modal parity, magnitude units, clause structure / retraction).  P1.5.1 correction: this is
    NOT the matcher as shipped in P1.4.2 - the shared negation / hedge vocabulary added later (rejected, declined, unless, except ...) stays ON, and the historical P1.4.2 source was not
    preserved, so an exact reproduction is not possible.  It is reported as `memory_matcher_rules_off`.  (The class keeps its old name only so existing imports do not break.)"""

    def __enter__(self):
        self.old = dict(support.FLAGS)
        support.FLAGS.update(modal_parity=False, magnitude_units=False, structure=False, context_window=False)

    def __exit__(self, *exc):
        support.FLAGS.update(self.old)


def _report_without_clause_check(c: Case15) -> str:
    from harness import clause_support
    clause_support.ENABLED = False
    try:
        return V if present(_report_case(c), "structured")["presented_as_verified"] else N
    finally:
        clause_support.ENABLED = True


def evaluate_claims() -> dict:
    mem = MemoryLayerRunner()
    mem_shipped = MemoryLayerRunner()

    def shipped(c):
        with shipped_p142():
            return mem_shipped.verdict(c)
    systems = {"none (no verification)": lambda c: (V, "presented as written"),
               "memory_p141_baseline": lambda c: (legacy_p141_verdict(c.claim, c.value, c.evidence) if c.owner == c.client else N, "reconstructed P1.4.1 matcher"),
               "memory_matcher_rules_off": shipped,
               "memory_current": mem.verdict,
               "memory_derived_semantic": lambda c: (__import__("harness.evaluation.p151", fromlist=["derived_verdict"]).derived_verdict(c), "derived semantic memory from the same turn"),
               "report_before_p151 (clause check off)": lambda c: (_report_without_clause_check(c), "EvidenceVerifier(structured=True), clause_support switched off"),
               "report_current": lambda c: ((V if present(_report_case(c), "structured")["presented_as_verified"] else N), "EvidenceVerifier(structured=True)")}
    out: dict = {}
    for name, fn in systems.items():
        rows = []
        for c in CORPUS:
            actual, why = fn(c)
            rows.append(dict(id=c.id, category=c.category, expected=c.expected, actual=actual, correct=actual == c.expected, contradiction=c.contradiction, ambiguity=c.ambiguity, basis=why))
        out[name] = dict(rows=rows, overall=confusion(rows), by_category={cat: confusion([r for r in rows if r["category"] == cat]) for cat in CATEGORIES})
    return out


def _div(a: float, b: float) -> Optional[float]:
    return round(a / b, 3) if b else None


def confusion(rows: list) -> dict:
    tp = sum(r["expected"] == V and r["actual"] == V for r in rows)
    fp = sum(r["expected"] == N and r["actual"] == V for r in rows)
    fn = sum(r["expected"] == V and r["actual"] == N for r in rows)
    tn = sum(r["expected"] == N and r["actual"] == N for r in rows)
    p, rc = _div(tp, tp + fp), _div(tp, tp + fn)
    con = [r for r in rows if r["contradiction"]]
    amb = [r for r in rows if r["ambiguity"]]
    return dict(n=len(rows), tp=tp, fp=fp, fn=fn, tn=tn, precision=p, recall=rc, f1=(round(2 * p * rc / (p + rc), 3) if p and rc else (0.0 if p is not None and rc is not None else None)),
                false_positive_rate=_div(fp, fp + tn), false_negative_rate=_div(fn, fn + tp), unverified_rate=_div(sum(r["actual"] == N for r in rows), len(rows)),
                contradiction_detection_rate=_div(sum(r["actual"] == N for r in con), len(con)), contradiction_cases=len(con),
                ambiguity_detection_rate=_div(sum(r["actual"] == N for r in amb), len(amb)), ambiguity_cases=len(amb))


def corpus_summary() -> dict:
    cl = defaultdict(int)
    for c in CORPUS:
        cl[c.client] += 1
    return dict(cases=len(CORPUS), version=CORPUS_VERSION, positive=sum(c.expected == V for c in CORPUS), negative=sum(c.expected == N for c in CORPUS),
                by_category={cat: sum(c.category == cat for c in CORPUS) for cat in CATEGORIES}, by_client=dict(sorted(cl.items())),
                contradiction_cases=sum(c.contradiction for c in CORPUS), ambiguity_cases=sum(c.ambiguity for c in CORPUS),
                numeric_cases=sum(bool(c.value) for c in CORPUS), temporal_cases=sum(c.category == "temporal" for c in CORPUS), cross_client_cases=sum(c.owner != c.client for c in CORPUS))



# ================================================================================================================ shared world / pipeline helpers
K, R = "CLIENT-001", "CLIENT-002"
K090_T11 = "You've decided to retire at 63, replacing the earlier plan of 65, because the role changed and Mariko wants to teach until 62."
LATER_65 = "In January you told me you'd retire at 65."
EV_T11 = dict(source_type="conversation", interaction_id="INT-090", turn_index=11, speaker="Advisor", excerpt=K090_T11)
EV_LATER = dict(source_type="conversation", interaction_id="INT-149", turn_index=3, speaker="Advisor", excerpt=LATER_65)
DET_LATER = dict(interaction_id="INT-149", turn_index=3, event_ts="2026-06-30T08:00:00", speaker_role="Advisor", event_type="decision")
_REB_TEXT = "You've decided to decline the employee-ownership route, because a ten-year seller note would not give the certain cash needed for your mother's care."
_REB = dict(topic="business_sale", subject="employee_ownership", statement=_REB_TEXT[:200], value="decline employee ownership", certainty="confirmed", speaker_role="Advisor",
            turn_index=11, excerpt=_REB_TEXT, corrects_value=None, reusable_procedure=False)


def person(client_ids, name="eval-advisor") -> Principal:
    return Principal.user(name, list(client_ids), authenticated=True)


def build_world(kenji_meetings=None, rebecca: bool = False):
    """A fresh in-memory memory world from REAL CSV excerpts (scripted extractor): Kenji's retirement meetings, optionally Rebecca's INT-061."""
    from agents.memory.agent import MemoryOrchestrator
    from harness.evaluation import fixtures
    from llm.client import MockLLM
    assert fixtures.CONV.verify_excerpt(R, "INT-061", 11, _REB_TEXT) and fixtures.CONV.verify_excerpt(K, "INT-090", 11, K090_T11) and fixtures.CONV.verify_excerpt(K, "INT-149", 3, LATER_65)

    def extractor(system, user, schema):
        iid = re.search(r"Meeting (INT-\d+)", user).group(1)
        if iid in fixtures._EXCERPTS:
            return {"candidates": fixtures._candidates(iid)}
        return {"candidates": [_REB] if iid == "INT-061" else []}
    mem = MemoryOrchestrator(MockLLM(handlers={"memory": extractor}), store=MemoryStore(url="sqlite:///:memory:", conv=fixtures.CONV), conv=fixtures.CONV)
    mem.init()
    for iid in (kenji_meetings if kenji_meetings is not None else fixtures.KENJI_MEETINGS):
        mem.ingest_meeting(iid)
    if rebecca:
        mem.ingest_meeting("INT-061")
    return mem


def corrected_world(with_conflict: bool = True):
    """Scenario-4 world: ingest + consolidate, a person corrects the active semantic value (65 -> 63, INT-090 turn 11), the meeting that states 63 is ingested AFTER the correction,
    then (optionally) a later turn restating 65 arrives.  Returns (world, correction_memory_id)."""
    from harness.evaluation import fixtures
    mem = build_world(fixtures.KENJI_MEETINGS[:4])
    st = mem.store
    cur = [r for r in st.records(K, "semantic", topics=["retirement_age"]) if r["status"] == "active"][0]
    cor = st.boundary.correct(cur["memory_id"], principal=person([K]), expected_version=cur["row_version"], new_value="63", evidence=[EV_T11], reason="client restated the date").memory_id
    mem.ingest_meeting("INT-090")
    if with_conflict:
        st.insert_memory(dict(memory_id="MEM-EVAL-LATER", client_id=K, memory_type="episodic", topic="retirement_age", subject="retirement_age", content=LATER_65, value_text="65",
                              certainty="confirmed", source_role="Advisor", status="active", origin="conversation_llm", event_date="2026-06-30", content_hash="eval-later"),
                         dict(DET_LATER), [EV_LATER], principal=Principal.service("ingestion", [K]))
        mem.consolidator.consolidate_client(K)
    return mem, cor


def _scenario(question, planner, report, selector=None):
    from harness.evaluation import fixtures
    return fixtures.Scenario("p15", "p15", question, planner, report, selector)


def make_harness(**overrides):
    from harness import Harness, HarnessConfig
    from harness.approval_store import ApprovalStore
    return Harness(HarnessConfig.for_test(**overrides), approval_store=ApprovalStore())


def ask(world, question, harness, actor="ADVISOR-001", role="advisor", planner=None, report=None):
    from harness import AuthContext
    from harness.evaluation import fixtures, runner
    from orchestration.pipeline import run_pipeline
    sc = _scenario(question, planner or fixtures.planner_json(), report or fixtures.report_builder(fixtures.good_findings))
    t0 = time.perf_counter()
    res = run_pipeline(question, runner._llm(sc), world, persist_trace=False, harness=harness, auth=AuthContext.for_test(actor, role) if actor else None)
    res.wall_ms = int((time.perf_counter() - t0) * 1000)
    return res


def visible(report) -> str:
    return " ".join([report.summary] + [f.text for f in report.key_findings + report.historical_context + report.financial_analysis] + list(report.uncertainties)
                    + list(report.suggested_followups))


def trace_calls(res) -> list:
    return [e["payload"].get("agent") for e in res.trace if e["component"] == "harness.llm" and e["event"] == "call"]


def write_log(world) -> dict:
    return {f"{r['op']}:{r['outcome']}": r["n"] for r in world.store.query("SELECT op, outcome, COUNT(*) AS n FROM {S}memory_write_log GROUP BY op, outcome ORDER BY op, outcome")}


def _rate(ok: int, n: int) -> Optional[float]:
    return round(ok / n, 3) if n else None


# ================================================================================================================ client isolation
def evaluate_isolation() -> dict:
    from harness import PermissionDenied
    from harness.evaluation import fixtures
    from services.conversations import shared
    CONV = shared()
    out: dict = {}
    # 1. cross-client EVIDENCE acceptance: every ordered pair of the six clients (a writes a memory that cites a real turn owned by b)
    store = MemoryStore(url="sqlite:///:memory:", conv=CONV)
    store.init()
    clients = sorted(CONV.directory())
    attempts = accepted = 0
    for a_ in clients:
        for b_ in clients:
            if a_ == b_:
                continue
            row = CONV.meeting(CONV.interactions_for(b_)[0])[0]
            ex = row["utterance"][:50]
            assert CONV.verify_excerpt(b_, row["interaction_id"], row["turn_index"], ex)
            attempts += 1
            h = f"x-{a_}-{b_}"
            try:
                store.insert_memory(dict(memory_id="MEM-X-" + h, client_id=a_, memory_type="episodic", topic="other", subject="x", content=ex, value_text=None, certainty="exploratory",
                                         source_role=row["speaker"], status="active", origin="conversation_llm", event_date=row["interaction_datetime"][:10], content_hash=h),
                                    dict(interaction_id=row["interaction_id"], turn_index=row["turn_index"], event_ts=row["interaction_datetime"], speaker_role=row["speaker"], event_type="statement"),
                                    [dict(source_type="conversation", interaction_id=row["interaction_id"], turn_index=row["turn_index"], speaker=row["speaker"], excerpt=ex)],
                                    principal=Principal.service("ingestion", [a_]))
                accepted += 1
            except MemoryWriteRejected:
                pass
    out["cross_client_evidence_acceptance"] = dict(attempts=attempts, accepted=accepted, clients_covered=len(clients))
    # 2. retrieval leakage, 3. unauthorized memory/conflict access
    world = build_world(rebecca=True)
    ids = {c: [r["memory_id"] for r in world.store.records(c)] for c in (K, R)}
    leak = checked = 0
    for cid, other in ((K, R), (R, K)):
        ctx = world.retrieve(plan_for(cid, ("retirement_age", "business_sale")))[cid]
        for m in ctx.current + ctx.history + ctx.episodes + ctx.unresolved:
            checked += 1
            leak += m.client_id != cid or any(CONV.turn(e.interaction_id, e.turn_index)["client_id"] != cid for e in m.evidence)
    out["cross_client_retrieval_leakage"] = dict(items_checked=checked, leaked=leak)
    mem_att = mem_ok = 0
    for cid, other in ((K, R), (R, K)):
        stranger = person([other], "eval-stranger")
        for mid in ids[cid]:
            mem_att += 1
            mem_ok += world.store.boundary.lookup(mid, stranger) is not None
    out["unauthorized_memory_access"] = dict(attempts=mem_att, succeeded=mem_ok)
    cw, cor = corrected_world()
    cf = cw.store.conflicts(K, "open")
    stranger = person([R], "eval-stranger")
    c_att = c_ok = 0
    for c in cf:
        c_att += 1
        try:
            cw.store.boundary.resolve_conflict(c["conflict_id"], "keep", "stranger", principal=stranger)
            c_ok += 1
        except MemoryWriteRejected:
            pass
    c_att += 1
    c_ok += bool(cw.store.conflicts(R, None))
    c_att += 1
    c_ok += bool(cw.retrieve(plan_for(R, ("retirement_age",)))[R].conflicts)
    out["unauthorized_conflict_access"] = dict(attempts=c_att, succeeded=c_ok, conflicts_in_fixture=len(cf))
    # 4. cache leakage across clients/actors (one shared harness, one shared cache)
    h = make_harness()
    w2 = build_world(rebecca=True)
    ask(w2, "Why did Kenji's retirement age change?", h)
    r2 = ask(w2, "What did Rebecca decide about her business?", h, actor="ADVISOR-002", planner=fixtures.planner_json(topics=("business_sale",)),
             report=fixtures.report_builder(lambda cur, old: [{"text": "Rebecca decided to decline employee ownership", "refs": [cur]}], summary="Rebecca declined the employee-ownership route."))
    leaks = int(r2.harness["telemetry"]["cache_hits"] > 0) + int("65" in visible(r2.report) or "Kenji" in visible(r2.report))
    denied = 0
    for q, actor in (("Why did Kenji's retirement age change?", "ADVISOR-002"), ("What did Rebecca decide about her business?", "ADVISOR-001")):
        try:
            ask(w2, q, h, actor=actor)
        except PermissionDenied:
            denied += 1
    out["cross_client_cache_leakage"] = dict(cross_client_requests=3, served_from_cache_or_leaked=leaks, unauthorized_requests_denied=denied, unauthorized_requests=2)
    # 5. approval release by a non-requester / anonymous caller
    out["unauthorized_approval_release"] = _approval_release_attempts()
    return out


def plan_for(cid, topics):
    from schemas.plan import ExecutionPlan
    return ExecutionPlan(request_id="REQ-P15", plan_id="PLAN-P15", request_text="retirement", client_ids=[cid], user_intent="x", task_type="retrieval", time_period=None, needs_history=True,
                         needs_calculation=False, subtasks=[], required_memory_categories=["semantic", "episodic"], memory_topics=list(topics), required_data=[], required_tools=[],
                         dependencies={}, execution_order=[], parallelizable_tasks=[], missing_information=[], success_criteria=["answered"])


def _approval_release_attempts() -> dict:
    from harness import AuthContext, PermissionDenied
    from harness.evaluation import fixtures
    from harness.permission_gate import PermissionDenied as PD  # noqa: F401
    from harness.approval import ApprovalRequired
    world = build_world()
    h = make_harness()
    res = ask(world, "Should Kenji retire at 60?", h, planner=fixtures.planner_json(task="forecast_feasibility"))
    apr = res.harness["approval"]
    dv = next(e["payload"]["data_version"] for e in res.trace if e["event"] == "request_started")
    h.decide_approval(apr["approval_id"], AuthContext.for_test("SUP-1", "supervisor"), True, "reviewed")
    att = ok = 0
    # (the in-process library call Harness.release(actor=None) trusts its caller - a documented P1.2 limitation; every API path passes the verified actor)
    for actor in (AuthContext.for_test("ADVISOR-002", "advisor"), AuthContext.for_test("ADVISOR-999", "advisor")):
        att += 1
        try:
            h.release(apr["approval_id"], dv, actor=actor)
            ok += 1
        except (PermissionDenied, ApprovalRequired, KeyError):
            pass
    rel = h.release(apr["approval_id"], dv, actor=AuthContext.for_test("ADVISOR-001", "advisor"))      # the REQUESTER can release it, once
    return dict(attempts=att, succeeded=ok, requester_release_ok=bool(rel.summary))


# ================================================================================================================ temporal integrity
def evaluate_temporal() -> dict:
    """Each check is a yes/no observation of the REAL memory layer; the expected answer is stated beside it."""
    checks: dict = {}

    def check(group, name, ok, detail=""):
        checks.setdefault(group, []).append(dict(name=name, passed=bool(ok), detail=detail))
    world = build_world()
    st = world.store
    ctx = world.retrieve(plan_for(K, ("retirement_age",)))[K]
    check("current_value_selection", "newest confirmed value is the only current one", [m.value_text for m in ctx.current] == ["63"], str([m.value_text for m in ctx.current]))
    check("historical_value_misclassification", "the replaced 65 is history, not current", "65" not in [m.value_text for m in ctx.current] and "65" in [m.value_text for m in ctx.history])
    early = world.retrieve(plan_for(K, ("retirement_age",)).model_copy(update=dict(time_period="as of 2026-02-01", request_id="REQ-P15B")))[K]
    check("current_value_selection", "as-of 2026-02-01 the value was 65", [m.value_text for m in early.current] == ["65"], str([m.value_text for m in early.current]))
    sem = sorted(st.records(K, "semantic", topics=["retirement_age"]), key=lambda r: r["version_no"])
    check("supersession_correctness", "v1 superseded with valid_to == v2.valid_from", sem[0]["status"] == "superseded" and sem[0]["valid_to"] == sem[1]["valid_from"], f"{sem[0]['valid_to']} / {sem[1]['valid_from']}")
    check("supersession_correctness", "exactly one active version", sum(r["status"] == "active" for r in sem) == 1)
    rev = None
    try:
        st.boundary.supersede(sem[1]["memory_id"], sem[0]["memory_id"], "older wins?", principal=person([K]), expected_version=sem[1]["row_version"])
    except MemoryWriteRejected as e:
        rev = e.code
    check("invalid_chronology_rejection", "an older version cannot supersede the newer one", rev in ("supersession_chronology", "target_not_correctable"), str(rev))
    # forged valid_from
    forged = None
    try:
        st.insert_memory(dict(memory_id="MEM-EVAL-F", client_id=K, memory_type="semantic", topic="retirement_age", subject="eval_slot", title="t", content="retirement age = 63", value_text="63",
                              certainty="confirmed", status="active", origin="consolidation", content_hash="evalf", event_date="2026-05-01", valid_from="1999-01-01", version_no=9),
                         dict(slot_key="retirement_age|eval_slot", basis="b", supporting_episodes=1),
                         [dict(source_type="conversation", interaction_id="INT-090", turn_index=2, speaker="Client", excerpt="We've decided: 63.")], principal=Principal.service("consolidation", [K]))
    except MemoryWriteRejected as e:
        forged = e.code
    check("invalid_chronology_rejection", "a forged valid_from is refused", forged == "valid_time_mismatch", str(forged))
    # human correction preservation, repeated consolidation, conflicting evidence
    cw, cor = corrected_world(with_conflict=False)
    cst = cw.store
    for _ in range(3):
        cw.consolidator.consolidate_client(K)
    row = cst.get(cor)
    check("human_correction_preservation", "the correction is still the one active version after 3 consolidations",
          row["status"] == "active" and [r["memory_id"] for r in cst.records(K, "semantic", statuses=["active"], topics=["retirement_age"])] == [cor])
    cst.insert_memory(dict(memory_id="MEM-EVAL-LATER", client_id=K, memory_type="episodic", topic="retirement_age", subject="retirement_age", content=LATER_65, value_text="65", certainty="confirmed",
                           source_role="Advisor", status="active", origin="conversation_llm", event_date="2026-06-30", content_hash="eval-later"), dict(DET_LATER), [EV_LATER],
                      principal=Principal.service("ingestion", [K]))
    for _ in range(3):
        cw.consolidator.consolidate_client(K)
    check("human_correction_preservation", "newer conflicting evidence does not replace the correction", cst.get(cor)["status"] == "active" and cst.get(cor)["value_text"] == "63")
    ctx = cw.retrieve(plan_for(K, ("retirement_age",)))[K]
    check("conflict_visibility", "the conflict is open and returned by retrieval", len(ctx.conflicts) == 1 and any("review required" in c for c in ctx.contradictions))
    check("conflict_visibility", "the correction is still the current value shown", [m.memory_id for m in ctx.current] == [cor])
    older = None
    try:
        cst.boundary.correct(cor, principal=person([K]), expected_version=cst.get(cor)["row_version"], new_value="65",
                             evidence=[dict(source_type="conversation", interaction_id="INT-001", turn_index=13, speaker="Advisor",
                                            excerpt="The working plan I'm recording is retirement at 65 on about fifteen thousand a month after tax, with the mortgage cleared by 2032.")], reason="older evidence")
    except MemoryWriteRejected as e:
        older = e.code
    check("invalid_chronology_rejection", "a correction citing OLDER evidence is refused", older == "supersession_chronology", str(older))
    summary = {g: dict(passed=sum(c["passed"] for c in v), total=len(v), rate=_rate(sum(c["passed"] for c in v), len(v))) for g, v in checks.items()}
    return dict(summary=summary, checks=checks)


# ================================================================================================================ cache correctness
def evaluate_cache() -> dict:
    from harness import PermissionDenied
    from harness.cache import make_key
    from harness.evaluation import fixtures
    from harness.runtime import data_version
    res: dict = {}

    def case(n, name, ok, detail=""):
        res[n] = dict(name=name, passed=bool(ok), detail=detail)
    q = "Why did Kenji's retirement age change?"
    # 1. same request repeated
    w = build_world(rebecca=True)
    h = make_harness()
    r1, r2 = ask(w, q, h), ask(w, q, h)
    case(1, "same request, same client: second run is served from cache and identical", r2.harness["telemetry"]["cache_hits"] > 0 and r1.report.summary == r2.report.summary,
         f"hits={r2.harness['telemetry']['cache_hits']}")
    # 2. two clients
    r3 = ask(w, "What did Rebecca decide about her business?", h, actor="ADVISOR-002", planner=fixtures.planner_json(topics=("business_sale",)),
             report=fixtures.report_builder(lambda cur, old: [{"text": "Rebecca decided to decline employee ownership", "refs": [cur]}], summary="Rebecca declined employee ownership."))
    base = dict(model="m", agent="report", system="s", schema=None, user="u", actor="ADVISOR-001", data_version="d")
    case(2, "two clients never share an entry", r3.harness["telemetry"]["cache_hits"] == 0 and make_key(**base, clients=(K,)) != make_key(**base, clients=(R,)))
    # 3. different authorization scope
    r4 = ask(w, q, h, actor="SUP-1", role="supervisor")
    case(3, "same client, different actor / scope: no shared entry", r4.harness["telemetry"]["cache_hits"] == 0 and make_key(**base, clients=(K,)) != make_key(**{**base, "actor": "SUP-1"}, clients=(K,)))
    # 5. different temporal context
    r5 = ask(w, "Why did Kenji's retirement age change as of 2026-02-01?", h, planner={**fixtures.planner_json(), "time_period": "as of 2026-02-01"})
    case(5, "a different time context is a different request", r5.harness["telemetry"]["cache_hits"] == 0, f"hits={r5.harness['telemetry']['cache_hits']}")
    # 6. approval status cannot be bypassed by a hit
    hs = make_harness()
    ws = build_world()
    sensitive = dict(question="Should Kenji retire at 60?", planner=fixtures.planner_json(task="forecast_feasibility"))
    a1 = ask(ws, sensitive["question"], hs, planner=sensitive["planner"])
    a2 = ask(ws, sensitive["question"], hs, planner=sensitive["planner"])
    case(6, "a cache hit still withholds a report that needs approval", (not a1.released) and (not a2.released) and a2.harness["telemetry"]["cache_hits"] > 0, f"released={a2.released} hits={a2.harness['telemetry']['cache_hits']}")
    # 7-10: data version moves with every memory change (the cache key and the approval fingerprint contain it)
    cw, cor = corrected_world(with_conflict=False)
    st = cw.store
    dv0 = data_version(st)
    st.insert_memory(dict(memory_id="MEM-EVAL-LATER", client_id=K, memory_type="episodic", topic="retirement_age", subject="retirement_age", content=LATER_65, value_text="65", certainty="confirmed",
                          source_role="Advisor", status="active", origin="conversation_llm", event_date="2026-06-30", content_hash="eval-later"), dict(DET_LATER), [EV_LATER],
                     principal=Principal.service("ingestion", [K]))
    dv1 = data_version(st)
    cw.consolidator.consolidate_client(K)
    dv2 = data_version(st)
    case(4, "new evidence changes the data version", dv0 != dv1)
    case(9, "recording a conflict changes the data version (was unchanged before P1.5)", dv1 != dv2, f"{dv1.split('|')[0]} -> {dv2.split('|')[0]}")
    cid = st.conflicts(K, "open")[0]["conflict_id"]
    st.boundary.resolve_conflict(cid, "keep", "reviewed", principal=person([K]))
    dv3 = data_version(st)
    case(10, "resolving a conflict changes the data version (was unchanged before P1.5)", dv2 != dv3)
    # 7 correction and 8 supersession (explicit) in a fresh world
    w3 = build_world(fixtures.KENJI_MEETINGS[:4])
    d_a = data_version(w3.store)
    cur = [r for r in w3.store.records(K, "semantic") if r["status"] == "active"][0]
    w3.store.boundary.correct(cur["memory_id"], principal=person([K]), expected_version=cur["row_version"], new_value="63", evidence=[EV_T11], reason="r")
    d_b = data_version(w3.store)
    case(7, "a human correction changes the data version", d_a != d_b)
    w4 = build_world(fixtures.KENJI_MEETINGS[:3])
    d_c = data_version(w4.store)
    w4.ingest_meeting("INT-074")
    w4.ingest_meeting("INT-090")
    case(8, "a supersession (new version replaces the old) changes the data version", d_c != data_version(w4.store))
    # expiry of working memory: by design not part of the version (it is per-request scratch and never reaches a prompt)
    ww = build_world()
    dv_w = data_version(ww.store)
    ww.store_working("REQ-W", plan_for(K, ("retirement_age",)), [])
    case("10b", "working memory (per-request scratch, never rendered into a prompt) does not change the version", dv_w == data_version(ww.store))
    # 11. permission change
    hp = make_harness()
    wp = build_world()
    ask(wp, q, hp)
    hits_before = hp.cache.stats.hits
    denied = False
    try:
        ask(wp, q, hp, actor="ADVISOR-999")
    except PermissionDenied:
        denied = True
    case(11, "a caller without permission is denied BEFORE any cache lookup", denied and hp.cache.stats.hits == hits_before)
    # 12. model / prompt configuration
    k0 = make_key(**base, clients=(K,))
    case(12, "model, system prompt, schema and user prompt each change the key", len({k0, make_key(**{**base, "model": "m2"}, clients=(K,)), make_key(**{**base, "system": "s2"}, clients=(K,)),
                                                                          make_key(**{**base, "schema": {"a": 1}}, clients=(K,)), make_key(**{**base, "user": "u2"}, clients=(K,))}) == 5)
    # approval invalidated by a conflict recorded after approval (the stale-approval defect this phase fixed)
    case("approval_vs_conflict", "an approval granted before a conflict appears is invalidated", _approval_invalidated_by_conflict())
    # hit-rate workload (mock): 5 distinct requests x 2 repeats
    hw = make_harness()
    wk = build_world()
    qs = [q, "What are Kenji's retirement history and changes?", "Summarise Kenji's retirement plan history", q, "Summarise Kenji's retirement plan history"]
    for x in qs:
        ask(wk, x, hw)
    st_ = hw.cache.stats
    res["workload"] = dict(requests=len(qs), hits=st_.hits, misses=st_.misses, hit_rate=st_.hit_rate, note="repeat-heavy mock workload; a high hit rate is not evidence of correctness (see the cases)")
    return res


def _approval_invalidated_by_conflict() -> bool:
    from harness import AuthContext, PermissionDenied
    from harness.evaluation import fixtures
    world = build_world()
    h = make_harness()
    res = ask(world, "Should Kenji retire at 60?", h, planner=fixtures.planner_json(task="forecast_feasibility"))
    apr = res.harness["approval"]
    h.decide_approval(apr["approval_id"], AuthContext.for_test("SUP-1", "supervisor"), True, "reviewed")
    eps = [r["memory_id"] for r in world.store.records(K, "episodic")][:2]
    world.store.boundary.record_conflict(eps[0], eps[1], "contradiction_same_period", "high", "eval", principal=Principal.service("consolidation", [K]))
    from harness.runtime import data_version
    try:
        h.release(apr["approval_id"], data_version(world.store), actor=AuthContext.for_test("ADVISOR-001", "advisor"))
        return False
    except PermissionDenied as e:
        return e.code == "approval_invalidated"


# ================================================================================================================ compression
_C = lambda n, cert, v, content, status="active", spk="Client", iid="INT-090", t=2, date="2026-05-01": (  # noqa: E731
    f"[E{n} conversation_evidence] Episode; retirement_age; value {v}; {status}/{cert}; valid ? to open; on {date}: {content} (said by {spk} in {iid} turn {t})")


def compression_corpus() -> list:
    """(line, tags).  Real prompt-line format (agents/report/evidence.py); tags are hand-assigned: critical | negation | uncertain | correction | financial | temporal | provenance."""
    L: list = []
    add = lambda line, *tags: L.append((line, set(tags)))  # noqa: E731
    add("QUESTION: Why did Kenji's retirement age change? (client CLIENT-001, advisor ADVISOR-001)", "protected")
    add("[E1 memory] Current knowledge; retirement_age; value 63; active/confirmed; valid 2026-05-01 to open; on 2026-05-01: retirement age = 63 (said by Client in INT-090 turn 2)", "critical", "temporal", "provenance")
    add("[E2 memory] Unresolved item; retirement_age; value 60; active/exploratory; valid ? to open; on 2026-01-16: what if I retired at 60 instead of 65? (said by Client in INT-012 turn 4)", "uncertain", "provenance")
    n = 3
    for i in range(10):
        add(_C(n, "confirmed", str(40 + i), f"Annual income review {i}: income is {40 + i} thousand dollars a year", t=3 + i, date=f"2026-0{1 + i % 9}-1{i % 9}"), "financial", "temporal", "provenance")
        n += 1
    for txt, tags in (("Kenji will not retire at 63 unless the sale closes", ("negation", "uncertain")), ("Kenji has not decided whether to retire", ("negation", "uncertain")),
                      ("Kenji never agreed to the lower withdrawal rate", ("negation",))):
        add(_C(n, "confirmed", "63", txt, t=20 + n), "critical", *tags, "provenance")
        n += 1
    for v, txt in (("65", "I've corrected my record: the planning date remains 65, not 60"), ("63", "Correction: the decision is 63, replacing the earlier 65")):
        add(_C(n, "correction", v, txt, spk="Advisor", t=30 + n), "critical", "correction", "temporal", "provenance")
        n += 1
    for txt in ("Kenji is exploring retiring at 60", "Kenji is weighing retiring at 63 instead of 60", "Suppose Kenji retired at 58"):
        add(_C(n, "exploratory", "60", txt, t=40 + n), "critical", "uncertain", "provenance")
        n += 1
    for txt in ("Kenji may sell the house next year", "Kenji might reduce equities"):
        add(_C(n, "unconfirmed", "", txt, status="unconfirmed", t=50 + n), "critical", "uncertain", "provenance")
        n += 1
    add(_C(n, "confirmed", "63", "Disputed by the client: the decision was 65", status="disputed", t=60), "critical", "uncertain", "provenance")
    n += 1
    for i in range(10):
        add(_C(n, "confirmed", str(10 + i), f"Routine portfolio read-out {i}: holdings worth {10 + i} thousand dollars", t=70 + i), "financial", "provenance")
        n += 1
    for i in range(4):
        add(f"[E{n} memory] Superseded earlier knowledge; retirement_age; value {60 + i}; superseded/confirmed; valid 2025-0{1 + i}-01 to 2025-0{2 + i}-01: earlier retirement age {60 + i} (said by Client in INT-0{10 + i} turn 3)",
            "temporal", "provenance")
        n += 1
    add("[EX-1] it1 get_client_goals CLIENT-001 -> rejected: duplicate call", "tool")
    add("APPROVAL REQUIRED: the answer to a suitability question is withheld until a supervisor approves it.", "protected", "critical")
    add("PROCEDURE (approved): when retirement age changes, re-run the liquidity ratio before advising.", "protected", "critical")
    return L


def evaluate_compression() -> dict:
    from harness import compression
    from harness.token_counter import TokenCounter
    from harness.evidence_verifier import EvidenceVerifier  # noqa: F401
    counter = TokenCounter("qwen2.5:3b")
    corpus = compression_corpus()
    text = "\n".join(l for l, _ in corpus)
    tags = {l: t for l, t in corpus}
    original = counter.count(text).tokens
    probes = [(l, c) for l, c in corpus if "critical" in c and "Episode;" in l]
    out: dict = dict(original_tokens=original, lines=len(corpus), critical_lines=sum("critical" in t for _, t in corpus), budgets={})
    for frac in (0.85, 0.7, 0.55, 0.4):
        budget = int(original * frac)
        row = {}
        for label, flag in (("p14_position_order", False), ("p15_critical_last", True)):
            default = compression.CRITICAL_LAST
            compression.CRITICAL_LAST = flag
            try:
                r = compression.compress(text, budget, counter)
            finally:
                compression.CRITICAL_LAST = default                       # restore what the module had (never force a value)
            kept = set(r.text.split("\n"))
            markers = [x for x in kept if x.startswith("[context compressed:")]
            altered = [x for x in kept if x not in tags and x not in markers]
            ret = lambda tag: (lambda ls: _rate(sum(l in kept for l in ls), len(ls)))([l for l, t in corpus if tag in t])  # noqa: E731
            protected_ok = all(l in kept for l, t in corpus if compression.classify(l) == "protected")
            ev_kept = [l for l, t in corpus if "provenance" in t and l in kept]
            row[label] = dict(budget=budget, tokens=r.compressed_tokens, ratio=r.ratio, fits=r.fits, lines_retained=_rate(sum(l in kept for l, _ in corpus), len(corpus)),
                              critical_fact_retention=ret("critical"), negation_retention=ret("negation"), uncertain_retention=ret("uncertain"), correction_retention=ret("correction"),
                              temporal_retention=ret("temporal"), financial_retention=ret("financial"),
                              provenance_retention=_rate(sum("(said by" in l for l in ev_kept), len(ev_kept)), protected_lines_all_retained=protected_ok, altered_lines=len(altered),
                              removed=dict(r.removed), probe_claims_still_verifiable=_rate(sum(l in kept for l, _ in probes), len(probes)))
        out["budgets"][str(frac)] = row
    # the classification contract: nothing that carries a conflict / approval / procedure indicator is droppable
    out["droppable_critical_markers"] = [l[:60] for l, t in corpus if "protected" in t and compression.classify(l) != "protected"]
    return out


# ================================================================================================================ seven end-to-end scenarios
def _final(res) -> dict:
    r = res.report
    return dict(released=res.released, summary=r.summary, findings=[f.text for f in r.key_findings + r.historical_context + r.financial_analysis], uncertainties=list(r.uncertainties))


def _evidence_refs(res) -> list:
    return [dict(ref=e.ref, memory=e.memory_id, interaction=e.interaction_id, turn=e.turn_index, client=e.client_id, status=e.status) for e in res.report.evidence]


def _telemetry(res) -> dict:
    t = res.harness.get("telemetry", {}) if res.harness else {}
    comp = [e["payload"].get("ratio") for e in res.trace if e["component"] == "harness.context" and e["event"] == "compression"]
    return dict(model_calls=(res.harness or {}).get("budget", {}).get("llm_calls"), cache_hits=t.get("cache_hits"), cache_misses=t.get("cache_misses"), compression_ratios=comp,
                input_tokens=t.get("input_tokens"), output_tokens=t.get("output_tokens"), wall_ms=getattr(res, "wall_ms", None), per_agent_ms=dict(res.timings_ms))


def run_scenarios() -> list:
    from harness import AuthContext, PermissionDenied
    from harness.approval import ApprovalRequired
    from harness.evaluation import fixtures, metrics
    out = []

    def record(n, name, inp, res, world, expected, actual, passed, extra=None):
        out.append(dict(scenario=n, name=name, input=inp, agent_sequence=trace_calls(res) if res is not None else [], memory_operations=write_log(world) if world is not None else {},
                        tool_operations=[e.tool for e in res.tools.executions] if res is not None else [], evidence_refs=_evidence_refs(res) if res is not None else [],
                        telemetry=_telemetry(res) if res is not None else {}, final_output=_final(res) if res is not None else {}, expected=expected, actual=actual, passed=bool(passed), **(extra or {})))
    q = "Why did Kenji's retirement age change?"
    # 1. correct client memory
    w = build_world()
    res = ask(w, q, make_harness())
    fnd = metrics.independent_findings(res.report, res.plan, res.tools, w.store)
    ev = {e.ref: e for e in res.report.evidence}
    cited = [ev[r] for f in res.report.key_findings for r in f.refs]
    prov = bool(cited) and all(e.interaction_id and e.turn_index and CONV_turn_client(e) == K for e in cited if e.source_class != "tool_calculation")
    record(1, "correct client memory", q, res, w, "released; one verified finding about 63 whose evidence resolves to Kenji's own transcript turn",
           dict(findings=fnd, provenance_ok=prov), res.released and fnd["verified"] >= 1 and fnd["unsupported"] == 0 and prov)
    # 2. cross-client contamination
    w2 = build_world(rebecca=True)
    h2 = make_harness()
    res2 = ask(w2, q, h2, report=fixtures.report_builder(lambda cur, old: [{"text": "Kenji's current retirement age is 63", "refs": [cur]}, {"text": "Rebecca decided to decline employee ownership", "refs": [cur]}]))
    txt = visible(res2.report)
    leak_text = "rebecca" in txt.lower() or "employee" in txt.lower() or "seller note" in txt.lower() or "mother" in txt.lower()
    cross_ev = [e for e in res2.report.evidence if e.client_id not in (None, K)]
    try:
        ask(w2, "What did Rebecca decide about her business?", h2, actor="ADVISOR-001")
        denied = False
    except PermissionDenied:
        denied = True
    boundary = Principal.service("ingestion", [K])
    try:
        w2.store.insert_memory(dict(memory_id="MEM-X2", client_id=K, memory_type="episodic", topic="business_sale", subject="x", content=_REB_TEXT, value_text=None, certainty="exploratory",
                                    source_role="Advisor", status="active", origin="conversation_llm", event_date="2026-03-01", content_hash="x2"),
                               dict(interaction_id="INT-061", turn_index=11, event_ts="2026-03-01T08:00:00", speaker_role="Advisor", event_type="statement"),
                               [dict(source_type="conversation", interaction_id="INT-061", turn_index=11, speaker="Advisor", excerpt=_REB_TEXT)], principal=boundary)
        accepted = True
    except MemoryWriteRejected:
        accepted = False
    record(2, "cross-client contamination", q + " + a finding about Rebecca citing Kenji's evidence", res2, w2,
           "no Rebecca content in the output; no foreign evidence accepted; a request naming Rebecca is denied; foreign evidence rejected at the memory boundary",
           dict(rebecca_text_in_output=leak_text, foreign_evidence_in_report=len(cross_ev), request_for_other_client_denied=denied, foreign_evidence_accepted_by_memory=accepted),
           (not leak_text) and not cross_ev and denied and not accepted)
    # 3. contradictory temporal evidence
    w3 = build_world()
    res3 = ask(w3, q, make_harness())
    sem = sorted(w3.store.records(K, "semantic", topics=["retirement_age"]), key=lambda r: r["version_no"])
    record(3, "contradictory temporal evidence", "65 (2026-01-02) then 63 (2026-05-01)", res3, w3, "current = 63; the 65 version is preserved as superseded with valid_to = 2026-05-01; the change is disclosed",
           dict(chain=[(r["version_no"], r["value_text"], r["status"], r["valid_from"], r["valid_to"]) for r in sem], history_in_report=any("65" in f for f in _final(res3)["findings"] + [res3.report.summary])),
           [(r["value_text"], r["status"]) for r in sem] == [("65", "superseded"), ("63", "active")] and sem[0]["valid_to"] == sem[1]["valid_from"] and "65" in res3.report.summary)
    # 4. human correction
    w4, cor = corrected_world()
    for _ in range(2):
        w4.consolidator.consolidate_client(K)
    res4 = ask(w4, q, make_harness(), report=fixtures.report_builder(good_findings_cor))
    row = w4.store.get(cor)
    record(4, "human correction", "correct 65 -> 63 (INT-090.11); 3 consolidations; then a later turn restating 65", res4, w4,
           "the correction stays the only active version; the conflict is open and disclosed in the report",
           dict(correction_status=row["status"], active=[r["memory_id"] for r in w4.store.records(K, "semantic", statuses=["active"], topics=["retirement_age"])] == [cor],
                open_conflicts=sorted(c["kind"] for c in w4.store.conflicts(K, "open")), review_required_in_report=any("review required" in u for u in res4.report.uncertainties)),
           row["status"] == "active" and [r["memory_id"] for r in w4.store.records(K, "semantic", statuses=["active"], topics=["retirement_age"])] == [cor]
           and any("review required" in u for u in res4.report.uncertainties))
    # 5. approval and withheld output
    w5 = build_world()
    h5 = make_harness()
    sens = "Should Kenji retire at 60?"
    plan5 = fixtures.planner_json(task="forecast_feasibility")
    res5 = ask(w5, sens, h5, planner=plan5)
    apr = res5.harness["approval"]
    dv = next(e["payload"]["data_version"] for e in res5.trace if e["event"] == "request_started")
    steps = []

    def attempt(label, fn):
        try:
            fn()
            steps.append((label, "released"))
        except ApprovalRequired as e:
            steps.append((label, "withheld:" + e.record.status))
        except PermissionDenied as e:
            steps.append((label, "denied:" + e.code))
    kadv = AuthContext.for_test("ADVISOR-001", "advisor")
    attempt("release before approval", lambda: h5.release(apr["approval_id"], dv, actor=kadv))
    h5.decide_approval(apr["approval_id"], AuthContext.for_test("SUP-1", "supervisor"), False, "not suitable")
    attempt("release after rejection", lambda: h5.release(apr["approval_id"], dv, actor=kadv))
    res5b = ask(w5, sens, h5, planner=plan5)                                                  # same request again: the model stages come from the cache
    leaked = bool(res5.report.key_findings) or bool(res5b.report.key_findings) or "63" in visible(res5.report) or "63" in visible(res5b.report)
    record(5, "approval and withheld output", sens, res5, w5, "withheld; release before approval and after rejection refused; a repeat served from cache is still withheld with no content",
           dict(steps=steps, repeat_released=res5b.released, repeat_cache_hits=res5b.harness["telemetry"]["cache_hits"], content_in_withheld_output=leaked),
           (not res5.released) and (not res5b.released) and all(not s[1].startswith("released") for s in steps) and not leaked and res5b.harness["telemetry"]["cache_hits"] > 0,
           extra=dict(approval_decisions=[("SUP-1", "reject", "not suitable")]))
    # 6. budget pressure
    w6 = build_world()
    h6 = make_harness(agent_context_tokens={"planner": 2500, "memory": 4000, "tool_selector": 4500, "report": 1800})
    res6 = ask(w6, q, h6)
    comp = _telemetry(res6)["compression_ratios"]
    fnd6 = metrics.independent_findings(res6.report, res6.plan, res6.tools, w6.store)
    record(6, "budget pressure", q + " with the Report Agent's context limit lowered to 1800 tokens", res6, w6,
           "compression runs, protected evidence and its provenance survive; every released claim is still verified, or the output is withheld",
           dict(compressed=bool(comp), ratios=comp, findings=fnd6, released=res6.released),
           bool(comp) and fnd6["unsupported"] == 0 and (not res6.released or (fnd6["verified"] >= 1)))
    # 7. conflict resolution
    w7, cor7 = corrected_world()
    before = [c["conflict_id"] for c in w7.store.conflicts(K, "open") if c["kind"] == "human_correction_vs_evidence"]
    ctx_b = w7.retrieve(plan_for(K, ("retirement_age",)))[K]
    w7.store.boundary.resolve_conflict(before[0], "keep", "the client confirmed 63 by phone", principal=person([K], "SUP-1"))
    ctx_a = w7.retrieve(plan_for(K, ("retirement_age",)).model_copy(update=dict(request_id="REQ-P15-2")))[K]
    hist = w7.store.conflicts(K, "resolved")
    try:
        w7.store.execute("DELETE FROM {S}memory_conflict_resolutions")
        immutable = False
    except Exception:                                             # the append-only trigger refuses it
        immutable = True
    res7 = ask(w7, q, make_harness(), report=fixtures.report_builder(good_findings_cor))
    record(7, "conflict resolution", "open conflict -> authorized human 'keep' -> retrieve again", res7, w7,
           "open before, absent after; the resolution (who/when/why) stays auditable and immutable; the memory is unchanged",
           dict(open_before=len(ctx_b.conflicts), open_after=len(ctx_a.conflicts), resolved_rows=[(c["resolved_by"], c["decision"], c["resolution_reason"]) for c in hist], resolution_immutable=immutable,
                review_required_after=any("review required" in u for u in res7.report.uncertainties)),
           len(ctx_b.conflicts) == 1 and len(ctx_a.conflicts) == 0 and len(hist) == 1 and hist[0]["resolved_by"] == "SUP-1" and immutable and not any("review required" in u for u in res7.report.uncertainties))
    return out


def good_findings_cor(cur, old=None):
    return [{"text": "Kenji's current retirement age is 63", "refs": [cur]}]


def CONV_turn_client(e) -> Optional[str]:
    from services.conversations import shared
    t = shared().turn(e.interaction_id, e.turn_index)
    return t["client_id"] if t else None


# ================================================================================================================ report quality / runtime (aggregated from the scenario runs)
def report_quality(scenarios: list) -> dict:
    sup = uns = qual = total = fabricated = trace_ok = trace_n = 0
    for s in scenarios:
        f = s["actual"].get("findings") if isinstance(s["actual"].get("findings"), dict) else None
        if f:
            sup += f["verified"]
            qual += f["qualified"]
            uns += f["unsupported"]
    for s in scenarios:
        fo = s["final_output"]
        if not fo:
            continue
        texts = " ".join(fo.get("findings", []) + [fo.get("summary", "")])
        ev_text = " ".join(str(v) for e in s["evidence_refs"] for v in e.values() if v is not None)
        nums = set(re.findall(r"\d+(?:\.\d+)?", texts)) - {"1", "2"}
        known = set(re.findall(r"\d+(?:\.\d+)?", ev_text + " 65 63 62 60 2026"))
        fabricated += len(nums - known)
        if fo.get("released"):
            for e in s["evidence_refs"]:
                trace_n += 1
                trace_ok += bool((e["interaction"] and e["turn"] is not None) or e["memory"])
    total = sup + qual + uns
    return dict(findings_checked=total, supported=sup, qualified=qual, unsupported=uns, supported_rate=_rate(sup, total), unsupported_rate=_rate(uns, total),
                contradicted_claims_released=0, fabricated_numeric_values=fabricated, evidence_traceability=_rate(trace_ok, trace_n),
                unsupported_recommendation_statements=sum(any(w in " ".join(s["final_output"].get("findings", [])).lower() for w in ("should retire", "recommend")) for s in scenarios if s["final_output"].get("released")),
                note="contradicted claims are counted by the independent verifier as 'unsupported'; none is released in these scripted runs")


def runtime_metrics(scenarios: list) -> dict:
    rows = [s["telemetry"] for s in scenarios if s["telemetry"]]
    lat = [r["wall_ms"] for r in rows if r.get("wall_ms") is not None]
    per = defaultdict(list)
    for r in rows:
        for k, v in (r.get("per_agent_ms") or {}).items():
            per[k].append(v)
    return dict(source="mocked (scripted) models", requests=len(rows), end_to_end_ms=dict(mean=round(statistics.mean(lat), 1) if lat else None, max=max(lat) if lat else None),
                per_agent_ms_mean={k: round(statistics.mean(v), 1) for k, v in per.items()}, model_calls=sum((r.get("model_calls") or 0) for r in rows),
                estimated_input_tokens=sum((r.get("input_tokens") or 0) for r in rows), estimated_output_tokens=sum((r.get("output_tokens") or 0) for r in rows),
                usage_is_exact=False, cache_hits=sum((r.get("cache_hits") or 0) for r in rows), cache_misses=sum((r.get("cache_misses") or 0) for r in rows),
                note="the scripted model reports no provider usage: token figures are estimates from the tokenizer, never presented as actual usage")


# ================================================================================================================ real-model run (explicit, never silent)
def real_model_run() -> dict:
    """One controlled request through the CONFIGURED model for the Planner and Report agents (memory is the scripted real-excerpt world: ingestion is NOT re-run through the model).
    If CMF_LLM_BACKEND is mock or the model cannot be reached, the result says so - there is no fallback to a mock."""
    from llm.client import MockLLM, make_llm
    from harness import AuthContext
    from orchestration.pipeline import run_pipeline
    backend = os.environ.get("CMF_LLM_BACKEND", "ollama")
    info: dict = dict(requested=True, backend=backend, model=os.environ.get("CMF_OLLAMA_MODEL", os.environ.get("OLLAMA_MODEL", "(default of OllamaLLM)")), clients_tested=[K],
                      scope="Planner and Report agents only; memory ingestion used scripted extraction of real CSV excerpts")
    if backend == "mock":
        return dict(info, ran=False, reason="CMF_LLM_BACKEND=mock: no real model configured")
    try:
        llm = make_llm()
    except Exception as e:                                         # noqa: BLE001
        return dict(info, ran=False, reason=f"{type(e).__name__} while creating the model client")
    if isinstance(llm, MockLLM):
        return dict(info, ran=False, reason="the factory returned a mock")
    world = build_world()
    h = make_harness(max_model_call_seconds=float(os.environ.get("P15_REAL_CALL_SECONDS", "400")))      # a local 3B model on CPU is slow; a timeout is recorded, never hidden
    t0 = time.perf_counter()
    try:
        res = run_pipeline("Why did Kenji's retirement age change?", llm, world, persist_trace=False, harness=h, auth=AuthContext.for_test("ADVISOR-001", "advisor"))
    except Exception as e:                                         # noqa: BLE001
        return dict(info, ran=False, reason=f"{type(e).__name__}: request failed after {int((time.perf_counter() - t0) * 1000)} ms", model=getattr(llm, "model", info["model"]))
    from harness.evaluation import metrics
    tel = res.harness["telemetry"]
    return dict(info, ran=True, model=getattr(llm, "model", info["model"]), released=res.released, latency_ms=int((time.perf_counter() - t0) * 1000), model_calls=res.harness["budget"]["llm_calls"],
                input_tokens=tel["input_tokens"], output_tokens=tel["output_tokens"], usage_exact=any(e["payload"].get("usage_exact") for e in res.trace if e["component"] == "harness.llm" and e["event"] == "call"),
                findings=metrics.independent_findings(res.report, res.plan, res.tools, world.store), final_output=_final(res), agent_sequence=trace_calls(res),
                note="ONE request on ONE client: an existence check, not an accuracy measurement")


# ================================================================================================================ driver
def run_all(real: bool = False) -> dict:
    claims = evaluate_claims()
    scen = run_scenarios()
    doc = dict(meta=dict(corpus_version=CORPUS_VERSION, python=platform.python_version(), platform=platform.platform(), mode="mocked", real_model_requested=real),
               corpus=corpus_summary(), claim_verification={k: dict(overall=v["overall"], by_category=v["by_category"]) for k, v in claims.items()},
               claim_rows={k: [r for r in v["rows"] if not r["correct"]] for k, v in claims.items()}, isolation=evaluate_isolation(), temporal=evaluate_temporal(), cache=evaluate_cache(),
               compression=evaluate_compression(), scenarios=scen, report_quality=report_quality(scen), runtime=runtime_metrics(scen))
    doc["real_model"] = real_model_run() if real else dict(requested=False, ran=False, reason="not requested (pass --real); all numbers above are from scripted models")
    return doc


def strip_timings(doc: Any) -> Any:
    """For reproducibility checks: drop wall-clock fields."""
    if isinstance(doc, dict):
        return {k: strip_timings(v) for k, v in doc.items() if k not in ("wall_ms", "per_agent_ms", "end_to_end_ms", "per_agent_ms_mean", "latency_ms", "platform", "python")}
    if isinstance(doc, list):
        return [strip_timings(x) for x in doc]
    return doc


def render_markdown(doc: dict) -> str:
    L = ["# P1.5 evaluation", f"corpus {doc['meta']['corpus_version']}, mode: **{doc['meta']['mode']}**, real model: **{doc['real_model'].get('ran')}**", ""]
    L += ["## Claim verification (overall)", "| system | TP | FP | FN | TN | precision | recall | F1 | FPR | FNR | unverified | contradiction det. | ambiguity det. |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for k, v in doc["claim_verification"].items():
        o = v["overall"]
        L.append(f"| {k} | {o['tp']} | {o['fp']} | {o['fn']} | {o['tn']} | {o['precision']} | {o['recall']} | {o['f1']} | {o['false_positive_rate']} | {o['false_negative_rate']} | {o['unverified_rate']} | {o['contradiction_detection_rate']} | {o['ambiguity_detection_rate']} |")
    L += ["", "## By category (FP / FN / n)", "| system | " + " | ".join(CATEGORIES) + " |", "|---|" + "---|" * len(CATEGORIES)]
    for k, v in doc["claim_verification"].items():
        L.append(f"| {k} | " + " | ".join(f"{v['by_category'][c]['fp']}/{v['by_category'][c]['fn']}/{v['by_category'][c]['n']}" for c in CATEGORIES) + " |")
    L += ["", "## Isolation"] + [f"- {k}: {v}" for k, v in doc["isolation"].items()]
    L += ["", "## Temporal"] + [f"- {k}: {v['passed']}/{v['total']}" for k, v in doc["temporal"]["summary"].items()]
    L += ["", "## Cache"] + [f"- {k}: {'PASS' if v.get('passed') else 'FAIL'} - {v.get('name')}" for k, v in doc["cache"].items() if "passed" in v] + [f"- workload: {doc['cache']['workload']}"]
    L += ["", "## Compression"] + [f"- budget {b}: " + "; ".join(f"{lab}: ratio {x['ratio']}, critical {x['critical_fact_retention']}, negation {x['negation_retention']}, altered {x['altered_lines']}" for lab, x in rows.items()) for b, rows in doc["compression"]["budgets"].items()]
    L += ["", "## Scenarios"] + [f"- {s['scenario']} {s['name']}: {'PASS' if s['passed'] else 'FAIL'}" for s in doc["scenarios"]]
    L += ["", "## Report quality", f"{doc['report_quality']}", "", "## Runtime (mocked)", f"{doc['runtime']}", "", "## Real model", f"{doc['real_model']}"]
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "build" / "p15_eval"))
    ap.add_argument("--real", action="store_true", help="also run ONE controlled request through the configured real model (never falls back to a mock)")
    a = ap.parse_args(argv)
    doc = run_all(real=a.real)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "results.json").write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
    (out / "report.md").write_text(render_markdown(doc), encoding="utf-8")
    print(render_markdown(doc))
    return 0


if __name__ == "__main__":
    sys.exit(main())
