"""Which episodes the Report Agent shows when retrieval returns more than it can use (agents/report/evidence.py: pick_episodes), and the safe fallback summary.

A history question widens retrieval to the whole timeline (chronology_episode_cap = 40) but the report shows at most EPISODES_PER_CLIENT (16).  Taking the first 12 kept the OLDEST meetings
and dropped the latest decision.  Adapted from the selective port of Bhavya's pick_episodes (commit 2278cdb); the ranking differs on purpose (see pick_episodes): the integrity boundary's
`verification` decides "verified" (a verified but exploratory episode is still verified), history-relevant episodes outrank ordinary ones, and every omission is reported.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_report_agent import K, agent_for, mem, plan, tstate  # noqa: E402
from agents.report.evidence import EPISODES_PER_CLIENT, build_evidence, is_chronology_critical, pick_episodes  # noqa: E402
from agents.report.validate import NumberChecker, check_prose, deterministic_summary, evidence_text  # noqa: E402
from schemas.memory import MemoryContext  # noqa: E402


def timeline(n, verified_at=None, certainty="confirmed", special=None):
    """n episodes, oldest first.  verified_at = indexes that are verified (None: all verified); special = {index: certainty}."""
    out = []
    for i in range(n):
        m = mem(f"EP-{i:02d}", "episodic", "60", "active", (special or {}).get(i, certainty), event=f"2026-01-{i + 1:02d}")
        if verified_at is not None and i not in verified_at:
            m = m.model_copy(update=dict(verification="unverified"))
        out.append(m)
    return out


def ids(items):
    return [m.memory_id for m in items]


# ---------------------------------------------------------------------------------------------------------------- undersized and empty collections
def test_empty_and_undersized_collections_are_returned_whole_and_in_order():
    assert pick_episodes([], 12) == ([], [])
    eps = timeline(5)
    shown, omitted = pick_episodes(eps, 12)
    assert ids(shown) == ids(eps) and omitted == []
    shown, omitted = pick_episodes(timeline(12), 12)                               # exactly the cap: nothing is omitted
    assert len(shown) == 12 and omitted == []


def test_a_cap_of_zero_shows_nothing_and_omits_everything():
    shown, omitted = pick_episodes(timeline(3), 0)
    assert shown == [] and len(omitted) == 3


# ---------------------------------------------------------------------------------------------------------------- recency
def test_over_the_cap_the_most_recent_are_kept_not_the_oldest():
    eps = timeline(20)                                                              # all verified, all ordinary
    shown, omitted = pick_episodes(eps, 12)
    assert ids(shown) == [f"EP-{i:02d}" for i in range(8, 20)]
    assert ids(omitted) == [f"EP-{i:02d}" for i in range(0, 8)]


def test_the_forty_episode_history_case_keeps_the_latest_decision():
    eps = timeline(40)
    shown, _ = pick_episodes(eps, EPISODES_PER_CLIENT)
    assert "EP-39" in ids(shown) and "EP-00" not in ids(shown) and len(shown) == EPISODES_PER_CLIENT


# ---------------------------------------------------------------------------------------------------------------- verified first, chronological output
def test_verified_episodes_are_prioritised_and_order_stays_chronological():
    eps = timeline(20, verified_at=(0, 19))                                         # the oldest and the newest are verified, the rest are not
    shown, _ = pick_episodes(eps, 4)
    assert ids(shown) == ["EP-00", "EP-17", "EP-18", "EP-19"]                       # both verified ones, then the most recent of the rest, oldest first


def test_more_verified_episodes_than_the_cap_keeps_the_latest_not_the_oldest():
    eps = timeline(20)                                                              # every episode verified
    shown, _ = pick_episodes(eps, 4)
    assert ids(shown) == ["EP-16", "EP-17", "EP-18", "EP-19"]                       # (the first-N rule would have kept EP-00..EP-03)


def test_a_verified_but_exploratory_episode_is_still_verified_for_selection():
    """Its evidence CLASS is 'ambiguous' (not a decision) but the integrity boundary verified it; selection must not demote it below unverified episodes."""
    eps = timeline(20, verified_at=(2,), certainty="confirmed", special={2: "exploratory"})
    shown, _ = pick_episodes(eps, 3)
    assert "EP-02" in ids(shown)


# ---------------------------------------------------------------------------------------------------------------- chronology-relevant history is not discarded silently
def test_an_old_correction_and_an_old_exploratory_value_outrank_newer_ordinary_tentative_episodes():
    """Among TENTATIVE episodes (nothing here is a decision except the correction): the correction is kept, and the one episode that considered a different value is represented before
    the same tentative remark is represented twice.  (A confirmed decision outranks exploratory remarks: tests/test_report_evidence_capacity.py.)"""
    eps = timeline(20, verified_at=(), certainty="unconfirmed", special={1: "correction", 3: "exploratory"})
    shown, omitted = pick_episodes(eps, 5)
    assert {"EP-01", "EP-03"} <= set(ids(shown))
    assert ids(shown) == sorted(ids(shown))                                          # still chronological
    assert is_chronology_critical(eps[1]) and not is_chronology_critical(eps[2])
    assert "EP-02" in ids(omitted)


def test_omissions_are_recorded_and_reported_in_the_report_uncertainties():
    eps = timeline(20, verified_at=(), special={1: "correction"})
    ctx = MemoryContext(client_id=K, episodes=eps)
    items, removed = build_evidence(plan(), {K: ctx}, tstate())
    (o,) = removed["episodes_omitted"]
    assert o["client_id"] == K and o["total"] == 20 and o["shown"] == EPISODES_PER_CLIENT and len(o["ids"]) == 4 and "EP-01" not in o["ids"]        # the correction is kept
    assert sum(1 for e in items if e.memory_id) == EPISODES_PER_CLIENT
    from schemas.report import AdvisorReport  # noqa: F401  (the contract is unchanged)
    a, _ = agent_for([json.dumps(dict(summary="The evidence does not state a definite retirement age."))])
    r = a.write("What is Kenji's retirement age?", plan(), {K: ctx}, tstate())
    assert any("20 stored episodes" in u and "4 omitted" in u for u in r.uncertainties), r.uncertainties


def test_no_omission_is_reported_when_everything_fits():
    _, removed = build_evidence(plan(), {K: MemoryContext(client_id=K, episodes=timeline(5))}, tstate())
    assert removed["episodes_omitted"] == []


# ---------------------------------------------------------------------------------------------------------------- selection never changes verification
def test_choosing_an_unverified_episode_does_not_make_it_verified():
    eps = timeline(20, verified_at=())                                               # none verified
    before = [m.model_dump() for m in eps]
    shown, _ = pick_episodes(eps, 12)
    assert [m.model_dump() for m in eps] == before                                   # the inputs are not modified
    assert all(m.verification == "unverified" for m in shown)
    items, _ = build_evidence(plan(), {K: MemoryContext(client_id=K, episodes=eps)}, tstate())
    eps_items = [e for e in items if e.memory_id and e.memory_id.startswith("EP-")]
    assert eps_items and all(e.evidence_class == "unverified" and e.verification == "unverified" for e in eps_items)


# ---------------------------------------------------------------------------------------------------------------- determinism
def test_selection_is_deterministic():
    eps = timeline(30, verified_at=(0, 7, 29), special={5: "correction", 11: "exploratory"})
    first = pick_episodes(eps, 10)
    for _ in range(3):
        again = pick_episodes(list(eps), 10)
        assert (ids(again[0]), ids(again[1])) == (ids(first[0]), ids(first[1]))
    assert ids(first[0]) == sorted(ids(first[0]))


# ---------------------------------------------------------------------------------------------------------------- safe fallback summary
def test_the_safe_fallback_summary_passes_the_number_checker_with_many_evidence_items():
    eps = timeline(20, verified_at=(), certainty="unconfirmed")                      # 12 shown, none definitive: no definite answer exists, so the fallback sentence is used
    ctx = MemoryContext(client_id=K, episodes=eps)
    items, _ = build_evidence(plan(), {K: ctx}, tstate())
    items = items * 2                                                                # 24 items: any count in the sentence would be 13 or more
    summary = deterministic_summary(items, plan())
    assert summary.startswith("Evidence was found") and not any(ch.isdigit() for ch in summary)
    nc = NumberChecker(evidence_text(items, "What is Kenji's retirement age?"))
    assert check_prose(summary, items, nc) is None
    old = f"Evidence was found ({len(items)} items) but it does not state a definite answer; see the findings and uncertainties."
    assert check_prose(old, items, nc) is not None                                   # the checker itself is unchanged: it still rejects an unsupported count of 13+
