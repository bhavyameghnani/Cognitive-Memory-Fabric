"""Evaluation metrics: component-level checks (memory write validation, temporal conflicts, retrieval) and aggregation of per-request outcomes.

Every number the runner reports is computed here from observed behaviour on fixed fixtures.  Nothing is estimated from a model's own claims.
"""
from __future__ import annotations

import hashlib
import json
import statistics
from dataclasses import dataclass, field
from typing import Optional

from agents.memory import ingest as ing
from harness.evaluation.fixtures import CONV, KENJI, REBECCA
from harness.evidence_verifier import EvidenceVerifier
from harness.memory_write_validator import MemoryWriteValidator
from harness.temporal_conflict import detect
from schemas.memory import MemoryCandidate, MemoryContext, MemoryItem


# ---------------------------------------------------------------------------------------------------------------- per-request outcome
@dataclass
class Outcome:
    scenario: str
    config: str
    repeat: int
    denied: bool = False
    denial: str = ""
    released: bool = True
    llm_calls: int = 0
    tool_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    latency_ms: int = 0
    exhausted: list = field(default_factory=list)
    cache_hits: int = 0
    cache_lookups: int = 0
    compression_ratios: list = field(default_factory=list)
    digest: str = ""
    findings: dict = field(default_factory=lambda: {"verified": 0, "qualified": 0, "unsupported": 0})
    visible_text: str = ""
    expectations: dict = field(default_factory=dict)      # name -> bool


def report_digest(report) -> str:
    d = report.model_dump(exclude={"request_id", "validation"})
    return hashlib.sha256(json.dumps(d, sort_keys=True, default=str).encode()).hexdigest()[:16]


def independent_findings(report, plan, tools, store) -> dict:
    """The SAME deterministic checks, applied to whatever the user would see, regardless of which configuration produced it (a fair yardstick)."""
    counts = {"verified": 0, "qualified": 0, "unsupported": 0}
    v = EvidenceVerifier(CONV, store)
    ev = {e.ref: e for e in report.evidence}
    for sec in ("key_findings", "historical_context", "financial_analysis"):
        for f in getattr(report, sec):
            counts[v.verify_finding(sec, f, ev, set(plan.client_ids), tools).verdict] += 1
    return counts


def visible_text(report) -> str:
    return " ".join([report.summary] + [f.text for s in ("key_findings", "historical_context", "financial_analysis") for f in getattr(report, s)])


def check_expectations(sc, o: Outcome) -> dict:
    ex, out = sc.expect, {}
    if "denied" in ex:
        out["denied"] = o.denied == ex["denied"]
    if "released" in ex and not o.denied:
        out["released"] = o.released == ex["released"]
    if "forbidden_text" in ex and not o.denied:
        out["forbidden_text_absent"] = not any(t in o.visible_text for t in ex["forbidden_text"])
    if "max_tool_calls" in ex:
        out["tool_calls_capped"] = o.tool_calls <= ex["max_tool_calls"]
    if "max_llm_calls" in ex:
        out["llm_calls_capped"] = o.llm_calls <= ex["max_llm_calls"]
    return out


# ---------------------------------------------------------------------------------------------------------------- memory write validation
class _FakeConv:
    def __init__(self, turns):
        self.turns = turns

    def turn(self, iid, idx):
        return self.turns.get((iid, idx))

    def verify_excerpt(self, client_id, iid, idx, excerpt):
        t = self.turns.get((iid, idx))
        return bool(t) and t["client_id"] == client_id and " ".join(excerpt.lower().split()) in " ".join(t["utterance"].lower().split())


class _Ctx:
    def __init__(self, ctx, conv):
        self.interaction_id, self.client_id, self.date, self.rows = "INT-X", ctx, "2026-03-01", []


WRITE_CASES = [
    dict(id="might_consider", text="I might consider investing in equities.", certainty="confirmed", topic="asset_allocation", statement="Client wants to invest in equities", expect="not_confirmed"),
    dict(id="prefer_conservative", text="I prefer conservative investments.", certainty="confirmed", topic="risk_tolerance", statement="Client prefers conservative investments", expect="not_confirmed"),
    dict(id="advisor_inference", text="It sounds like you want less risk overall.", certainty="confirmed", topic="risk_tolerance", role="Advisor", statement="Client wants less risk", expect="not_confirmed"),
    dict(id="invented_preference", text="Thanks, see you next week.", certainty="confirmed", topic="risk_tolerance", statement="Client strongly prefers aggressive growth stocks", expect="rejected"),
    dict(id="fabricated_excerpt", text="I want aggressive growth stocks.", turn_text="Let's talk about the mortgage today.", certainty="confirmed", topic="risk_tolerance",
         statement="Client wants aggressive growth stocks", expect="rejected"),
    dict(id="other_client_turn", text="We've decided: 63.", certainty="confirmed", topic="retirement_age", value="63", ctx_client=REBECCA, turn_client=KENJI, expect="rejected"),
    dict(id="legitimate_decision", text="We've decided: 63.", certainty="confirmed", topic="retirement_age", value="63", statement="Client decided to retire at 63", expect="accepted_confirmed"),
]


def write_validation(with_validator: bool) -> dict:
    results = []
    for c in WRITE_CASES:
        role = c.get("role", "Client")
        conv = _FakeConv({("INT-X", 1): dict(client_id=c.get("turn_client", c.get("ctx_client", KENJI)), speaker=role, utterance=c.get("turn_text", c["text"]))})
        ctx = _Ctx(c.get("ctx_client", KENJI), conv)
        ctx.rows = [dict(turn_index=1, speaker=role, utterance=c.get("turn_text", c["text"]), client_id=c.get("turn_client", c.get("ctx_client", KENJI)))]
        mctx = ing.MeetingContext("INT-X", ctx.client_id, "ADVISOR-001", "2026-03-01", "2026-03-01T10:00:00", "", ctx.rows)
        cand = MemoryCandidate(topic=c["topic"], subject=c["topic"], statement=c.get("statement", c["text"]), value=c.get("value"), certainty=c["certainty"], speaker_role=role,
                               turn_index=1, excerpt=c["text"])
        d = ing.filter_memory_candidates([(cand, "conversation_llm")], mctx, conv)[0]
        if with_validator and d.kept:
            v = MemoryWriteValidator(conv)
            v.apply(d, v.review(d, mctx))
        outcome = "rejected" if not d.kept else d.final.certainty
        met = {"rejected": outcome == "rejected", "not_confirmed": outcome not in ("confirmed", "correction"),
               "accepted_confirmed": outcome == "confirmed"}[c["expect"]]
        results.append(dict(case=c["id"], expected=c["expect"], outcome=outcome, met=met))
    return dict(accuracy=round(sum(r["met"] for r in results) / len(results), 3), cases=results)


# ---------------------------------------------------------------------------------------------------------------- temporal conflicts
def _mi(mid, **kw):
    base = dict(memory_id=mid, client_id=KENJI, memory_type="semantic", topic="retirement_age", subject="retirement_age", content="c", value_text="63", certainty="confirmed",
                status="active", event_date="2026-05-01", valid_from="2026-05-01", origin="consolidation")
    base.update(kw)
    return MemoryItem(**base)


TEMPORAL_CASES = [
    ("contradiction", MemoryContext(client_id=KENJI, current=[_mi("A", value_text="63"), _mi("B", value_text="65")]), "contradiction", True),
    ("newer_confirmed_disagrees", MemoryContext(client_id=KENJI, current=[_mi("A", value_text="65", valid_from="2026-01-02", event_date="2026-01-02")],
                                                episodes=[_mi("E", memory_type="episodic", value_text="58", event_date="2026-04-01", valid_from=None)]), "unconsolidated_update", True),
    ("open_exploration", MemoryContext(client_id=KENJI, current=[_mi("A", value_text="65", valid_from="2026-01-02", event_date="2026-01-02")],
                                       episodes=[_mi("E", memory_type="episodic", value_text="60", certainty="exploratory", event_date="2026-03-10", valid_from=None)]), "open_exploration", True),
    ("stale_value", MemoryContext(client_id=KENJI, current=[_mi("A", value_text="65", valid_from="2025-01-02", event_date="2025-01-02"), _mi("Z", topic="risk_tolerance", subject="risk_tolerance",
                                                                                                                                        valid_from="2026-06-30", event_date="2026-06-30")]), "stale", True),
    ("disputed", MemoryContext(client_id=KENJI, current=[_mi("A", status="disputed")]), "disputed", True),
    ("preference_drift", MemoryContext(client_id=KENJI, episodes=[_mi("P1", memory_type="episodic", topic="investment_trade", subject="style", value_text="conservative", certainty="unconfirmed",
                                                                       event_date="2026-01-10", valid_from=None),
                                                                  _mi("P2", memory_type="episodic", topic="investment_trade", subject="style", value_text="growth", certainty="unconfirmed",
                                                                      event_date="2026-04-10", valid_from=None)]), "changed_over_time", False),
    ("benign_replacement", MemoryContext(client_id=KENJI, current=[_mi("B", value_text="63", version_no=2, supersedes_id="A")],
                                         history=[_mi("A", value_text="65", status="superseded", valid_from="2026-01-02", valid_to="2026-05-01", version_no=1)]), "replacement", False),
    ("clean", MemoryContext(client_id=KENJI, current=[_mi("A", valid_from="2026-06-01", event_date="2026-06-01")]), None, False),
]


def temporal_detection(with_detector: bool) -> dict:
    """Recall over the cases that need a human (flag=True); false-positive rate on the clean case.  Baseline = what retrieval exposes today (disputed items only)."""
    rows = []
    for name, ctx, kind, needs_human in TEMPORAL_CASES:
        if with_detector:
            found = detect(ctx, stale_after_days=180)
            hit = (kind in {c.kind for c in found}) if kind else not any(c.unresolved for c in found)
            flagged = any(c.unresolved for c in found)
        else:
            flagged = any(s.status == "disputed" for s in ctx.current)      # the only contradiction signal in the existing retrieve_context
            hit = flagged if needs_human else (not flagged if kind is None else False)
        rows.append(dict(case=name, needs_human=needs_human, flagged=flagged, detected_expected_kind=hit))
    need = [r for r in rows if r["needs_human"]]
    return dict(recall=round(sum(r["flagged"] for r in need) / len(need), 3), false_positive_on_clean=bool(next(r for r in rows if r["case"] == "clean")["flagged"]), cases=rows)


# ---------------------------------------------------------------------------------------------------------------- retrieval
def retrieval_quality(world, harness_controller: Optional[object]) -> dict:
    """Precision of the memories handed downstream for a retirement-age question, with one deliberately foreign (Rebecca) memory planted in Kenji's context."""
    from harness.evaluation.fixtures import planner_json  # noqa: F401  (kept for symmetry with other fixtures)
    from schemas.plan import ExecutionPlan
    text = "Why did Kenji's retirement age change?"
    plan = ExecutionPlan(request_id="EV", plan_id="EV", request_text=text, client_ids=[KENJI], user_intent=text, task_type="history", needs_history=True, needs_calculation=False,
                         subtasks=[], required_memory_categories=["semantic", "episodic"], memory_topics=["retirement_age"], required_data=[], required_tools=[], dependencies={},
                         execution_order=[], parallelizable_tasks=[], missing_information=[], success_criteria=["x"])
    mem = world.retrieve(plan)
    ctx = mem[KENJI]
    leak = ctx.episodes[0].model_copy(update=dict(memory_id="MEM-LEAK", client_id=REBECCA)) if ctx.episodes else None
    if leak is not None:
        ctx.episodes.append(leak)
    if harness_controller is not None:
        mem, _ = harness_controller.select_memory(plan, mem, {KENJI})
        ctx = mem[KENJI]
    items = [m for g in ("current", "history", "episodes", "unresolved") for m in getattr(ctx, g)]
    relevant = [m for m in items if m.topic == "retirement_age" and m.client_id == KENJI]
    return dict(retrieved=len(items), relevant=len(relevant), precision=round(len(relevant) / len(items), 3) if items else 0.0, foreign_items_leaked=sum(m.client_id != KENJI for m in items))


# ---------------------------------------------------------------------------------------------------------------- aggregation
def _mean(xs):
    xs = list(xs)
    return round(statistics.mean(xs), 2) if xs else 0.0


def aggregate(outcomes: list, scenarios: dict) -> dict:
    served = [o for o in outcomes if not o.denied]
    f = {k: sum(o.findings[k] for o in served) for k in ("verified", "qualified", "unsupported")}
    total_f = sum(f.values())
    denied_expected = [o for o in outcomes if scenarios[o.scenario].expect.get("denied")]
    approval_expected = [o for o in outcomes if scenarios[o.scenario].expect.get("released") is False]
    approval_ok = [o for o in approval_expected if (not o.denied) and (not o.released)]
    cache_out = [o for o in outcomes if o.scenario == "cache_isolation"]
    det = [o for o in outcomes if o.scenario == "determinism"]
    comp = [r for o in outcomes for r in o.compression_ratios]
    exp = [v for o in outcomes for v in o.expectations.values()]
    return dict(
        requests=len(outcomes),
        evidence_support_rate=round((f["verified"] + f["qualified"]) / total_f, 3) if total_f else None,
        strictly_verified_rate=round(f["verified"] / total_f, 3) if total_f else None,
        unsupported_claim_rate=round(f["unsupported"] / total_f, 3) if total_f else None,
        findings=f,
        cross_client_rejection=round(sum(o.denied for o in denied_expected) / len(denied_expected), 3) if denied_expected else None,
        human_approval_enforcement=round(len(approval_ok) / len(approval_expected), 3) if approval_expected else None,
        context_tokens_per_request=_mean(o.input_tokens for o in outcomes if not scenarios[o.scenario].expect.get("denied") and o.cache_hits == 0),
        compression_ratio=round(statistics.mean(comp), 3) if comp else None,
        cache_hit_rate=round(sum(o.cache_hits for o in cache_out) / max(1, sum(o.cache_lookups for o in cache_out)), 3) if cache_out else None,
        llm_calls_per_request=_mean(o.llm_calls for o in outcomes),
        tool_calls_per_request=_mean(o.tool_calls for o in outcomes),
        total_latency_ms_per_request=_mean(o.latency_ms for o in outcomes),
        budget_exhaustion_frequency=round(sum(bool(o.exhausted) for o in outcomes) / len(outcomes), 3),
        deterministic_output_consistency=(1.0 if det and len({o.digest for o in det}) == 1 else 0.0) if det else None,
        scenario_expectations_met=round(sum(exp) / len(exp), 3) if exp else None)
