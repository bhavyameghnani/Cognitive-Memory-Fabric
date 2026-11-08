"""Memory-quality evaluation harness for the Memory Orchestrator (deterministic; no LLM and no network by default).

Ground truth comes from reports/generation_manifest.json (the generator's authored outcomes, "QC only") and data/eval/memory_eval_cases.json.
It runs against a memory DB (default: the shipped seed data/seed/cmf_memory_demo.db, copied to a temp file so the original is never touched).

Three suites
  extraction  did ingestion capture what the manifest says was decided / corrected / left tentative, at the right certainty?
              recall per authored kind, and the unsupported-commitment rate (confirmed/correction memories with no authored decision behind them)
  chains      does the consolidated semantic version chain equal the gold chain per topic?
  retrieval   per case: current-value accuracy, history recall, episode Recall/Precision/MRR over gold (interaction, topic) pairs,
              cross-client leakage, and a joint score = sqrt(value accuracy * evidence recall).  Run in several retrieval modes
              (keyword-only vs hybrid embedding) and episode caps so ranking quality is visible.

Usage:  PYTHONPATH=src python -m evaluation.memory_eval [--db PATH] [--caps 3,5,8] [--embedder hash|ollama|openai] [--json OUT]
"""
from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import sys
import tempfile
from dataclasses import replace
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[2]
SEED_DB = ROOT / "data" / "seed" / "cmf_memory_demo.db"
MANIFEST = ROOT / "reports" / "generation_manifest.json"
CASES = ROOT / "data" / "eval" / "memory_eval_cases.json"

from agents.memory import embeddings as emb  # noqa: E402
from agents.memory.config import RetrievalConfig  # noqa: E402
from agents.memory.retrieve import retrieve_context  # noqa: E402
from agents.memory.store import MemoryStore  # noqa: E402
from schemas.plan import ExecutionPlan  # noqa: E402
from services.conversations import shared  # noqa: E402

KIND_CERTAINTY = {"decision": {"confirmed"}, "plan": {"confirmed"}, "correction": {"correction"},
                  "tentative": {"exploratory", "hypothetical", "unconfirmed", "recommendation"},
                  "deferred": {"exploratory", "hypothetical", "unconfirmed", "recommendation"}}
COMMIT_KINDS = ("decision", "plan", "correction")
OVERLAP_MIN = 0.4
STOP = {"that", "this", "with", "your", "from", "have", "will", "would", "about", "their", "they", "them", "than", "then", "into", "been", "were", "what"}


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", text.lower()) if (len(w) >= 4 or w.isdigit()) and w not in STOP}


def overlap(authored: str, memory_text: str) -> float:
    """Match score in [0,1]: the better of (share of the authored text found in the memory text+excerpt) and (share of the memory's own
    statement found in the authored text).  The second direction keeps short, correct statements from being penalised by long authored rationales."""
    a, m = words(authored), words(memory_text)
    return max(len(a & m) / len(a) if a else 0.0, len(a & m) / len(m) if m else 0.0)


def _rate(n: int, d: int) -> Optional[float]:
    return round(n / d, 4) if d else None


# ------------------------------------------------------------------ suites
def eval_extraction(store: MemoryStore, manifest: dict) -> dict:
    done = {r["interaction_id"] for r in store.query("SELECT interaction_id FROM {S}ingestion_log WHERE status IN ('completed','degraded')")}
    rows = store.query("SELECT m.memory_id, m.certainty, m.content, e.interaction_id FROM {S}memory_records m JOIN {S}episodic_details e USING (memory_id) "
                       "WHERE m.memory_type='episodic'")
    evid = {}
    for r in store.query("SELECT memory_id, excerpt FROM {S}memory_evidence"):
        evid.setdefault(r["memory_id"], []).append(r["excerpt"])
    by_int: dict[str, list[dict]] = {}
    for r in rows:
        r["text"] = r["content"]                      # short statement first; the evidence excerpt is used as a second chance below
        r["full"] = r["content"] + " " + " ".join(evid.get(r["memory_id"], []))
        by_int.setdefault(r["interaction_id"], []).append(r)

    per_kind: dict[str, list[int]] = {k: [0, 0] for k in KIND_CERTAINTY}      # [hit, total]
    misses = []
    authored_commit, recap = set(), {it["interaction_id"] for it in manifest["interactions"] if {"history_recall", "summary"} & set(it["scenario_tags"])}
    for it in manifest["interactions"]:
        iid = it["interaction_id"]
        if iid not in done:
            continue
        for kind, text in it["authored_outcomes"]:
            if kind not in KIND_CERTAINTY:
                continue
            if kind in COMMIT_KINDS:
                authored_commit.add(iid)
            per_kind[kind][1] += 1
            hit = any(m["certainty"] in KIND_CERTAINTY[kind] and max(overlap(text, m["text"]), overlap(text, m["full"])) >= OVERLAP_MIN for m in by_int.get(iid, []))
            per_kind[kind][0] += hit
            if not hit:
                misses.append({"interaction": iid, "kind": kind, "authored": text[:100]})
    commits = [m for iid in done for m in by_int.get(iid, []) if m["certainty"] in ("confirmed", "correction")]
    # a confirmed memory in a recap/summary meeting restates an earlier decision, so it is not counted as unsupported
    unsupported = [m for m in commits if m["interaction_id"] not in authored_commit and m["interaction_id"] not in recap]
    return {"interactions_evaluated": len(done),
            "recall_by_kind": {k: {"hit": h, "total": t, "recall": _rate(h, t)} for k, (h, t) in per_kind.items()},
            "unsupported_commitments": {"count": len(unsupported), "of": len(commits), "rate": _rate(len(unsupported), len(commits)),
                                        "examples": [{"memory_id": m["memory_id"], "interaction": m["interaction_id"], "content": m["content"][:90]} for m in unsupported[:5]]},
            "misses": misses[:15]}


def eval_chains(store: MemoryStore, client: str, gold: dict[str, list[str]]) -> dict:
    out, ok = {}, 0
    for topic, want in gold.items():
        rows = sorted(store.records(client, "semantic", topics=[topic]), key=lambda r: r["version_no"] or 0)
        got = [r["value_text"] for r in rows]
        out[topic] = {"gold": want, "got": got, "match": got == want}
        ok += got == want
    return {"topics": out, "accuracy": _rate(ok, len(gold))}


def _plan(case: dict, client: str) -> ExecutionPlan:
    q = case["query"]
    return ExecutionPlan(request_id="EVAL", plan_id="PLAN-EVAL", request_text=q, client_ids=[client], user_intent=q, task_type=case.get("task_type", "retrieval"),
                         time_period=case.get("time_period"), needs_history=case.get("needs_history", False), needs_calculation=False, subtasks=[],
                         required_memory_categories=["semantic", "episodic"], memory_topics=case["topics"], required_data=[], required_tools=[], dependencies={},
                         execution_order=[], parallelizable_tasks=[], missing_information=[], success_criteria=["answered"])


def eval_case(store: MemoryStore, case: dict, client: str, cfg: RetrievalConfig) -> dict:
    ctx = retrieve_context(store, _plan(case, client), client, "EVAL", cfg)
    cur = {m.topic: m.value_text for m in ctx.current}
    want_cur = case["gold_current"]
    value_acc = sum(cur.get(t) == v for t, v in want_cur.items()) / len(want_cur)
    hist = {}
    for m in ctx.history:
        hist.setdefault(m.topic, set()).add(m.value_text)
    want_hist = case.get("gold_history", {})
    hist_rec = (sum(v in hist.get(t, set()) for t, vs in want_hist.items() for v in vs) / sum(len(vs) for vs in want_hist.values())) if want_hist else None
    gold = {tuple(x) for x in case["relevant"]}
    got = [(e.evidence[0].interaction_id if e.evidence else e.event_date, e.topic) for e in ctx.episodes]
    hits = [g for g in got if g in gold]
    recall = len(set(hits)) / len(gold)
    precision = len(hits) / len(got) if got else 0.0
    mrr = next((1 / (i + 1) for i, g in enumerate(got) if g in gold), 0.0)
    leak = sum(1 for m in ctx.current + ctx.history + ctx.episodes + ctx.unresolved if m.client_id != client)
    return {"id": case["id"], "value_acc": value_acc, "history_recall": hist_rec, "recall": recall, "precision": precision, "mrr": mrr,
            "joint": math.sqrt(value_acc * recall), "leak": leak, "episodes": len(got), "notes": [n for n in ctx.notes if "unavailable" in n]}


def mean(xs):
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 4) if xs else None


def eval_retrieval(store: MemoryStore, cases: dict, caps: list[int], embedder) -> dict:
    client, out = cases["client_id"], {}
    base = RetrievalConfig()
    for cap in caps:
        for mode, weight in (("keyword", 0.0), ("hybrid", 1.0)):
            cfg = replace(base, episode_cap=cap, semantic_weight=weight, embedder=embedder)
            res = [eval_case(store, c, client, cfg) for c in cases["retrieval_cases"]]
            out[f"cap={cap}/{mode}"] = {
                "value_acc": mean(r["value_acc"] for r in res), "history_recall": mean(r["history_recall"] for r in res),
                "episode_recall": mean(r["recall"] for r in res), "episode_precision": mean(r["precision"] for r in res),
                "mrr": mean(r["mrr"] for r in res), "joint": mean(r["joint"] for r in res), "leaks": sum(r["leak"] for r in res),
                "embedding_errors": sum(len(r["notes"]) for r in res), "cases": res}
    return out


def run(db: Path, caps: list[int], embedder) -> dict:
    manifest, cases = json.loads(MANIFEST.read_text()), json.loads(CASES.read_text())
    with tempfile.TemporaryDirectory() as td:
        copy = Path(td) / "eval_memory.db"
        shutil.copyfile(db, copy)
        store = MemoryStore(url=f"sqlite:///{copy}", conv=shared())
        try:
            return {"db": str(db), "embedder": getattr(embedder, "name", "none"), "extraction": eval_extraction(store, manifest),
                    "chains": eval_chains(store, cases["client_id"], cases["gold_chains"]), "retrieval": eval_retrieval(store, cases, caps, embedder)}
        finally:
            store.close()


def render(rep: dict) -> str:
    L = [f"Memory evaluation  db={rep['db']}  embedder={rep['embedder']}", ""]
    x = rep["extraction"]
    L.append(f"EXTRACTION ({x['interactions_evaluated']} ingested meetings)")
    for k, v in x["recall_by_kind"].items():
        L.append(f"  recall[{k:<10}] {v['hit']}/{v['total']}  = {v['recall']}")
    u = x["unsupported_commitments"]
    L.append(f"  unsupported commitments: {u['count']}/{u['of']} = {u['rate']}   (confirmed/correction memories in non-recap meetings with no authored decision)")
    L.append("")
    c = rep["chains"]
    L.append(f"SEMANTIC CHAINS  accuracy={c['accuracy']}")
    for t, v in c["topics"].items():
        L.append(f"  {'ok ' if v['match'] else 'BAD'} {t}: got {v['got']} want {v['gold']}")
    L.append("")
    L.append("RETRIEVAL   (mean over cases)")
    L.append(f"  {'config':<18}{'value':>7}{'hist':>7}{'ep-rec':>8}{'ep-prec':>9}{'mrr':>7}{'joint':>7}{'leaks':>7}")
    for name, m in rep["retrieval"].items():
        f = lambda v: "  -  " if v is None else f"{v:.3f}"
        L.append(f"  {name:<18}{f(m['value_acc']):>7}{f(m['history_recall']):>7}{f(m['episode_recall']):>8}{f(m['episode_precision']):>9}{f(m['mrr']):>7}{f(m['joint']):>7}{m['leaks']:>7}")
    return "\n".join(L)


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=SEED_DB)
    ap.add_argument("--caps", default="3,5,8", help="comma-separated episode caps to evaluate")
    ap.add_argument("--embedder", choices=["hash", "ollama", "openai"], default="hash")
    ap.add_argument("--json", type=Path, help="also write the full report as JSON")
    a = ap.parse_args(argv)
    embedder = emb.CachedEmbedder({"hash": emb.HashingEmbedder, "ollama": emb.OllamaEmbedder, "openai": emb.OpenAIEmbedder}[a.embedder]())
    rep = run(a.db, [int(c) for c in a.caps.split(",")], embedder)
    print(render(rep))
    if a.json:
        a.json.write_text(json.dumps(rep, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
