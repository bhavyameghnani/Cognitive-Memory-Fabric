"""Which episodes the Report agent shows when retrieval returns more than it can use.  A history question widens retrieval to the whole timeline (chronology_episode_cap), but the
report shows at most EPISODES_PER_CLIENT of them.  Taking the first N (the oldest) dropped the latest decision; verified episodes come first, then the most recent of the rest."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_report_agent import mem  # noqa: E402
from agents.report.evidence import pick_episodes  # noqa: E402


def timeline(n, verified_at=()):
    out = []
    for i in range(n):
        m = mem(f"EP-{i:02d}", "episodic", "60", "active", "confirmed", event=f"2026-01-{i + 1:02d}")
        if i not in verified_at:
            m = m.model_copy(update=dict(verification="unverified"))
        out.append(m)
    return out


def ids(items):
    return [m.memory_id for m in items]


def test_under_the_cap_everything_is_kept_in_order():
    eps = timeline(5)
    assert ids(pick_episodes(eps, 12)) == ids(eps)


def test_over_the_cap_the_most_recent_are_kept_not_the_oldest():
    eps = timeline(20)
    assert ids(pick_episodes(eps, 12)) == [f"EP-{i:02d}" for i in range(8, 20)]


def test_a_verified_episode_is_never_squeezed_out_and_order_stays_chronological():
    eps = timeline(20, verified_at=(0, 19))                  # the oldest and the newest are verified
    got = ids(pick_episodes(eps, 4))
    assert got == ["EP-00", "EP-17", "EP-18", "EP-19"]       # both verified ones, then the most recent of the rest, oldest first
