"""LLM-first ingestion (agents/memory/llm_first.py): fact checks, blind verifier vote, downgrade-only combination, re-anchoring, persistence.
Scripted models only (MockLLM); every quoted excerpt is genuine text from data/conversations.csv."""
import re
import unittest

from test_memory_support import *  # noqa: F401,F403

from agents.memory import ingest as ing  # noqa: E402
from agents.memory import llm_first  # noqa: E402
from agents.memory.config import CERTAINTY_RANK, OrchestratorConfig  # noqa: E402
from agents.memory.llm_first import VerifiedItem  # noqa: E402
from llm.client import LLMError, MockLLM  # noqa: E402
from schemas.memory import MemoryCandidate  # noqa: E402

K090_T2_SENT = "We've decided: 63."
P057_T4 = "Maybe down to twenty-five percent."
K010_T6 = "I'll make it a priority this week."
K010_T3 = "Last time we agreed you would send over the plan summary for your Northgate deferred compensation account."


class Extract:
    """Scripted extractor: candidates per conversation id ('Conversation INT-090 on ...')."""

    def __init__(self, by_iid):
        self.by_iid, self.prompts = by_iid, []

    def __call__(self, system, user, schema):
        self.prompts.append(user)
        return {"candidates": self.by_iid.get(re.search(r"Conversation (INT-\d+)", user).group(1), [])}


class Verify:
    """Scripted verifier: one verdict for every item shown; records what it was shown (to prove it is blind)."""

    def __init__(self, certainty="confirmed", supported=True, worthy=True, fail=False, target=True):
        self.certainty, self.supported, self.worthy, self.fail, self.target, self.prompts = certainty, supported, worthy, fail, target, []

    def __call__(self, system, user, schema):
        self.prompts.append(user)
        if self.fail:
            return LLMError("verifier down")
        if system == llm_first.TARGET_SYSTEM:
            return {"is_target": self.target}
        n = len(re.findall(r"^Item \d+", user, re.M))
        return {"items": [dict(id=i, certainty=self.certainty, supported=self.supported, worth_remembering=self.worthy) for i in range(n)]}


def llm(extract, verify):
    return MockLLM(handlers={"memory": extract, "memory_verifier": verify})


def run(iid, cands, verify):
    m = llm(Extract({iid: cands}), verify)
    ctx = ing.MeetingContext.load(CONV, iid)
    return llm_first.run(m, m, ctx, CONV, "client")


class CombineTests(unittest.TestCase):
    def c(self, certainty, excerpt=K090_T2_SENT):
        return MemoryCandidate(**cand(2, excerpt, "retirement_age", certainty, value="63"))

    def test_agreement_keeps_label_with_high_confidence(self):
        v = llm_first.combine(self.c("confirmed"), VerifiedItem(id=0, certainty="confirmed", supported=True, worth_remembering=True))
        self.assertEqual((v.final, v.bucket, v.confidence), ("confirmed", "agree_all", llm_first.CONFIDENCE["agree_all"]))

    def test_downgrade_only_both_directions(self):
        for ext, ver in (("confirmed", "exploratory"), ("exploratory", "confirmed"), ("recommendation", "confirmed"), ("confirmed", "unconfirmed")):
            v = llm_first.combine(self.c(ext), VerifiedItem(id=0, certainty=ver, supported=True, worth_remembering=True))
            self.assertLessEqual(CERTAINTY_RANK[v.final], CERTAINTY_RANK[ext], (ext, ver))
            self.assertLessEqual(CERTAINTY_RANK[v.final], CERTAINTY_RANK[ver], (ext, ver))

    def test_regex_hedge_vetoes_two_agreeing_models(self):
        c = MemoryCandidate(**cand(4, P057_T4, "asset_allocation", "confirmed", value="25"))
        v = llm_first.combine(c, VerifiedItem(id=0, certainty="confirmed", supported=True, worth_remembering=True))
        self.assertEqual((v.final, v.bucket), ("exploratory", "regex_hedge"))

    def test_missing_verifier_fails_safe(self):
        v = llm_first.combine(self.c("confirmed"), None)
        self.assertEqual((v.final, v.bucket), ("unconfirmed", "unverified"))


class RunTests(unittest.TestCase):
    def test_whole_conversation_is_shown_to_the_extractor(self):
        ex = Extract({})
        m = llm(ex, Verify())
        llm_first.run(m, m, ing.MeetingContext.load(CONV, "INT-090"), CONV, "Kenji")
        shown = re.findall(r"\[turn (\d+) \|", ex.prompts[0])
        self.assertEqual([int(t) for t in shown], [r["turn_index"] for r in CONV.meeting("INT-090")])

    def test_verifier_is_blind_to_the_extractor_label(self):
        ver = Verify()
        run("INT-090", [cand(2, K090_T2_SENT, "retirement_age", "confirmed", value="63", statement="Kenji decided to retire at 63.")], ver)
        self.assertNotIn("confirmed", ver.prompts[0])
        self.assertIn(K090_T2_SENT, ver.prompts[0])

    def test_fabricated_excerpt_rejected(self):
        decs, _, _ = run("INT-090", [cand(2, "We have decided to retire at sixty next year", "retirement_age", "confirmed", value="60")], Verify())
        self.assertEqual([(d.decision, d.reason) for d in decs], [("rejected", "excerpt does not exist in the cited turn")])

    def test_nonexistent_turn_fails_extraction_after_retries(self):
        decs, _, err = run("INT-090", [cand(99, K090_T2_SENT, "retirement_age", "confirmed", value="63")], Verify())
        self.assertEqual(decs, [])
        self.assertIn("does not exist", err)

    def test_unsupported_or_unworthy_rejected_by_the_verifier(self):
        c = [cand(2, K090_T2_SENT, "retirement_age", "confirmed", value="63")]
        self.assertIn("not supported", run("INT-090", c, Verify(supported=False))[0][0].reason)
        self.assertIn("not worth", run("INT-090", c, Verify(worthy=False))[0][0].reason)

    def test_verifier_failure_stores_only_unconfirmed(self):
        decs, votes, err = run("INT-090", [cand(2, K090_T2_SENT, "retirement_age", "confirmed", value="63")], Verify(fail=True))
        self.assertTrue(decs[0].kept)
        self.assertEqual(decs[0].final.certainty, "unconfirmed")
        self.assertIsNotNone(err)

    def test_bare_confirmation_is_reanchored_to_the_content_turn(self):
        statement = "Kenji agreed to send over the plan summary for his Northgate deferred compensation account."
        decs, _, _ = run("INT-010", [cand(6, K010_T6, "retirement_income", "unconfirmed", statement=statement)], Verify("unconfirmed"))
        d = decs[0]
        self.assertTrue(d.kept)
        self.assertEqual((d.final.turn_index, d.final.speaker_role), (3, "Advisor"))
        self.assertTrue(CONV.verify_excerpt("CLIENT-001", "INT-010", 3, d.final.excerpt))
        self.assertEqual([(x["turn_index"], x["role"]) for x in d.extra_evidence], [(6, "supporting")])

    def test_duplicates_merged(self):
        c = cand(2, K090_T2_SENT, "retirement_age", "confirmed", value="63")
        self.assertEqual([d.decision for d in run("INT-090", [c, c], Verify())[0]], ["accepted", "merged"])


class TargetCheckTests(unittest.TestCase):
    """retirement_age / asset_allocation hold one current value per client: a confirmed number there must be the client's agreed target."""
    P133 = "It is a small diversifier at about five percent, although it is less liquid than the rest."

    def test_holdings_readout_is_not_a_target(self):
        self.assertTrue(CONV.verify_excerpt("CLIENT-001", "INT-133", 9, self.P133))
        decs, votes, _ = run("INT-133", [cand(9, self.P133, "asset_allocation", "confirmed", value="5", role="Advisor",
                                              statement="The real estate fund is a small diversifier at about five percent.")], Verify(target=False))
        self.assertEqual((decs[0].final.certainty, votes[0].bucket), ("unconfirmed", "not_target"))
        self.assertTrue(any("not the client's agreed asset_allocation" in n for n in decs[0].notes))

    def test_agreed_target_passes_and_other_topics_are_not_checked(self):
        ver = Verify(target=True)
        decs, _, _ = run("INT-090", [cand(2, K090_T2_SENT, "retirement_age", "confirmed", value="63")], ver)
        self.assertEqual(decs[0].final.certainty, "confirmed")
        self.assertEqual(sum("Proposed current value" in p for p in ver.prompts), 1)
        c = MemoryCandidate(**cand(2, K090_T2_SENT, "liquidity_reserve", "confirmed", value="6"))
        self.assertEqual(llm_first.check_target(None, c), (c, None))

    def test_unavailable_check_fails_safe(self):
        c = MemoryCandidate(**cand(2, K090_T2_SENT, "retirement_age", "confirmed", value="63"))
        out, note = llm_first.check_target(MockLLM(handlers={}), c)
        self.assertEqual(out.certainty, "unconfirmed")
        self.assertIn("unavailable", note)


class OrchestratorModeTests(unittest.TestCase):
    def test_llm_first_mode_persists_with_measured_confidence_and_consolidates(self):
        m = llm(Extract({"INT-090": [cand(2, K090_T2_SENT, "retirement_age", "confirmed", value="63", statement="Kenji and Mariko decided to retire at 63.")]}), Verify())
        store = MemoryStore(url="sqlite:///:memory:", conv=CONV, clock=Clock())
        orch = MemoryOrchestrator(m, store=store, conv=CONV, config=OrchestratorConfig(ingest_mode="llm_first"))
        orch.init()
        out = orch.ingest_meeting("INT-090")
        self.assertEqual(out["stored"], 1)
        ep = store.records(KENJI, "episodic")
        self.assertEqual([(e["certainty"], e["confidence"], e["origin"]) for e in ep], [("confirmed", llm_first.CONFIDENCE["agree_all"], "conversation_llm")])
        sem = store.records(KENJI, "semantic")
        self.assertEqual([(s["topic"], s["value_text"]) for s in sem], [("retirement_age", "63")])

    def test_default_mode_is_still_rules(self):
        self.assertEqual(OrchestratorConfig().ingest_mode, "rules")


if __name__ == "__main__":
    unittest.main()


class SlotCheckTests(unittest.TestCase):
    """A confirmed value may only supersede the current value of its slot when the verifier says it really replaces it."""
    K068 = "I've decided: if she goes, we'll contribute up to thirty thousand a year."
    K091 = "You've decided to set aside fourteen thousand dollars in cash for Ren's possible semester in Seoul."

    def ingest_two(self, same_thing):
        ext = Extract({"INT-068": [cand(8, self.K068, "education_funding", "confirmed", value="30000", subject="education_funding",
                                        statement="Kenji decided to contribute up to thirty thousand a year if she goes.")],
                       "INT-091": [cand(11, self.K091, "education_funding", "confirmed", value="14000", subject="education_funding", role="Advisor",
                                        statement="You've decided to set aside fourteen thousand dollars in cash for Ren's semester.")]})
        ver = Verify()
        slot_calls = []

        def verifier(system, user, schema):
            if system == llm_first.SLOT_SYSTEM:
                slot_calls.append(user)
                return {"match": 0} if same_thing else {"match": -1, "new_subject": "son_semester_abroad"}
            return ver(system, user, schema)
        m = MockLLM(handlers={"memory": ext, "memory_verifier": verifier})
        store = MemoryStore(url="sqlite:///:memory:", conv=CONV, clock=Clock())
        orch = MemoryOrchestrator(m, store=store, conv=CONV, config=OrchestratorConfig(ingest_mode="llm_first"))
        orch.init()
        orch.ingest_meeting("INT-068")
        orch.ingest_meeting("INT-091")
        return sorted((s["subject"], s["value_text"], s["status"]) for s in store.records(KENJI, "semantic")), slot_calls

    def test_different_thing_gets_its_own_slot(self):
        sem, calls = self.ingest_two(same_thing=False)
        self.assertEqual(len(calls), 1)
        self.assertEqual(sem, [("education_funding", "30000", "active"), ("son_semester_abroad", "14000", "active")])

    def test_real_replacement_still_supersedes(self):
        sem, _ = self.ingest_two(same_thing=True)
        self.assertEqual(sem, [("education_funding", "14000", "active"), ("education_funding", "30000", "superseded")])

    def test_a_fresh_subject_for_the_same_thing_is_merged(self):
        ext = Extract({"INT-068": [cand(8, self.K068, "education_funding", "confirmed", value="30000", subject="graduate_school",
                                        statement="Kenji decided to contribute up to thirty thousand a year if she goes.")],
                       "INT-091": [cand(11, self.K091, "education_funding", "confirmed", value="14000", subject="seoul_semester", role="Advisor",
                                        statement="You've decided to set aside fourteen thousand dollars in cash for Ren's semester.")],
                       "INT-114": []})
        calls = []

        def verifier(system, user, schema):
            if system == llm_first.SLOT_SYSTEM:
                calls.append(user)
                return {"match": -1, "new_subject": ""}
            return Verify()(system, user, schema)
        m = MockLLM(handlers={"memory": ext, "memory_verifier": verifier})
        store = MemoryStore(url="sqlite:///:memory:", conv=CONV, clock=Clock())
        orch = MemoryOrchestrator(m, store=store, conv=CONV, config=OrchestratorConfig(ingest_mode="llm_first"))
        orch.init()
        orch.ingest_meeting("INT-068")
        orch.ingest_meeting("INT-091")
        self.assertEqual(len(calls), 1)                                   # a second, unrelated value on the topic: asked once, kept separate
        self.assertEqual(sorted(s["subject"] for s in store.records(KENJI, "semantic")), ["graduate_school", "seoul_semester"])
        # the same semester mentioned again under yet another name: the verifier matches it to existing memory 1 -> merged, not a third slot
        c = MemoryCandidate(**cand(11, self.K091, "education_funding", "confirmed", value="14000", subject="ren_seoul_program", role="Advisor"))

        def match_seoul(system, user, schema):
            n = next(i for i, line in enumerate(re.findall(r"^\d+\. \(([^,]+),", user, re.M)) if "seoul" in line)
            return {"match": n, "new_subject": ""}
        out, note = llm_first.check_slot(MockLLM(handlers={"memory_verifier": match_seoul}), store, ing.MeetingContext.load(CONV, "INT-091"), c)
        self.assertEqual(out.subject, "seoul_semester")
        self.assertIn("confirms", note)

    def test_single_valued_topics_never_split(self):
        c = MemoryCandidate(**cand(2, K090_T2_SENT, "retirement_age", "confirmed", value="63", subject="retirement_age"))
        out, note = llm_first.check_slot(None, object(), ing.MeetingContext.load(CONV, "INT-090"), c)
        self.assertEqual((out.subject, note), ("retirement_age", None))
