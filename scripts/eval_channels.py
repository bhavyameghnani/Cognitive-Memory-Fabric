"""LLM-first ingestion on the extra channels (generated meetings, email threads, chats) loaded by the multichannel adapter.

    PYTHONPATH=src .venv/bin/python scripts/eval_channels.py [--workers 6] [--judge gpt-4.1]

Nothing is written to a memory database.  Answer key (evaluation only, never read by ingestion):
  * datasets/evaluation/scenario_links.csv: what each generated record contributes to its client storyline ("storyline_note").  Scored: does a kept
    memory of that record capture the point?  (blind LLM judge, as in eval_llm_first.py)
  * datasets/evaluation/routine_records.csv: administrative records with no decision, correction, preference, figure or commitment.  Scored: memories
    kept from them (ideally none).
Writes reports/llm_first_channels_eval.json and .md.
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import random
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import llm  # noqa: E402,F401
from eval_llm_first import CACHE, CachedLLM, judge_meeting  # noqa: E402
from agents.memory import ingest as ing, llm_first  # noqa: E402
from harness.memory_write_validator import MemoryWriteValidator  # noqa: E402
from llm.client import OpenAILLM, make_llm  # noqa: E402
from services.conversations import MultiChannelConversations  # noqa: E402

EVAL = ROOT / "datasets" / "evaluation"
OUT_JSON, OUT_MD = ROOT / "reports" / "llm_first_channels_eval.json", ROOT / "reports" / "llm_first_channels_eval.md"


def rows(name):
    with open(EVAL / name, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--judge", default="gpt-4.1")
    a = ap.parse_args()
    conv = MultiChannelConversations()
    iids = sorted(i for i in conv.meetings if not i.startswith("INT-"))
    notes = defaultdict(list)
    for l in rows("scenario_links.csv"):
        if l["record_origin"] == "generated" and l["source_id"] in conv.meetings:
            notes[l["source_id"]].append(("storyline", l["storyline_note"]))
    routine = {r["record_id"] for r in rows("routine_records.csv")}
    names = {c: d.get("first_name") or c for c, d in conv.directory().items()}

    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    base = make_llm()
    ext, ver = CachedLLM(base, cache), CachedLLM(llm_first.make_verifier_llm(base), cache)
    wv = MemoryWriteValidator(conv)
    print(f"{len(iids)} records · {len(notes)} in scenarios · {len(routine & set(iids))} routine · extractor {ext.model} · verifier {ver.model}", flush=True)

    def one(iid):
        ctx = ing.MeetingContext.load(conv, iid)
        decs, votes, err = llm_first.run(ext, ver, ctx, conv, names[ctx.client_id])
        decs = copy.deepcopy(decs)
        for d in decs:
            if d.kept:
                wv.apply(d, wv.review(d, ctx))
        return iid, decs, err

    t0 = time.time()
    with ThreadPoolExecutor(a.workers) as pool:
        res = {iid: (decs, err) for iid, decs, err in pool.map(one, iids)}
    judge = CachedLLM(OpenAILLM(model=a.judge, max_tokens=800), cache)

    def jm(iid):
        kept = [(i, d) for i, d in enumerate(res[iid][0]) if d.kept]
        return iid, judge_meeting(judge, iid, notes[iid], {"llm_first": kept})["llm_first"]
    with ThreadPoolExecutor(a.workers) as pool:
        matched = dict(pool.map(jm, [i for i in iids if notes[i]]))
    CACHE.write_text(json.dumps(cache))

    by_ch = defaultdict(Counter)
    misses, found_samples, routine_kept, reasons = [], [], [], Counter()
    for iid in iids:
        decs, err = res[iid]
        ch = conv.meeting(iid)[0]["channel"]
        kept = [d for d in decs if d.kept]
        c = by_ch[ch]
        c["records"] += 1
        c["kept"] += len(kept)
        c["errors"] += bool(err)
        for d in decs:
            if not d.kept:
                reasons[d.reason.split(":")[0][:60]] += 1
        if iid in routine:
            c["routine_records"] += 1
            c["routine_kept"] += len(kept)
            routine_kept += [dict(iid=iid, certainty=d.final.certainty, statement=d.final.statement) for d in kept]
        for gi, (_, note) in enumerate(notes[iid]):
            c["storyline_points"] += 1
            di = matched[iid].get(gi)
            if di is None:
                misses.append(dict(iid=iid, channel=ch, note=note))
            else:
                c["storyline_found"] += 1
                d = decs[di]
                found_samples.append(dict(iid=iid, channel=ch, note=note, certainty=d.final.certainty, statement=d.final.statement,
                                          evidence=f"{iid} msg {d.final.turn_index}"))
    tot = sum(by_ch.values(), Counter())
    random.seed(3)
    report = dict(generated_at=time.strftime("%Y-%m-%d %H:%M"), records=len(iids), extractor=ext.model, verifier=ver.model, judge=a.judge, seconds=round(time.time() - t0, 1),
                  storyline_recall=round(tot["storyline_found"] / tot["storyline_points"], 3) if tot["storyline_points"] else None,
                  routine_memories_per_record=round(tot["routine_kept"] / max(1, tot["routine_records"]), 2),
                  by_channel={k: dict(v) for k, v in sorted(by_ch.items())}, rejection_reasons=dict(reasons.most_common()),
                  misses=misses, found=random.sample(found_samples, min(20, len(found_samples))), routine_kept=routine_kept)
    OUT_JSON.write_text(json.dumps(report, indent=2))
    OUT_MD.write_text(render(report))
    print(json.dumps({k: report[k] for k in ("storyline_recall", "routine_memories_per_record", "by_channel", "rejection_reasons")}, indent=1))


def render(r):
    L = ["# LLM-first on email, chat and generated meetings", "",
         f"{r['records']} records · extractor `{r['extractor']}` · verifier `{r['verifier']}` · judge `{r['judge']}` · {r['generated_at']}", "",
         "Answer key (evaluation only): `datasets/evaluation/scenario_links.csv` storyline notes for generated records, and the routine records that contain "
         "no decision. All numbers after the harness write validator.", "",
         f"- **Storyline points captured:** {r['storyline_recall']}",
         f"- **Memories kept per routine record** (ideally 0): {r['routine_memories_per_record']}", "",
         "| Channel | Records | Memories kept | Storyline points found | Routine records | Memories from routine |", "|---|---|---|---|---|---|"]
    for ch, c in r["by_channel"].items():
        L.append(f"| {ch} | {c.get('records', 0)} | {c.get('kept', 0)} | {c.get('storyline_found', 0)}/{c.get('storyline_points', 0)} | {c.get('routine_records', 0)} | {c.get('routine_kept', 0)} |")
    L += ["", "## Storyline points missed", ""] + [f"- `{m['iid']}` ({m['channel']}): {m['note']}" for m in r["misses"]]
    L += ["", "## Sample of storyline points found", ""] + [f"- `{f['iid']}` {f['certainty']}: {f['statement']}  \n  key: {f['note']}" for f in r["found"]]
    L += ["", "## Memories kept from routine records", ""] + [f"- `{x['iid']}` {x['certainty']}: {x['statement']}" for x in r["routine_kept"]]
    return "\n".join(L) + "\n"


if __name__ == "__main__":
    main()
