"""Spec section J walkthroughs on the real conversations: Kenji (retirement target chain), Rebecca (employee ownership + liquidity), Peg (open items)."""
import unittest

from test_memory_support import *  # noqa: F401,F403


def ingest(orch, ids):
    for i in ids:
        orch.ingest_meeting(i)


class KenjiChainTests(unittest.TestCase):
    def setUp(self):
        self.orch, self.store, _, _ = make(KENJI_SCRIPT)
        ingest(self.orch, KENJI_MEETINGS)

    def versions(self):
        return sorted(self.store.records(KENJI, "semantic", topics=["retirement_age"]), key=lambda r: r["version_no"])

    def test_v1_65_superseded_v2_63_active(self):
        v1, v2 = self.versions()
        self.assertEqual((v1["version_no"], v1["value_text"], v1["status"], v1["valid_from"], v1["valid_to"]), (1, "65", "superseded", "2026-01-02", "2026-05-01"))
        self.assertEqual((v2["version_no"], v2["value_text"], v2["status"], v2["valid_from"], v2["valid_to"]), (2, "63", "active", "2026-05-01", None))
        self.assertEqual(v2["supersedes_id"], v1["memory_id"])
        self.assertEqual(v1["origin"], "consolidation")
        self.assertEqual(self.orch.consolidator.validate_chains(KENJI), [])

    def test_january_plan_of_65_lands_under_retirement_age(self):
        # the earlier build filed it under retirement_income, so the history started in March
        eps = [e for e in self.store.records(KENJI, "episodic") if e["interaction_id"] == "INT-001"]
        self.assertEqual([(e["topic"], e["value_text"], e["certainty"]) for e in eps], [("retirement_age", "65", "confirmed")])
        self.assertEqual(self.store.count("memory_records", "client_id=? AND topic='retirement_income'", (KENJI,)), 0)

    def test_exploratory_and_correction_episodes_kept_but_never_change_the_value(self):
        eps = sorted(self.store.records(KENJI, "episodic"), key=lambda e: (e["event_ts"], e["turn_index"]))
        self.assertEqual([(e["event_date"], e["certainty"], e["value_text"]) for e in eps],
                         [("2026-01-02", "confirmed", "65"), ("2026-01-16", "exploratory", "60"), ("2026-01-16", "exploratory", "60"), ("2026-03-04", "correction", "65"),
                          ("2026-04-16", "exploratory", "63"), ("2026-05-01", "confirmed", "63"), ("2026-05-01", "confirmed", "63")])
        self.assertTrue(all(e["status"] == "active" for e in eps))
        self.assertNotIn("60", {v["value_text"] for v in self.versions()})
        # the 4 January 16 turn was over-claimed as confirmed by the model and downgraded by the code guard
        self.assertEqual(self.store.query("SELECT certainty_in, certainty_out FROM {S}memory_candidates WHERE interaction_id='INT-012' AND turn_index=4 AND origin='conversation_llm'"),
                         [{"certainty_in": "confirmed", "certainty_out": "exploratory"}])

    def test_correction_reinforces_v1_without_a_new_version(self):
        v1, _ = self.versions()
        self.assertEqual(v1["supporting_episodes"], 2)
        rels = {(r.rel_type, r.source_id[:8]) for r in self.store.relationships_among([e["memory_id"] for e in self.store.records(KENJI, "episodic")] + [v1["memory_id"]])}
        self.assertIn(("supports", "MEM-EPI-"), rels)
        corr = next(e for e in self.store.records(KENJI, "episodic") if e["certainty"] == "correction")
        cors = self.store.query("SELECT t.value_text, t.certainty, t.interaction_id FROM {S}memory_relationships r JOIN (SELECT m.memory_id, m.value_text, m.certainty, e.interaction_id FROM {S}memory_records m "
                                "JOIN {S}episodic_details e ON e.memory_id=m.memory_id) t ON t.memory_id=r.target_id WHERE r.source_id=? AND r.rel_type='corrects'", (corr["memory_id"],))
        self.assertEqual(cors[0]["value_text"], "60")                  # links to the corrected claim (the 60 exploration)
        self.assertEqual(cors[0]["certainty"], "exploratory")          # an exploration is not a decision, so it is NOT superseded
        self.assertEqual(self.store.get(self.store.query("SELECT target_id FROM {S}memory_relationships WHERE source_id=? AND rel_type='corrects'", (corr["memory_id"],))[0]["target_id"])["status"], "active")

    def test_exploratory_episodes_link_with_explores(self):
        n = self.store.count("memory_relationships", "rel_type='explores'")
        self.assertEqual(n, 3)                                          # two 60s explore v1, the 63 exploration explores v1 as well (still current then)
        for r in self.store.query("SELECT * FROM {S}memory_relationships WHERE rel_type IN ('supersedes','derived_from')"):
            self.assertNotEqual(r["source_id"], r["target_id"])

    def test_current_semantic_evidence_is_the_deciding_turn(self):
        ctx = self.orch.retrieve(plan("What is Kenji's current retirement target, and how did it evolve?", [KENJI], ["retirement_age"], cats=("semantic", "episodic"),
                                      task_type="history", needs_history=True))[KENJI]
        self.assertEqual([(m.value_text, m.valid_from, m.status) for m in ctx.current], [("63", "2026-05-01", "active")])
        self.assertEqual((ctx.current[0].evidence[0].interaction_id, ctx.current[0].evidence[0].turn_index), ("INT-090", 2))
        self.assertEqual([(m.value_text, m.valid_to, m.status) for m in ctx.history], [("65", "2026-05-01", "superseded")])
        self.assertEqual([e.event_date for e in ctx.episodes], sorted(e.event_date for e in ctx.episodes))
        self.assertEqual(len(ctx.episodes), 7)
        self.assertEqual(ctx.unresolved, [])                           # explorations were settled by the decision
        self.assertEqual(ctx.episodes_omitted, 0)
        self.assertTrue(all(e.topic == "retirement_age" for e in ctx.episodes))   # equity allocation / Northgate shares are not returned
        for e in ctx.episodes:
            self.assertTrue(e.evidence and CONV.verify_excerpt(KENJI, e.evidence[0].interaction_id, e.evidence[0].turn_index, e.evidence[0].excerpt))

    def test_as_of_returns_the_value_that_was_valid_then(self):
        ctx = self.orch.retrieve(plan("What was Kenji's retirement target as of 2026-04-20?", [KENJI], ["retirement_age"], time_period="as of 2026-04-20"))[KENJI]
        self.assertEqual([m.value_text for m in ctx.current], ["65"])
        self.assertTrue(all(e.event_date <= "2026-04-20" for e in ctx.episodes))
        self.assertEqual([m.value_text for m in ctx.history], [])

    def test_unresolved_before_the_decision(self):
        orch, store, _, _ = make(KENJI_SCRIPT)
        ingest(orch, KENJI_MEETINGS[:4])                                # up to INT-074: the 63 is only being considered
        ctx = orch.retrieve(plan("open items for Kenji", [KENJI], [], task_type="followup_review"))[KENJI]
        self.assertEqual([(u.event_date, u.certainty, u.value_text) for u in ctx.unresolved], [("2026-04-16", "exploratory", "63")])
        self.assertEqual(ctx.episodes, [])                              # follow-ups without a topic surface open items only


class RebeccaTests(unittest.TestCase):
    def setUp(self):
        self.orch, self.store, _, _ = make(REBECCA_SCRIPT)
        ingest(self.orch, list(REBECCA_SCRIPT))

    def test_employee_ownership_decision(self):
        eps = [e for e in self.store.records(REBECCA, "episodic") if e["interaction_id"] == "INT-061"]
        self.assertEqual([(e["topic"], e["certainty"], e["turn_index"], e["event_date"]) for e in eps], [("business_sale", "confirmed", 11, "2026-03-26")])
        ev = self.store.evidence_map([eps[0]["memory_id"]])[eps[0]["memory_id"]][0]
        self.assertEqual((ev.interaction_id, ev.turn_index), ("INT-061", 11))
        self.assertIn("ten-year seller note", ev.excerpt)
        sem = self.store.records(REBECCA, "semantic", topics=["business_sale"])
        self.assertEqual([(s["value_text"], s["status"], s["valid_from"], s["origin"]) for s in sem], [("decline employee ownership", "active", "2026-03-26", "consolidation")])

    def test_liquidity_and_care_reserve(self):
        ctx = self.orch.retrieve(plan("Rebecca's liquidity, care reserve and the sale", [REBECCA], ["liquidity_reserve", "care_costs", "business_sale"], cats=("semantic", "episodic")))[REBECCA]
        by = {(e.topic, e.certainty): e for e in ctx.episodes}
        self.assertIn(("care_costs", "confirmed"), by)                  # INT-099 decision
        self.assertEqual(by[("care_costs", "confirmed")].evidence[0].interaction_id, "INT-099")
        self.assertEqual(by[("liquidity_reserve", "recommendation")].evidence[0].interaction_id, "INT-112")   # advisor wants about 18 months
        self.assertEqual(by[("liquidity_reserve", "unconfirmed")].status, "unconfirmed")          # the $220,000 read-out is not a client decision
        # no semantic liquidity value: only confirmed/correction episodes with a value may create semantic memory
        self.assertEqual([m.topic for m in ctx.current], ["business_sale", "care_costs"])
        # the care reserve is conditional on closing and must not be presented as an asset
        self.assertTrue(any("not a current asset" in n and by[("care_costs", "confirmed")].memory_id in n for n in ctx.notes))
        # the unmet recommendation and the read-out are open items
        self.assertEqual({u.certainty for u in ctx.unresolved}, {"recommendation", "unconfirmed"})

    def test_no_kenji_data_in_rebeccas_context(self):
        self.assertEqual(self.store.records(KENJI, "episodic"), [])


class PegOpenItemsTests(unittest.TestCase):
    """The pension survivor-benefit story belongs to Grace (CLIENT-004, INT-050), not Peg.  Peg's real open items: Maplewood Commons weighed then declined 'for now'
    (INT-072 -> INT-125) and Brian's equipment contribution, still only an idea after the loan was declined (INT-063)."""

    def test_open_items_until_a_later_decision_resolves_them(self):
        orch, store, _, _ = make(PEG_OPEN_SCRIPT)
        ingest(orch, ["INT-063", "INT-072"])
        ctx = orch.retrieve(plan("What is still unresolved for Peg?", [PEG], [], task_type="followup_review"))[PEG]
        self.assertEqual({(u.subject, u.certainty, u.evidence[0].interaction_id) for u in ctx.unresolved},
                         {("brian_equipment", "exploratory", "INT-063"), ("maplewood_commons", "exploratory", "INT-072")})
        self.assertNotIn("brian_loan", {u.subject for u in ctx.unresolved})          # the loan was decided (declined)
        ingest(orch, ["INT-125"])
        ctx = orch.retrieve(plan("What is still unresolved for Peg?", [PEG], [], task_type="followup_review", rid="REQ-2"))[PEG]
        self.assertEqual([(u.subject, u.certainty) for u in ctx.unresolved], [("brian_equipment", "exploratory")])     # Maplewood now settled, Brian equipment still open
        self.assertEqual([e for e in ctx.episodes], [])

    def test_peg_pension_survivor_amount_stays_open(self):
        """Peg DOES have a survivor-benefit open item: INT-009 records Frank's pension survivor amount as unconfirmed (she believes about half) and INT-119 still
        uses her own estimate 'until you have checked the booklet'.  It is never confirmed, so it must stay unresolved and must never become semantic memory."""
        s1 = "The working plan I'm recording is treating the survivor amount of Frank's pension as unconfirmed, with you believing it is about half."
        s2 = "The working plan I'm recording is using your own estimate of about half for the survivor income from Frank's pension in the stress case, until you have checked the booklet."
        script = {"INT-009": [cand(15, s1, "insurance_estate", "confirmed", "about half", subject="survivor_benefit", role="Advisor")],     # over-claimed 'confirmed'
                  "INT-119": [cand(19, s2, "insurance_estate", "unconfirmed", "about half", subject="survivor_benefit", role="Advisor")]}
        orch, store, _, _ = make(script)
        ingest(orch, ["INT-009", "INT-119"])
        eps = sorted(store.records(PEG, "episodic", topics=["insurance_estate"]), key=lambda e: e["event_date"])
        self.assertEqual([(e["interaction_id"], e["certainty"], e["status"], e["topic"]) for e in eps],
                         [("INT-009", "unconfirmed", "unconfirmed", "insurance_estate"), ("INT-119", "unconfirmed", "unconfirmed", "insurance_estate")])
        self.assertEqual(store.records(PEG, "semantic"), [])
        ctx = orch.retrieve(plan("What is still unresolved for Peg?", [PEG], [], task_type="followup_review"))[PEG]
        self.assertEqual([(u.event_date, u.evidence[0].interaction_id) for u in ctx.unresolved], [("2026-01-12", "INT-009"), ("2026-06-02", "INT-119")])
        ctx = orch.retrieve(plan("Frank's pension survivor benefit", [PEG], ["insurance_estate"], rid="REQ-3"))[PEG]
        self.assertEqual(ctx.current, [])                              # no confirmed value: nothing is presented as known
        self.assertEqual(len(ctx.unresolved), 2)

    def test_equity_allocation_35_25_30(self):
        orch, store, _, _ = make(PEG_EQUITY_SCRIPT)
        ingest(orch, ["INT-003", "INT-057", "INT-060", "INT-087"])
        vs = sorted(store.records(PEG, "semantic", topics=["asset_allocation"]), key=lambda r: r["version_no"])
        self.assertEqual([(v["value_text"], v["status"]) for v in vs], [("35", "superseded"), ("25", "superseded"), ("30", "active")])
        self.assertEqual(orch.consolidator.validate_chains(PEG), [])
        ctx = orch.retrieve(plan("Peg's equity allocation", [PEG], ["asset_allocation"], task_type="history", needs_history=True))[PEG]
        self.assertEqual(ctx.unresolved, [])                           # the 25 exploration was settled by INT-060


class GraceSurvivorCorrectionTests(unittest.TestCase):
    """Grace (CLIENT-004): INT-029 advisor summary 'about half', corrected in INT-050 to 'sixty percent for life, with a lump-sum alternative not yet evaluated'."""

    def test_correction_links_to_the_earlier_claim_and_keeps_it(self):
        s1 = "The company sent a summary. It gives you a lifetime survivor payment of about half his pension, or a lump sum option."
        s2 = "I've corrected my own record: my February summary that the survivor payment was about half of Samuel's pension was imprecise; the form says sixty percent for life, with a lump-sum alternative not yet evaluated."
        script = {"INT-029": [cand(15, "It gives you a lifetime survivor payment of about half his pension, or a lump sum option.", "insurance_estate", "unconfirmed", "about half",
                                   subject="survivor_payment", role="Advisor")],
                  "INT-050": [cand(17, s2, "insurance_estate", "correction", "sixty percent for life", subject="survivor_payment", role="Advisor", corrects="half")]}
        orch, store, _, _ = make(script)
        ingest(orch, ["INT-029", "INT-050"])
        old = next(e for e in store.records(GRACE, "episodic") if e["interaction_id"] == "INT-029")
        self.assertEqual((old["certainty"], old["status"]), ("unconfirmed", "unconfirmed"))      # content immutable, not rewritten
        corr = store.query("SELECT target_id FROM {S}memory_relationships WHERE rel_type='corrects'")
        self.assertEqual([c["target_id"] for c in corr], [old["memory_id"]])
        sem = store.records(GRACE, "semantic")
        self.assertEqual([(m["value_text"], m["status"]) for m in sem], [("sixty percent for life", "active")])
        ctx = orch.retrieve(plan("Grace's survivor payment", [GRACE], ["insurance_estate"], cats=("semantic", "episodic")))[GRACE]
        self.assertEqual([e.certainty for e in ctx.episodes], ["unconfirmed", "correction"])
        self.assertEqual([u.certainty for u in ctx.unresolved], [])    # the unconfirmed claim was settled by the later correction


class CorrectionSupersedesWrongDecisionTests(unittest.TestCase):
    """Rebecca INT-058: 'my earlier note that you had accepted the Lakeshore offer was wrong'.  The wrongly recorded decision itself is not in the CSV, so it is
    seeded directly from the real INT-058 turn 3 wording; the correction goes through the normal pipeline."""

    def test_confirmed_decision_is_superseded_only_by_a_correction_of_that_value(self):
        orch, store, _, _ = make({"INT-058": [cand(13, "I've corrected my own record: my earlier note that you had accepted the Lakeshore offer was wrong; you are inclined to accept and have not signed.",
                                                    "business_sale", "correction", None, subject="lakeshore_offer", role="Advisor", corrects="accepted")]})
        ex = "After our last meeting, my note says you had accepted the Lakeshore offer."
        rec = dict(memory_id="MEM-EPI-seeded0001", client_id=REBECCA, memory_type="episodic", topic="business_sale", subject="lakeshore_offer", content="accepted the Lakeshore offer",
                   value_text="accepted", certainty="confirmed", source_role="Advisor", status="active", origin="conversation_llm", event_date=CONV.turn("INT-058", 3)["interaction_datetime"][:10], content_hash="seed1")
        det = dict(interaction_id="INT-058", turn_index=3, event_ts=CONV.turn("INT-058", 3)["interaction_datetime"], speaker_role="Advisor", event_type="decision")
        store.insert_memory(rec, det, [dict(source_type="conversation", interaction_id="INT-058", turn_index=3, speaker="Advisor", excerpt=ex)], principal=svc("ingestion", REBECCA))
        orch.ingest_meeting("INT-058")
        self.assertEqual(store.get("MEM-EPI-seeded0001")["status"], "superseded")
        events = store.lifecycle_events("MEM-EPI-seeded0001")
        self.assertEqual([(e["from_status"], e["to_status"]) for e in events], [(None, "active"), ("active", "superseded")])
        self.assertEqual(events[-1]["actor"], "consolidation")
        self.assertTrue(events[-1]["related_memory_id"].startswith("MEM-EPI-"))


if __name__ == "__main__":
    unittest.main()
