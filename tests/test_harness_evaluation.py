"""The evaluation runner: repeatable, comparable across configurations, and honest (controls switched off visibly lose what they protect)."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from harness.evaluation import fixtures, metrics, runner  # noqa: E402

SUBSET = ["baseline", "harness_full", "no_evidence_verification", "no_permission_gate", "no_approval", "no_cache", "no_compression", "no_write_validation", "no_temporal_detection"]


@pytest.fixture(scope="module")
def doc(tmp_path_factory):
    out = tmp_path_factory.mktemp("eval")
    d = runner.run(SUBSET, out)
    return d, out


def m(doc_, cfg, key):
    return doc_[0]["results"][cfg]["metrics"][key]


def test_artifacts_and_metadata_are_recorded(doc):
    d, out = doc
    assert (out / "results.json").is_file() and (out / "report.md").is_file() and list((out / "traces").glob("*.json"))
    meta = d["meta"]
    assert meta["fixture_version"] and meta["strands_agents"] != "unknown" and meta["model"].startswith("mock") and meta["base_harness_config"]["max_llm_calls"] == 20
    md = (out / "report.md").read_text(encoding="utf-8")
    assert "Evidence support rate" in md and "not LLM accuracy" in md
    json.loads((out / "results.json").read_text(encoding="utf-8"))


def test_fifteen_required_measurements_are_present(doc):
    keys = ["evidence_support_rate", "unsupported_claim_rate", "memory_write_accuracy", "cross_client_rejection", "temporal_conflict_recall", "retrieval_precision",
            "context_tokens_per_request", "compression_ratio", "cache_hit_rate", "llm_calls_per_request", "tool_calls_per_request", "total_latency_ms_per_request",
            "budget_exhaustion_frequency", "human_approval_enforcement", "deterministic_output_consistency"]
    got = doc[0]["results"]["harness_full"]["metrics"]
    assert all(k in got and got[k] is not None for k in keys), [k for k in keys if got.get(k) is None]


def test_full_harness_beats_baseline_where_it_claims_to(doc):
    assert m(doc, "baseline", "cross_client_rejection") == 0.0 and m(doc, "harness_full", "cross_client_rejection") == 1.0
    assert m(doc, "baseline", "human_approval_enforcement") == 0.0 and m(doc, "harness_full", "human_approval_enforcement") == 1.0
    assert m(doc, "harness_full", "unsupported_claim_rate") < m(doc, "baseline", "unsupported_claim_rate")
    assert m(doc, "harness_full", "memory_write_accuracy") > m(doc, "baseline", "memory_write_accuracy")
    assert m(doc, "harness_full", "temporal_conflict_recall") > m(doc, "baseline", "temporal_conflict_recall")
    assert m(doc, "harness_full", "foreign_memories_leaked") == 0 and m(doc, "baseline", "foreign_memories_leaked") == 1
    assert m(doc, "harness_full", "llm_calls_per_request") < m(doc, "baseline", "llm_calls_per_request")
    assert m(doc, "harness_full", "tool_calls_per_request") < m(doc, "baseline", "tool_calls_per_request")
    assert m(doc, "harness_full", "scenario_expectations_met") == 1.0 and m(doc, "baseline", "scenario_expectations_met") < 1.0
    assert m(doc, "harness_full", "temporal_false_positive") is False


def test_each_ablation_loses_exactly_what_its_control_protects(doc):
    assert m(doc, "no_permission_gate", "cross_client_rejection") == 0.0 and m(doc, "no_permission_gate", "foreign_memories_leaked") == 1
    assert m(doc, "no_evidence_verification", "unsupported_claim_rate") > m(doc, "harness_full", "unsupported_claim_rate")
    assert m(doc, "no_approval", "human_approval_enforcement") == 0.0
    assert m(doc, "no_cache", "cache_hit_rate") == 0.0 and m(doc, "harness_full", "cache_hit_rate") > 0
    assert m(doc, "no_compression", "compression_ratio") is None and m(doc, "harness_full", "compression_ratio") is not None
    assert m(doc, "no_write_validation", "memory_write_accuracy") < m(doc, "harness_full", "memory_write_accuracy")
    assert m(doc, "no_temporal_detection", "temporal_conflict_recall") < m(doc, "harness_full", "temporal_conflict_recall")
    # controls that were NOT switched off keep working in every ablation
    assert m(doc, "no_cache", "cross_client_rejection") == 1.0 and m(doc, "no_compression", "human_approval_enforcement") == 1.0


def test_results_are_reproducible_apart_from_latency(doc):
    again = runner.run(["baseline", "harness_full"])
    for cfg in ("baseline", "harness_full"):
        a, b = dict(doc[0]["results"][cfg]["metrics"]), dict(again["results"][cfg]["metrics"])
        a.pop("total_latency_ms_per_request"), b.pop("total_latency_ms_per_request")
        assert a == b


def test_baseline_is_the_unmodified_pipeline_and_fixtures_use_real_excerpts():
    assert runner.CONFIGS["baseline"] is None                                     # no harness object is involved at all
    world = fixtures.build_world()
    assert world.store.count("memory_records") > 3                                  # seeded from genuine CSV excerpts (build_world asserts each one)
    assert len(fixtures.scenarios()) >= 12 and len({s.id for s in fixtures.scenarios()}) == len(fixtures.scenarios())


def test_cli_listing_and_unknown_config(capsys):
    assert runner.main(["--list"]) == 0 and "harness_full" in capsys.readouterr().out
    assert runner.main(["--configs", "nonsense"]) == 2
