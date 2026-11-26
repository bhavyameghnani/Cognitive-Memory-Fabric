"""A question about what was SAID in the past is not a request for advice.

The live Ask CMF held this factual history question for human approval (APR-26fde9b3...) because the word "should" matched SUITABILITY_Q:

    "What did Daniel and Rebecca agree should happen regarding a conversation with Rebecca's accountant? Identify who proposed speaking, who was supposed to participate, and what the source
     actually establishes."

harness/approval.py now removes REPORTED HISTORY (a past auxiliary + a reporting verb, or "who/what/which" + a past reporting verb, up to the end of that clause) before the keyword test.  These tests pin
both sides: the history questions that are no longer held, and the genuine advice requests (including ones that try to hide inside a history question) that still are.  Every other approval category and the
forecast / feasibility rule are untouched."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from harness.approval import ApprovalPolicy, RiskCategory, asks_for_advice, without_reported_history  # noqa: E402

ACCOUNTANT = ("What did Daniel and Rebecca agree should happen regarding a conversation with Rebecca's accountant? Identify who proposed speaking, who was supposed to participate, and what the source "
              "actually establishes.")
HELD = [RiskCategory.SUITABILITY_CONCLUSION]


class Base(unittest.TestCase):
    policy = ApprovalPolicy()

    def cats(self, text, task="history"):
        return self.policy.classify_report(SimpleNamespace(task_type=task), text)


class ReportedHistoryIsNotAdvice(Base):
    def test_the_exact_accountant_question_is_not_held(self):
        self.assertFalse(asks_for_advice(ACCOUNTANT))
        self.assertEqual(self.cats(ACCOUNTANT), [])

    def test_the_trigger_word_was_inside_the_reported_clause(self):
        self.assertNotIn("should", without_reported_history(ACCOUNTANT).lower())
        self.assertIn("who was supposed to participate", without_reported_history(ACCOUNTANT))          # only the reported clauses are removed; the rest of the request is kept

    def test_a_historical_question_containing_should_sell_is_not_held(self):
        for t in ("Did the advisor say Rebecca should sell?", "What did Daniel and Rebecca agree should happen with the Lakeshore sale?", "Who proposed that Rebecca should sell the company?",
                  "What did the advisor recommend about selling in March?", "When did Rebecca decide to sell?", "What was agreed should be done about the rollover?",
                  "Did Rebecca want to sell before April 22?", "What did the advisor tell Rebecca she should do about the exclusivity agreement?"):
            self.assertEqual(self.cats(t), [], t)

    def test_a_past_correction_or_record_question_is_not_held(self):
        for t in ("What did the advisor correct about the Lakeshore offer?", "What was recorded as the decision to sell?", "Who said Rebecca should wait two years, and what was recorded?"):
            self.assertEqual(self.cats(t), [], t)


class GenuineAdviceIsStillHeld(Base):
    def test_a_question_asking_whether_rebecca_should_sell_is_held(self):
        for t in ("Should Rebecca sell the company to Lakeshore?", "Should she sell?", "What should Rebecca do about the Lakeshore offer?", "Do you think Rebecca should sell?",
                  "Is it wise for Rebecca to sell now?", "Tell me whether Rebecca should sell."):
            self.assertEqual(self.cats(t), HELD, t)

    def test_a_recommendation_request_about_investment_suitability_is_held(self):
        for t in ("What do you recommend for Rebecca's portfolio after the sale?", "Is a 40 percent equity allocation suitable for Rebecca?", "Can she afford to accept the offer?",
                  "Is it safe to invest in bonds with the proceeds?", "Please rebalance Rebecca's portfolio.", "Recommend an investment policy for the sale proceeds."):
            self.assertEqual(self.cats(t), HELD, t)

    def test_advice_hidden_inside_or_beside_a_history_question_is_still_held(self):
        for t in ("What did the advisor say, and should Rebecca sell?",                    # a comma ends the reported clause
                  "What did the advisor say and should Rebecca sell?",                     # so does a conjunction that opens a new question
                  "What did the advisor say about the offer? Should Rebecca sell?",        # a second sentence
                  "Did the advisor say she should sell, and do you recommend it?",
                  "What did Daniel recommend; what do you recommend?"):
            self.assertEqual(self.cats(t), HELD, t)

    def test_a_judgement_about_the_past_advice_is_still_held(self):
        for t in ("Was the advisor right to recommend selling?", "Was it a good idea to sell in March?", "Was it a good plan to sell?", "Did the advisor say it was wise to sell?"):
            self.assertEqual(self.cats(t), HELD, t)

    def test_a_present_tense_question_is_not_treated_as_history(self):
        for t in ("What do the notes agree should happen next?", "What does the advisor recommend now?", "What should happen with the accountant call?"):
            self.assertEqual(self.cats(t), HELD, t)

    def test_what_is_not_a_blanket_exemption(self):
        self.assertEqual(self.cats("What should Rebecca sell first?"), HELD)
        self.assertEqual(self.cats("What would you recommend Rebecca buy?"), HELD)


class NounsThatNameADecisionAreHistory(Base):
    LAKESHORE = ("What is the history of Rebecca's decision about the Lakeshore company sale? Was the offer ever accepted, and what did Rebecca actually say versus what was recorded? Clearly distinguish "
                 "Rebecca's decision to sell from accepting or signing an offer, signing any exclusivity or purchase agreement, and completing the sale.")

    def test_a_decision_to_sell_is_a_record_not_a_request_for_advice(self):
        self.assertEqual(self.cats(self.LAKESHORE), [])
        for t in ("Summarise the offer to buy the company.", "When did Rebecca first state her intention to sell?", "What was Kenji's plan to switch funds?"):
            self.assertEqual(self.cats(t), [], t)

    def test_the_full_lakeshore_question_with_its_prohibition_is_not_held(self):
        self.assertEqual(self.cats(self.LAKESHORE + " Do not provide investment recommendations or forward-looking suitability advice."), [])
        self.assertEqual(self.cats("Do not give forward-looking advice. Should Rebecca sell?"), HELD)

    def test_a_request_for_a_judgement_or_a_recommendation_is_still_held(self):
        for t in ("Was Rebecca's decision to sell wise?", "Is the decision to sell right?", "Recommend a plan to sell the business.", "What was Rebecca's plan to sell, and should she sell now?",
                  "Should Rebecca sell the business?", "How should I prepare for Rebecca's next portfolio review?"):
            self.assertEqual(self.cats(t), HELD, t)


class OtherPolicyBehaviourIsUnchanged(Base):
    def test_a_forecast_feasibility_plan_is_still_gated_whatever_the_wording(self):
        for t in (ACCOUNTANT, "What did Kenji decide about retirement?", "Do not make recommendations."):
            self.assertEqual(self.cats(t, task="forecast_feasibility"), HELD, t)

    def test_prohibitions_of_advice_still_work_and_do_not_launder_advice(self):
        self.assertEqual(self.cats("Summarise the meetings. Do not make investment recommendations."), [])
        self.assertEqual(self.cats("Do not make investment recommendations. Should we sell?"), HELD)
        self.assertEqual(self.cats("Do not hesitate to recommend a sale."), HELD)

    def test_the_policy_can_still_be_switched_off_only_by_policy(self):
        self.assertEqual(ApprovalPolicy(required=False).classify_report(SimpleNamespace(task_type="forecast_feasibility"), "Should she sell?"), [])

    def test_no_other_category_depends_on_the_text_classifier(self):
        pol = ApprovalPolicy()
        for cat in (RiskCategory.SENSITIVE_MEMORY_WRITE, RiskCategory.EXTERNAL_ACTION, RiskCategory.IRREVERSIBLE_OPERATION, RiskCategory.HIGH_RISK_TOOL):
            self.assertTrue(pol.needs(cat), cat)
        self.assertEqual(pol.classify_report(SimpleNamespace(task_type="history"), ACCOUNTANT), [])           # classify_report only ever returns the suitability category

    def test_the_existing_observed_requests_are_unchanged(self):
        self.assertEqual(self.cats("Can Kenji afford to retire at 60?", task="forecast_feasibility"), HELD)
        self.assertEqual(self.cats("What did Kenji decide about retirement?"), [])


if __name__ == "__main__":
    unittest.main()
