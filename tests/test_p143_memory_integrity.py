"""P1.4.3 - remediation of the P1.4.2 audit findings F1 (verification), F2 (metadata), F3 (trigger definitions), F4 (slot identity) and the TTL boundary.

Everything runs against temporary / in-memory SQLite stores.  Nothing here points at an operational database.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from agents.memory import support  # noqa: E402
from agents.memory.errors import MemoryWriteRejected  # noqa: E402
from agents.memory.integrity import Principal  # noqa: E402
from harness.evaluation.p15 import MemoryLayerRunner  # noqa: E402
from harness.evaluation.p15_corpus import Case15, N, V  # noqa: E402
from p143_probes import P  # noqa: E402

K = "CLIENT-001"

# the 11 probes that the audit REPRODUCED as unsupported-but-VERIFIED against P1.4.2 (audit finding F1)
ORIGINAL_FALSE_POSITIVES = {"B7", "B8", "B15", "B18", "C8", "E1", "E2", "E3", "M3", "M5", "M8"}
# the 4 probes whose source DOES support the claim but the deterministic matcher declines (recorded, not loosened)
KNOWN_FALSE_NEGATIVES = {"B16", "D10", "F6", "F8"}


@pytest.fixture(scope="module")
def outcomes():
    run = MemoryLayerRunner()
    out = {}
    for pid, claim, ev, val, exp, op in P:
        got, why = run.verdict(Case15(pid, "x", K, claim, ev, val, exp, "-", open_item=bool(op)))
        out[pid] = (got, why, exp)
    return out


# ============================================================================================================ F1: the 73 audit probes
def test_the_corpus_is_the_complete_audit_set():
    assert len(P) == 73 and len({p[0] for p in P}) == 73
    assert ORIGINAL_FALSE_POSITIVES <= {p[0] for p in P} and all(p[4] == N for p in P if p[0] in ORIGINAL_FALSE_POSITIVES)
    assert KNOWN_FALSE_NEGATIVES <= {p[0] for p in P} and all(p[4] == V for p in P if p[0] in KNOWN_FALSE_NEGATIVES)


def test_no_unsupported_claim_of_the_73_is_stored_verified(outcomes):
    fp = sorted(pid for pid, (got, _, exp) in outcomes.items() if got == V and exp == N)
    assert fp == [], fp


def test_each_of_the_11_original_false_positives_is_now_unverified(outcomes):
    for pid in sorted(ORIGINAL_FALSE_POSITIVES):
        assert outcomes[pid][0] == N, (pid, outcomes[pid])


def test_every_supported_probe_that_verified_before_still_verifies(outcomes):
    verified = {pid for pid, (got, _, exp) in outcomes.items() if got == V}
    legit = {p[0] for p in P if p[4] == V} - KNOWN_FALSE_NEGATIVES
    assert verified == legit == {"B1", "C7", "C12", "C15", "D4", "D5", "F4"}


def test_the_false_negatives_are_recorded_explicitly_not_hidden(outcomes):
    fn = {pid for pid, (got, _, exp) in outcomes.items() if got == N and exp == V}
    assert fn == KNOWN_FALSE_NEGATIVES                                    # conservative: they stay unverified, the matcher is not loosened to rescue them


@pytest.mark.parametrize("pid", [p[0] for p in P])
def test_probe_through_the_real_boundary(outcomes, pid):
    got, why, exp = outcomes[pid]
    if exp == N:
        assert got == N, (pid, why)
    elif pid in KNOWN_FALSE_NEGATIVES:
        assert got == N, pid
    else:
        assert got == V, (pid, why)


# ============================================================================================================ F1: new failure modes found while fixing
NEW_MODES = [  # (claim, source, must_verify)
    ("I will retire at 63.", "My wife will retire at 63.", False),
    ("I will retire at 63.", "My daughter, Mei, will retire at 63.", False),
    ("Kenji will retire at 63.", "I will retire at 63.", False),                                   # a named third person from the client's own sentence
    ("I will retire at 63.", "He will retire at 63.", False),
    ("I will retire at 63, but my wife will not.", "I will not retire at 63, but my wife will.", False),   # negation present on BOTH sides, attached to the wrong person
    ("I will retire at 63 and my wife at 60.", "I will retire at 63 and my wife at 60.", True),
    ("My wife will retire at 63.", "I will retire at 63 and my wife at 60.", False),                 # the 63 belongs to the speaker
    ("I will retire at 63.", "I will retire at 63 and my wife at 60.", True),
    ("I will retire at 63.", "I declined to retire at 63.", False),
    ("I will retire at 63.", "I refused to retire at 63.", False),
    ("I will retire at 63.", "I will retire at 63 except if the sale fails.", False),
    ("I will retire at 63.", "I will retire at 63. Scratch that.", False),                          # a retraction in ANOTHER sentence of the same excerpt
    ("I will retire at 63.", "I will retire at 63. Never mind.", False),
    ("I will retire at 63.", "My advisor suggested that I retire at 63.", False),
    ("I will retire at 63.", "My accountant told me to retire at 63.", False),
    ("I will retire at 63.", "I will retire at 63.", True),
]


@pytest.mark.parametrize("claim,source,must", NEW_MODES)
def test_new_failure_modes(claim, source, must):
    run = MemoryLayerRunner()
    got, why = run.verdict(Case15("N", "x", K, claim, source, "63", V if must else N, "-"))
    assert (got == V) is must, (claim, source, why)


def test_the_matcher_returns_false_when_it_cannot_decide_and_never_calls_a_model():
    assert support.structure_ok("I will retire at 63.", "I will retire at 63.") is True
    assert support.structure_ok("I will retire at 63.", "My wife will retire at 63.") is False
    assert support.retracted("I will retire at 63. Just kidding.") and not support.retracted("I will retire at 63.")
    import inspect
    src = inspect.getsource(support)
    assert "import llm" not in src and "from llm" not in src and "MockLLM" not in src        # deterministic: no model client is reachable from the matcher


# ============================================================================================================ F1 / derived authority
def _derived_status(claim, source, derived_value="63"):
    """Episode from (claim, source), then a consolidation-class semantic memory citing the SAME turn; returns (episode verification, derived verification)."""
    run = MemoryLayerRunner()
    ep, _ = run.verdict(Case15("G", "x", K, claim, source, "63", V, "-"))
    idx = max(i for (_, i) in run.conv.rows)
    mid = "SEM-G"
    rec = dict(memory_id=mid, client_id=K, memory_type="semantic", topic="retirement_age", subject="g", title="t", content="retirement age = " + derived_value, value_text=derived_value,
               certainty="confirmed", status="active", origin="consolidation", content_hash=mid, event_date="2026-06-01", valid_from="2026-06-01", version_no=1)
    ev = dict(source_type="conversation", interaction_id="INT-P15", turn_index=idx, speaker="Client", excerpt=source)
    run.store.insert_memory(rec, dict(slot_key="retirement_age|g", basis="b", supporting_episodes=1), [ev], principal=Principal.service("consolidation", [K]))
    return ep, run.store.get(mid)["verification"], run


def test_derived_memory_from_a_verified_episode_is_verified():
    ep, derived, _ = _derived_status("I will retire at 63.", "I will retire at 63.")
    assert (ep, derived) == (V, "verified")


@pytest.mark.parametrize("claim,source", [("I will retire at 63.", "My wife will retire at 63."),          # the former false positive: its derived memory must not be verified either
                                          ("I will retire at 63.", "I will not retire at 63."),                  # contradicted
                                          ("I will retire at 63.", "I may retire at 63."),                       # ambiguous
                                          ("I will retire at 63.", "I rejected retiring at 63.")])
def test_derived_memory_never_outranks_an_unverified_episode(claim, source):
    ep, derived, _ = _derived_status(claim, source)
    assert ep == N and derived == "unverified"


def test_derived_memory_with_a_value_the_verified_episode_does_not_state_is_unverified():
    _, derived, _ = _derived_status("I will retire at 63.", "I will retire at 63.", derived_value="65")
    assert derived == "unverified"


def test_mixed_sources_derived_status_follows_the_episode_that_states_the_value():
    run = MemoryLayerRunner()
    run.verdict(Case15("M1", "x", K, "I will retire at 63.", "I will retire at 63.", "63", V, "-"))
    run.verdict(Case15("M2", "x", K, "I will retire at 65.", "My wife will retire at 65.", "65", N, "-"))
    vs = {r["value_text"]: r["verification"] for r in run.store.records(K, "episodic")}
    assert vs == {"63": "verified", "65": "unverified"}


def test_the_derived_status_survives_repeated_consolidation_and_reopen(tmp_path):
    from agents.memory.agent import MemoryOrchestrator
    from agents.memory.store import MemoryStore
    from llm.client import MockLLM
    from services.conversations import shared
    url = "sqlite:///" + str(tmp_path / "d.db").replace("\\", "/")
    conv = shared()
    store = MemoryStore(url=url, conv=conv)
    o = MemoryOrchestrator(MockLLM(handlers={}), store=store, conv=conv)
    o.init()
    before = {r["memory_id"]: r["verification"] for r in store.records(K, "semantic")}
    for _ in range(2):
        o.consolidator.consolidate_client(K)
    store.close()
    again = MemoryStore(url=url, conv=conv)
    MemoryOrchestrator(MockLLM(handlers={}), store=again, conv=conv)
    after = {r["memory_id"]: r["verification"] for r in again.records(K, "semantic")}
    assert all(after.get(m, v) == v for m, v in before.items())          # a reopen / re-consolidation never upgrades a row
