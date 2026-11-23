"""M1: evidence attribution, certainty ceilings and finality (src/agents/memory/attribution.py and every write path that applies it).

WHAT THESE TESTS ARE.  Deterministic MECHANISM tests: the model is SCRIPTED (MockLLM), so they prove what the CODE does with a given extractor/verifier answer, including a confidently wrong one.
They are NOT a real-model semantic evaluation and say nothing about extraction quality or about the accuracy of the wording rules on real conversations (that has not been measured).
No API key, no network, no seed/demo database: every store is `:memory:` and the conversations are a small synthetic CSV built in a temporary directory, so each adversarial case is exact.
"""
import csv
import re
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from agents.memory import attribution as A                                  # noqa: E402
from agents.memory import consolidate as cons                              # noqa: E402
from agents.memory import ingest as ing                                    # noqa: E402
from agents.memory import llm_first                                        # noqa: E402
from agents.memory.agent import MemoryOrchestrator                         # noqa: E402
from agents.memory.config import CERTAINTY_RANK, OrchestratorConfig        # noqa: E402
from agents.memory.errors import MemoryWriteRejected                       # noqa: E402
from agents.memory.integrity import Principal                              # noqa: E402
from agents.memory.store import MemoryStore                                # noqa: E402
from harness.memory_write_validator import MemoryWriteValidator            # noqa: E402
from llm.client import MockLLM                                             # noqa: E402
from services.conversations import Conversations                           # noqa: E402

CLIENT = "CLIENT-001"
HEADER = ["interaction_id", "client_id", "advisor_id", "interaction_datetime", "channel", "turn_index", "speaker", "utterance", "meeting_context", "source_type", "consent_status"]

# (interaction, datetime, [(speaker, utterance), ...]) - every sentence quoted below is real text of this synthetic file
MEETINGS = [
    ("SYN-001", "2026-01-10T09:00:00", [("Advisor", "Good morning, Alex. Let's review your retirement plan."), ("Client", "I want to retire at 65. We've decided: 65."), ("Advisor", "Thanks, I will record it.")]),
    ("SYN-002", "2026-03-10T09:00:00", [("Advisor", "Alex, thanks for coming back."), ("Client", "I've changed my mind. We've decided: 63."), ("Advisor", "Noted.")]),
    ("SYN-003", "2026-04-10T09:00:00", [("Advisor", "Here's what I'm taking away. You've decided to retire at 60."), ("Client", "Let me think about that.")]),
    ("SYN-004", "2026-05-10T09:00:00", [("Advisor", "Alex, one more thing."), ("Client", "We've decided: 61. Please don't treat it as final yet.")]),
    ("SYN-005", "2026-06-10T09:00:00", [("Advisor", "Here's what I'm taking away. You've decided to retire at 62."), ("Client", "That's right.")]),
    ("SYN-006", "2026-07-10T09:00:00", [("Advisor", "Your risk tolerance is moderate."), ("Client", "Hmm.")]),
    ("SYN-007", "2026-08-10T09:00:00", [("Advisor", "Alex, quick check."), ("Client", "I've corrected myself: it is 62, not 63.")]),
    ("SYN-008", "2026-09-10T09:00:00", [("Third party", "The accountant says you will retire at 59."), ("Client", "I haven't decided anything.")]),
    ("SYN-009", "2026-10-10T09:00:00", [("Advisor", "Alex, quick note."), ("Client", "Nothing has been decided. We've decided: 66.")]),
]
SEN = {  # the sentences the scripted extractor cites
    "c65": "We've decided: 65.", "c63": "We've decided: 63.", "adv60": "You've decided to retire at 60.", "neg61": "Please don't treat it as final yet.",
    "adv62": "You've decided to retire at 62.", "risk": "Your risk tolerance is moderate.", "fix62": "I've corrected myself: it is 62, not 63.",
    "third59": "The accountant says you will retire at 59.", "hav": "I haven't decided anything.", "nothing": "Nothing has been decided. We've decided: 66.",
}


@pytest.fixture(scope="module")
def conv(tmp_path_factory):
    p = tmp_path_factory.mktemp("m1conv") / "conversations.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        for iid, ts, turns in MEETINGS:
            for i, (sp, text) in enumerate(turns, 1):
                w.writerow([iid, CLIENT, "ADVISOR-001", ts, "video", i, sp, text, "Retirement plan", "synthetic_meeting", "consented"])
    return Conversations(p)


def cand(turn, excerpt, certainty, value="63", topic="retirement_age", role="Client", **kw):
    return dict(topic=topic, subject="retirement_age", statement=excerpt[:200], value=value, certainty=certainty, speaker_role=role, turn_index=turn, excerpt=excerpt,
                corrects_value=kw.get("corrects"), reusable_procedure=False)


class Extract:
    """Scripted extractor.  Answers for rules mode ('Meeting X on') and for llm_first mode ('Conversation X (')."""

    def __init__(self, by_iid):
        self.by_iid = by_iid

    def __call__(self, system, user, schema):
        m = re.search(r"(?:Meeting|Conversation) (\S+) (?:on|\()", user)
        return {"candidates": self.by_iid.get(m.group(1), [])}


class Verify:
    """Scripted verifier: ALWAYS says `confirmed`, supported and worth remembering, a figure of the client's own - i.e. maximally confident.  The policy must hold regardless."""

    def __call__(self, system, user, schema):
        if system == llm_first.VALUE_SYSTEM:
            return {"client_figure": True, "quantity": "age"}
        n = len(re.findall(r"^Item \d+", user, re.M))
        return {"items": [dict(id=i, certainty="confirmed", supported=True, worth_remembering=True) for i in range(n)]}


def world(conv, by_iid, mode="llm_first", validator=True):
    store = MemoryStore(url="sqlite:///:memory:", conv=conv, clock=lambda: datetime(2026, 11, 1, 9, 0, 0))
    llm = MockLLM(handlers={"memory": Extract(by_iid), "memory_verifier": Verify()})
    wv = MemoryWriteValidator(conv, store) if validator else None
    o = MemoryOrchestrator(llm, store=store, conv=conv, config=OrchestratorConfig(ingest_mode=mode), write_validator=wv)
    o.init()
    return o, store


def episodes(store):
    return sorted(store.records(CLIENT, "episodic"), key=lambda r: (r["event_ts"], r["turn_index"]))


def semantic(store):
    return sorted(store.records(CLIENT, "semantic", topics=["retirement_age"]), key=lambda r: r["version_no"] or 0)


def ingest(o, *ids):
    return [o.ingest_meeting(i) for i in ids]


# ================================================================================================== A. the policy itself (pure functions)
NEGATED = ["Please don't treat this as final yet.", "I haven't decided.", "This is only a possibility.", "I'm still considering it.", "I may change my mind.",
           "Nothing has been decided.", "This is not confirmed.", "I'll confirm on Wednesday's call.", "It isn't settled.", "We haven't made a decision yet.", "Please do not consider it final."]
FINAL = ["We've decided: 63.", "It is final.", "The decision is final.", "Yes, let's go with 63.", "I've decided to retire at 63.", "That's settled, we're going with five installments.",
         "I don't think that's wrong, we've decided: 63."]


@pytest.mark.parametrize("sentence", NEGATED)
def test_explicit_non_finality_is_never_accepted_as_a_confirmed_decision(sentence):
    f = A.finality(sentence)
    assert f.status in ("not_final", "unresolved") and f.label in ("unconfirmed", "exploratory"), sentence
    v = A.assess("confirmed", "Client", sentence)
    assert CERTAINTY_RANK[v.certainty] < CERTAINTY_RANK["confirmed"] and v.lowered


@pytest.mark.parametrize("sentence", FINAL)
def test_plain_decisions_and_their_negation_lookalikes_stay_final(sentence):
    assert A.finality(sentence).status == "final", sentence                    # the finality rule must not create new false positives on ordinary decisions
    assert A.assess("confirmed", "Client", sentence).certainty == "confirmed"


def test_negation_is_judged_in_context_not_by_a_single_keyword():
    # a negation about something ELSE, or about history, does not veto an unrelated decision ...
    assert A.finality("We've decided: 63.", "The mortgage isn't settled yet. We've decided: 63.").status == "final"
    assert A.finality("We've decided: 63.", "Please upload it. I'll confirm when it arrives. We've decided: 63.").status == "final"           # a logistics deferral
    assert A.finality("In January you told me you'd retire at 65.", "In March you corrected me to say you hadn't decided. In January you told me you'd retire at 65.").status == "final"
    # ... but a negation that refers to the decision does, and an unclear scope abstains (unresolved, flagged for review) instead of guessing
    assert A.finality("I'm leaning toward five.", "I'm leaning toward five. Please don't treat it as final yet.").status == "unresolved"
    assert A.finality("We've decided: 63.", "Nothing has been decided about the house. We've decided: 63.").status == "unresolved"
    assert A.finality("We've decided 63, but don't treat it as final yet.").status == "unresolved"
    v = A.assess("confirmed", "Client", "We've decided 63, but don't treat it as final yet.")
    assert v.needs_review and v.certainty == "unconfirmed"


def test_attribution_classes_and_ceilings():
    assert A.classify_attribution("Client", "We've decided: 63.") == A.CLIENT_DIRECT
    assert A.classify_attribution("Advisor", "You've decided to retire at 60.") == A.ADVISOR_REPORTED
    assert A.classify_attribution("Advisor", "Your risk tolerance is moderate.") == A.ADVISOR_INTERPRETATION
    assert A.classify_attribution("Third party", "x") == A.AMBIGUOUS and A.classify_attribution(None, "x") == A.AMBIGUOUS
    assert A.classify_attribution("Client", "x", origin="consolidation") == A.SYSTEM_INFERENCE
    for role, ex, expected in [("Client", "We've decided: 63.", "confirmed"), ("Advisor", "You've decided to retire at 60.", "unconfirmed"), ("Advisor", "Your risk tolerance is moderate.", "unconfirmed"),
                               ("Third party", "x", "unconfirmed"), (None, "x", "unconfirmed")]:
        assert A.assess("confirmed", role, ex).certainty == expected, (role, ex)
        assert A.assess("correction", role, ex).certainty == (expected if expected != "confirmed" else "correction")
    # downgrade only: weaker labels are left alone and nothing is ever raised
    for label in ("recommendation", "exploratory", "hypothetical", "unconfirmed", "n/a"):
        assert A.assess(label, "Advisor", "I recommend 60 percent.").certainty == label
    for sp in ("Client", "Advisor", "Third party", None):
        for lab in CERTAINTY_RANK:
            assert CERTAINTY_RANK[A.assess(lab, sp, "We've decided: 63.").certainty] <= CERTAINTY_RANK[lab]


def test_a_client_spoken_confirmation_is_what_lifts_the_ceiling_and_it_must_be_real():
    ex = "You've decided to retire at 62."
    assert A.assess("confirmed", "Advisor", ex, evidence=[dict(speaker="Client", excerpt="That's right.")]).certainty == "confirmed"
    for reply in ("Yes, but I'm still considering it.", "Let me think about that.", "No.", "Maybe, I'm not sure."):
        assert A.assess("confirmed", "Advisor", ex, evidence=[dict(speaker="Client", excerpt=reply)]).certainty == "unconfirmed", reply
    assert A.assess("confirmed", "Advisor", ex, evidence=[dict(speaker="Advisor", excerpt="That's right.")]).certainty == "unconfirmed"       # the ADVISOR agreeing with the advisor is not confirmation
    rows = [dict(turn_index=1, speaker="Advisor", utterance=ex), dict(turn_index=2, speaker="Client", utterance="That's right.")]
    assert A.find_client_confirmation(rows, 1)["turn_index"] == 2 and A.find_client_confirmation(rows, 2) is None
    far = rows[:1] + [dict(turn_index=2, speaker="Advisor", utterance="Next point."), dict(turn_index=3, speaker="Client", utterance="That's right.")]
    assert A.find_client_confirmation(far, 1) is None                           # only the IMMEDIATE next turn counts: a later 'yes' may answer something else


def test_natural_wording_confirmations_are_recognised_and_hedges_still_block_them():
    """Replies from the paraphrased evaluation set (INT-036 turn 10, INT-146 turn 6): plain agreement in natural wording lifts the ceiling like 'That's right.' does."""
    ex = "You've decided to retire at 62."
    for reply in ("That makes sense to me. Please go ahead and arrange that.", "That's all accurate.", "That looks right.", "Spot on.", "Perfect. That matches what I remember.",
                  "That's what I understood, yes.", "Good. That's it.", "That's the story.", "That sounds sensible. Yes, please set that up."):
        rows = [dict(turn_index=1, speaker="Advisor", utterance=ex), dict(turn_index=2, speaker="Client", utterance=reply)]
        assert A.find_client_confirmation(rows, 1) is not None, reply
    for reply in ("That makes sense, but I'm not sure yet.", "That looks about right, though I still want to think about it.", "Makes sense? I don't know.",
                  "Good question. I haven't decided.", "Good question.", "Perfect, but let me think about it.", "Yes. I need to think about it more. I haven't made up my mind."):
        rows = [dict(turn_index=1, speaker="Advisor", utterance=ex), dict(turn_index=2, speaker="Client", utterance=reply)]
        assert A.find_client_confirmation(rows, 1) is None, reply


def test_the_rank_table_matches_the_pipeline_configuration():
    assert A.RANK == CERTAINTY_RANK


# ================================================================================================== B. mandatory regression fixtures through the whole write path
@pytest.mark.parametrize("mode", ["llm_first", "rules"])
def test_1_kenji_style_change_of_a_direct_client_decision_65_to_63_keeps_history_and_attribution(conv, mode):
    o, st = world(conv, {"SYN-001": [cand(2, SEN["c65"], "confirmed", "65")], "SYN-002": [cand(2, SEN["c63"], "confirmed", "63")]}, mode)
    ingest(o, "SYN-001", "SYN-002")
    v = semantic(st)
    assert [(x["version_no"], x["value_text"], x["status"], x["valid_from"], x["valid_to"]) for x in v] == [(1, "65", "superseded", "2026-01-10", "2026-03-10"), (2, "63", "active", "2026-03-10", None)]
    assert v[1]["supersedes_id"] == v[0]["memory_id"] and o.consolidator.validate_chains(CLIENT) == []
    for x in v:                                                                # provenance: the semantic memory is NOT a System-sourced fact
        assert (x["source_role"], x["certainty"], x["origin"]) == ("Client", "confirmed", "consolidation")
        ev = st.evidence_map([x["memory_id"]])[x["memory_id"]]
        assert ev and ev[0].speaker == "Client" and ev[0].excerpt in (SEN["c65"], SEN["c63"])
        assert "client_direct" in st.query("SELECT basis FROM {S}semantic_details WHERE memory_id=?", (x["memory_id"],))[0]["basis"]
    before = {t: st.count(t) for t in ("memory_records", "memory_evidence", "memory_relationships", "memory_lifecycle_events")}
    assert ingest(o, "SYN-001", "SYN-002") == [dict(interaction_id="SYN-001", skipped=True), dict(interaction_id="SYN-002", skipped=True)]            # idempotent
    o.ingest_meeting("SYN-002", force=True)
    assert {t: st.count(t) for t in before} == before                           # a forced re-run adds nothing


def test_2_a_hedged_client_reply_never_reaches_confirmed_or_semantic_memory(conv):
    o, st = world(conv, {"SYN-004": [cand(2, "We've decided: 61. Please don't treat it as final yet.", "confirmed", "61")]})
    ingest(o, "SYN-004")
    (e,) = episodes(st)
    assert e["certainty"] in ("unconfirmed", "exploratory") and e["source_role"] == "Client" and semantic(st) == []
    audit = st.query("SELECT reason, certainty_in, certainty_out FROM {S}memory_candidates")[0]
    assert audit["certainty_in"] == "confirmed" and "finality" in audit["reason"] and "needs review" in audit["reason"]       # unresolved scope: flagged, not guessed


def test_3_an_advisor_only_recap_is_kept_as_an_advisor_reported_candidate_with_provenance_and_is_not_a_client_decision(conv):
    for mode in ("llm_first", "rules"):
        o, st = world(conv, {"SYN-003": [cand(1, SEN["adv60"], "confirmed", "60", role="Advisor")]}, mode)
        ingest(o, "SYN-003")
        (e,) = episodes(st)
        assert (e["certainty"], e["source_role"], e["event_type"], e["status"]) == ("unconfirmed", "Advisor", "reported_decision", "unconfirmed"), mode
        ev = st.evidence_map([e["memory_id"]])[e["memory_id"]]
        assert [(x.interaction_id, x.turn_index, x.speaker, x.excerpt) for x in ev] == [("SYN-003", 1, "Advisor", SEN["adv60"])]                   # nothing discarded
        assert semantic(st) == [], mode
        assert "attribution=advisor_reported_client" in st.query("SELECT selection_note FROM {S}episodic_details")[0]["selection_note"]


def test_4_explicitly_negated_finality_is_not_final_even_when_extractor_and_verifier_are_confident(conv):
    o, st = world(conv, {"SYN-009": [cand(2, SEN["nothing"], "confirmed", "66")]})
    ingest(o, "SYN-009")
    (e,) = episodes(st)
    assert CERTAINTY_RANK[e["certainty"]] < CERTAINTY_RANK["confirmed"] and semantic(st) == []


def test_5_a_direct_client_confirmed_decision_creates_a_client_attributed_semantic_memory(conv):
    o, st = world(conv, {"SYN-002": [cand(2, SEN["c63"], "confirmed", "63")]})
    ingest(o, "SYN-002")
    (e,), (s,) = episodes(st), semantic(st)
    assert (e["certainty"], e["source_role"], e["event_type"]) == ("confirmed", "Client", "decision")
    assert (s["value_text"], s["source_role"], s["status"]) == ("63", "Client", "active")


def test_5b_an_advisor_recap_the_client_immediately_confirms_is_a_confirmed_decision_that_cites_both_turns(conv):
    for mode in ("llm_first", "rules"):
        o, st = world(conv, {"SYN-005": [cand(1, SEN["adv62"], "confirmed", "62", role="Advisor")]}, mode)
        ingest(o, "SYN-005")
        (e,), (s,) = episodes(st), semantic(st)
        assert e["certainty"] == "confirmed" and e["source_role"] == "Advisor", mode                       # the primary speaker stays the Advisor: attribution is not erased
        assert [(x.turn_index, x.speaker, x.role) for x in st.evidence_map([e["memory_id"]])[e["memory_id"]]] == [(1, "Advisor", "primary"), (2, "Client", "supporting")]
        assert (s["value_text"], s["source_role"]) == ("62", "Advisor")                                   # and the semantic memory is not 'System'-sourced either
        assert [x.speaker for x in st.evidence_map([s["memory_id"]])[s["memory_id"]]] == ["Advisor", "Client"]


def test_6_an_advisor_interpretation_presented_as_fact_is_capped_and_labelled(conv):
    o, st = world(conv, {"SYN-006": [cand(1, SEN["risk"], "confirmed", None, topic="risk_tolerance", role="Advisor")]})
    ingest(o, "SYN-006")
    (e,) = st.records(CLIENT, "episodic")
    assert (e["certainty"], e["source_role"], e["event_type"]) == ("unconfirmed", "Advisor", "advisor_interpretation") and st.records(CLIENT, "semantic") == []


def test_7_a_client_decision_later_corrected_by_the_client_keeps_history(conv):
    o, st = world(conv, {"SYN-002": [cand(2, SEN["c63"], "confirmed", "63")], "SYN-007": [cand(2, SEN["fix62"], "correction", "62", corrects="63")]})
    ingest(o, "SYN-002", "SYN-007")
    assert [(x["value_text"], x["status"], x["source_role"]) for x in semantic(st)] == [("63", "superseded", "Client"), ("62", "active", "Client")]
    eps = {e["value_text"]: e for e in episodes(st)}
    assert eps["62"]["certainty"] == "correction" and eps["63"]["status"] == "superseded"                    # the wrongly recorded decision is superseded, never deleted
    assert any(r.rel_type == "corrects" for r in st.relationships_among([e["memory_id"] for e in eps.values()]))


def test_8_a_later_advisor_recap_never_overwrites_a_direct_client_statement(conv):
    o, st = world(conv, {"SYN-002": [cand(2, SEN["c63"], "confirmed", "63")], "SYN-003": [cand(1, SEN["adv60"], "confirmed", "60", role="Advisor")]})
    ingest(o, "SYN-002", "SYN-003")
    assert [(x["value_text"], x["status"]) for x in semantic(st)] == [("63", "active")]                      # 60 (advisor-only, later) does not supersede 63
    adv = [e for e in episodes(st) if e["value_text"] == "60"][0]
    assert (adv["certainty"], adv["source_role"]) == ("unconfirmed", "Advisor") and adv["status"] == "unconfirmed"
    ingest(o, "SYN-003")                                                                                      # re-running changes nothing
    assert [(x["value_text"], x["status"]) for x in semantic(st)] == [("63", "active")]


def test_9_unclear_speaker_attribution_cannot_be_cited_or_confirmed(conv):
    o, st = world(conv, {"SYN-008": [cand(1, SEN["third59"], "confirmed", "59", role="Client"), cand(2, SEN["hav"], "confirmed", "59")]})
    r = ingest(o, "SYN-008")[0]
    assert ("conversation_llm", "rejected", "confirmed", "confirmed") in r["decisions"]                       # the third party's message is never memory evidence
    assert all(e["source_role"] in ("Client", "Advisor") for e in episodes(st)) and semantic(st) == []
    assert all(e["certainty"] != "confirmed" for e in episodes(st))                                           # and the client's "I haven't decided anything." is not a decision


def test_10_missing_or_ambiguous_evidence_references_store_nothing(conv):
    # a turn that does not exist: the LLM-first extraction is refused as a whole (turn_index validation), nothing is stored and the failure is reported
    o, st = world(conv, {"SYN-002": [cand(9, SEN["c63"], "confirmed", "63")]})
    r = ingest(o, "SYN-002")[0]
    assert r["llm_error"] and r["decisions"] == [] and st.records(CLIENT) == []
    # a quotation that is not in the turn / a real sentence cited at the WRONG turn: each candidate is rejected, stored nowhere, and stays auditable
    bad = [cand(2, "We have definitely decided on sixty-three.", "confirmed", "63"), cand(1, SEN["c63"], "confirmed", "63", role="Client")]
    o, st = world(conv, {"SYN-002": bad})
    r = ingest(o, "SYN-002")[0]
    assert [d[1] for d in r["decisions"]].count("rejected") == 2 and episodes(st) == [] and semantic(st) == []
    assert len(st.query("SELECT * FROM {S}memory_candidates WHERE decision='rejected'")) == 2


def test_a_confident_model_cannot_override_the_ceiling_in_either_mode_or_with_or_without_the_validator(conv):
    for mode in ("llm_first", "rules"):
        for validator in (True, False):
            o, st = world(conv, {"SYN-003": [cand(1, SEN["adv60"], "confirmed", "60", role="Advisor")]}, mode, validator)
            ingest(o, "SYN-003")
            assert [e["certainty"] for e in episodes(st)] == ["unconfirmed"] and semantic(st) == [], (mode, validator)


# ================================================================================================== C. bypass attempts: every write path enforces the same policy
def direct_world(conv):
    st = MemoryStore(url="sqlite:///:memory:", conv=conv, clock=lambda: datetime(2026, 11, 1, 9, 0, 0))
    st.init()
    return st


def erec(mid, **kw):
    base = dict(memory_id=mid, client_id=CLIENT, memory_type="episodic", topic="retirement_age", subject="retirement_age", content=SEN["adv60"], value_text="60", certainty="confirmed",
                source_role="Advisor", status="active", origin="conversation_llm", event_date="2026-04-10", content_hash=mid)
    base.update(kw)
    return base


def ev(iid, turn, speaker, text, role="primary"):
    return dict(source_type="conversation", interaction_id=iid, turn_index=turn, speaker=speaker, excerpt=text, role=role)


DET3 = dict(interaction_id="SYN-003", turn_index=1, event_ts="2026-04-10T09:00:00", speaker_role="Advisor", event_type="decision")
ING = Principal.service("ingestion", [CLIENT])
CP = Principal.service("consolidation", [CLIENT])


def refused(code, fn):
    with pytest.raises(MemoryWriteRejected) as e:
        fn()
    assert e.value.code == code, e.value.code


def test_direct_persistence_of_an_advisor_confirmed_episode_is_refused_by_the_integrity_boundary(conv):
    st = direct_world(conv)
    refused("attribution_ceiling", lambda: st.insert_memory(erec("A1"), dict(DET3), [ev("SYN-003", 1, "Advisor", SEN["adv60"])], principal=ING))
    refused("attribution_ceiling", lambda: st.boundary.create(erec("A2", certainty="correction"), dict(DET3), [ev("SYN-003", 1, "Advisor", SEN["adv60"])], principal=ING))
    # a client-spoken row that is not an affirmation does not lift the ceiling either
    refused("attribution_ceiling", lambda: st.insert_memory(erec("A3"), dict(DET3), [ev("SYN-003", 1, "Advisor", SEN["adv60"]), ev("SYN-003", 2, "Client", "Let me think about that.", "supporting")], principal=ING))
    assert st.records(CLIENT) == []


def test_the_honest_forms_of_the_same_records_are_accepted(conv):
    st = direct_world(conv)
    st.insert_memory(erec("B1", certainty="unconfirmed", status="unconfirmed"), dict(DET3, event_type="reported_decision"), [ev("SYN-003", 1, "Advisor", SEN["adv60"])], principal=ING)
    st.insert_memory(erec("B2", certainty="confirmed", content=SEN["adv62"], value_text="62", event_date="2026-06-10", content_hash="b2"),
                     dict(DET3, interaction_id="SYN-005", event_ts="2026-06-10T09:00:00"), [ev("SYN-005", 1, "Advisor", SEN["adv62"]), ev("SYN-005", 2, "Client", "That's right.", "supporting")], principal=ING)
    assert {r["memory_id"]: r["certainty"] for r in st.records(CLIENT, "episodic")} == {"B1": "unconfirmed", "B2": "confirmed"}


def test_hedged_or_non_final_evidence_cannot_back_a_confirmed_memory_even_directly(conv):
    st = direct_world(conv)
    det = dict(interaction_id="SYN-004", turn_index=2, event_ts="2026-05-10T09:00:00", speaker_role="Client", event_type="decision")
    refused("attribution_ceiling", lambda: st.insert_memory(erec("C1", source_role="Client", content="We've decided: 61. Please don't treat it as final yet.", value_text="61",
                                                                  event_date="2026-05-10"), det, [ev("SYN-004", 2, "Client", "We've decided: 61. Please don't treat it as final yet.")], principal=ING))


def test_the_recorded_source_role_must_be_the_speaker_of_the_cited_turn(conv):
    st = direct_world(conv)
    det_c = dict(interaction_id="SYN-002", turn_index=2, event_ts="2026-03-10T09:00:00", speaker_role="Client", event_type="decision")
    refused("source_attribution_mismatch", lambda: st.insert_memory(erec("D1", source_role="Client"), dict(DET3), [ev("SYN-003", 1, "Advisor", SEN["adv60"])], principal=ING))   # an advisor turn labelled Client
    refused("source_attribution_mismatch", lambda: st.insert_memory(erec("D2", source_role="Advisor", content=SEN["c63"], value_text="63", event_date="2026-03-10"), det_c,
                                                                    [ev("SYN-002", 2, "Client", SEN["c63"])], principal=ING))                                                    # a client turn labelled Advisor
    st.insert_memory(erec("D3", source_role="Client", content=SEN["c63"], value_text="63", event_date="2026-03-10"), det_c, [ev("SYN-002", 2, "Client", SEN["c63"])], principal=ING)


def sem(mid, value, role, **kw):
    base = dict(memory_id=mid, client_id=CLIENT, memory_type="semantic", topic="retirement_age", subject="retirement_age", title="t", content=f"retirement age = {value}", value_text=value,
                certainty="confirmed", source_role=role, status="active", origin="consolidation", content_hash=mid, event_date="2026-04-10", valid_from="2026-04-10", version_no=1)
    base.update(kw)
    return base


def test_consolidation_cannot_write_a_semantic_memory_from_advisor_only_evidence_or_as_a_system_fact(conv):
    st = direct_world(conv)
    sd = dict(slot_key="retirement_age|retirement_age", basis="b", supporting_episodes=1)
    advisor_ev = [ev("SYN-003", 1, "Advisor", SEN["adv60"])]
    refused("attribution_ceiling", lambda: st.insert_memory(sem("S1", "60", "Advisor"), dict(sd), advisor_ev, principal=CP))                 # advisor-only evidence
    refused("source_attribution_mismatch", lambda: st.insert_memory(sem("S2", "60", "System"), dict(sd), advisor_ev, principal=CP))        # attribution replaced by 'System'
    refused("source_attribution_mismatch", lambda: st.insert_memory(sem("S3", "63", None, event_date="2026-03-10", valid_from="2026-03-10"), dict(sd), [ev("SYN-002", 2, "Client", SEN["c63"])], principal=CP))
    refused("source_attribution_mismatch", lambda: st.insert_memory(sem("S4", "63", "System", event_date="2026-03-10", valid_from="2026-03-10"), dict(sd), [ev("SYN-002", 2, "Client", SEN["c63"])], principal=CP))
    assert st.records(CLIENT) == []


def test_the_write_validator_and_consolidator_exclude_advisor_episodes_that_no_client_confirms(conv):
    adv = dict(memory_id="E-A", certainty="confirmed", status="active", value_text="60", topic="retirement_age", subject="retirement_age", speaker_role="Advisor", event_type="reported_decision",
               event_date="2026-04-10", event_ts="2026-04-10T09:00:00", interaction_id="SYN-003", turn_index=1)
    cli = dict(adv, memory_id="E-C", speaker_role="Client", event_type="decision", value_text="63", event_date="2026-03-10", event_ts="2026-03-10T09:00:00", interaction_id="SYN-002", turn_index=2)
    assert cons.eligible(cli) is True and cons.eligible(adv) is False and cons.eligible(dict(adv, event_type="decision")) is False
    assert cons.eligible(dict(adv, event_type="decision"), client_ok=True) is True                           # only when a client-spoken row confirms it
    assert cons.eligible(dict(cli, event_type="reported_decision")) is False                                  # a recorded advisor-reported decision never qualifies
    assert [v.value for v in cons.fold([cli, adv])] == ["63"]                                                 # an advisor-only episode cannot even join the fold
    confirmed_adv = dict(adv, event_type="decision")                                                          # a client-CONFIRMED advisor episode is recorded as a decision (see test_5b)
    assert [v.value for v in cons.fold([cli, confirmed_adv], attributable={"E-A"})] == ["63", "60"]            # ... and joins the fold only when the consolidator found the confirmation
    assert [v.value for v in cons.fold([cli, adv], attributable={"E-A"})] == ["63"]                             # an advisor-REPORTED episode never does, whatever is found later


def test_a_semantic_memory_built_from_legacy_advisor_rows_is_refused_by_the_validator(conv):
    """Rows stored before this policy existed can be advisor-spoken + confirmed.  Approval of a semantic version created from one is refused."""
    st = direct_world(conv)
    wv = MemoryWriteValidator(conv, st)
    legacy = dict(memory_id="LEG", certainty="confirmed", status="active", topic="retirement_age", subject="retirement_age", speaker_role="Advisor", content=SEN["adv60"], value_text="60")
    ok, why = wv.approve_semantic(CLIENT, cons.Version("60", "2026-04-10", legacy), st)
    assert ok is False and "not client-attributable" in why
    ok, why = wv.approve_semantic(CLIENT, cons.Version("63", "2026-03-10", dict(legacy, memory_id="LEG2", speaker_role="Client", content=SEN["c63"], value_text="63")), st)
    assert ok is False and "no evidence" in why                                                               # a client row passes the attribution check, then needs its evidence like any other


# ================================================================================================== D. the original audit traces, on the REAL multichannel data
def real_world(by_iid):
    from services.conversations import MultiChannelConversations
    mc = MultiChannelConversations()
    store = MemoryStore(url="sqlite:///:memory:", conv=mc, clock=lambda: datetime(2026, 11, 1, 9, 0, 0))
    o = MemoryOrchestrator(MockLLM(handlers={"memory": Extract(by_iid), "memory_verifier": Verify()}), store=store, conv=mc, config=OrchestratorConfig(ingest_mode="llm_first"),
                           write_validator=MemoryWriteValidator(mc, store))
    o.init()
    return o, store


def rcand(turn, excerpt, certainty, topic, subject, value, role):
    return dict(topic=topic, subject=subject, statement=excerpt[:200], value=value, certainty=certainty, speaker_role=role, turn_index=turn, excerpt=excerpt, corrects_value=None,
                reusable_procedure=False)


THR010_T3 = "I'm leaning toward five installments, but I'll confirm on Wednesday's call."
THR010_T3B = "Please don't treat it as final yet."
THR010_T4 = "your decision to elect five annual installments starting at 63 is recorded"


def test_audit_trace_b_the_advisors_email_recap_alone_is_not_a_confirmed_client_decision_and_creates_no_semantic_value():
    """THR-010: the client hedges ('I'll confirm on Wednesday', 'don't treat it as final'); the ADVISOR's later email says 'your decision ... is recorded'.  Before M1 the recap became an
    active CONFIRMED semantic value (with source_role 'System')."""
    o, st = real_world({"THR-010": [rcand(3, THR010_T3, "confirmed", "retirement_income", "deferred_comp_payout", "5", "Client"),
                                    rcand(4, THR010_T4, "confirmed", "retirement_income", "deferred_comp_payout", "5", "Advisor")]})
    ingest(o, "THR-010")
    by_turn = {e["turn_index"]: e for e in st.records(CLIENT, "episodic")}
    assert by_turn[3]["certainty"] in ("exploratory", "unconfirmed") and by_turn[3]["source_role"] == "Client"
    assert (by_turn[4]["certainty"], by_turn[4]["source_role"], by_turn[4]["event_type"]) == ("unconfirmed", "Advisor", "reported_decision")
    assert st.records(CLIENT, "semantic") == []
    ev = st.evidence_map([by_turn[4]["memory_id"]])[by_turn[4]["memory_id"]]
    assert [(x.interaction_id, x.turn_index, x.speaker) for x in ev] == [("THR-010", 4, "Advisor")]          # provenance kept


def test_audit_trace_c5_a_client_sentence_saying_not_to_treat_it_as_final_is_not_a_confirmed_memory():
    o, st = real_world({"THR-010": [rcand(3, THR010_T3B, "confirmed", "retirement_income", "x", "40", "Client")]})
    ingest(o, "THR-010")
    (e,) = st.records(CLIENT, "episodic")
    assert CERTAINTY_RANK[e["certainty"]] < CERTAINTY_RANK["confirmed"] and st.records(CLIENT, "semantic") == []


def test_audit_trace_d_an_advisor_recap_as_sole_evidence_cannot_create_a_retirement_age():
    o, st = real_world({"THR-010": [rcand(4, THR010_T4, "confirmed", "retirement_age", "retirement_age", "63", "Advisor")]})
    ingest(o, "THR-010")
    (e,) = st.records(CLIENT, "episodic")
    assert (e["certainty"], e["source_role"]) == ("unconfirmed", "Advisor") and st.records(CLIENT, "semantic") == []
