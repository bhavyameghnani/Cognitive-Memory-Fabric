"""Context window for cited CLIENT turns (support.SourceContext): additive, narrow, and every negative control stays unverified."""
from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents.memory import support  # noqa: E402
from agents.memory.integrity import Principal  # noqa: E402
from agents.memory.store import MemoryStore  # noqa: E402

SEED = Path(__file__).resolve().parents[1] / "data" / "seed" / "cmf_memory_demo.db"
TOPIC = "retirement age"
ADVISOR = "Since April 22 we focused on retention bonus tax. The next topic is retirement age decision."


def check(claim, cited, value="63", prior=ADVISOR, name="Kenji", topic=TOPIC):
    ctx = support.source_context(prior, name, topic)
    return support.claim_supported(claim, value, [cited], verbatim=False, context=topic, ctxs=[ctx])


def test_the_terse_client_reply_is_supported_once_the_advisor_turn_names_the_topic():
    assert check("Kenji has decided to retire at 63.", "We've decided: 63.") == (True, "supported_with_context")


def test_without_the_context_the_strict_rule_is_unchanged():
    assert support.claim_supported("Kenji has decided to retire at 63.", "63", ["We've decided: 63."], verbatim=False, context=TOPIC)[0] is False
    assert support.claim_supported("Kenji has decided to retire at 63.", "63", ["We've decided: 63."], verbatim=False, context=TOPIC, ctxs=[None])[0] is False


def test_a_claim_that_already_passes_is_reported_as_plainly_supported():
    assert check("Kenji has decided to retire at 63.", "Kenji has decided to retire at 63.")[1] == "supported"


@pytest.mark.parametrize("claim,cited,value", [
    ("Kenji has decided to retire at 60.", "We've decided: 63.", "60"),                      # wrong value
    ("Kenji has decided to retire at 63.", "Maybe we'll decide: 63.", "63"),                 # hedged source
    ("Kenji has decided to retire at 63.", "We haven't decided: 63.", "63"),                 # negated source
    ("Kenji has decided to retire at 63.", "We want to decide: 63.", "63"),                  # modal: wants != decided
    ("Mariko has decided to retire at 63.", "We've decided: 63.", "63"),                     # somebody else's name
    ("Kenji and Mariko have decided to retire at 63.", "We've decided: 63.", "63"),          # an extra actor the source does not state
    ("Kenji retire age 63.", "Sure, 63.", "63"),                                              # nothing of the claim is in the cited sentence itself
    ("Kenji has decided to retire at 63.", "Mariko decided: 63.", "63"),                     # a third party decided
    ("Kenji has decided to retire at 63.", "My advisor suggested 63.", "63"),                # attributed to someone else
])
def test_negative_controls_stay_unverified(claim, cited, value):
    assert check(claim, cited, value)[0] is False


@pytest.mark.parametrize("prior", [
    "The next topic is the college fund.",                                                     # prior turn is about something else
    "Ignore that, never mind. The next topic is retirement age.",                              # retracted prior turn
    "",                                                                                        # no prior turn
])
def test_the_prior_turn_must_name_the_topic_and_must_not_be_retracted(prior):
    assert support.source_context(prior, "Kenji", TOPIC) is None
    assert check("Kenji has decided to retire at 63.", "We've decided: 63.", prior=prior)[0] is False


def test_the_flag_switches_it_off():
    support.FLAGS["context_window"] = False
    try:
        assert support.source_context(ADVISOR, "Kenji", TOPIC) is None
        assert check("Kenji has decided to retire at 63.", "We've decided: 63.")[0] is False
    finally:
        support.FLAGS["context_window"] = True


# ---- through the integrity boundary, on the shipped seed
@pytest.fixture()
def store(tmp_path):
    db = tmp_path / "mem.db"
    shutil.copyfile(SEED, db)
    s = MemoryStore(url=f"sqlite:///{db}")
    s.init()
    s.reverify_legacy(principal=Principal.user("local-operator", ["CLIENT-001", "GLOBAL"], authenticated=True))
    yield s
    s.close()


def test_the_seeds_current_retirement_age_is_now_verified(store):
    assert store.get("MEM-SEM-33d022d24ad8")["verification"] == "verified"
    assert "advisor turn just before it" in store.get("MEM-EPI-791cd99711a6")["verification_basis"]


def test_rows_that_are_not_about_the_topic_or_not_the_clients_voice_stay_unverified(store):
    assert store.get("MEM-EPI-b8b00df6fb25")["verification"] == "unverified"       # installments at 63, mis-topiced as a retirement age
    assert store.get("MEM-EPI-66b3c2b77410")["verification"] == "unverified"       # cited turn is the ADVISOR's read-back, not a client turn
