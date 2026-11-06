"""Evaluation of claim verification against HAND-WRITTEN labels (deterministic fixtures; no live LLM)."""
import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from harness.evaluation import adversarial, runner  # noqa: E402

AUDITED_FALSE_POSITIVES = {"a01-negated", "a02-wrong-person", "a03-substring-number", "a04-wrong-runway", "a05-wrong-target", "a07-historical-vs-current", "a09-wrong-unit"}


@pytest.fixture(scope="module")
def modes():
    return {m: adversarial.run(m) for m in ("none", "lexical", "structured")}


def test_fixtures_are_hand_labelled_and_cover_the_twelve_audited_cases():
    ids = [c.id for c in adversarial.CASES]
    assert len(ids) == len(set(ids)) and len(ids) >= 60
    assert {c.expected for c in adversarial.CASES} == {adversarial.V, adversarial.N}
    audited = [c for c in adversarial.CASES if c.group == "audited"]
    assert {c.id.split("-")[0] for c in audited} >= {f"a{i:02d}" for i in range(1, 13)}
    assert {c.group for c in adversarial.CASES} >= {"audited", "paraphrase", "held_out", "structural", "tool", "unseen_r1"}
    assert sum(c.expected == adversarial.N for c in adversarial.CASES) > sum(c.expected == adversarial.V for c in adversarial.CASES)       # it is mostly a test of what must NOT verify


def test_labels_are_not_produced_by_any_verifier():
    """Independence: EvidenceVerifier appears once, in the function that SCORES a system; no label is computed, filtered or repaired with it."""
    import ast
    tree = ast.parse(inspect.getsource(adversarial))
    calls = [(fn.name, n) for fn in ast.walk(tree) if isinstance(fn, ast.FunctionDef) for n in ast.walk(fn)
             if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "EvidenceVerifier"]
    assert [name for name, _ in calls] == ["present"], "the verifier may be constructed only in the function that scores a system"
    for c in adversarial.CASES:
        assert isinstance(c.expected, str) and c.expected in (adversarial.V, adversarial.N) and c.why                                      # every label carries a human reason


def test_structured_verifier_has_no_false_positive_and_no_false_negative_on_the_labelled_set(modes):
    s = modes["structured"]
    wrong = [r["id"] for r in s["rows"] if not r["correct"]]
    assert s["false_positive_verifications"] == 0 and s["false_negative_verifications"] == 0 and not wrong, wrong


def test_the_audited_lexical_verifier_really_did_verify_wrong_claims(modes):
    lex = modes["lexical"]
    fps = {r["id"] for r in lex["rows"] if r["expected"] == adversarial.N and r["actual"] == adversarial.V}
    assert AUDITED_FALSE_POSITIVES <= fps, AUDITED_FALSE_POSITIVES - fps                                                                    # the seven audited false verifications
    assert lex["false_positive_verifications"] >= 20 and lex["false_negative_verifications"] >= 1


def test_without_any_verifier_every_wrong_claim_is_presented_as_verified(modes):
    n = modes["none"]
    assert n["false_positive_verifications"] == n["should_reject"] and n["false_negative_verifications"] == 0


def test_every_structured_rejection_has_a_reason(modes):
    for r in modes["structured"]["rows"]:
        if r["expected"] == adversarial.N:
            assert r["label"] in ("unsupported", "qualified") and r["outcome"] in ("UNVERIFIED", "CONTRADICTED", "AMBIGUOUS") and r["reasons"], r["id"]


def test_the_runner_reports_the_comparison(tmp_path):
    doc = runner.run(["baseline", "no_structured_verification", "harness_full"], tmp_path)
    m = {n: doc["results"][n]["metrics"] for n in doc["results"]}
    assert (m["baseline"]["claim_verifier"], m["no_structured_verification"]["claim_verifier"], m["harness_full"]["claim_verifier"]) == ("none", "lexical", "structured")
    assert m["harness_full"]["false_positive_verifications"] == 0 < m["no_structured_verification"]["false_positive_verifications"] < m["baseline"]["false_positive_verifications"]
    assert m["harness_full"]["adversarial_cases"] == len(adversarial.CASES)
    md = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "FALSE POSITIVES" in md and "a01-negated" in md and "CIRCULAR" in md and "expected (hand-written label)" in md
    assert all("adversarial" in doc["results"][n]["components"] for n in doc["results"])
