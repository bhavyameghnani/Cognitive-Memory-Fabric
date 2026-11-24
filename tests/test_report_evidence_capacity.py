"""B2A: Report Agent evidence capacity and selection (agents/report/evidence.py: episode_groups / pick_episodes / select_for_prompt; agents/report/agent.py).

The failure these tests guard: retrieval can supply many more episodes and source turns than the report keeps, and the model is shown only a bounded part of what is kept.  The OLD rules kept
the oldest 12 episodes and the first 16 evidence lines, and the Phase 0 rule that replaced the first one let a flood of exploratory remarks push a confirmed decision out.  Selection must keep
the decisions, the changes and what a change replaced, rank tentative remarks BELOW them, never alter a certainty or a verification, and say what it left out.
Synthetic fixtures only; the real-seed scenario is covered by the three end-to-end tests listed in the final report (tests/test_e2e_api.py).
"""
import json
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_report_agent import K, agent_for, env, ex, llm_out, mem, plan, tstate  # noqa: E402
from agents.report.evidence import (DECISION_CERTAINTY, EPISODES_PER_CLIENT, PROMPT_EVIDENCE_CHARS, PROMPT_ITEMS, TURN_SHARE, build_evidence, episode_groups, pick_episodes,  # noqa: E402
                                    prompt_lines, prompt_tier, select_for_prompt)
from schemas.memory import EvidenceRef, MemoryContext  # noqa: E402
from schemas.report import EvidenceItem, is_source_turn  # noqa: E402

OTHER = "CLIENT-009"


def ep(i, certainty="exploratory", value="60", verified=False, status="active", corrects=None, topic="retirement_age", speaker="Client", client=K):
    """Episode number i (chronological: the date follows i).  Unverified unless verified=True; never anything else."""
    day = f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}"
    m = mem(f"EP-{i:03d}", "episodic", value, status, certainty, event=day, client=client, topic=topic, content=f"Statement number {i} about {topic} {value}.",
            ev=[EvidenceRef(interaction_id=f"INT-{i:03d}", turn_index=2, speaker=speaker, excerpt=f"Excerpt {i}.")])
    return m.model_copy(update=dict(verification="verified" if verified else "unverified", corrects_value=corrects))


def ids(items):
    return [m.memory_id for m in items]


def turn_env(interaction, turns, date="2026-03-04"):
    """A get_source_evidence conversation-mode result: turns = [(turn_index, speaker, text)]."""
    return env("get_source_evidence", {"turns": [{"interaction_id": interaction, "turn_index": i, "speaker": s, "text": x} for i, s, x in turns]}, as_of=date)


# ============================================================================================================ 1-3 decisions, changes, superseded history
class Selection(unittest.TestCase):
    def test_the_latest_confirmed_decision_survives_a_flood_of_newer_exploratory_episodes(self):
        eps = [ep(i) for i in range(5)] + [ep(5, "confirmed", "63")] + [ep(i) for i in range(6, 41)]          # 35 exploratory remarks AFTER the decision
        shown, omitted = pick_episodes(eps, 8)
        self.assertIn("EP-005", ids(shown))
        self.assertEqual(episode_groups(eps)[5], 0)                                                             # it is the current decision of its slot
        self.assertEqual(len(shown), 8)
        self.assertEqual(ids(shown), sorted(ids(shown)))                                                        # chronological order is kept
        self.assertEqual(sum(m.certainty == "exploratory" for m in shown), 7)                                   # the exploratory remarks fill what is left, they do not displace it

    def test_the_old_phase0_failure_a_decision_among_many_exploratory_remarks(self):
        """21 episodes like the Kenji seed: 10 exploratory, 1 correction, 1 verified, 6 unverified decisions, 3 unconfirmed.  Every decision, the correction and what it replaced are kept."""
        eps = ([ep(0, "exploratory", "65"), ep(1, "confirmed", "65"), ep(2, "exploratory", "60"), ep(3, "unconfirmed", "60"), ep(4, "exploratory", "60"), ep(5, "unconfirmed", "65"),
                ep(6, "exploratory", "60"), ep(7, "correction", "65", corrects="60"), ep(8, "unconfirmed", "65"), ep(9, "exploratory", "63"), ep(10, "exploratory", "63"),
                ep(11, "confirmed", "63"), ep(12, "exploratory", "63"), ep(13, "exploratory", "63"), ep(14, "exploratory", "63"), ep(15, "confirmed", "63", verified=True),
                ep(16, "exploratory", "65"), ep(17, "unconfirmed", "63"), ep(18, "confirmed", "63"), ep(19, "confirmed", "63"), ep(20, "confirmed", "63")])
        shown, omitted = pick_episodes(eps, EPISODES_PER_CLIENT)
        decisions = [m for m in eps if m.certainty in DECISION_CERTAINTY]
        self.assertTrue(set(ids(decisions)) <= set(ids(shown)), set(ids(decisions)) - set(ids(shown)))
        self.assertIn("EP-006", ids(shown))                                                                      # the latest 60 that the correction (EP-007, corrects 60) replaced
        self.assertFalse([m for m in omitted if m.certainty in DECISION_CERTAINTY or m.corrects_value], ids(omitted))   # no decision and no change record is left out: only tentative remarks

    def test_an_earlier_decision_stays_available_to_explain_a_later_correction(self):
        eps = ([ep(0, "confirmed", "65")] + [ep(i) for i in range(1, 11)] + [ep(11, "correction", "63", corrects="65")] + [ep(i, value="63") for i in range(12, 31)] + [ep(31, "confirmed", "63")])
        shown, _ = pick_episodes(eps, 5)
        self.assertTrue({"EP-031", "EP-011", "EP-000"} <= set(ids(shown)), ids(shown))                          # current decision, the correction, and the decision it corrected
        g = episode_groups(eps)
        self.assertEqual((g[31], g[11], g[0]), (0, 1, 2))

    def test_a_correction_that_names_no_value_still_keeps_the_decision_it_replaced(self):
        eps = [ep(0, "confirmed", "65")] + [ep(i) for i in range(1, 12)] + [ep(12, "correction", "63"), ep(13, "confirmed", "63")]
        shown, _ = pick_episodes(eps, 4)
        self.assertTrue({"EP-000", "EP-012", "EP-013"} <= set(ids(shown)), ids(shown))

    def test_corrections_are_not_always_below_confirmed_decisions_and_not_always_above(self):
        """Not a simplistic rule: a correction outranks an ORDINARY earlier decision of its slot, but the current decision outranks everything."""
        eps = [ep(i, "confirmed", "63") for i in range(10)] + [ep(10, "correction", "63"), ep(11, "confirmed", "63")]
        shown, omitted = pick_episodes(eps, 3)
        self.assertIn("EP-011", ids(shown))                                                                      # the latest decision
        self.assertIn("EP-010", ids(shown))                                                                      # the correction record, kept although 9 plain decisions compete
        self.assertEqual(len(omitted), 9)

    def test_corrected_superseded_and_disputed_evidence_is_not_silently_discarded(self):
        eps = [ep(0, "confirmed", "65", status="superseded")] + [ep(i) for i in range(1, 30)] + [ep(30, "confirmed", "63"), ep(31, "confirmed", "63", status="disputed")]
        shown, omitted = pick_episodes(eps, 4)
        self.assertTrue({"EP-000", "EP-031", "EP-030"} <= set(ids(shown)), ids(shown))                          # superseded and disputed history is kept
        self.assertTrue(all(m.certainty == "exploratory" for m in omitted))

    def test_omitted_decisions_are_counted_and_said_in_the_report(self):
        eps = [ep(i, "confirmed", "63") for i in range(20)]                                                       # 20 plain decisions, room for 16
        ctx = MemoryContext(client_id=K, episodes=eps)
        items, removed = build_evidence(plan(), {K: ctx}, tstate())
        (o,) = removed["episodes_omitted"]
        self.assertEqual((o["total"], o["shown"], len(o["ids"]), o["critical"], o["decisions"]), (20, EPISODES_PER_CLIENT, 4, 4, 4))
        a, _ = agent_for(llm_out())
        r = a.write("q", plan(), {K: ctx}, tstate())
        self.assertTrue(any("4 omitted, 4 of them recording a decision, correction or superseded value" in u for u in r.uncertainties), r.uncertainties)

    def test_off_topic_episodes_rank_last_but_plan_topics_that_match_nothing_change_nothing(self):
        eps = [ep(0, "confirmed", "63", topic="retirement_age"), ep(1, "confirmed", "5", topic="liquidity_reserve"), ep(2, "exploratory", "63", topic="retirement_age")]
        self.assertEqual(ids(pick_episodes(eps, 2, ["retirement_age"])[0]), ["EP-000", "EP-002"])                # the on-topic exploratory remark beats an off-topic decision
        self.assertEqual(ids(pick_episodes(eps, 2, ["no_such_topic"])[0]), ["EP-000", "EP-001"])                # the plan matches nothing: the decision ranking decides
        self.assertEqual(ids(pick_episodes(eps, 2)[0]), ["EP-000", "EP-001"])

    def test_one_representative_of_every_considered_value_before_any_value_twice(self):
        eps = [ep(i, value="60") for i in range(10)] + [ep(10, value="63"), ep(11, value="58")] + [ep(i, value="60") for i in range(12, 20)]
        shown, _ = pick_episodes(eps, 4)
        self.assertTrue({"EP-010", "EP-011"} <= set(ids(shown)), ids(shown))                                   # the 63 and the 58 that were considered, besides the latest 60

    def test_selection_is_deterministic(self):
        eps = [ep(i, c, v) for i, (c, v) in enumerate([("confirmed", "65"), ("exploratory", "60"), ("correction", "63"), ("confirmed", "63"), ("unconfirmed", "63")] * 8)]
        first = pick_episodes(eps, 10)
        for _ in range(3):
            self.assertEqual([ids(x) for x in pick_episodes(list(eps), 10)], [ids(x) for x in first])
        self.assertEqual(ids(first[0]), sorted(ids(first[0])))


# ============================================================================================================ 4-6 tentative stays tentative; attribution; provenance
class Fidelity(unittest.TestCase):
    def test_selection_never_changes_a_certainty_or_a_verification(self):
        eps = [ep(i, c, "63", verified=(i % 7 == 0)) for i, c in enumerate(["exploratory", "confirmed", "unconfirmed", "hypothetical", "recommendation"] * 8)]
        before = [m.model_dump() for m in eps]
        shown, _ = pick_episodes(eps, 10)
        self.assertEqual([m.model_dump() for m in eps], before)
        for m in shown:
            src = next(x for x in eps if x.memory_id == m.memory_id)
            self.assertEqual((m.certainty, m.verification, m.status, m.value_text), (src.certainty, src.verification, src.status, src.value_text))

    def test_tentative_and_unverified_evidence_keeps_its_class_in_the_evidence_list_and_in_the_prompt(self):
        eps = [ep(0, "exploratory", "60"), ep(1, "unconfirmed", "60"), ep(2, "confirmed", "63"), ep(3, "exploratory", "63", verified=True)]
        items, _ = build_evidence(plan(), {K: MemoryContext(client_id=K, episodes=eps)}, tstate())
        by = {e.memory_id: e for e in items}
        self.assertEqual((by["EP-000"].evidence_class, by["EP-001"].evidence_class), ("unverified", "unverified"))
        self.assertEqual(by["EP-002"].evidence_class, "unverified")                                              # a confirmed-looking episode the boundary did not verify is NOT verified
        self.assertEqual(by["EP-003"].evidence_class, "ambiguous")                                                # verified but exploratory: not a decision
        self.assertEqual((by["EP-000"].certainty, by["EP-003"].certainty), ("exploratory", "exploratory"))
        lines = {ln.split()[0].lstrip("["): ln for ln in prompt_lines(items)}
        for e in items:
            if e.evidence_class == "unverified":
                self.assertIn("UNVERIFIED", lines[e.ref])

    def test_who_said_it_survives_selection_and_the_prompt(self):
        eps = [ep(0, "confirmed", "63", speaker="Advisor"), ep(1, "confirmed", "63", speaker="Client")] + [ep(i) for i in range(2, 30)]
        items, _ = build_evidence(plan(), {K: MemoryContext(client_id=K, episodes=eps)}, tstate())
        text = "\n".join(prompt_lines(items))
        self.assertIn("said by Advisor in INT-000 turn 2", text)
        self.assertIn("said by Client in INT-001 turn 2", text)

    def test_source_turns_keep_speaker_and_wording_and_both_sides_of_an_exchange_are_shown(self):
        t = tstate(ex(1, turn_env("INT-049", [(3, "Advisor", "I understood that you wanted to retire at 60. Is that right?"), (4, "Client", "I was only exploring the possibility."),
                                              (5, "Advisor", "The wording of my file note was too strong.")])))
        items, _ = build_evidence(plan(), {}, t)
        lines = prompt_lines(items)
        self.assertEqual(len(lines), 3)
        joined = "\n".join(lines)
        self.assertRegex(joined, r"INT-049 turn 4, recorded speaker Client .*I was only exploring the possibility")
        self.assertRegex(joined, r"INT-049 turn 5, recorded speaker Advisor .*too strong")
        turn = next(e for e in items if e.turn_index == 4)
        self.assertEqual((turn.interaction_id, turn.speaker, turn.event_date, turn.excerpt, turn.source_class), ("INT-049", "Client", "2026-03-04", "I was only exploring the possibility.", "conversation_evidence"))
        self.assertTrue(turn.tool_execution_id)

    def test_episode_provenance_fields_survive_selection(self):
        eps = [ep(i, "exploratory") for i in range(30)] + [ep(30, "confirmed", "63", verified=True)]
        items, _ = build_evidence(plan(), {K: MemoryContext(client_id=K, episodes=eps)}, tstate())
        e = next(x for x in items if x.memory_id == "EP-030")
        self.assertEqual((e.interaction_id, e.turn_index, e.event_date, e.client_id, e.certainty, e.verification, e.memory_type, e.value, e.excerpt),
                         ("INT-030", 2, "2026-02-03", K, "confirmed", "verified", "episodic", "63", "Excerpt 30."))


# ============================================================================================================ 7 duplicates
class Duplicates(unittest.TestCase):
    def test_a_source_turn_read_by_two_calls_is_one_piece_of_evidence(self):
        t = tstate(ex(1, turn_env("INT-049", [(3, "Advisor", "Is that right?"), (4, "Client", "I was only exploring the possibility.")])),
                   ex(2, turn_env("INT-049", [(4, "Client", "I was only exploring the possibility."), (5, "Advisor", "The wording was too strong.")])))
        items, removed = build_evidence(plan(), {}, t)
        turns = [(e.interaction_id, e.turn_index) for e in items if is_source_turn(e)]
        self.assertEqual(turns, [("INT-049", 3), ("INT-049", 4), ("INT-049", 5)])
        self.assertEqual(removed["duplicate_source_turns"], ["INT-049#4 (also in EX-REQ-R-02)"])
        self.assertEqual(sum("exploring the possibility" in ln for ln in prompt_lines(items)), 1)
        self.assertEqual(next(e for e in items if e.turn_index == 4).tool_execution_id, "EX-REQ-R-01")           # the first read is the one kept

    def test_the_same_memory_in_two_buckets_is_listed_once(self):
        m = ep(3, "confirmed", "63", verified=True)
        items, _ = build_evidence(plan(), {K: MemoryContext(client_id=K, episodes=[m], current=[m])}, tstate())
        self.assertEqual(sum(e.memory_id == "EP-003" for e in items), 1)


# ============================================================================================================ 8 omissions are surfaced
class Omissions(unittest.TestCase):
    def test_evidence_the_model_was_not_shown_is_said_and_listed(self):
        eps = [mem(f"S-{i}", "semantic", str(i), "superseded", "confirmed", content="x" * 800, topic=f"t{i}") for i in range(40)]
        a, llm = agent_for(llm_out())
        r = a.write("q", plan(), {K: MemoryContext(client_id=K, history=eps)}, tstate())
        shown, hidden = r.validation["evidence_in_prompt_refs"], r.validation["evidence_not_in_prompt_refs"]
        self.assertTrue(hidden)
        self.assertEqual(sorted(shown + hidden, key=lambda x: int(x[1:])), [e.ref for e in r.evidence])           # every item is accounted for: shown to the model or listed as not shown
        self.assertTrue(any(f"{len(hidden)} of {len(r.evidence)} evidence items were not shown to the model" in u for u in r.uncertainties), r.uncertainties)
        self.assertTrue(set(hidden) <= {e.ref for e in r.evidence})                                               # hidden evidence is still in the evidence list the verifier checks

    def test_nothing_is_reported_when_everything_fits(self):
        a, _ = agent_for(llm_out())
        r = a.write("q", plan(), {K: MemoryContext(client_id=K, episodes=[ep(0, "confirmed", "63")])}, tstate())
        self.assertEqual(r.validation["evidence_not_in_prompt_refs"], [])
        self.assertFalse(any("not shown to the model" in u or "stored episodes" in u for u in r.uncertainties))

    def test_the_retrieval_budget_gaps_reach_the_report_as_uncertainties(self):
        """B1's missing_information (what retrieval could not fetch) is passed through unchanged."""
        gap = "Not retrieved: INT-022 (2026-02-03; matching turn(s) 1) because the tool-call budget of 12 was used"
        ref = "Reference not resolved: INT-090#1 mentions 'april 22' (ambiguous: 2 meetings of this client fall on that date (INT-082, THR-017)); no meeting was assumed"
        a, _ = agent_for(llm_out())
        r = a.write("q", plan(), {K: MemoryContext(client_id=K, episodes=[ep(0, "confirmed", "63")])}, tstate(missing_information=[gap, ref]))
        self.assertIn("Missing information: " + gap, r.uncertainties)
        self.assertIn("Missing information: " + ref, r.uncertainties)


# ============================================================================================================ 9 the context budget is respected
def turn_item(i, interaction, speaker, text_len=260, date="2026-03-04"):
    return EvidenceItem(ref=f"E{i}", source_class="conversation_evidence", description="y" * text_len, interaction_id=interaction, turn_index=i, speaker=speaker, tool_execution_id="EX-1",
                        event_date=date, client_id=K)


class Budget(unittest.TestCase):
    def test_the_prompt_never_exceeds_the_item_or_character_budget(self):
        items = [turn_item(i, f"INT-{i % 9:03d}", "Client" if i % 2 else "Advisor", date=f"2026-01-{1 + i % 9:02d}") for i in range(1, 121)]
        shown, hidden = select_for_prompt(items)
        self.assertLessEqual(len(shown), PROMPT_ITEMS)
        self.assertLessEqual(sum(len(ln) + 1 for ln in prompt_lines(items)), PROMPT_EVIDENCE_CHARS)
        self.assertEqual(len(shown) + len(hidden), 120)
        self.assertEqual(select_for_prompt(items)[0], shown)                                                      # deterministic

    def test_the_whole_prompt_stays_inside_the_model_context_budget(self):
        eps = [ep(i, ["exploratory", "confirmed", "unconfirmed"][i % 3], "63") for i in range(60)]
        t = tstate(*[ex(n, turn_env(f"INT-{n:03d}", [(k, "Client" if k % 2 else "Advisor", "a statement about the retirement age " * 8) for k in range(1, 9)])) for n in range(1, 9)])
        a, llm = agent_for(llm_out())
        a.write("What is the history of Kenji's retirement age?", plan(), {K: MemoryContext(client_id=K, episodes=eps)}, t)
        user = llm.calls[0]["user"]
        self.assertLess(len(user), PROMPT_EVIDENCE_CHARS + 2500)                                                  # evidence budget + request frame + gaps: far below the 5,000-token report ceiling
        self.assertLessEqual(len([ln for ln in user.splitlines() if re.match(r"\[E\d+ ", ln)]), PROMPT_ITEMS)

    def test_turns_and_memory_each_keep_a_share_of_the_budget(self):
        mems = [ep(i, "confirmed", "63") for i in range(16)]
        mem_items, _ = build_evidence(plan(), {K: MemoryContext(client_id=K, episodes=mems)}, tstate())
        turns = [turn_item(100 + i, f"INT-{i % 6:03d}", "Client") for i in range(1, 80)]
        items = mem_items + turns
        for n, e in enumerate(items, 1):
            e.ref = f"E{n}"
        shown, _ = select_for_prompt(items)
        n_turns = sum(is_source_turn(e) for e in shown)
        self.assertGreaterEqual(n_turns, int(PROMPT_ITEMS * TURN_SHARE) - 1)                                       # a long retrieval of turns does not crowd the turns out ...
        self.assertGreaterEqual(len(shown) - n_turns, 8)                                                           # ... and does not crowd every memory out either
        self.assertEqual(sum(prompt_tier(e) in (0, 1) for e in shown if not is_source_turn(e)), len(shown) - n_turns)

    def test_turns_are_spread_over_the_meetings_client_first(self):
        turns = [turn_item(n, f"INT-{m:03d}", "Advisor" if n % 2 else "Client", date=f"2026-0{m}-01") for m in (1, 2, 3) for n in range(10 * m, 10 * m + 10)]
        for n, e in enumerate(turns, 1):
            e.ref = f"E{n}"
        shown, _ = select_for_prompt(turns, max_items=9, max_chars=10 ** 6)
        per = {m: [e for e in shown if e.interaction_id == f"INT-{m:03d}"] for m in (1, 2, 3)}
        self.assertEqual({m: len(v) for m, v in per.items()}, {1: 3, 2: 3, 3: 3})                                  # every meeting represented before any is represented twice
        self.assertTrue(all(e.speaker == "Client" for v in per.values() for e in v))                               # the client's own turns first


# ============================================================================================================ 10-11 isolation and withheld text
class Isolation(unittest.TestCase):
    def test_another_clients_episodes_never_take_a_slot_or_reach_the_evidence(self):
        mine = [ep(i, "exploratory") for i in range(16)]
        foreign = [ep(100 + i, "confirmed", "99", client=OTHER) for i in range(5)]                                  # high-priority decisions that belong to someone else
        items, removed = build_evidence(plan(), {K: MemoryContext(client_id=K, episodes=mine + foreign), OTHER: MemoryContext(client_id=OTHER, episodes=foreign)}, tstate())
        self.assertEqual(sorted(e.memory_id for e in items if e.memory_id), sorted(m.memory_id for m in mine))        # all 16 of the client's own, none of the foreign
        self.assertTrue(all(e.client_id == K for e in items))
        self.assertEqual(removed["episodes_omitted"], [])                                                          # the foreign ones did not count toward the 16-episode cap either
        self.assertTrue(any("EP-100" in r for r in removed["ownership_removed"]))
        text = json.dumps([e.model_dump() for e in items])
        self.assertNotIn("EP-100", text)

    def test_unverified_text_is_labelled_for_the_model_and_never_presented_as_verified(self):
        m = ep(0, "confirmed", "63").model_copy(update=dict(content="Kenji is absolutely certain he will never change his mind."))
        items, _ = build_evidence(plan(), {K: MemoryContext(client_id=K, episodes=[m])}, tstate())
        (line,) = prompt_lines(items)
        self.assertIn("UNVERIFIED - needs confirmation, not an established client fact", line)
        self.assertEqual(items[0].verification, "unverified")


if __name__ == "__main__":
    unittest.main()
