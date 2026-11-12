"""Repeatable evaluation runner.

Replays the SAME fixed scenarios, with the SAME scripted model answers, across harness configurations (an existing-CMF baseline, the full harness, and
single-control ablations), then writes machine-readable results, per-request trace artifacts and a Markdown comparison.

    PYTHONPATH=src python -m harness.evaluation.runner [--out build/harness_eval] [--configs baseline,harness_full,no_cache] [--list]

No network, no credentials.  Scripted models make results reproducible; they measure how the harness reacts to good and bad model output, NOT how accurate a
real LLM is.  Any claim of improved accuracy must come from running this against the numbers it prints, not from the design.
"""
from __future__ import annotations

import argparse
import json
import platform
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional

from harness.approval_store import ApprovalStore
from harness import AuthContext, Harness, HarnessConfig, PermissionDenied
from harness.context_manager import ContextBudgetController
from harness.evaluation import adversarial, fixtures, metrics
from harness.token_counter import TokenCounter
from llm.client import MockLLM
from orchestration.pipeline import run_pipeline

FIXTURE_VERSION = "1"
ROOT = Path(__file__).resolve().parents[3]

CONFIGS: dict = {
    "baseline": None,                                         # the existing CMF pipeline, no harness
    "harness_full": {},
    "no_cache": {"cache_enabled": False},
    "no_compression": {"compression_enabled": False},
    "no_evidence_verification": {"evidence_verification": False},
    "no_structured_verification": {"structured_claim_verification": False},      # the audited lexical verifier
    "no_permission_gate": {"permission_gate": False},
    "no_write_validation": {"memory_write_validation": False},
    "no_approval": {"approval_required_for_high_risk": False},
    "no_temporal_detection": {"temporal_conflict_detection": False},
    "direct_transport": {"strands_execution": False},
}


def _handler(spec: Any):
    """dict -> always that dict; list -> consumed in order, the last element repeats; callable -> itself."""
    if callable(spec):
        return spec
    if isinstance(spec, list):
        queue = list(spec)
        return lambda s, u, sc: queue.pop(0) if len(queue) > 1 else queue[0]
    return lambda s, u, sc: spec


@dataclass
class _RecordingMock(MockLLM):
    """MockLLM that also remembers the output schema of each call, so the baseline's prompt size is counted on the same basis as the harness's
    (system + schema + user + fixed overhead)."""
    schemas: list = field(default_factory=list)

    def complete(self, agent, system, user, schema=None):
        out = super().complete(agent, system, user, schema)
        self.schemas.append(schema)
        return out


def _llm(sc) -> MockLLM:
    h = {"planner": _handler(sc.planner), "report": _handler(sc.report)}
    if sc.selector is not None:
        h["tool_selector"] = _handler(sc.selector)
    return _RecordingMock(handlers=h)


def run_scenario(sc, cfg_name: str, overrides: Optional[dict], out_dir: Optional[Path] = None) -> list:
    world = fixtures.build_world()
    llm = _llm(sc)
    harness = None
    if overrides is not None:
        harness = Harness(HarnessConfig.for_test(**{**overrides, **sc.harness_overrides}), approval_store=ApprovalStore())   # scripted scenarios: a volatile store, never the real database
    auth = AuthContext(sc.actor, sc.role, True, source="evaluation") if sc.actor else None
    counter = TokenCounter(llm.model)
    outcomes = []
    for i in range(sc.repeat):
        o = metrics.Outcome(sc.id, cfg_name, i)
        calls_before, t0 = len(llm.calls), time.perf_counter()
        try:
            res = run_pipeline(sc.question, llm, world, persist_trace=False, harness=harness, auth=auth)
        except PermissionDenied as e:
            o.denied, o.denial = True, e.code
            o.latency_ms = int((time.perf_counter() - t0) * 1000)
            o.llm_calls = len(llm.calls) - calls_before
            o.expectations = metrics.check_expectations(sc, o)
            outcomes.append(o)
            continue
        o.latency_ms = int((time.perf_counter() - t0) * 1000)
        o.llm_calls = len(llm.calls) - calls_before
        o.tool_calls = sum(e.status in ("success", "error", "timeout") for e in res.tools.executions)
        o.released = res.released
        o.digest = metrics.report_digest(res.report)
        o.visible_text = metrics.visible_text(res.report)
        o.findings = metrics.independent_findings(res.report, res.plan, res.tools, world.store)
        if res.harness:
            tel, bud = res.harness["telemetry"], res.harness["budget"]
            o.input_tokens, o.output_tokens = tel["input_tokens"], tel["output_tokens"]
            o.exhausted = [e["reason"] for e in bud["exhausted"]]
            o.cache_hits, o.cache_lookups = tel["cache_hits"], tel["cache_hits"] + tel["cache_misses"]
            o.llm_calls = bud["llm_calls"]                      # model calls actually made (cache hits excluded)
            o.tool_calls = bud["tool_calls"] if bud["tool_calls"] else o.tool_calls
            o.compression_ratios = [e["payload"]["ratio"] for e in res.trace if e["component"] == "harness.context" and e["event"] == "compression"]
        else:
            new = list(zip(llm.calls[calls_before:], llm.schemas[calls_before:]))
            o.input_tokens = sum(counter.n(c["system"]) + (counter.n(json.dumps(sch)) if sch else 0) + counter.n(c["user"]) + 24 for c, sch in new)
        o.expectations = metrics.check_expectations(sc, o)
        outcomes.append(o)
        if out_dir is not None:
            d = out_dir / "traces"
            d.mkdir(parents=True, exist_ok=True)
            (d / f"{cfg_name}__{sc.id}__{i}.json").write_text(json.dumps(res.trace, indent=1, default=str), encoding="utf-8")
    return outcomes


def _git() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True, timeout=5).stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def metadata() -> dict:
    try:
        import importlib.metadata as md
        strands = md.version("strands-agents")
    except Exception:
        strands = "unknown"
    return dict(fixture_version=FIXTURE_VERSION, python=sys.version.split()[0], platform=platform.platform(), strands_agents=strands, model="mock-qwen (scripted)", git_commit=_git(),
                started=time.strftime("%Y-%m-%dT%H:%M:%S"), base_harness_config={k: v for k, v in asdict(HarnessConfig.for_test()).items() if k not in ("warnings",)})


def run(configs: Optional[list] = None, out_dir: Optional[Path] = None) -> dict:
    names = configs or list(CONFIGS)
    scs = {s.id: s for s in fixtures.scenarios()}
    results: dict = {}
    for name in names:
        ov = CONFIGS[name]
        outs = [o for sc in scs.values() for o in run_scenario(sc, name, ov, out_dir)]
        agg = metrics.aggregate(outs, scs)
        world = fixtures.build_world()
        ctl = ContextBudgetController(HarnessConfig.for_test(), TokenCounter("mock")) if ov is not None else None
        # component-level checks use the harness component only when its control is on
        wv = metrics.write_validation(with_validator=ov is not None and ov.get("memory_write_validation", True))
        td = metrics.temporal_detection(with_detector=ov is not None and ov.get("temporal_conflict_detection", True))
        rq = metrics.retrieval_quality(world, ctl if (ov is not None and ov.get("permission_gate", True)) else None)
        mode = "none" if ov is None or not ov.get("evidence_verification", True) else "lexical" if not ov.get("structured_claim_verification", True) else "structured"
        adv = adversarial.run(mode)
        agg.update(adversarial_cases=adv["cases"], false_positive_verifications=adv["false_positive_verifications"], false_negative_verifications=adv["false_negative_verifications"],
                   adversarial_correct=adv["correct"], claim_verifier=mode)
        agg.update(memory_write_accuracy=wv["accuracy"], temporal_conflict_recall=td["recall"], temporal_false_positive=td["false_positive_on_clean"],
                   retrieval_precision=rq["precision"], foreign_memories_leaked=rq["foreign_items_leaked"])
        results[name] = dict(config=CONFIGS[name], metrics=agg, outcomes=[asdict(o) for o in outs], components=dict(write_validation=wv, temporal=td, retrieval=rq, adversarial=adv))
    doc = dict(meta=metadata(), results=results)
    if out_dir is not None:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "results.json").write_text(json.dumps(doc, indent=1, default=str), encoding="utf-8")
        (out_dir / "report.md").write_text(render_markdown(doc), encoding="utf-8")
    return doc


ROWS = [("claim_verifier", "Claim verifier under test"), ("adversarial_cases", "Adversarial claim cases (hand-labelled)"),
        ("false_positive_verifications", "FALSE POSITIVES: wrong claims presented as verified"), ("false_negative_verifications", "False negatives: true claims not presented as verified"),
        ("evidence_support_rate", "Evidence support rate (CIRCULAR: judged by the harness's own structured verifier; use the hand-labelled rows above)"), ("unsupported_claim_rate", "Unsupported claim rate (CIRCULAR, same caveat)"), ("memory_write_accuracy", "Memory write accuracy (7 adversarial cases)"),
        ("cross_client_rejection", "Cross-client / unauthorised access rejected"), ("temporal_conflict_recall", "Temporal conflict recall"),
        ("temporal_false_positive", "Temporal false positive on clean memory"), ("retrieval_precision", "Memory retrieval precision (1 foreign memory planted)"),
        ("foreign_memories_leaked", "Foreign memories leaked downstream"), ("context_tokens_per_request", "Prompt tokens per model-served request (fixed non-denied scenarios, estimated)"),
        ("compression_ratio", "Compression ratio (compressed prompts only)"), ("cache_hit_rate", "Cache hit rate (cache scenario)"), ("llm_calls_per_request", "Model calls per request"),
        ("tool_calls_per_request", "Tool calls per request"), ("total_latency_ms_per_request", "Latency per request (ms)"), ("budget_exhaustion_frequency", "Budget exhaustion frequency"),
        ("human_approval_enforcement", "Sensitive answers withheld without approval"), ("deterministic_output_consistency", "Deterministic output consistency"),
        ("scenario_expectations_met", "Scenario expectations met")]


def render_markdown(doc: dict) -> str:
    names = list(doc["results"])
    m = doc["meta"]
    lines = ["# CMF harness evaluation", "", f"- fixtures v{m['fixture_version']} | {m['model']} | python {m['python']} | strands-agents {m['strands_agents']} | commit {m['git_commit']} | {m['started']}",
             "- Scripted models: these numbers show how each configuration reacts to fixed good and bad model output. They are not LLM accuracy.", "",
             "| metric | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    for key, label in ROWS:
        vals = []
        for n in names:
            v = doc["results"][n]["metrics"].get(key)
            vals.append("n/a" if v is None else str(v))
        lines.append(f"| {label} | " + " | ".join(vals) + " |")
    lines += ["", "## Per-scenario outcome (denied / withheld / served; model calls; tool calls)", "", "| scenario | " + " | ".join(names) + " |", "|---|" + "---|" * len(names)]
    sc_ids = list(dict.fromkeys(o["scenario"] for o in doc["results"][names[0]]["outcomes"]))
    for sid in sc_ids:
        cells = []
        for n in names:
            os_ = [o for o in doc["results"][n]["outcomes"] if o["scenario"] == sid]
            o = os_[-1]
            state = "DENIED" if o["denied"] else ("withheld" if not o["released"] else "served")
            bad = [k for o2 in os_ for k, v in o2["expectations"].items() if not v]
            cells.append(f"{state} {o['llm_calls']}m/{o['tool_calls']}t" + (f" ✗{','.join(sorted(set(bad)))}" if bad else ""))
        lines.append(f"| {sid} | " + " | ".join(cells) + " |")
    lines += ["", "✗ marks an expectation the configuration failed (for example an unsupported claim reaching the user)."]
    lines += ["", "## Adversarial claim cases: expected (hand-written label) vs actual, per verifier", "",
              "Labels live in `harness/evaluation/adversarial.py` and are never produced by a verifier. `none` = the existing CMF pipeline (nothing is verified).", ""]
    shown = [n for n in ("baseline", "no_structured_verification", "harness_full") if n in doc["results"]]
    if shown:
        lines += ["| case | expected | " + " | ".join(shown) + " |", "|---|---|" + "---|" * len(shown)]
        by = {n: {r["id"]: r for r in doc["results"][n]["components"]["adversarial"]["rows"]} for n in shown}
        for cid, r0 in by[shown[0]].items():
            cells = []
            for n in shown:
                r = by[n][cid]
                cells.append(("ok " if r["correct"] else "**WRONG** ") + r["actual"].lower() + (f" ({','.join(r['reasons'])})" if r["reasons"] and n == "harness_full" else ""))
            lines.append(f"| {cid} | {r0['expected'].lower()} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="harness.evaluation.runner")
    ap.add_argument("--out", default=str(ROOT / "build" / "harness_eval"), help="output directory (results.json, report.md, traces/)")
    ap.add_argument("--configs", default="", help="comma separated subset of: " + ", ".join(CONFIGS))
    ap.add_argument("--list", action="store_true")
    a = ap.parse_args(argv)
    if not HarnessConfig.from_env().evaluation_enabled:
        print("evaluation is disabled (HARNESS_EVALUATION_ENABLED=false)")
        return 2
    if a.list:
        print("configs:", ", ".join(CONFIGS), "\nscenarios:", ", ".join(s.id for s in fixtures.scenarios()))
        return 0
    chosen = [c.strip() for c in a.configs.split(",") if c.strip()] or None
    for c in chosen or []:
        if c not in CONFIGS:
            print(f"unknown config {c!r}")
            return 2
    doc = run(chosen, Path(a.out))
    print(render_markdown(doc))
    print(f"\nwritten to {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
