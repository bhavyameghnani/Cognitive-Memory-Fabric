"""Compare the two ingestion modes against the dataset generator's authored outcomes (the answer key).

    PYTHONPATH=src .venv/bin/python scripts/eval_llm_first.py [--client CLIENT-001] [--limit N] [--verifier-model gpt-4.1-mini] [--workers 6]

Nothing is written to any memory database: both pipelines run up to the validated Decision list and stop.  LLM responses are cached in
build/eval/llm_cache.json, so a re-run with the same prompts is free and deterministic.  Writes reports/llm_first_eval.json and .md.

Answer key: reports/generation_manifest.json "authored_outcomes" (what each scripted meeting was written to contain).  It lists OUTCOMES only
(decisions, plans, tentative options, deferrals, corrections, actions), not every fact worth remembering, so it measures recall and certainty
accuracy; candidates that match no outcome are reported and sampled for manual review, not counted as false positives.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import re
import sys
import threading
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import llm  # noqa: E402,F401  (loads .env)
from agents.memory import ingest as ing, llm_first, rules  # noqa: E402
from llm.client import OpenAILLM, make_llm  # noqa: E402
from services.conversations import Conversations, shared  # noqa: E402
from harness.memory_write_validator import MemoryWriteValidator  # noqa: E402
import copy  # noqa: E402

MANIFEST = ROOT / "reports" / "generation_manifest.json"
CACHE = ROOT / "build" / "eval" / "llm_cache.json"
OUT_JSON, OUT_MD = ROOT / "reports" / "llm_first_eval.json", ROOT / "reports" / "llm_first_eval.md"
SCORED = {"decision": {"confirmed"}, "plan": {"confirmed"}, "tentative": {"exploratory", "hypothetical"}, "correction": {"correction"}}
RECALL_KINDS = ("decision", "plan", "tentative", "correction", "deferred")
WEAK = {"exploratory", "hypothetical", "unconfirmed", "recommendation"}
STRONG = {"confirmed", "correction"}
MATCH_THRESHOLD = 0.5       # lexical fallback only (--judge none)
JUDGE_SYSTEM = """You grade a memory-extraction system. For ONE conversation you get the outcomes the conversation was written to contain (the answer key)
and a shuffled list of extracted memory statements from unnamed systems. For each outcome, list the ids of the statements that express THE SAME outcome
(the same decision, plan, option being weighed, deferral or correction; the certainty wording may differ, the content must not). When an outcome bundles
several parts, a statement that captures its central part counts (e.g. the retirement age of a retirement plan). Best match first.
An outcome with no matching statement gets an empty list. Output JSON only: {"matches": [{"outcome": 0, "statements": [3, 7]}, ...]}."""
STOP = set("the and for that with this you your are was were have has had not but from will would can could into about than then them they their our its it's "
           "his her him she who what when which while there here been being also only just more most some any all each other such very same own out off over under "
           "again further once both few nor too yes".split())


# ---------------------------------------------------------------------------------------------- cached LLM
class CachedLLM:
    """Wraps an LLM client; caches VALIDATED outputs by (model, agent, system, user).  Thread-safe."""
    _lock = threading.Lock()

    def __init__(self, inner, cache: dict):
        self.inner, self.cache, self.model = inner, cache, inner.model
        self.local = threading.local()
        self.stats = Counter()

    def _key(self, agent, system, user):
        return hashlib.sha256(json.dumps([self.model, agent, system, user]).encode()).hexdigest()

    def complete(self, agent, system, user, schema=None):
        k = self._key(agent, system, user)
        self.local.last = k
        with self._lock:
            hit = self.cache.get(k)
        if hit is not None:
            self.stats["cache_hit"] += 1
            return hit["out"]
        out = self.inner.complete(agent, system, user, schema)
        u = self.inner.pop_usage() if hasattr(self.inner, "pop_usage") else None
        with self._lock:
            self.cache[k] = dict(out=out, prompt_tokens=getattr(u, "prompt_tokens", 0), completion_tokens=getattr(u, "completion_tokens", 0), agent=agent)
            self.stats["calls"] += 1
        return out

    def reject_last(self):
        with self._lock:
            self.cache.pop(getattr(self.local, "last", None), None)


# ---------------------------------------------------------------------------------------------- matching
def stems(text: str) -> set[str]:
    words = {w[:5] for w in re.findall(r"[a-z]{3,}", text.lower().replace("'", "")) if w not in STOP}
    return words | set(rules.extract_numbers(text))


def match_score(gold: str, cand_text: str) -> float:
    g = stems(gold)
    return len(g & stems(cand_text)) / len(g) if g else 0.0


def cand_text(d) -> str:
    c = d.final
    return f"{c.statement} {c.excerpt} {c.value or ''} {c.topic.replace('_', ' ')}"


from pydantic import BaseModel, Field  # noqa: E402
from llm.client import generate_structured  # noqa: E402


class _Match(BaseModel):
    outcome: int
    statements: list[int] = Field(default_factory=list)


class _Judged(BaseModel):
    matches: list[_Match] = Field(default_factory=list)


def judge_meeting(judge, iid, golds, pools):
    """golds=[(kind,text)], pools={pipeline: [(decision_index, Decision)]} -> {pipeline: {gold_index: decision_index}}; blind to which pipeline wrote what."""
    items, seen = [], {}
    for name, kept in pools.items():
        for i, d in kept:
            key = d.final.statement.strip().lower()
            if key not in seen:
                seen[key] = len(items)
                items.append(d.final.statement)
    order = list(range(len(items)))
    random.Random(iid).shuffle(order)
    shown = {pos: idx for pos, idx in enumerate(order)}            # shown id -> item index
    user = "Outcomes:\n" + "\n".join(f"{k}. ({kind}) {t}" for k, (kind, t) in enumerate(golds)) + "\n\nStatements:\n" + \
           "\n".join(f"{pos}. {items[idx]}" for pos, idx in shown.items())
    res = generate_structured(judge, "judge", JUDGE_SYSTEM, user, _Judged, retries=2)
    best = {m.outcome: [shown[p] for p in m.statements if p in shown] for m in res.matches}
    out = {}
    for name, kept in pools.items():
        by_item = {seen[d.final.statement.strip().lower()]: i for i, d in kept}
        out[name] = {g: next((by_item[x] for x in best.get(g, []) if x in by_item), None) for g in range(len(golds))}
    return out


# ---------------------------------------------------------------------------------------------- pipelines
def run_rules(llm_c, conv, iid, label):
    ctx = ing.MeetingContext.load(conv, iid)
    sel = ing.select_salient_turns(ctx.rows)
    res, err = ing.extract_memory_candidates(llm_c, ctx, sel, label)
    cands = [(c, "conversation_llm") for c in res.candidates] + [(c, "conversation_rule") for c in ing.rule_candidates(ctx)]
    return ing.filter_memory_candidates(cands, ctx, conv, {t.turn_index for t in sel}), {}, err


def run_llm_first(llm_c, ver_c, conv, iid, label):
    ctx = ing.MeetingContext.load(conv, iid)
    return llm_first.run(llm_c, ver_c, ctx, conv, label)


def score(gold_by_iid, results, matches=None):
    """results[iid] = (decisions, votes, err); matches[iid] = {gold_index: decision_index|None} from the judge (None = lexical matching)."""
    m = Counter()
    per_kind = defaultdict(Counter)
    certainty_conf = Counter()
    bucket_acc = defaultdict(Counter)
    unmatched_samples, misses, overconf = [], [], []
    for iid, (decs, votes, err) in results.items():
        kept = [(i, d) for i, d in enumerate(decs) if d.kept]
        m["meetings"] += 1
        m["kept"] += len(kept)
        m["errors"] += bool(err)
        used = set()
        golds = [g for g in gold_by_iid.get(iid, []) if g[0] in RECALL_KINDS]
        for gi, (kind, text) in enumerate(golds):
            if matches is not None:
                di = matches[iid].get(gi)
                best = (1.0, di, decs[di]) if di is not None else (0, None, None)
            else:
                best = max(((match_score(text, cand_text(d)), i, d) for i, d in kept), default=(0, None, None), key=lambda x: x[0])
            per_kind[kind]["gold"] += 1
            if best[0] < MATCH_THRESHOLD:
                misses.append(dict(iid=iid, kind=kind, gold=text))
                continue
            per_kind[kind]["found"] += 1
            used.add(best[1])
            pred = best[2].final.certainty
            if kind in SCORED:
                ok = pred in SCORED[kind]
                per_kind[kind]["certainty_ok"] += ok
                certainty_conf[(kind, pred)] += 1
                if kind == "tentative" and pred in STRONG:
                    m["over_confident"] += 1
                    overconf.append(dict(iid=iid, gold=text, predicted=pred, statement=best[2].final.statement, excerpt=best[2].final.excerpt,
                                         votes=[n for n in best[2].notes if n.startswith("votes")]))
                if kind in ("decision", "plan") and pred in WEAK:
                    m["under_confident"] += 1
                v = votes.get(best[1])
                if v is not None:
                    bucket_acc[v.bucket]["n"] += 1
                    bucket_acc[v.bucket]["ok"] += ok
        for i, d in kept:
            if i not in used:
                m["unmatched"] += 1
                unmatched_samples.append(dict(iid=iid, certainty=d.final.certainty, topic=d.final.topic, statement=d.final.statement))
    return m, per_kind, certainty_conf, bucket_acc, unmatched_samples, misses, overconf


def summary(name, sc):
    m, pk, conf, ba, um, misses, oc = sc
    gold = sum(v["gold"] for v in pk.values())
    found = sum(v["found"] for v in pk.values())
    sgold = sum(pk[k]["found"] for k in SCORED)
    sok = sum(pk[k]["certainty_ok"] for k in SCORED)
    return dict(pipeline=name, meetings=m["meetings"], recall=round(found / gold, 3) if gold else None, found=found, gold=gold,
                certainty_accuracy=round(sok / sgold, 3) if sgold else None, over_confident=m["over_confident"], under_confident=m["under_confident"],
                kept_per_meeting=round(m["kept"] / max(1, m["meetings"]), 2), unmatched=m["unmatched"], llm_errors=m["errors"],
                recall_by_kind={k: f'{pk[k]["found"]}/{pk[k]["gold"]}' for k in RECALL_KINDS},
                certainty_by_kind={k: f'{pk[k]["certainty_ok"]}/{pk[k]["found"]}' for k in SCORED},
                confusion={f"{k}->{p}": n for (k, p), n in sorted(conf.items())},
                over_confident_cases=oc,
                bucket_accuracy={b: dict(n=v["n"], accuracy=round(v["ok"] / v["n"], 3) if v["n"] else None) for b, v in ba.items()})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--client")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--verifier-model", default="", help="override the verifier model ('same' = the extractor); default: llm_first.make_verifier_llm")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--conversations", help="alternative transcript CSV (e.g. build/eval/paraphrased_conversations.csv); default data/conversations.csv")
    ap.add_argument("--subset-of", help="only the conversations present in this CSV (to compare original wording with a paraphrased set)")
    ap.add_argument("--out", help="report path stem (default reports/llm_first_eval)")
    ap.add_argument("--manifest", help="answer key in the generation_manifest.json format (default reports/generation_manifest.json); e.g. data/simulation/sim_manifest.json")
    ap.add_argument("--judge", default="gpt-4.1", help="model that matches memories to the answer key; 'none' = lexical matching")
    a = ap.parse_args()

    conv = Conversations(Path(a.conversations)) if a.conversations else shared()
    global OUT_JSON, OUT_MD
    if a.out:
        OUT_JSON, OUT_MD = Path(a.out + ".json"), Path(a.out + ".md")
    man = json.loads((Path(a.manifest) if a.manifest else MANIFEST).read_text())
    gold = {i["interaction_id"]: [tuple(x) for x in i["authored_outcomes"]] for i in man["interactions"]}
    iids = [i for i in sorted(conv.meetings) if not a.client or conv.meetings[i][0]["client_id"] == a.client][: a.limit]
    if a.subset_of:
        with open(a.subset_of, newline="", encoding="utf-8") as f:
            keep = {r["interaction_id"] for r in csv.DictReader(f)}
        iids = [i for i in iids if i in keep]
    names = {cid: d.get("first_name") or cid for cid, d in conv.directory().items()}

    CACHE.parent.mkdir(parents=True, exist_ok=True)
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    base = make_llm()
    ext = CachedLLM(base, cache)
    ver = CachedLLM(llm_first.make_verifier_llm(base) if not a.verifier_model else (base if a.verifier_model == "same" else OpenAILLM(model=a.verifier_model)), cache)
    print(f"{len(iids)} conversations · extractor {ext.model} · verifier {ver.model}", flush=True)

    t0 = time.time()
    out = {"rules": {}, "llm_first": {}}         # + rules_raw / llm_first_raw: before the harness write validator

    def one(iid):
        label = names.get(conv.meetings[iid][0]["client_id"], "the client")
        return iid, run_rules(ext, conv, iid, label), run_llm_first(ext, ver, conv, iid, label)

    wv = MemoryWriteValidator(conv)

    def validated(res, iid):
        """The production write path: the harness MemoryWriteValidator re-checks every kept candidate (both pipelines)."""
        decs, votes, err = copy.deepcopy(res)
        ctx = ing.MeetingContext.load(conv, iid)
        for d in decs:
            if d.kept:
                wv.apply(d, wv.review(d, ctx))
        return decs, votes, err

    with ThreadPoolExecutor(a.workers) as pool:
        for n, (iid, r, l) in enumerate(pool.map(one, iids), 1):
            out["rules"][iid], out["llm_first"][iid] = validated(r, iid), validated(l, iid)
            out.setdefault("rules_raw", {})[iid], out.setdefault("llm_first_raw", {})[iid] = r, l
            if n % 10 == 0:
                print(f"  {n}/{len(iids)}", flush=True)
    CACHE.write_text(json.dumps(cache))

    toks = Counter()
    for v in cache.values():
        toks[v["agent"] + "_in"] += v.get("prompt_tokens", 0)
        toks[v["agent"] + "_out"] += v.get("completion_tokens", 0)
    matches = None
    if a.judge != "none":
        judge = CachedLLM(OpenAILLM(model=a.judge, max_tokens=800), cache)

        def jm(iid):
            golds = [g for g in gold.get(iid, []) if g[0] in RECALL_KINDS]
            pools = {k: [(i, d) for i, d in enumerate(out[k][iid][0]) if d.kept] for k in out}
            return iid, (judge_meeting(judge, iid, golds, pools) if golds else {k: {} for k in out})
        with ThreadPoolExecutor(a.workers) as pool:
            jr = dict(pool.map(jm, iids))
        CACHE.write_text(json.dumps(cache))
        matches = {k: {iid: jr[iid][k] for iid in iids} for k in out}
    sc = {k: score(gold, v, matches[k] if matches else None) for k, v in out.items()}
    report = dict(generated_at=time.strftime("%Y-%m-%d %H:%M"), conversations=len(iids), transcripts=str(a.conversations or "data/conversations.csv"), client=a.client, extractor_model=ext.model, verifier_model=ver.model,
                  match_threshold=MATCH_THRESHOLD, judge=a.judge, seconds=round(time.time() - t0, 1), llm_calls=dict(ext.stats + ver.stats), cached_tokens_all_runs=dict(toks),
                  results=[summary(k, s) for k, s in sc.items()])
    random.seed(7)
    report["samples"] = {k: dict(unmatched=random.sample(s[4], min(15, len(s[4]))), missed=random.sample(s[5], min(15, len(s[5])))) for k, s in sc.items()}
    OUT_JSON.write_text(json.dumps(report, indent=2))
    OUT_MD.write_text(render_md(report))
    for r in report["results"]:
        print(json.dumps({k: r[k] for k in ("pipeline", "recall", "certainty_accuracy", "over_confident", "under_confident", "kept_per_meeting", "unmatched", "llm_errors")}))
        print("   recall", r["recall_by_kind"], " certainty", r["certainty_by_kind"])
        if r["bucket_accuracy"]:
            print("   buckets", r["bucket_accuracy"])


def render_md(r: dict) -> str:
    L = [f"# LLM-first vs rules ingestion: evaluation", "",
         f"{r['conversations']} conversations{' of ' + r['client'] if r['client'] else ''} from `{r.get('transcripts')}` · extractor `{r['extractor_model']}` · verifier `{r['verifier_model']}` · generated {r['generated_at']}",
         "", "Answer key: the generator's authored outcomes (`reports/generation_manifest.json`). "
         + (f"Matching: a blind LLM judge (`{r['judge']}`, used by neither pipeline) sees the key and a shuffled, unlabelled mix of both pipelines' memories."
            if r.get("judge") != "none" else f"Matching is lexical (stemmed content words + numbers, threshold {r['match_threshold']}).")
         + " Identical for both pipelines. Unmatched memories are not counted as errors: the key lists outcomes, not every fact.", "",
         "| Metric | " + " | ".join(x["pipeline"] for x in r["results"]) + " |", "|---|" + "---|" * len(r["results"])]
    for k, label in (("recall", "Recall of outcomes"), ("certainty_accuracy", "Certainty accuracy (matched)"), ("over_confident", "Over-confident (tentative → confirmed)"),
                     ("under_confident", "Under-confident (decision → weak)"), ("kept_per_meeting", "Memories kept per conversation"), ("unmatched", "Kept but matching no outcome"),
                     ("llm_errors", "LLM failures")):
        L.append(f"| {label} | " + " | ".join(str(x[k]) for x in r["results"]) + " |")
    for k in RECALL_KINDS:
        L.append(f"| recall: {k} | " + " | ".join(x["recall_by_kind"][k] for x in r["results"]) + " |")
    for k in SCORED:
        L.append(f"| certainty: {k} | " + " | ".join(x["certainty_by_kind"][k] for x in r["results"]) + " |")
    lf = next(x for x in r["results"] if x["pipeline"] == "llm_first_raw")
    L += ["", "## Agreement buckets (llm_first): measured certainty accuracy", "", "| Bucket | n | accuracy |", "|---|---|---|"]
    L += [f"| {b} | {v['n']} | {v['accuracy']} |" for b, v in sorted(lf["bucket_accuracy"].items())]
    for name, s in ((k, v) for k, v in r["samples"].items() if not k.endswith("_raw")):
        L += ["", f"## {name}: sample of outcomes it missed", ""] + [f"- `{x['iid']}` {x['kind']}: {x['gold']}" for x in s["missed"]]
        L += ["", f"## {name}: sample of kept memories matching no outcome", ""] + [f"- `{x['iid']}` {x['certainty']} · {x['topic']}: {x['statement']}" for x in s["unmatched"]]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
