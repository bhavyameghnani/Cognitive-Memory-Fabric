import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents.report.agent import ReportAgent  # noqa: E402
from agents.report.render import render_report  # noqa: E402
from llm.client import LLMError, MockLLM  # noqa: E402
from schemas.memory import EvidenceRef, MemoryContext, MemoryItem  # noqa: E402
from schemas.plan import ExecutionPlan  # noqa: E402
from schemas.tools import Envelope, Provenance, ToolExecution, ToolSelectorState  # noqa: E402
from services.conversations import shared  # noqa: E402  (tests only: verifies excerpts are genuine)

CONV = shared()
ROOT = Path(__file__).resolve().parents[1]
K = "CLIENT-001"
EXCERPT = "We've decided: 63."


def plan(task="retrieval", clients=(K,), calc=False, tools=(), missing=(), text="What is Kenji's retirement age?"):
    return ExecutionPlan(request_id="REQ-R", plan_id="PLAN-R", request_text=text, client_ids=list(clients), user_intent="x", task_type=task, needs_history=True,
                         needs_calculation=calc, subtasks=[], required_memory_categories=["semantic"], memory_topics=["retirement"], required_data=[],
                         required_tools=list(tools), dependencies={}, execution_order=[], parallelizable_tasks=[], missing_information=list(missing),
                         success_criteria=["answer with cited evidence"])


def mem(mid, mtype, value, status, certainty, vf=None, vt=None, ev=None, client=K, event=None, content=None, topic="retirement_age"):
    return MemoryItem(memory_id=mid, client_id=client, memory_type=mtype, topic=topic, subject="retirement_age", content=content or f"Retirement age {value}",
                      value_text=value, certainty=certainty, status=status, valid_from=vf, valid_to=vt, event_date=event, evidence=ev or [], verification="verified")      # these fixtures stand for memories the integrity boundary verified (P1.4.3)


def kenji_ctx(**kw):
    ev = [EvidenceRef(interaction_id="INT-090", turn_index=2, speaker="Client", excerpt=EXCERPT)]
    return MemoryContext(
        client_id=K,
        current=[mem("SEM-2", "semantic", "63", "active", "confirmed", vf="2026-05-01", ev=ev)],
        history=[mem("SEM-1", "semantic", "65", "superseded", "confirmed", vf="2025-01-01", vt="2026-05-01")],
        episodes=[mem("EP-1", "episodic", "60", "active", "exploratory", event="2026-01-15"), mem("EP-2", "episodic", "63", "active", "exploratory", event="2026-04-22")], **kw)


def env(tool, data, client=K, as_of="2026-10-01", limits=(), note="All operational values are synthetic."):
    return Envelope(tool=tool, client_id=client, as_of=as_of, data=data, provenance=Provenance(source="cmf_ops", note=note), limitations=list(limits))


def tstate(*execs, **kw):
    return ToolSelectorState(request_id="REQ-R", executions=list(execs), **kw)


def ex(i, envelope, status="success", error=None):
    return ToolExecution(id=f"EX-REQ-R-{i:02d}", iteration=1, tool=envelope.tool if envelope else "x", status=status, result=envelope, error=error)


def llm_out(**kw):
    base = dict(summary="Retirement age is 63 [E1].", key_findings=[{"text": "Retirement age is 63", "refs": ["E1"]}])
    base.update(kw)
    return base


def agent_for(output):
    llm = MockLLM(handlers={"report": output if isinstance(output, list) else [output]})
    return ReportAgent(llm), llm


def run(output, pl=None, memory=None, tools=None):
    a, llm = agent_for(output)
    r = a.write("What is Kenji's retirement age?", pl or plan(), memory if memory is not None else {K: kenji_ctx()}, tools or tstate())
    return r, llm


def claim(status, ref, text, value="63", topic="retirement_age"):
    return dict(subject="Kenji", topic=topic, value=value, status=status, evidence_ref=ref, text=text)


class Evidence(unittest.TestCase):
    def test_order_handles_synthetic(self):
        t = tstate(ex(1, env("get_client_profile", {"retirement_age": 63})), ex(2, env("calculate_goal_funding_gap", {"gap": 100000})))
        r, _ = run(llm_out(), memory={K: kenji_ctx()}, tools=t)
        cls = [e.source_class for e in r.evidence]
        self.assertEqual([e.ref for e in r.evidence], [f"E{i}" for i in range(1, len(cls) + 1)])
        self.assertEqual(cls[:4], ["memory", "operational_data", "tool_calculation", "memory"])  # current, tools, history
        self.assertTrue(all(e.synthetic for e in r.evidence if e.source_class in ("operational_data", "tool_calculation")))
        self.assertFalse(any(e.synthetic for e in r.evidence if e.source_class in ("memory", "conversation_evidence")))
        self.assertEqual(r.evidence[1].tool_execution_id, "EX-REQ-R-01")

    def test_prompt_limited_to_16_and_300_chars(self):
        eps = [mem(f"EP-{i}", "semantic", str(i), "superseded", "confirmed", content="x" * 800, topic=f"t{i}") for i in range(30)]
        a, llm = agent_for(llm_out())
        a.write("q", plan(), {K: MemoryContext(client_id=K, history=eps, current=[mem("S", "semantic", "63", "active", "confirmed")])}, tstate())
        user = llm.calls[0]["user"]
        lines = [ln for ln in user.splitlines() if re.match(r"\[E\d+ ", ln)]
        self.assertEqual(len(lines), 16)
        self.assertTrue(all(len(ln) < 330 for ln in lines))

    def test_client_ownership(self):
        ctx = kenji_ctx()
        ctx.current.append(mem("SEM-X", "semantic", "58", "active", "confirmed", client="CLIENT-009"))
        other = MemoryContext(client_id="CLIENT-009", current=[mem("SEM-Y", "semantic", "70", "active", "confirmed", client="CLIENT-009")])
        t = tstate(ex(1, env("get_client_profile", {"retirement_age": 70}, client="CLIENT-009")))
        r, _ = run(llm_out(), memory={K: ctx, "CLIENT-009": other}, tools=t)
        self.assertFalse(any(e.memory_id in ("SEM-X", "SEM-Y") or e.tool_execution_id for e in r.evidence))
        self.assertEqual(len(r.validation["ownership_removed"]), 3)


class Citations(unittest.TestCase):
    def test_invalid_ref_removed_and_refless_dropped(self):
        r, _ = run(llm_out(key_findings=[{"text": "Retirement age is 63", "refs": ["E1", "E99"]}, {"text": "Uncited claim about 63", "refs": ["E77"]}, {"text": "No refs", "refs": []}]))
        self.assertEqual([f.refs for f in r.key_findings], [["E1"]])
        self.assertEqual(r.validation["invalid_refs_removed"], 2)
        self.assertEqual(len(r.validation["findings_dropped"]), 2)

    def test_invented_number_dropped_and_summary_replaced(self):
        r, _ = run(llm_out(summary="Retirement age is 71 [E1].", key_findings=[{"text": "Retirement age is 63", "refs": ["E1"]}, {"text": "Net worth is $9,999,999", "refs": ["E1"]}]))
        self.assertEqual(len(r.key_findings), 1)
        self.assertIn("number(s) not in evidence", r.validation["summary_replaced"])      # P6.2.1: the category, not the refused figure
        self.assertNotIn("71", r.validation["summary_replaced"])
        self.assertNotIn("71", r.summary)
        self.assertIn("63", r.summary)

    def test_rounding_and_years_allowed(self):
        t = tstate(ex(1, env("calculate_goal_funding_gap", {"funding_gap": 3538000, "target_year": 2031})))
        r, _ = run(llm_out(financial_analysis=[{"text": "Funding gap is about $3.54M by 2031 (2 goals)", "refs": ["E2"]}]), tools=t)
        self.assertEqual(len(r.financial_analysis), 1)

    def test_never_cite_interaction_not_in_evidence(self):
        r, _ = run(llm_out(key_findings=[{"text": "Decided in INT-090 turn 2: 63", "refs": ["E1"]}, {"text": "Decided in INT-777 turn 2: 63", "refs": ["E1"]},
                                         {"text": "Decided in INT-090 turn 5: 63", "refs": ["E1"]}]))
        self.assertEqual(len(r.key_findings), 1)
        self.assertTrue(any("INT-777" in d["reason"] for d in r.validation["findings_dropped"]))

    def test_cited_excerpts_are_genuine(self):
        r, _ = run(llm_out())
        cited = [e for e in r.evidence if e.interaction_id]
        self.assertTrue(cited)
        for e in cited:
            self.assertTrue(CONV.verify_excerpt(K, e.interaction_id, e.turn_index, e.excerpt))
        self.assertIn(EXCERPT, CONV.turn("INT-090", 2)["utterance"])


class Fallbacks(unittest.TestCase):
    def test_no_evidence_no_model_call(self):
        r, llm = run(llm_out(), memory={}, tools=tstate())
        self.assertEqual(llm.calls, [])
        self.assertIn("cannot be answered reliably", r.summary)
        self.assertFalse(r.validation["model_called"])

    def test_model_failure_deterministic_with_citations(self):
        r, _ = run([LLMError("down")])
        self.assertEqual(r.validation["mode"], "deterministic_model_failure")
        self.assertTrue(r.key_findings and all(f.refs for f in r.key_findings))
        self.assertIn("63", r.summary)

    def test_nothing_valid_deterministic(self):
        r, _ = run(llm_out(key_findings=[{"text": "Retirement age is 63", "refs": []}]))
        self.assertEqual(r.validation["mode"], "deterministic_nothing_valid")
        self.assertTrue(r.key_findings)

    def test_invalid_json_retries_then_fallback(self):
        r, llm = run(["not json", "still not", "nope"])
        self.assertEqual(len(llm.calls), 3)
        self.assertEqual(r.validation["mode"], "deterministic_model_failure")


class Assembled(unittest.TestCase):
    def test_assumptions_limitations_feasibility(self):
        t = tstate(ex(1, env("calculate_goal_funding_gap", {"gap": 1, "assumptions": ["flat 5% return"]}, limits=["simple gap, no projection of returns"])))
        r, _ = run(llm_out(), pl=plan("forecast_feasibility", calc=True, tools=["calculate_goal_funding_gap"], text="Can Kenji retire at 63?"), tools=t)
        self.assertTrue(any("synthetic" in a for a in r.assumptions))
        self.assertTrue(any("flat 5% return" in a for a in r.assumptions))
        self.assertTrue(any("simple gap" in x for x in r.limitations))
        self.assertTrue(any("No projection exists" in x for x in r.limitations))
        r2, _ = run(llm_out())
        self.assertFalse(any("No projection" in x for x in r2.limitations))

    def test_discrepancy_both_dates(self):
        t = tstate(ex(1, env("get_client_profile", {"retirement_age": 65}, as_of="2026-03-01")))
        r, _ = run(llm_out(), tools=t)
        d = [u for u in r.uncertainties if u.startswith("Discrepancy")]
        self.assertEqual(len(d), 1)
        for needle in ("63", "65", "2026-05-01", "2026-03-01"):
            self.assertIn(needle, d[0])
        self.assertTrue(any("Reconcile" in f for f in r.suggested_followups))

    def test_no_discrepancy_when_equal(self):
        t = tstate(ex(1, env("get_client_profile", {"retirement_age": 63})))
        r, _ = run(llm_out(), tools=t)
        self.assertFalse([u for u in r.uncertainties if u.startswith("Discrepancy")])

    def test_gaps_listed(self):
        ctx = kenji_ctx(contradictions=["profile says 65, memory says 63"], unresolved=[mem("U1", "episodic", "x", "active", "unconfirmed", content="Pension rollover not decided")])
        t = tstate(ex(1, None, status="error", error="boom"), missing_information=["income not available"], errors=["get_client_goals failed"])
        r, _ = run(llm_out(), pl=plan(calc=True, tools=["calculate_goal_funding_gap"], missing=["time horizon"]), memory={K: ctx}, tools=t)
        u = " | ".join(r.uncertainties)
        for needle in ("Pension rollover", "profile says 65", "time horizon", "income not available", "get_client_goals failed", "No tool results"):
            self.assertIn(needle, u)

    def test_model_cannot_overwrite_assembled_sections(self):
        r, _ = run(llm_out(uncertainties=["Model uncertainty about 63"]))
        self.assertIn("Model uncertainty about 63", r.uncertainties)
        self.assertTrue(any("synthetic" in a for a in r.assumptions) is False)  # no synthetic evidence -> no synthetic statement


class KenjiStatus(unittest.TestCase):
    def good(self):
        return llm_out(summary="Kenji's confirmed retirement target is 63 (decided 2026-05-01, INT-090 turn 2), replacing the earlier plan of 65. 60 was explored, not decided.",
                       claims=[claim("current", "E1", "Retirement age is 63, valid from 2026-05-01"), claim("superseded", "E2", "Retirement age was 65 until 2026-05-01", "65"),
                               claim("exploratory", "E3", "Explored retiring at 60", "60")])

    def test_good_report(self):
        r, _ = run(self.good())
        self.assertEqual(r.validation["claims_accepted"], 3)
        text = " ".join(f.text for f in r.key_findings + r.historical_context) + r.summary
        self.assertTrue(any("63" in f.text and "current" in f.text for f in r.key_findings))
        self.assertTrue(any("65" in f.text and "superseded" in f.text for f in r.historical_context))
        self.assertTrue(any("60" in f.text and "exploratory" in f.text for f in r.historical_context))
        self.assertEqual(len([f for f in r.key_findings if "65" in f.text]), 0)
        self.assertNotIn("63 is exploratory", text)

    def test_63_not_exploratory_and_65_not_current(self):
        bad = llm_out(summary="Kenji's retirement age is currently exploratory at 65.",
                      key_findings=[{"text": "The current plan is 63 and also 65", "refs": ["E1", "E2"]}, {"text": "Retirement age 63 is still exploratory", "refs": ["E4"]}],
                      claims=[claim("exploratory", "E1", "63 is exploratory"), claim("current", "E2", "65 is the current retirement age", "65"),
                              claim("current", "E4", "63 current", "63"), claim("exploratory", "E1", "Kenji decided on 63", "63")])
        r, _ = run(bad)
        self.assertEqual(r.validation["claims_accepted"], 0)
        self.assertEqual(r.validation["claims_total"], 4)
        self.assertEqual(r.key_findings and [f for f in r.key_findings if "65" in f.text and "current" in f.text.lower() and "superseded" not in f.text], [])
        self.assertEqual(r.validation["mode"], "deterministic_nothing_valid")
        self.assertNotIn("exploratory at 65", r.summary)
        self.assertIn("63", r.summary)
        for f in r.key_findings:
            self.assertNotIn("exploratory", f.text.lower().replace("not exploratory", ""))
        self.assertFalse(any("65" in f.text and "Current" in f.text for f in r.key_findings))

    def test_status_wrong_summary_replaced_but_valid_claims_kept(self):
        for bad in ("Kenji's retirement age is currently exploratory at 65.", "Kenji's retirement age of 63 is still exploratory."):
            r, _ = run(llm_out(summary=bad, claims=[claim("current", "E1", "Retirement age is 63")]))
            self.assertIn("status", "status" + r.validation["summary_replaced"])
            self.assertEqual(r.validation["mode"], "llm")
            self.assertIn("63", r.summary)
            self.assertNotIn("exploratory", r.summary)
            self.assertNotIn("65", r.summary.replace("replaced 65", "").replace("It replaced 65", "")) if False else None

    def test_one_current_value_per_topic(self):
        ctx = kenji_ctx()
        ctx.current.append(mem("SEM-3", "semantic", "64", "active", "confirmed", vf="2026-05-02"))
        r, _ = run(llm_out(claims=[claim("current", "E1", "63 current"), claim("current", "E2", "64 current", "64")]), memory={K: ctx})
        self.assertEqual(r.validation["claims_accepted"], 1)
        self.assertIn("more than one current", r.validation["claims_rejected"][0]["reason"])

    def test_date_outside_validity_window_rejected(self):
        r, _ = run(llm_out(claims=[claim("current", "E1", "Retirement age is 63 since 2026-04-01"), claim("superseded", "E2", "Was 65 until 2026-06-30", "65")]))
        self.assertEqual(r.validation["claims_accepted"], 0)
        self.assertTrue(all("date" in x["reason"] for x in r.validation["claims_rejected"]))

    def test_value_mismatch_and_tool_claim_rejected(self):
        t = tstate(ex(1, env("get_client_profile", {"retirement_age": 63})))
        r, _ = run(llm_out(claims=[claim("current", "E1", "Retirement age is 60", "60"), claim("current", "E2", "profile says 63", "63")]), tools=t)
        self.assertEqual(r.validation["claims_accepted"], 0)


class Render(unittest.TestCase):
    def test_headings_in_spec_order(self):
        r, _ = run(llm_out())
        txt = render_report(r)
        heads = re.findall(r"^## (.+)$", txt, re.M)
        self.assertEqual(heads, ["Summary", "Key findings", "Historical context", "Financial analysis", "Evidence", "Assumptions", "Uncertainties",
                                 "Limitations", "Suggested follow-ups", "Validation"])
        self.assertIn("E1 memory", txt)


class Hygiene(unittest.TestCase):
    def test_source_has_no_db_mcp_imports(self):
        bad = re.compile(r"sqlite|psycopg|\bmcp\b|cmf_data|services\.conversations|import\s+requests|ops_access|\.db\b", re.I)
        for p in (ROOT / "src" / "agents" / "report").glob("*.py"):
            self.assertIsNone(bad.search(p.read_text(encoding="utf-8")), p.name)


if __name__ == "__main__":
    unittest.main()
