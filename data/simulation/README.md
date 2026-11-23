# Simulated continuation set (`SIM-001` … `SIM-025`)

Twenty-five new, fully synthetic interactions that continue the six clients' storylines after the original record ends (30 June 2026). They serve two purposes:

1. **Demonstration.** The UI tab *Simulate* replays them one at a time through the production ingestion path (`src/services/simulation.py`). Until an interaction is ingested, the system has never seen it.
2. **Evaluation on unseen, naturally worded data.** `sim_manifest.json` lists the outcomes each interaction was written to contain, in the same format as `reports/generation_manifest.json`, so `scripts/eval_llm_first.py --manifest data/simulation/sim_manifest.json` scores extraction against it.

| File | Content |
|---|---|
| `sim_conversations.csv` | 25 interactions, 230 turns; the columns of `data/conversations.csv`; `source_type = synthetic_simulation`. Channels: 12 email, 7 telephone, 6 chat. |
| `sim_manifest.json` | 99 authored outcomes: 26 plan, 17 tentative, 12 decision, 12 correction, 10 deferred, 12 advisor actions, 10 client actions; with the turns that state each one. |
| `generation_log.jsonl` | One line per model call: model, tokens, estimated cost, prompt hash, validation result. |

## How it was made

`scripts/generate_synthetic_conversations.py` (model `gpt-4.1`, temperature 0.8). For each client the model received the client, advisor and family names from `datasets/character_registry.csv` and the four most recent interactions, then wrote one new interaction at a time in date order, each one added to the context of the next. The prompt asks for natural speech without the scripted set's stock recap phrases, and for at least one hard case per interaction (a half-accepted recap, a hedge, a reversal or a correction). Code validated every response (speakers, cited turns, dates, no other client named) before it was written. Total cost: about USD 0.30.

## Caveats

- The answer key is what the generator **says** it planted. It was checked structurally, not read by a person. Outcome texts can be broader or narrower than the transcript supports.
- The matching judge in `eval_llm_first.py` is `gpt-4.1`, the same model that generated this set. A shared bias between generator and judge cannot be excluded.
- The set is small (77 scored outcomes). Differences of one or two outcomes are within noise.
