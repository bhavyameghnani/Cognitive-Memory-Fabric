"""Generate NEW synthetic client interactions that continue the six clients' storylines after the existing data (which ends 2026-06-30).

    PYTHONPATH=src .venv/bin/python scripts/generate_synthetic_conversations.py --per-client 4 --max-usd 10
    PYTHONPATH=src .venv/bin/python scripts/generate_synthetic_conversations.py --client CLIENT-001 --per-client 2 --dry-run

Output (never under data/conversations.csv or datasets/, which are hash-locked inputs of the test suite):
    data/simulation/sim_conversations.csv   same columns as data/conversations.csv; ids SIM-001..; source_type "synthetic_simulation"
    data/simulation/sim_manifest.json       the AUTHORED answer key per interaction, in the format of reports/generation_manifest.json
                                            (so scripts/eval_llm_first.py --manifest can score extraction on it), plus generation metadata
    data/simulation/generation_log.jsonl    one line per model call: model, tokens, cost estimate, prompt hash, validation result

How it works.  For each client, the model sees: the client and advisor names, the family / third parties from datasets/character_registry.csv, and
the most recent interactions (meeting context + full transcript).  It writes ONE new interaction at a time, in date order, with an explicit list
of the outcomes it planted (decision, plan, tentative, correction, deferred, c_action, a_action) and the turns that carry each.  Each new
interaction is added to the context of the next one, so the storyline stays continuous.  Wording rules ask for natural speech, not the stock recap
phrases of the scripted set, so the new data is a harder, more realistic test (see docs/llm_first_ingestion.md, "natural wording").

Validation is code, not the model: turn order, speakers, non-empty text, outcome kinds, cited turns that exist, a date after the previous one,
no other client's name.  A response that fails validation is retried with the error (at most --retries times) and otherwise dropped.

Cost control: prices per million tokens are listed in PRICES; the script stops BEFORE a call whose worst case (prompt + max output tokens) would
exceed --max-usd, and it prints the running total.  The key is read only from the environment (OPENAI_API_KEY), never written anywhere.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
import llm  # noqa: E402,F401  (loads .env)
from llm.client import OpenAILLM, generate_structured  # noqa: E402
from services.conversations import CSV_PATH, Conversations, channel_rows  # noqa: E402

OUT_DIR = ROOT / "data" / "simulation"
OUT_CSV, OUT_MANIFEST, OUT_LOG = OUT_DIR / "sim_conversations.csv", OUT_DIR / "sim_manifest.json", OUT_DIR / "generation_log.jsonl"
COLUMNS = ["interaction_id", "client_id", "advisor_id", "interaction_datetime", "channel", "turn_index", "speaker", "utterance", "meeting_context", "source_type", "consent_status"]
REGISTRY = ROOT / "datasets" / "character_registry.csv"
START = datetime(2026, 7, 1, 9, 0)
# USD per 1M tokens (input, output).  Check https://openai.com/api/pricing before a large run; the cap uses these numbers.
PRICES = {"gpt-4.1": (2.00, 8.00), "gpt-4.1-mini": (0.40, 1.60), "gpt-4o-mini": (0.15, 0.60), "gpt-4o": (2.50, 10.00)}
KINDS = ("decision", "plan", "tentative", "correction", "deferred", "c_action", "a_action")
CHANNELS = ("video", "telephone", "in-person", "email", "chat")


class Turn(BaseModel):
    speaker: Literal["Advisor", "Client"]
    text: str = Field(..., min_length=1)


class Outcome(BaseModel):
    kind: Literal["decision", "plan", "tentative", "correction", "deferred", "c_action", "a_action"]
    text: str = Field(..., min_length=5, description="the outcome in plain words, with its figures")
    turns: list[int] = Field(default_factory=list, description="1-based indices of the turns that carry it")


class Interaction(BaseModel):
    channel: Literal["video", "telephone", "in-person", "email", "chat"]
    days_after_previous: int = Field(..., ge=3, le=40)
    meeting_context: str = Field(..., min_length=3, max_length=120, description="a short agenda line, like the existing data")
    turns: list[Turn] = Field(..., min_length=6, max_length=24)
    outcomes: list[Outcome] = Field(..., min_length=1, max_length=5)
    scenario_tags: list[str] = Field(default_factory=list)


SYSTEM = """You write realistic, fully synthetic transcripts of conversations between a financial advisor and a private client, for research on
long-term memory in AI assistants.  Every person, company and figure is fictional.

Write ONE new interaction that continues the client's story after the recent interactions you are shown.  Rules:
- Keep everything consistent with the history (names, family, figures, open items), unless the client deliberately changes a position.
- Speech must sound natural: no stock phrases such as "You've decided...", "the working plan I'm recording is", "I've corrected my own record".
  People hedge, interrupt themselves, answer briefly, and sometimes confirm or reject an advisor's summary in their own words.
- Plant 1-4 outcomes and list each one in `outcomes` with its kind:
  decision (the CLIENT commits), plan (an agreed working assumption), tentative (an option being explored, NOT decided),
  correction (an earlier statement or record is corrected), deferred (a choice explicitly postponed),
  c_action / a_action (a task for the client / the advisor).
  Include at least one case that is easy to get wrong: an advisor's recap the client only half accepts, a hedge, a reversal, or a correction.
- `turns` of an outcome are the 1-based positions of the turns that state it.
- For channel email or chat, each turn is one message, shorter and more informal for chat.
- Third parties (accountants, family) may be mentioned but never speak; only Advisor and Client speak.
- Do not mention any other client of the firm."""


class MeteredLLM(OpenAILLM):
    """OpenAILLM that adds up the tokens of EVERY call, including retries whose output failed validation (all of them are billed)."""
    tokens_in: int = 0
    tokens_out: int = 0

    def complete(self, agent, system, user, schema=None):
        try:
            return super().complete(agent, system, user, schema)
        finally:
            u = self.pop_usage()
            if u is not None:
                self.tokens_in += u.input_tokens
                self.tokens_out += u.output_tokens


def price(model: str, pin: int, pout: int) -> float:
    i, o = PRICES.get(model, PRICES["gpt-4.1"])
    return (pin * i + pout * o) / 1_000_000


def registry() -> dict:
    with open(REGISTRY, newline="", encoding="utf-8") as f:
        return {r["character_id"]: r for r in csv.DictReader(f)}


def people_of(reg: dict, client_id: str) -> list[str]:
    return [f"{r['display_name']} ({r['role']})" for k, r in reg.items() if r["client_scope"] == client_id and not k.startswith(("CLIENT-", "ADVISOR-"))]


def history(conv: Conversations, client_id: str, extra: list[list[dict]], n: int, chars: int) -> str:
    """The n most recent interactions of the client (existing + already generated), as compact transcripts."""
    metas = [conv.meeting(i) for i in conv.interactions_for(client_id)] + extra
    metas = sorted(metas, key=lambda rows: rows[0]["interaction_datetime"])[-n:]
    out = []
    for rows in metas:
        r0 = rows[0]
        body = "\n".join(f"  {r['speaker']}: {r['utterance']}" for r in rows)
        out.append(f"[{r0['interaction_datetime'][:10]} · {r0['channel']} · {r0['meeting_context']}]\n{body[:chars]}")
    return "\n\n".join(out)


def validate(it: Interaction, client_id: str, others: list[str]) -> None:
    n = len(it.turns)
    if it.turns[0].speaker != "Advisor" and it.channel not in ("email", "chat"):
        raise ValueError("a meeting or call opens with the advisor")
    if not any(t.speaker == "Client" for t in it.turns):
        raise ValueError("the client never speaks")
    for o in it.outcomes:
        if not o.turns or any(not (1 <= t <= n) for t in o.turns):
            raise ValueError(f"outcome {o.text[:40]!r} cites turns {o.turns}; valid turns are 1..{n}")
    text = " ".join(t.text for t in it.turns)
    for name in others:
        if re.search(rf"\b{re.escape(name)}\b", text):
            raise ValueError(f"the transcript mentions another client of the firm ({name})")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--client", action="append", help="CLIENT-00N (repeatable); default: all six")
    ap.add_argument("--per-client", type=int, default=4)
    ap.add_argument("--model", default="gpt-4.1")
    ap.add_argument("--max-usd", type=float, default=10.0, help="hard spending cap for this run")
    ap.add_argument("--history", type=int, default=4, help="recent interactions shown as context")
    ap.add_argument("--retries", type=int, default=2)
    ap.add_argument("--temperature", type=float, default=0.8)
    ap.add_argument("--dry-run", action="store_true", help="print the first prompt and the cost cap, call nothing")
    a = ap.parse_args()

    conv = Conversations(CSV_PATH)
    conv.rows += channel_rows()                      # email / chat / generated meetings are part of the story too
    conv._index()
    reg = registry()
    clients = a.client or sorted(conv.directory())
    names = {cid: reg[cid]["first_name"] for cid in conv.directory() if cid in reg}

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    existing = list(csv.DictReader(open(OUT_CSV, newline="", encoding="utf-8"))) if OUT_CSV.exists() else []
    manifest = json.loads(OUT_MANIFEST.read_text()) if OUT_MANIFEST.exists() else {"generator": {}, "interactions": []}
    next_no = 1 + max([int(r["interaction_id"].split("-")[1]) for r in existing] or [0])
    gen = MeteredLLM(model=a.model, temperature=a.temperature, max_tokens=6000)
    max_out = 6000
    spent, made = 0.0, 0

    for cid in clients:
        reg_c = reg[cid]
        advisor_id = conv.directory()[cid]["advisor_id"]
        advisor = reg[advisor_id]["display_name"]
        mine = [r for r in existing if r["client_id"] == cid]
        extra = [sorted([r for r in mine if r["interaction_id"] == i], key=lambda r: int(r["turn_index"])) for i in dict.fromkeys(r["interaction_id"] for r in mine)]
        for x in extra:
            for r in x:
                r["turn_index"] = int(r["turn_index"])
        last = max([r["interaction_datetime"] for r in conv.rows if r["client_id"] == cid] + [r["interaction_datetime"] for r in mine])
        last_dt = max(datetime.fromisoformat(last[:19]), START - timedelta(days=3))
        others = [n for c, n in names.items() if c != cid and n]
        for k in range(a.per_client):
            user = (f"Client: {reg_c['display_name']} ({cid}).  Advisor: {advisor} ({advisor_id}).\n"
                    f"People in the client's life (may be mentioned, never speak): {', '.join(people_of(reg, cid)) or 'none recorded'}.\n"
                    f"The previous interaction was on {last_dt:%Y-%m-%d}.  Interaction {k + 1} of {a.per_client} in this batch; vary the channel.\n\n"
                    f"RECENT INTERACTIONS (oldest first):\n{history(conv, cid, extra, a.history, 3500)}")
            worst = price(a.model, len(SYSTEM + user) // 3 + 800, max_out)
            if a.dry_run:
                print(user[:3000], f"\n\n[dry run] worst case per call ≈ ${worst:.3f}; cap ${a.max_usd:.2f}")
                return 0
            if spent + worst > a.max_usd:
                print(f"stopping: the next call could exceed the cap (spent ${spent:.2f}, cap ${a.max_usd:.2f})")
                break
            t0 = time.time()
            before = (gen.tokens_in, gen.tokens_out)
            try:
                it = generate_structured(gen, "simulation_generator", SYSTEM, user, Interaction, retries=a.retries, validator=lambda x: validate(x, cid, others))
                ok, err = True, ""
            except Exception as e:                       # validation kept failing, or the API refused: log it, the spend is still counted
                it, ok, err = None, False, f"{type(e).__name__}: {str(e)[:300]}"
            usage = (gen.tokens_in - before[0], gen.tokens_out - before[1])
            cost = price(a.model, *usage)
            spent += cost
            with open(OUT_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(dict(at=datetime.now().isoformat(timespec="seconds"), client_id=cid, model=a.model, prompt_tokens=usage[0], completion_tokens=usage[1],
                                        usd=round(cost, 4), prompt_sha256=hashlib.sha256(user.encode()).hexdigest()[:16], ok=ok, error=err, seconds=round(time.time() - t0, 1))) + "\n")
            if not ok:
                print(f"{cid}: dropped ({err[:120]})  total ${spent:.2f}")
                continue
            iid = f"SIM-{next_no:03d}"
            next_no += 1
            last_dt = last_dt + timedelta(days=it.days_after_previous)
            ts = last_dt.replace(hour=9 + (next_no % 8), minute=0).isoformat(timespec="seconds")
            rows = [dict(interaction_id=iid, client_id=cid, advisor_id=advisor_id, interaction_datetime=ts, channel=it.channel, turn_index=i, speaker=t.speaker,
                         utterance=" ".join(t.text.split()), meeting_context=it.meeting_context, source_type="synthetic_simulation", consent_status="synthetic")
                    for i, t in enumerate(it.turns, 1)]
            existing += rows
            extra.append([dict(r) for r in rows])
            manifest["interactions"].append(dict(interaction_id=iid, client_id=cid, step=len(extra), scenario_tags=it.scenario_tags,
                                                 authored_outcomes=[[o.kind, o.text] for o in it.outcomes],
                                                 outcome_turns={str(j): o.turns for j, o in enumerate(it.outcomes)}))
            made += 1
            print(f"{iid} {cid} {ts[:10]} {it.channel:9s} {len(rows):2d} turns, {len(it.outcomes)} outcomes  (${cost:.3f}, total ${spent:.2f})", flush=True)
            # write after every interaction: an interrupted run keeps what it paid for
            with open(OUT_CSV, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, fieldnames=COLUMNS, lineterminator="\n")
                w.writeheader()
                w.writerows({k: r[k] for k in COLUMNS} for r in existing)
            manifest["generator"] = dict(script="scripts/generate_synthetic_conversations.py", model=a.model, temperature=a.temperature,
                                         note="fully synthetic; outcomes are what the generator was asked to plant and listed itself, checked only structurally")
            OUT_MANIFEST.write_text(json.dumps(manifest, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"done: {made} interactions, ${spent:.2f} spent (cap ${a.max_usd:.2f}) -> {OUT_CSV.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
