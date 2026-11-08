# Cognitive Memory Fabric (CMF)

A long-term memory layer for a wealth-advisor assistant. It turns an advisor's meetings with a client into four kinds of memory, keeps the *history* of what the client said (not just the latest value), and answers questions with a report where every claim cites its evidence. All client data is **synthetic**.

| Memory | Holds | Example |
|---|---|---|
| Working | scratch notes for the current question (expire after 4 h) | "preparing Kenji's next portfolio review" |
| Episodic | specific events, each tied to a meeting turn | "Mar 3: discussed retirement planning" |
| Semantic | stable facts, **versioned** (superseded, never overwritten) | retirement age 65 -> 63 |
| Procedural | how the advisor's firm does things | "Retirement readiness review" checklist |

**Four agents, in order:** Planner -> Memory Orchestrator -> Tool Selector (13 read-only MCP tools over the operational data) -> Report Agent. Each agent only sees what the previous ones hand it; the Report Agent has no database or tool access. Agent specs are in `docs/agent_prompts/`.

**Brain UI** (`ui/cmf-brain-ui`): a brain map of the memories, a per-memory dossier (evidence quotes, version history, lifecycle audit trail), and **Ask CMF**, which answers a question and shows how the agents performed: stage timings, which memories were retrieved vs. actually cited, tool calls, and a warning when a fallback replaced a model step.

## Quick start

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env                            # set OPENAI_API_KEY (needed only for Ask CMF)
python utils/seed_demo_db.py install            # demo databases: Kenji's memories + operational data, no LLM needed to browse
cd ui/cmf-brain-ui && npm install && cd ../..
PYTHONPATH=src .venv/bin/python -m api.main     # terminal 1: backend  http://127.0.0.1:8000
cd ui/cmf-brain-ui && npm run dev               # terminal 2: frontend http://localhost:5173
```

Details, configuration and troubleshooting: [Run the app](#run-the-app-backend--frontend).

## Repository map

| Path | What |
|---|---|
| `ui/cmf-brain-ui/` | React + Vite frontend |
| `api/` | FastAPI backend (read endpoints, `/api/ask`, memory lifecycle) |
| `src/agents/` | `planner`, `memory`, `tool_selector`, `report` agents |
| `src/mcp_server/` | The 13 read-only financial tools (MCP) |
| `src/orchestration/` | Pipeline, CLI (`init-memory`, `ingest`, `ask`, `trace`), request tracing |
| `src/llm/` | LLM abstraction: OpenAI, Ollama (Qwen) or a mock; loads `.env` |
| `src/cmfgen/`, `src/opdata/` | Generators for the synthetic meetings (Phase 1) and operational data (Phase 2) |
| `sql/` | Memory schema (`cmf_memory`) and operational schema |
| `data/` | `conversations.csv` (150 meetings), `operational/` (financial tables), `seed/` (demo databases) |
| `docs/`, `reports/` | Design notes, data dictionaries, validation and audit reports |
| `utils/` | `seed_demo_db.py` and notes on optional integrations |
| `tests/` | Pytest suite (scripted models; no network needed) |

The rest of this file: [how to run](#run-the-app-backend--frontend), then the build history by phase (1: conversations, 2: operational data, 3: agents).

## Run the app (backend + frontend)

Two processes: a **FastAPI backend** (`api/main.py`, port 8000) and a **React + Vite frontend** (`ui/cmf-brain-ui`, port 5173). The frontend calls `/api/...` on its own origin and the Vite dev server proxies that to the backend, so there is no URL to hard-code in the UI.

**Prerequisites:** Python 3.10+ (developed on 3.14), Node 20.19+ (or 22.12+), and for *Ask CMF* and ingestion an LLM: an OpenAI API key, or a local Ollama with `qwen2.5:3b`. (Browsing the brain map needs no LLM.)

### 1. One-time setup (from the repo root)

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env            # then edit .env: set OPENAI_API_KEY (or CMF_LLM_BACKEND=ollama)
cd ui/cmf-brain-ui && npm install && cd ../..
```

`.env` is git-ignored and is the single place for configuration; the backend and the frontend both read it (the frontend sees only `VITE_*` variables).

### 2. Load the memory database

The brain map is empty until the memory DB (`CMF_MEMORY_DATABASE_URL`) has memories.

**Option A (default): install the demo databases shipped in `data/seed/`.** No LLM or API key needed to browse.

```bash
python utils/seed_demo_db.py install            # writes build/cmf_memory.db and build/cmf_ops_smoke.db, the paths .env.example points at
```

It refuses to overwrite existing files unless you pass `--force`. The seed holds CLIENT-001 (Kenji): 71 memories from his 25 meetings (62 episodic, 6 semantic, 3 procedural), no working memories. Ask CMF still needs an LLM.

**Option B: rebuild from the meetings yourself** (needs the LLM; this is what produced the seed):

```bash
.venv/bin/python src/opdata/load_db.py --sqlite build/cmf_ops_smoke.db            # operational data used by the tools
PYTHONPATH=src .venv/bin/python -m orchestration.cli init-memory --reset          # empty memory schema
PYTHONPATH=src .venv/bin/python -m orchestration.cli ingest --client CLIENT-001   # extracts memories from Kenji's 25 meetings
```

Only ingested clients have memories (the other five show 0 until you run `ingest --client CLIENT-00N`). Maintainers can refresh the seed from their local DBs with `python utils/seed_demo_db.py export`.

### 3. Start the backend (terminal 1, repo root)

```bash
PYTHONPATH=src .venv/bin/python -m api.main          # http://127.0.0.1:8000  (check: curl localhost:8000/api/health)
```

### 4. Start the frontend (terminal 2)

```bash
cd ui/cmf-brain-ui && npm run dev                    # http://localhost:5173
```

Open http://localhost:5173. Production build: `npm run build` (output in `ui/cmf-brain-ui/dist`, git-ignored); type check: `npm run lint`.

### Configuration (`.env`, see `.env.example`)

| Variable | Default | Used by |
|---|---|---|
| `CMF_LLM_BACKEND` | `ollama` (`openai` in the example) | LLM choice: `openai`, `ollama` or `mock` |
| `OPENAI_API_KEY`, `CMF_OPENAI_MODEL`, `CMF_OPENAI_BASE_URL` | – / `gpt-4o-mini` / OpenAI URL | OpenAI backend. The key is read only from the environment, never logged |
| `CMF_LLM_BASE_URL`, `CMF_LLM_MODEL`, `CMF_LLM_NUM_CTX` | `http://localhost:11434`, `qwen2.5:3b`, `6144` | Ollama backend |
| `CMF_EMBED_BACKEND`, `CMF_EMBED_MODEL` | `none` / backend default | Hybrid episode ranking: `none` (off), `hash` (offline, lexical only), `ollama` (`nomic-embed-text`) or `openai` (`text-embedding-3-small`) |
| `CMF_MEMORY_DATABASE_URL`, `CMF_DATABASE_URL` | SQLite files under `build/` | Memory DB (the UI reads it) and operational data DB |
| `CMF_ALLOWED_CLIENTS` | empty = all | Tool-server client allow-list |
| `CMF_API_HOST`, `CMF_API_PORT` | `127.0.0.1`, `8000` | Backend bind address |
| `CMF_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Browser origins allowed to call the API directly |
| `VITE_DEV_PORT` | `5173` | Frontend dev-server port |
| `VITE_API_PROXY_TARGET` | `http://localhost:8000` | Where the dev server forwards `/api`. **Change it together with `CMF_API_PORT`** |
| `VITE_API_BASE_URL` | empty | Absolute API URL for a deployed UI (then add the UI's origin to `CMF_CORS_ORIGINS`) |

Real environment variables always override `.env`. Paths in `.env` are relative to the repo root, so run the commands above from there.

### Memory-quality evaluation

```bash
PYTHONPATH=src .venv/bin/python -m evaluation.memory_eval [--caps 3,5,8] [--embedder hash|ollama|openai] [--json out.json]
```

Runs on a temp copy of the seed memory DB, with no LLM and no network by default. It reports extraction recall per authored kind (from `reports/generation_manifest.json`), unsupported-commitment rate, semantic version-chain accuracy, and retrieval metrics (value accuracy, episode recall/precision/MRR, joint score, cross-client leaks) for keyword-only vs hybrid ranking at several episode caps. Gold cases are in `data/eval/memory_eval_cases.json` (CLIENT-001 only, matching the seed DB).

### Troubleshooting

- **Brain map empty / 0 memories:** step 2 was skipped, or `CMF_MEMORY_DATABASE_URL` points at a different DB than the one you loaded (the seed installs to `build/cmf_memory.db`).
- **"CMF could not answer" in Ask CMF:** the LLM is not reachable. Check `OPENAI_API_KEY` / network, or that Ollama is running. A run that fell back to rule-based steps shows an amber "Degraded run" banner in the agent-performance panel.
- **UI shows network errors / 502 on `/api`:** the backend is not running, or `VITE_API_PROXY_TARGET` does not match `CMF_API_PORT`.
- **Port already in use:** change `CMF_API_PORT` + `VITE_API_PROXY_TARGET` (backend) or `VITE_DEV_PORT` (frontend).
- Every Ask stores short-lived working memories (4 h TTL), so the Working count grows as you ask.
- The UI loads its fonts from Google Fonts; offline it falls back to system fonts.
- Run the tests with `.venv/bin/python -m pytest -q`.

## Phase 1: Longitudinal synthetic advisor–client conversations

Source-evidence dataset for CMF. This phase builds **only** the raw conversations: no memory extraction, RAG,
embeddings, graph store or agent (those are Phases 2-3, below, and use this dataset as input).

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

Dataset generation and validation (this phase): Python 3.9+, standard library only, nothing to install.
The agents, API and UI need more: see [Run the app](#run-the-app-backend--frontend) (Python 3.10+, Node 20.19+ / 22.12+).

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

## Agents (Phase 3)

Planner -> Memory Orchestrator -> Tool Selector (MCP) -> Report Agent. Specs: `docs/agent_prompts/`.

| Component | Code |
|---|---|
| Memory Orchestrator (`cmf_memory`, SQLite now) | `src/agents/memory/`, `sql/100_cmf_memory_schema.sql` |
| MCP server (13 read-only deterministic tools) | `src/mcp_server/` |
| Tool Selector | `src/agents/tool_selector/` |
| Report Agent | `src/agents/report/` |
| Pipeline / CLI | `src/orchestration/` |

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python src/opdata/load_db.py --sqlite build/cmf_ops_smoke.db
.venv/bin/python -m pytest -q tests
PYTHONPATH=src .venv/bin/python -m orchestration.cli init-memory --reset   # then: ingest --client CLIENT-001 ; ask "..." ; trace <id>
```

LLM backends: `CMF_LLM_BACKEND=ollama` (default, Qwen), `openai` (set `OPENAI_API_KEY` in `.env` or your shell, optional `CMF_OPENAI_MODEL`, default gpt-4o-mini), `mock`.
Status: unit tests use a scripted model. Real runs: OpenAI ingest of CLIENT-001 and one `ask` only (Qwen and PostgreSQL not run).
