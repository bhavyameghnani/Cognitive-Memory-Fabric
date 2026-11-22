"""Robustness test set: rewrite a sample of meetings into natural, varied wording (same turns, speakers, facts, figures and decision status;
none of the dataset's stock recap phrases).  Evaluation-only: written to build/eval/, never to data/ and never ingested into memory.

    PYTHONPATH=src .venv/bin/python scripts/make_paraphrased_set.py [--n 60] [--model gpt-4.1]
    PYTHONPATH=src .venv/bin/python scripts/eval_llm_first.py --conversations build/eval/paraphrased_conversations.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import llm  # noqa: E402,F401
from pydantic import BaseModel  # noqa: E402
from eval_llm_first import CACHE, MANIFEST, CachedLLM  # noqa: E402
from llm.client import OpenAILLM, generate_structured  # noqa: E402
from services.conversations import CSV_PATH, shared  # noqa: E402

OUT = ROOT / "build" / "eval" / "paraphrased_conversations.csv"
SYSTEM = """You rewrite one advisor-client conversation so it sounds like different real people talking naturally. Rewrite EVERY turn.
Keep exactly: the same turns in the same order and the same speaker for each turn; every fact, figure, amount, age, date and name; and the status of every
item: what was decided stays decided, what was only being considered stays only considered (keep the hedging), what was corrected stays a correction,
what was deferred stays deferred. Do not add or remove information.
Change: the wording and sentence structure. Never use these stock phrases or close variants: "you've decided", "we've decided", "still weighing",
"an idea and not an instruction", "for the record", "here's where we landed", "let me read it back", "I've corrected my own record", "the working plan
I'm recording is", "was too strong". Express the same meaning in your own varied, conversational words (people say "yeah, let's go with that",
"I'm kind of torn", "scratch what I wrote last time", and so on). Keep each turn about the same length.
Output JSON only: {"turns": [{"turn_index": 1, "utterance": "..."}, ...]}."""


class _Turn(BaseModel):
    turn_index: int
    utterance: str


class _Rewrite(BaseModel):
    turns: list[_Turn]


def pick(n: int, seed: int = 11) -> list[str]:
    man = json.loads(MANIFEST.read_text())["interactions"]
    kinds = {i["interaction_id"]: {k for k, _ in i["authored_outcomes"]} for i in man}
    must = sorted(i for i, k in kinds.items() if k & {"correction", "deferred"})
    rest = sorted(i for i, k in kinds.items() if i not in must and k & {"decision", "plan", "tentative"})
    random.Random(seed).shuffle(rest)
    return sorted((must + rest)[:n])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=60)
    ap.add_argument("--model", default="gpt-4.1")
    a = ap.parse_args()
    conv = shared()
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    rw = CachedLLM(OpenAILLM(model=a.model, max_tokens=6000, temperature=0.7), cache)
    iids = pick(a.n)

    def one(iid):
        rows = conv.meeting(iid)
        user = f"Context: {rows[0]['meeting_context']}\n\n" + "\n".join(f"[turn {r['turn_index']} | {r['speaker']}] {r['utterance']}" for r in rows)
        want = [r["turn_index"] for r in rows]

        def check(res: _Rewrite):
            got = [t.turn_index for t in res.turns]
            if got != want:
                raise ValueError(f"return exactly turns {want} in order")
        res = generate_structured(rw, "paraphrase", SYSTEM, user, _Rewrite, retries=2, validator=check)
        new = {t.turn_index: t.utterance.strip() for t in res.turns}
        return [dict(r, turn_index=r["turn_index"], utterance=new[r["turn_index"]]) for r in rows]

    with ThreadPoolExecutor(6) as pool:
        out = [row for rows in pool.map(one, iids) for row in rows]
    CACHE.write_text(json.dumps(cache))
    with open(CSV_PATH, newline="", encoding="utf-8") as f:
        cols = csv.DictReader(f).fieldnames
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(out)
    print(f"{len(iids)} conversations, {len(out)} turns -> {OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
