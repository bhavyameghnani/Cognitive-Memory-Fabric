# CMF Phase 1: Longitudinal Synthetic Advisor–Client Conversations

Source-evidence dataset for the future Cognitive Memory Fabric (CMF). This phase builds **only** the raw
conversations. There is no memory extraction, RAG, embeddings, graph store or agent here.

- 150 complete meetings (one *interaction* = one whole meeting, 12-24 turns, mean 14.7)
- 6 fictional clients x 25 meetings, 3 fictional advisors, 2026-01-02 .. 2026-06-30
- One CSV row per conversational turn: `data/conversations.csv`

## How the dialogue is produced (no LLM)

**No language model is used.** The dialogue is hand-authored text assembled by deterministic Python:

- each client has a hand-written 25-meeting storyline (`src/cmfgen/clients/c1.py` .. `c6.py`);
- pivotal moments (changes of mind, corrections, decisions, rationale) are hand-written scripts;
- routine segments (portfolio review, market commentary, cash flow, document requests, follow-ups on open
  action items, openings/closings) are parameterised templates that read per-client state;
- a fixed seed controls scheduling, channel/time slots and which template variant is chosen.

See `docs/dataset_design.md` for the design and an honest list of limitations.

## Requirements

Python 3.9+ (tested on 3.11). Standard library only, nothing to install.

## Regenerate and validate

```bash
python src/generate_dataset.py                 # writes data/conversations.csv + reports/generation_manifest.json
python src/validate_dataset.py                 # writes reports/dataset_validation.json; exit code 1 on failure
python src/compare_quality.py                  # writes reports/dataset_quality_comparison.json (v1 backup vs current)
python src/date_audit.py data/conversations.csv   # relative-date audit on its own
python src/final_audit.py                      # final acceptance audit -> reports/final_acceptance_audit.json + docs/final_dataset_acceptance.md
```

Options: `python src/generate_dataset.py --seed 20260101 --out data/conversations.csv`.
The same seed gives a byte-identical CSV (verified by MD5 across repeated runs).

## Revision history

- **v1**: first dataset (3,292 turns). Preserved untouched in `backup/v1/` (CSV, manifest, validation report, source, design doc).
- **v2** (current): targeted quality revision. Same 150 interaction IDs, clients, advisors, datetimes, channels and action ledger;
  filler removed, 36 thin meetings reviewed (29 rewritten, 1 extended, 6 half-year reviews extended), 7 more correction scenarios, relative dates fixed, voice pools added.
  See `docs/dataset_design.md` and `reports/dataset_quality_comparison.json`.

- **v2 final**: after the final QA pass (4 interactions corrected) the dataset is frozen. See `docs/final_dataset_acceptance.md`.

## Layout

| Path | Purpose |
|---|---|
| `data/conversations.csv` | Final dataset, one row per turn |
| `src/generate_dataset.py` | CLI entry point |
| `src/cmfgen/` | Engine (`engine.py`), segment library (`beats.py`), concept explanations (`explain.py`), scheduling (`build.py`), client storylines (`clients/`) |
| `src/validate_dataset.py` | Automated validation (33 checks) |
| `src/date_audit.py`, `src/compare_quality.py` | Relative-date audit; v1-vs-v2 comparison report |
| `src/cmfgen/voice.py`, `revisions.py`, `rev_c1..6.py` | Revision-2 voice pools and per-meeting rewrites |
| `reports/dataset_validation.json` | Validation results and statistics |
| `reports/dataset_quality_comparison.json` | v1 vs v2 metrics (with definitions), preservation checks, manual-review section |
| `reports/manual_review_notes.json` | Human-style review notes for the 36 previously thin meetings and other samples (not computed) |
| `backup/v1/` | The original dataset and its source, kept for comparison |
| `backup/v2_pre_final/` | Snapshot of v2 immediately before the final QA corrections |
| `docs/final_dataset_acceptance.md`, `reports/final_acceptance_audit.json` | Freeze decision, fact trace, review checks, limitations |
| `reports/generation_manifest.json` | Generator-side ground truth (authoring tags, action ledger). **QC only. Never feed it to the CMF ingestion pipeline.** |
| `docs/data_dictionary.md` | Column definitions and the definition of an interaction |
| `docs/dataset_design.md` | Clients, scenarios, methodology, known limitations |

## Evidence integrity note

The CSV contains only source conversation fields. It has no memory types, salience, supersession or extracted
entities. The manifest file is kept separate on purpose and is meant only for scoring a future CMF's
extraction against what the authors intended.

## Phase 2: synthetic operational data layer

A separate, structured financial data layer (accounts, holdings, monthly snapshots, goals, liabilities) built around the same six clients,
for future calculation tools. It does **not** modify `data/conversations.csv`. Not an agent, MCP server or memory system.

```bash
python src/opdata/generate.py          # data/operational/*.csv (deterministic)
python src/opdata/validate.py          # reports/operational_validation_report.{json,md}
python src/opdata/load_db.py --reset   # PostgreSQL schema cmf_ops (needs CMF_DATABASE_URL or --dsn)
```

See `docs/synthetic_operational_data.md` (method, provenance, limits) and `docs/operational_data_dictionary.md`.
