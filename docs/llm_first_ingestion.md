# LLM-first ingestion and multichannel memory

Two additions to memory ingestion. LLM-first is the default ingestion mode; the rules pipeline is kept as a baseline (`CMF_INGEST_MODE=rules`). The extra channels are opt-in.

| Setting | Values | What it does |
|---|---|---|
| `CMF_INGEST_MODE` | `llm_first` (default), `rules` | `llm_first`: the LLM reads the whole conversation. Code checks facts. A second, blind LLM votes on certainty. |
| `CMF_VERIFIER_MODEL` | default `gpt-4.1-mini`; `same` | Verifier for `llm_first` (OpenAI backend only). Using a different model than the extractor measured +5 points certainty accuracy. |
| `CMF_MEMORY_CHANNELS` | empty (default), `all`, or `meetings,email,chat` | Also ingest the generated follow-up meetings, email threads and chats from `datasets/`. |

Code: `src/agents/memory/llm_first.py`, `src/services/conversations.py` (adapter) and `src/agents/memory/consolidate.py` (fallback). Tests: `tests/test_llm_first.py`, `tests/test_memory_channels.py`. Evaluation: `scripts/eval_llm_first.py`, `scripts/eval_channels.py`, `scripts/make_paraphrased_set.py`.

## LLM-first pipeline

Everything after the candidate list is the existing code: the harness `MemoryWriteValidator`, persistence through the integrity boundary, consolidation (still the only writer of semantic memory) and the audit trail.

| Stage | Who | What it does |
|---|---|---|
| ① Extract | LLM (`CMF_OPENAI_MODEL`, default gpt-4o-mini) | Sees every turn or message. No regex pre-selection. Proposes `MemoryCandidate`s (same schema as the rules path). |
| ② Fact check | Code | Turn exists. Excerpt is really in it (near-misses repaired). Speaker comes from the data. Value appears in the excerpt. A third party's message can never be cited. Never judges certainty. |
| ② Re-anchor | Code | If the cited sentence is a bare "Yes, let's do that", primary evidence moves to the earlier sentence that states the content. The reply is kept as `supporting` evidence. |
| ③ Verify | LLM (`CMF_VERIFIER_MODEL`) | Labels each candidate's certainty **blind** (never sees the extractor's label). Also says whether the statement is supported and worth remembering. |
| ④ Combine | Code | Final certainty = the **lower** of extractor and verifier (downgrade only). A regex **hedge** ("maybe", "leaning", "what if") vetoes both. Unsupported or unworthy → rejected. |
| ⑤ Value | LLM (verifier) | Every confirmed number that could become a current value must be the **client's own figure**: a decision, an agreed plan or working assumption, a completed action, or a fact about their situation, whoever says it. An unaccepted recommendation, the advisor reading out balances or spending from a system, a what-if, or a corrected-away value is kept as unconfirmed. The verifier also names the **kind of quantity** (age, months, percent, one-off dollars, dollars per month…). That becomes part of the slot, so dollars a month can never replace a number of days. Retirement age must be an age, and equity allocation a percent. If the check can't run, it fails safe to unconfirmed. |
| ⑥ Slot | LLM (verifier) | Before a confirmed value enters a topic that already has current values of the same kind, the verifier says which one (if any) it is about. A match reuses that subject; otherwise the memory keeps its own. This stops both an unrelated value overwriting a current one and one fact being split across subjects. |

Each memory stores a `confidence` equal to the measured certainty accuracy of its agreement bucket. Its audit note records every vote, for example `votes: extractor=confirmed verifier=confirmed regex=confirmed -> confirmed (agree_all, confidence 0.96)`. Disagreements are flagged `needs review`.

| Agreement bucket | n | Certainty correct | Stored confidence |
|---|---|---|---|
| extractor, verifier and regex agree | 101 | 96% | 0.96 |
| the two LLMs agree, regex differs | 44 | 82% | 0.82 |
| verifier higher (extractor's lower label kept) | 7 | 86% | 0.86 |
| verifier lower (lower label kept) | 5 | 20% | 0.2 |
| regex hedge veto | 3 | 0% | 0.3 (safety prior) |

The disagreement buckets are small samples, and in them the downgraded label was usually the wrong one. Downgrade-only is kept on purpose because over-confidence is the costlier error. Those memories get low confidence and a review flag instead. Later runs (`reports/llm_first_eval*.md`) agree within noise: agree_all 94–97%, agree_llms 85–96%.

## Multichannel adapter

`MultiChannelConversations` loads `data/conversations.csv` **plus** the generated meetings (`MTG-`), email threads (`THR-`) and chats (`CHT-`). It presents them through the unchanged `Conversations` interface: one interaction per meeting, thread or chat, and one turn per message. The integrity boundary, the harness write validator and the evidence checks therefore verify them with their existing code.

- **Dates:** A memory's observation time is the cited message's own timestamp. A thread can span weeks, so it is set per row; persistence now takes it from the cited turn, which is identical for every turn of a meeting.
- **Third parties:** A third-party sender (e.g. the client's CPA) appears as context with speaker `Third party` and can never be cited. Both pipelines enforce this.
- **Restricted records:** Records with `SYN-ACC-RESTRICTED` (agent access: none) in `datasets/governance/source_control.csv` are not loaded.
- **Excluded inputs:** The evaluation answer key and the CRM layer are never read.

**Two consolidation fixes (apply to both modes).**
- **Fallback creator.** A version's creator is the earliest confirmed episode with that value. If the write validator rejects that creator (e.g. a reported preference, "She'd be happier with 63"), the next confirmed episode with the same value now creates the version, valid from its own date. Each fallback creator must also pass the validator. Before this fix, one early wrongly-labelled episode blocked a later real decision.
- **No version churn after out-of-order evidence.** An email thread can deliver an earlier-dated fact after a later one, so the chain is rebuilt. Consolidation used to compare its target chain against *all* stored versions, including the superseded ones from before the rebuild. It therefore "rebuilt" again on every later run: one client had the same two values recreated about 29 times. It now compares against the live chain (from the current version back while dates decrease), so a re-run changes nothing.

## Results

All numbers are on the **production path** (after the harness write validator). Matching against the answer key is done by a blind LLM judge (`gpt-4.1`, used by neither pipeline). It sees the key and a shuffled, unlabelled mix of both pipelines' memories.

**Meetings and calls.** Answer key: the generator's authored outcomes (`reports/generation_manifest.json`).

| | Rules | LLM-first |
|---|---|---|
| All 150, original wording: recall / certainty | **91.1%** / 85.1% | 84.4% / **89.4%** |
| Same 60, original wording | **85.1%** / 75.9% | 78.4% / **79.2%** |
| Same 60, **natural wording** | 66.2% / 42.5% | **74.3% / 79.2%** |
| Over-confident (tentative stored as confirmed), each set | 0 | 0 |

The value check (⑤) trades a few certainty points for safer current values. Before it, the same sets scored 90.4%, 85.4% and 83.3%. The losses are mostly plans an advisor records as a "working assumption", which the verifier sometimes reads as not yet agreed. What it blocks is visible in the profile: holdings and spending read-outs, unaccepted recommendations, and values of a different kind replacing each other (see "All clients" below).

"Natural wording" means the same 60 meetings rewritten by `gpt-4.1` (`scripts/make_paraphrased_set.py`, output in `build/eval/`, never in `data/`). Every fact, figure and decision status is kept, but the dataset's stock recap phrases ("You've decided…", "still weighing… an idea and not an instruction", "I've corrected my own record") are gone. On that set the rules pipeline marks 15 decisions as weaker than they were. Its accuracy depends on the scripted phrasing.

Before the write validator, LLM-first recall is 94.1%, 89.2% and 85.1% on the three sets. See the first limitation below for the gap.

**Email, chat and generated meetings** (`reports/llm_first_channels_eval.md`). Answer key: storyline notes in `datasets/evaluation/scenario_links.csv`, plus the 12 routine records that contain no decision.

| Channel | Storyline points captured | Memories invented from routine records |
|---|---|---|
| Email (21 threads) | 13 / 15 | 0 |
| Generated meetings (11) | 7 / 12 | n/a |
| Chat (27) | 5 / 21 | 0 |

About half the chat points are deliberate non-decisions ("late-night musing; nothing due", "refuses to decide by text"). A manual read found no case where a musing was stored as a decision.

### All clients, end to end

Production CLI, all channels, `build/cmf_memory_llm_first_v3.db`: 209 conversations (6 clients), all completed, about 16 minutes. Each client's current values were reviewed by hand against the authored decisions.

- **Right:**
  - Retirement ages: Kenji 63 (from 65), Grace 63.
  - Equity allocations: Kenji 75% → 60%, Sofia 90% → 75%, Grace 25%, Peg 35% → 25% → 30%.
  - Lucas's 9-month cash reserve; Rebecca's $600k care reserve, kept separate from the $6k/month care cost.
  - Yuki's $30k/yr and Ren's $14k semester; Peg's $5,200/month aide and "5 days" kept separate; Peg's $6k church gift.
- **Still wrong (6 of about 50 current values):**
  - Kenji's history shows 60 from Feb 10 to Mar 5. The advisor's email overstated an exploration; both LLMs believed it. The current value, 63, is right.
  - Rebecca's $14,500 spending and Peg's $210k cash are advisor read-outs that the value check missed.
  - Rebecca's "cash account" is a payment amount.
  - Sofia's "2 years" is the value a correction says was wrong, and her purchase price is replaced by the $40k house fund.
  - Grace's "up to fifteen" gift is stored as 15, not 15,000.
- **Re-runs:** re-running consolidation creates nothing (no version churn).

## Known limitations

- **The write validator costs LLM-first recall.** Its support check is word overlap between the statement and **one** quoted sentence. LLM-first reasons across turns ("Does she have any scholarships?" / "A small one."), so some correct memories are rejected. This drops recall by about 7–10 points and is why rules has higher recall on the scripted wording. The validator was not loosened. Suggested follow-up: check the statement against all cited evidence rows (content turn plus confirming reply), keeping every other rule.
- **Variation between runs.** gpt-4o-mini's phrasing varies between runs and prompt versions even at temperature 0, so production recall has ranged between 84% and 90% on the 150 meetings.
- **Pre-existing gap.** `INT-121` is marked `SYN-ACC-RESTRICTED`, but it is in the frozen `data/conversations.csv`, which both pipelines already ingest. The adapter only filters the new channels.
- **Residual current-value errors.** About 1 in 8 current values is still wrong (listed above). Two cheap next steps: let the regex read-out templates ("I'm seeing about $…") veto a current value, as the hedge veto does; and normalise "fifteen" to 15,000 when the slot already holds thousands.
- **Weak acceptance is read as agreement.** The advisor says "I'd want closer to 15 months"; the client replies "Okay, that's consistent with my own numbers". LLM-first stores that as confirmed; rules stores a recommendation.
- **Synthetic data, outcome-only key.** Precision is not measured beyond the routine-record check. Re-measure the confidence table on real, anonymised conversations before relying on it.
- **Cost.** About 2 LLM calls per conversation, plus one value check per confirmed number and occasional slot checks.

## Try it in the UI

A pre-built memory for all six clients and all channels ships as `data/seed/cmf_memory_llm_first.db`, so no LLM calls are needed to browse it.

```bash
cp .env.development.example .env                                  # then put your OPENAI_API_KEY in .env (only needed for "Ask CMF")
cp data/seed/cmf_memory_llm_first.db build/cmf_memory_llm_first.db  # working copy: the app writes to it, the seed stays clean
PYTHONPATH=src .venv/bin/python -m api.main                        # terminal 1
cd ui/cmf-brain-ui && npm run dev                                  # terminal 2 -> http://localhost:5173
```

`CMF_MEMORY_CHANNELS=all` must be set wherever this database is used. Its memories cite email, chat and meeting records that only resolve with the multichannel adapter.

## Reproduce

```bash
PYTHONPATH=src .venv/bin/python scripts/eval_llm_first.py                       # 150 meetings, both pipelines
PYTHONPATH=src .venv/bin/python scripts/make_paraphrased_set.py                 # natural-wording set (60)
PYTHONPATH=src .venv/bin/python scripts/eval_llm_first.py --conversations build/eval/paraphrased_conversations.csv --out reports/llm_first_eval_paraphrased
PYTHONPATH=src .venv/bin/python scripts/eval_channels.py                        # email, chat, generated meetings
CMF_INGEST_MODE=llm_first CMF_MEMORY_CHANNELS=all PYTHONPATH=src .venv/bin/python -m orchestration.cli ingest --client CLIENT-001
```

LLM responses are cached in `build/eval/llm_cache.json`, so re-running unchanged prompts costs nothing.
