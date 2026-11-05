"""Stage 1 and 3 rules: noise filtering, certainty guard, topic/value inference.  Run: python -m pytest -q tests"""
import unittest

from test_memory_support import *  # noqa: F401,F403  (sys.path setup)

from agents.memory import ingest as ing  # noqa: E402
from agents.memory import rules  # noqa: E402
from agents.memory.config import FilterConfig  # noqa: E402


class NoiseFilteringTests(unittest.TestCase):
    def selected(self, iid, cfg=None):
        return {t.turn_index: t for t in ing.select_salient_turns(CONV.meeting(iid), cfg)}

    def test_greetings_readouts_and_scheduling_are_dropped(self):
        sel = self.selected("INT-099")                       # Rebecca, care reserve meeting
        for noise in (1, 2, 8, 9, 10, 11, 12, 13):           # agenda, 'keep it tight', portfolio read-out, fund questions, 'nothing urgent'
            self.assertNotIn(noise, sel, f"turn {noise} is noise")
        for keep in (6, 15):                                  # 'Put aside six hundred thousand at closing', the recap decision
            self.assertIn(keep, sel)
        self.assertNotIn("portfolio is about", " ".join(t.text for t in sel.values()))
        self.assertTrue(all(t.reasons for t in sel.values()), "selection reasons are logged")

    def test_snippets_keep_key_sentences_only(self):
        sel = self.selected("INT-099")
        self.assertNotIn("I'll book us for", sel[15].text)
        self.assertNotIn("written recap", sel[15].text)
        self.assertIn("You've decided to set aside six hundred thousand", sel[15].text)

    def test_spending_average_template_dropped(self):
        sel = self.selected("INT-061")
        self.assertNotIn(7, sel)                              # "I'd like to check your spending against what we assumed... about $14,500 a month"
        self.assertIn(11, sel)

    def test_filter_config_is_configurable(self):
        strict = self.selected("INT-001", FilterConfig(min_score=4.5))
        loose = self.selected("INT-001", FilterConfig(min_score=1.0))
        self.assertLess(len(strict), len(loose))
        self.assertEqual(list(strict), [13])
        self.assertLessEqual(len(self.selected("INT-099", FilterConfig(max_turns=2))), 2)

    def test_routine_sentences_score_below_threshold(self):
        cfg = FilterConfig()
        for s in ("Good morning, Kenji. I've put the numbers for today on one page.", "Yes, that's right.", "Does Monday, May 4 work for the next one?",
                  "The portfolio is about $2.11 million today, roughly flat since we last looked.", "Good question."):
            self.assertLess(rules.score_sentence(s, "Advisor", cfg)[0], cfg.min_score, s)


class CertaintyGuardTests(unittest.TestCase):
    def g(self, certainty, text, role="Client"):
        return rules.guard_certainty(certainty, text, role)[0]

    def test_exploratory_wording_without_decision_downgrades_confirmed(self):
        self.assertEqual(self.g("confirmed", K012_T4), "exploratory")
        self.assertEqual(self.g("confirmed", K012_T11, "Advisor"), "exploratory")

    def test_no_decision_wording_downgrades_confirmed_to_unconfirmed(self):
        self.assertEqual(self.g("confirmed", "In retirement I think we'd want fifteen thousand, with more travel and health costs I haven't budgeted for."), "unconfirmed")

    def test_decision_wording_keeps_confirmed(self):
        self.assertEqual(self.g("confirmed", K090_T11, "Advisor"), "confirmed")
        self.assertEqual(self.g("confirmed", K090_T2), "confirmed")
        self.assertEqual(self.g("confirmed", K001_T13, "Advisor"), "confirmed")

    def test_negated_decision_is_not_a_decision(self):
        self.assertFalse(rules.has_decision("I'm only thinking out loud, I haven't decided anything."))
        self.assertTrue(rules.has_decision("You've decided not to lend Brian eighty thousand dollars."))

    def test_over_broad_cue_removed(self):
        # an earlier build treated "Keep the maybes as maybes" as a decision
        self.assertFalse(rules.has_decision("Keep the maybes as maybes."))
        self.assertEqual(self.g("confirmed", "Keep the maybes as maybes."), "unconfirmed")
        self.assertFalse(rules.has_decision("The equity share is about 60 percent, in line with what we agreed."))

    def test_correction_without_correction_wording_downgrades(self):
        self.assertEqual(self.g("correction", "Honestly, the plan has always been 65."), "unconfirmed")
        self.assertEqual(self.g("correction", K049_T17, "Advisor"), "correction")

    def test_upgrades_never_happen(self):
        for weak in ("exploratory", "hypothetical", "unconfirmed", "recommendation"):
            for text in (K090_T11, K090_T2, K049_T17):
                new = rules.guard_certainty(weak, text, "Advisor")[0]
                self.assertLessEqual(rules.CERTAINTY_RANK[new], rules.CERTAINTY_RANK[weak], (weak, text))
        self.assertEqual(self.g("exploratory", K090_T11, "Advisor"), "exploratory")

    def test_client_cannot_make_a_recommendation(self):
        self.assertEqual(self.g("recommendation", "I'd suggest we wait.", "Client"), "unconfirmed")


class InferenceTests(unittest.TestCase):
    def test_kenji_january_plan_is_retirement_age_not_income(self):
        self.assertEqual(rules.infer_topic(K001_T13), "retirement_age")
        self.assertEqual(rules.priority_topic(K001_T13), "retirement_age")
        self.assertEqual(rules.infer_value("retirement_age", K001_T13), "65")

    def test_age_values(self):
        self.assertEqual(rules.infer_value("retirement_age", K090_T11), "63")
        self.assertEqual(rules.infer_value("retirement_age", K074_T13), "63")
        self.assertEqual(rules.infer_value("retirement_age", K049_T17, "correction"), "65")
        self.assertEqual(rules.infer_value("retirement_age", K090_T2), "63")

    def test_allocation_values(self):
        self.assertEqual(rules.infer_value("asset_allocation", "You've decided to raise equities from 25 percent to 30 percent and add bonds."), "30")
        self.assertEqual(rules.infer_value("asset_allocation", "keeping equities at about 35 percent of the portfolio."), "35")

    def test_number_words_and_value_support(self):
        self.assertIn("600000", rules.extract_numbers("six hundred thousand dollars"))
        self.assertIn("35", rules.extract_numbers("thirty-five percent"))
        self.assertTrue(rules.value_supported("65", K001_T13))
        self.assertFalse(rules.value_supported("70", K001_T13))
        self.assertTrue(rules.value_supported("$600,000 at closing", "set aside six hundred thousand dollars of the proceeds at closing"))
        self.assertFalse(rules.value_supported("sell everything immediately", K001_T13))

    def test_value_normalisation_is_short(self):
        self.assertEqual(rules.normalise_value("retirement_age", "age 63"), "63")
        self.assertEqual(rules.normalise_value("retirement_age", "about sixty-five"), "65")
        self.assertIsNone(rules.normalise_value("retirement_age", "not decided"))
        self.assertEqual(rules.normalise_subject("retirement_age", "Kenji's retirement target"), "retirement_age")


if __name__ == "__main__":
    unittest.main()
