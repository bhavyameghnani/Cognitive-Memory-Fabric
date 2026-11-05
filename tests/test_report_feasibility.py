"""Regression for the real-OpenAI run 'Can Kenji retire at 63?': the model asserted feasibility although the tools show a 958K gap and no projection exists."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_report_agent import K, EXCERPT, claim, ex, env, kenji_ctx, llm_out, mem, plan, tstate, agent_for  # noqa: E402
from agents.report.claims import check_claim  # noqa: E402
from schemas.memory import EvidenceRef  # noqa: E402

GAP = {"resources_basis": "investable_portfolio", "resources": 3541489.73, "goals": [{"goal_id": "G1", "type": "Retirement", "status": "active", "target": 4500000.0,
       "resources": 3541489.73, "simple_gap": 958510.27, "coverage_percent": 78.7, "target_date": "2031-01-01", "amount_provenance": "synthetic"}], "skipped": []}
LIQ = {"monthly_expenses": 11800.0, "cash_months": 13.21, "target_months": 15, "shortfall_amount": 21171.87}
Q = "Can Kenji retire at 63 based on the available financial data?"


def run(summary, **kw):
    pl = plan(task="forecast_feasibility", calc=True, text=Q)
    tools = tstate(ex(1, env("calculate_goal_funding_gap", GAP, limits=["Not a projection."])), ex(2, env("calculate_liquidity_ratio", LIQ)))
    a, _ = agent_for(llm_out(summary=summary, **kw))
    return a.write(Q, pl, {K: kenji_ctx()}, tools)


class FeasibilityGuard(unittest.TestCase):
    def test_asserted_feasibility_is_replaced(self):
        r = run("Kenji can retire at 63, as he has confirmed this decision [E1].")
        self.assertNotIn("can retire", r.summary)
        self.assertIn("cannot be confirmed", r.summary)
        self.assertIn("958,510.27", r.summary)
        self.assertIn("feasibility guard", r.validation["summary_replaced"])

    def test_summary_without_hedge_is_replaced_even_without_assertion(self):
        self.assertIn("cannot be confirmed", run("Kenji's retirement target is 63 [E1].").summary)

    def test_hedged_model_summary_is_kept(self):
        s = "Kenji decided on 63 [E1], but feasibility cannot be confirmed because no projection exists."
        self.assertEqual(run(s).summary, s)

    def test_financial_analysis_is_built_from_tool_results_with_citations(self):
        r = run("Kenji can retire at 63 [E1].")
        txt = " ".join(f.text for f in r.financial_analysis)
        for needle in ("$4,500,000.00", "$3,541,489.73", "$958,510.27", "78.7%", "13.21 months", "15-month"):
            self.assertIn(needle, txt)
        valid = {e.ref for e in r.evidence}
        self.assertTrue(all(f.refs and set(f.refs) <= valid for f in r.financial_analysis))
        self.assertTrue(any(e.tool_execution_id and e.synthetic for e in r.evidence if e.ref in {x for f in r.financial_analysis for x in f.refs}))

    def test_non_feasibility_question_is_not_touched(self):
        a, _ = agent_for(llm_out(summary="Retirement age is 63 [E1]."))
        r = a.write("What is Kenji's retirement age?", plan(), {K: kenji_ctx()}, tstate())
        self.assertEqual(r.summary, "Retirement age is 63 [E1].")

    def test_deterministic_fallback_also_states_feasibility_is_unconfirmed(self):
        pl = plan(task="forecast_feasibility", calc=True, text=Q)
        tools = tstate(ex(1, env("calculate_goal_funding_gap", GAP)))
        a, _ = agent_for(["not json"] * 3)
        r = a.write(Q, pl, {K: kenji_ctx()}, tools)
        self.assertIn("cannot be confirmed", r.summary)
        self.assertTrue(r.financial_analysis)


class EpisodeBacksCurrent(unittest.TestCase):
    def test_confirmed_episode_with_same_value_backs_a_current_claim(self):
        ev = [EvidenceRef(interaction_id="INT-090", turn_index=2, speaker="Client", excerpt=EXCERPT)]
        ctx = kenji_ctx()
        ctx.episodes.append(mem("EP-3", "episodic", "63", "active", "confirmed", event="2026-05-01", ev=ev))
        a, _ = agent_for(llm_out(claims=[claim("current", "E5", "Kenji decided to retire at 63 on 2026-05-01.")]))
        r = a.write("What is Kenji's retirement age?", plan(), {K: ctx}, tstate())
        self.assertEqual(next(e for e in r.evidence if e.ref == "E5").memory_id, "EP-3")
        self.assertEqual(r.validation["claims_accepted"], 1, r.validation["claims_rejected"])

    def test_exploratory_or_older_episode_does_not_back_a_current_claim(self):
        a, _ = agent_for(llm_out(claims=[claim("current", "E3", "Kenji is retiring at 63.")]))
        r = a.write("What is Kenji's retirement age?", plan(), {K: kenji_ctx()}, tstate())
        self.assertEqual(r.validation["claims_accepted"], 0)


if __name__ == "__main__":
    unittest.main()
