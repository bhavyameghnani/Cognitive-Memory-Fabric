"""D3 (remediation plan; Bhavya's commit 20432e5, ported selectively): a client's OWN figure must never be re-attributed to the advisor's question, and a question is not a confirmation.

What is ported: in llm_first.fact_check, a client sentence that states a figure of its own is kept as the primary evidence instead of being re-anchored onto an advisor sentence
(`attribution.is_bare_affirmation` decides what is NOT content of its own).  What is deliberately NOT ported: (a) skipping every question in `reanchor` - "Does Friday work? / Yes."
needs the question as the content; (b) the widened `_AFFIRM` list ("Good morning.", "Perfect.", "That makes sense." must not confirm anything).
Scripted models only; every quoted excerpt is genuine text of data/conversations.csv.  Mechanism tests: they say nothing about extraction accuracy on real data.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pytest  # noqa: E402

from test_llm_first import Verify, run  # noqa: E402
from test_memory_support import CONV, cand  # noqa: E402
from agents.memory import attribution as A  # noqa: E402
from agents.memory import ingest as ing  # noqa: E402
from agents.memory import llm_first  # noqa: E402
from agents.memory.config import CERTAINTY_RANK  # noqa: E402
from schemas.memory import MemoryCandidate  # noqa: E402

REC = "You've decided to retire at 62."          # an advisor recap (the classification, not the data, is what matters here)
RECAP_EVIDENCE = lambda reply: [dict(speaker="Client", excerpt=reply)]       # noqa: E731


def check(iid, turn, excerpt, topic, certainty, statement, value=None):
    ctx = ing.MeetingContext.load(CONV, iid)
    c = MemoryCandidate(**cand(turn, excerpt, topic, certainty, value=value, statement=statement))
    return llm_first.fact_check(c, ctx, CONV)


# ================================================================================================== the defect (fails on the unported code)
def test_a_client_figure_is_never_reattributed_to_the_advisor_question():
    """INT-017: turn 3 'Do you have a target price?' (Advisor), turn 4 'Around six hundred thousand. ...' (Client)."""
    excerpt = "Around six hundred thousand."
    assert CONV.verify_excerpt("CLIENT-003", "INT-017", 4, excerpt)
    decs, _, _ = run("INT-017", [cand(4, excerpt, "housing_property", "exploratory", statement="We should plan around a target price for the home.")], Verify("exploratory"))
    d = decs[0]
    assert d.kept
    assert (d.final.turn_index, d.final.speaker_role, d.final.excerpt) == (4, "Client", excerpt)
    assert any("not re-anchored" in n for n in d.notes), d.notes


def test_the_client_who_states_values_independently_is_not_moved_onto_the_advisors_question_naming_65():
    """INT-090: turn 3 (Advisor) '... how does this compare with your original plan of 65?'  turn 4 (Client) 'Two years earlier than 65, three years later than the 60 I was toying with.'
    (No value is given: a value that appears in the client's sentence would make it score 1.0 and no re-anchoring would be tried.)  The extractor words the statement like the advisor's question, so the advisor sentence scores better than the client's own: the precondition of the defect."""
    t4 = CONV.turn("INT-090", 4)
    assert t4["speaker"] == "Client" and "toying with" in t4["utterance"]
    statement = "This compares with the client's original plan of 65."
    ctx = ing.MeetingContext.load(CONV, "INT-090")
    pre = llm_first.reanchor(MemoryCandidate(**cand(4, t4["utterance"], "retirement_age", "exploratory", statement=statement)), ctx)
    assert pre is not None and pre[0]["speaker"] == "Advisor" and pre[0]["turn_index"] == 3          # without D3 the memory would move to the advisor's question
    cand_, why, notes, extra = check("INT-090", 4, t4["utterance"], "retirement_age", "exploratory", statement)
    assert cand_ is not None, why
    assert (cand_.turn_index, cand_.speaker_role) == (4, "Client") and extra == []
    assert any("not re-anchored" in n for n in notes), notes


def test_a_tentative_client_figure_stays_the_clients_and_stays_tentative():
    """The client's own hedged sentence is kept as evidence AND the existing guards still lower the label (a confident model cannot make it a decision)."""
    sent = "Maybe down to twenty-five percent."
    decs, _, _ = run("INT-057", [cand(4, sent, "asset_allocation", "confirmed", value="25", statement="Prior-turn question about equities should drive this statement.")], Verify("confirmed"))
    d = decs[0]
    assert d.final.speaker_role == "Client" and d.final.excerpt == sent
    assert CERTAINTY_RANK[d.final.certainty] < CERTAINTY_RANK["confirmed"]


# ================================================================================================== existing legitimate flows must be unchanged
def test_a_bare_yes_to_an_advisor_question_still_takes_its_content_from_the_question():
    """INT-007: turn 11 (Advisor) 'Does Friday, January 30 work for the next one?'  turn 12 (Client) 'Yes. Thank you for going slowly.'  The question IS the content.  Skipping every question in
    reanchor() (the other half of Bhavya's change) would lose this; it is deliberately not ported."""
    ctx = ing.MeetingContext.load(CONV, "INT-007")
    c = MemoryCandidate(**cand(12, "Yes. Thank you for going slowly.", "retirement_age", "unconfirmed", statement="Does Friday, January 30 work for the next one?"))
    found = llm_first.reanchor(c, ctx)
    assert found is not None and found[0]["turn_index"] == 11 and found[1].rstrip().endswith("?")


def test_a_client_correct_to_an_advisor_recap_still_cites_both_turns():
    """INT-001: turn 13 (Advisor) recap 'My notes from today read as follows. The working plan ...', turn 14 (Client) 'Correct.'"""
    assert CONV.turn("INT-001", 14)["utterance"].strip() == "Correct."
    cand_, why, notes, extra = check("INT-001", 14, "Correct.", "retirement_age", "confirmed", "The working plan is retirement at 65, recorded as the current plan and not a fixed decision.", value="65")
    assert cand_ is not None, why
    assert cand_.turn_index == 13 and cand_.speaker_role == "Advisor"                     # content from the recap ...
    assert [(e["turn_index"], e["speaker"], e["role"]) for e in extra] == [(14, "Client", "supporting")]   # ... and the client's reply kept as supporting evidence


def test_the_existing_reply_without_content_case_is_unchanged():
    decs, _, _ = run("INT-010", [cand(6, "I'll make it a priority this week.", "retirement_income", "unconfirmed", statement="Last time we agreed you would send over the plan summary for your Northgate deferred compensation account.")], Verify("unconfirmed"))
    d = decs[0]
    assert d.kept and d.final.turn_index == 3 and [(x["turn_index"], x["role"]) for x in d.extra_evidence] == [(6, "supporting")]


# ================================================================================================== bare-affirmation helper (our, narrower _AFFIRM)
@pytest.mark.parametrize("text", ["Yes, let's do that.", "That's right.", "Correct.", "Sounds good.", "Yes. Thank you for going slowly."])
def test_bare_affirmations(text):
    assert A.is_bare_affirmation(text), text


@pytest.mark.parametrize("text", ["Around six hundred thousand.", "No, 63.", "Maybe 62?", "What if I retire at 62?", "Should I retire at 62?", "Good morning.", "Perfect.",
                                  "That makes sense to me.", "Yes, but I'm still considering it.", "Sure, but what about the mortgage?", "", "Good question."])
def test_what_is_not_a_bare_affirmation(text):
    assert not A.is_bare_affirmation(text), text


# ================================================================================================== phrases that must never lift an advisor-reported decision
NEVER_CONFIRM = ["Maybe 62?", "What if I retire at 62?", "Should I retire at 62?", "Good morning.", "Good morning, Kenji. Should I retire at 62?", "Perfect.", "That makes sense to me.",
                 "Sure, but what about the mortgage?", "Yes, but I'm still considering it.", "Let me think about that.", "No.", "Maybe, I'm not sure.", "Probably 62.", "I guess so."]


@pytest.mark.parametrize("reply", NEVER_CONFIRM)
def test_these_client_replies_never_lift_an_advisor_reported_decision(reply):
    v = A.assess("confirmed", "Advisor", REC, evidence=RECAP_EVIDENCE(reply), value="62")
    assert v.certainty == "unconfirmed", reply
    assert not A.client_confirms(RECAP_EVIDENCE(reply), "62"), reply


@pytest.mark.parametrize("reply", ["That's right.", "Correct.", "Yes, let's do that.", "Yes, 62 is the plan.", "We agreed on 62 for the plan"])
def test_real_confirmations_still_lift_it(reply):
    assert A.assess("confirmed", "Advisor", REC, evidence=RECAP_EVIDENCE(reply), value="62").certainty == "confirmed", reply


def test_an_advisor_speaking_is_never_confirmation_and_negation_vetoes():
    assert A.assess("confirmed", "Advisor", REC, evidence=[dict(speaker="Advisor", excerpt="That's right.")], value="62").certainty == "unconfirmed"
    for reply in ("No, I haven't decided that.", "I don't want to retire at 62.", "Not 62, 65."):
        assert A.assess("confirmed", "Advisor", REC, evidence=RECAP_EVIDENCE(reply), value="62").certainty == "unconfirmed", reply
