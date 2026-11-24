"""Search-term quality of the Tool Selector's code-driven source discovery (agents/tool_selector/source_discovery.py).

The wording of an instruction ("including", "show", "explain", "change", "conversation turns" ...) describes the OUTPUT asked for; it is never the subject of the search.  In the live chronology run
(trace REQ-13620587) the searches were ['retirement', 'change', 'including']: the last two matched unrelated turns and spent tool calls.  Possessives ("Kenji's") must not leave a stray
token either.  Domain words (retirement, mortgage, vesting ...) must keep working.  Disposable data only: the frozen conversations.csv is read, never written.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import pytest  # noqa: E402

from agents.tool_selector import source_discovery as SD  # noqa: E402
from services.conversations import shared  # noqa: E402

CONV = shared()
K = "CLIENT-001"
NAMES = {v["first_name"] for v in CONV.directory().values()}
QUESTION = ("Give me the chronology of Kenji's retirement age with the source conversation turns, including any corrections. Distinguish what Kenji explicitly stated from what the advisor "
            "reported or inferred. Show the dates and evidence for each change, and identify anything that remains unverified or uncertain.")
GENERIC = ["including", "show", "explain", "change", "changes", "changed", "conversation", "conversations", "turn", "turns", "stated", "reported", "inferred", "unverified", "uncertain",
           "remains", "anything", "each", "distinguish", "identify", "evidence", "dates", "corrections"]


def terms(text, client=K):
    return SD.source_search_terms(text, client, CONV, NAMES)


def test_generic_request_words_are_never_search_terms():
    got = terms(QUESTION)
    assert got, "the subject of the request must still be found"
    for t in got:
        assert not set(t.split()) & set(GENERIC), (t, got)


@pytest.mark.parametrize("word", GENERIC)
def test_each_generic_word_is_a_stop_word(word):
    assert word in SD._STOP


@pytest.mark.parametrize("word", ["retirement", "mortgage", "vesting", "portfolio", "pension", "equity", "layoff", "estate"])
def test_domain_words_are_not_stop_words(word):
    assert word not in SD._STOP


def test_a_request_made_only_of_instruction_words_finds_no_search_term():
    assert terms("Please show and explain everything, including each change and any conversation turns that remain.") == []


def test_the_subject_is_found_next_to_generic_words():
    got = terms("Show the retirement chronology including each change")
    assert got and all("retire" in t for t in got), got


def test_possessives_leave_no_stray_token_and_names_are_still_excluded():
    assert SD._tokens("Kenji's retirement age") == ["kenji", "retirement", "age"]
    assert SD._tokens("it's the client's plan, they're sure") == ["it", "the", "client", "plan", "they're", "sure"]
    assert SD._tokens("Rebecca's mortgage") == ["rebecca", "mortgage"]
    for t in terms("Kenji's retirement age"):
        assert "kenji" not in t and "'" not in t, t


def test_a_possessive_name_does_not_become_part_of_a_phrase():
    # before the possessive was stripped, "kenji's" was neither a stop word nor a known first name, so it could start a candidate phrase
    assert not any("kenji" in t for t in terms("Kenji's retirement age and Kenji's mortgage"))


def test_every_chosen_term_really_occurs_in_the_clients_turns():
    import re
    texts = [SD._norm(r["utterance"]) for r in CONV.rows if r["client_id"] == K]
    for t in terms(QUESTION):
        assert any(re.search(r"(?<!\w)" + re.escape(t) + r"(?!\w)", x) for x in texts), t
