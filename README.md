# Cognitive Memory Fabric (CMF)

A long-term memory layer for a wealth-advisor assistant. It turns an advisor's meetings with a client into four kinds of memory, keeps the *history* of what the client said (not just the latest value), and answers questions with a report where every claim cites its evidence. All client data is **synthetic**.

| Memory | Holds | Example |
|---|---|---|
| Working | scratch notes for the current question (expire after 4 h) | "preparing Kenji's next portfolio review" |
| Episodic | specific events, each tied to a meeting turn | "Mar 3: discussed retirement planning" |
| Semantic | stable facts, **versioned** (superseded, never overwritten) | retirement age 65 -> 63 |
| Procedural | how the advisor's firm does things | "Retirement readiness review" checklist |

**Four agents, in order:** Planner -> Memory Orchestrator -> Tool Selector (13 read-only MCP tools over the operational data) -> Report Agent. Each agent only sees what the previous ones hand it; the Report Agent has no database or tool access. Agent specs are in `docs/agent_prompts/`.

**Harness** (`src/harness/`, see [docs/harness.md](docs/harness.md)): a request-scoped layer around the four agents - one shared budget and trace, Strands-based model-call control, token-aware compression, a safe response cache, permission gate, evidence verifier, temporal-conflict detector, memory write validator, human-approval gate and a repeatable evaluation runner (`python -m harness.evaluation.runner`).

**Brain UI** (`ui/cmf-brain-ui`): a brain map of the memories, a per-memory dossier (evidence quotes, version history, lifecycle audit trail), and **Ask CMF**, which answers a question and shows how the agents performed: stage timings, which memories were retrieved vs. actually cited, tool calls, and a warning when a fallback replaced a model step.

## Quick start

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.development.example .env                # LOCAL DEVELOPMENT profile (no authentication, loopback only); set OPENAI_API_KEY (needed only for Ask CMF)
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
cp .env.development.example .env   # local development; then edit .env: set OPENAI_API_KEY (or CMF_LLM_BACKEND=ollama).  For a shared deployment use .env.example (production), see below
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
| `HARNESS_PROFILE` | `production` | `production` or `development` (see *Security profiles*) |
| `CMF_AUTH_JWT_SECRET`, `CMF_AUTH_TRUSTED_GATEWAY`, `CMF_AUTH_ISSUER`, `CMF_AUTH_AUDIENCE` | empty / `false` | Signed-token identity (secret >= 32 chars; the API will not start in trusted-gateway mode without it) |
| `CMF_API_DOCS_ENABLED` | `false` | Serve `/docs` and `/openapi.json` in production (verified callers only) |
| `CMF_API_HOST`, `CMF_API_PORT` | `127.0.0.1`, `8000` | Backend bind address |
| `CMF_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Browser origins allowed to call the API directly |
| `VITE_DEV_PORT` | `5173` | Frontend dev-server port |
| `VITE_API_PROXY_TARGET` | `http://localhost:8000` | Where the dev server forwards `/api`. **Change it together with `CMF_API_PORT`** |
| `VITE_API_BASE_URL` | empty | Absolute API URL for a deployed UI (then add the UI's origin to `CMF_CORS_ORIGINS`) |

Real environment variables always override `.env`. Paths in `.env` are relative to the repo root, so run the commands above from there.

### Legacy memories: `reverify`

Memories that predate the integrity boundary (including the shipped demo seed) are marked `unverified` and the memory row itself is immutable. To let them earn `verified`, run

```bash
PYTHONPATH=src .venv/bin/python -m orchestration.cli reverify [--client CLIENT-001]
```

It re-resolves each memory's cited transcript turn(s) and judges it with the same rules a new write goes through; passes are recorded in the append-only `memory_reverifications` table (migration 124, applied by `init`), failures stay `unverified` and are listed with a reason. Offline operator tool: no API route, no LLM. It is idempotent. Expect a minority to pass on the demo seed: the checker only accepts a claim that a *single cited source sentence* supports, so paraphrases of terse turns (e.g. "We've decided: 63.") stay unverified by design.

### Memory-quality evaluation

```bash
PYTHONPATH=src .venv/bin/python -m evaluation.memory_eval [--caps 3,5,8] [--embedder hash|ollama|openai] [--json out.json]
```

Runs on a temp copy of the seed memory DB, with no LLM and no network by default. It reports extraction recall per authored kind (from `reports/generation_manifest.json`), unsupported-commitment rate, semantic version-chain accuracy, and retrieval metrics (value accuracy, episode recall/precision/MRR, joint score, cross-client leaks) for keyword-only vs hybrid ranking at several episode caps. Gold cases are in `data/eval/memory_eval_cases.json` (CLIENT-001 only, matching the seed DB).

### Security profiles: production (default) vs local development

| | `.env.example` (**production**, the default) | `.env.development.example` (local only) |
|---|---|---|
| `HARNESS_PROFILE` | `production` | `development` |
| Authentication | **required**: `Authorization: Bearer <signed JWT>`, verified with `CMF_AUTH_JWT_SECRET` (>= 32 chars) | none: token-less requests from a *loopback* peer act as an admin for all clients |
| `X-CMF-Actor-*` headers | refused (never authentication) | refused |
| Safety controls (`HARNESS_PERMISSION_GATE`, evidence verification, write validation, approval) | cannot be switched off; an attempt is ignored with a warning | may be switched off for experiments |
| Network binding | any address | **loopback only**; the API refuses to start otherwise and prints a warning at start-up |
| `/docs`, `/openapi.json` | off (`CMF_API_DOCS_ENABLED=true` serves them to verified callers only) | on |
| Only unauthenticated route | `GET /api/health` -> `{"ok": true}` | same |

`CMF_AUTH_TRUSTED_GATEWAY=true` declares that identities arrive as signed tokens; the API **refuses to start** in that mode without `CMF_AUTH_JWT_SECRET`.
Mint a token for testing: `PYTHONPATH=src python -m harness.auth_tokens --actor ADVISOR-001 --role advisor` (reads the secret from the environment), then
`curl -H "Authorization: Bearer <token>" localhost:8000/api/clients`. Details: [docs/harness.md](docs/harness.md#permissions-and-identity).

### Troubleshooting

- **Every call answers 401:** the production profile needs a signed Bearer token (see above). For local work copy `.env.development.example`. The bundled UI sends no token, so it works only with the development profile (or behind a gateway that adds the header).
- **API refuses to start ("loopback"):** the development profile may only bind to `127.0.0.1` / `::1` / `localhost`; fix `CMF_API_HOST` or switch to the production profile with a secret.
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

## Harness (Phase 4)

The harness (`src/harness/`, long form: [docs/harness.md](docs/harness.md)) is **not a fifth agent**. It is request-scoped infrastructure around the four agents: it decides who may ask what, how much a request may cost, what the agents may see, and what is allowed to leave. The memory layer's own write rules live in `src/agents/memory/` (the *memory integrity boundary*, below).

```
request (API / CLI)
  -> identity (signed token -> AuthContext) -> PermissionGate          before anything else, and before any cache lookup
  -> shared Budget + trace opened (time, tokens, model calls, tool calls)
  -> Planner -> Memory -> Tool Selector -> Report                      every model call goes through the harness LLM wrapper (Strands-based)
  -> Evidence Verifier checks every finding the Report Agent wrote
  -> Approval gate: release now, or withhold until a human approves
  -> answer + trace
```

### Components (`src/harness/`)

| Module | What it does |
|---|---|
| `config.py` | `HarnessConfig`: every `HARNESS_*` / `CMF_AUTH_*` setting, validated at start-up (`ConfigError`). In `production` a missing or hostile variable never weakens a control |
| `auth_tokens.py`, `permission_gate.py` | Signed-token identity (`AuthContext`: actor, role, client scope, tool scope) and the gate that checks it. Out-of-scope clients look exactly like missing ones |
| `budget.py`, `token_counter.py`, `context_manager.py` | One shared budget per request; per-agent context limits; exact token counts when `tiktoken` is available, otherwise a conservative estimate |
| `compression.py` | Deletion-only prompt compression. Duplicates go first, then old tool results, plain episodes, superseded history; lines carrying a caveat (human correction, negation, exploratory / hypothetical / unconfirmed, disputed, unverified, open conflict) go last. A surviving line is byte-identical; each omission leaves a marker with the handles and the caveat kinds omitted. If protected content still does not fit, the request fails safely (`ContextOverflow`) |
| `cache.py`, `llm.py` | Local response cache (not provider prompt caching). Key = model, agent, system-prompt hash, schema hash, full prompt hash, actor, client scope (JSON list), `data_version`. TTL- and size-bounded; only output that passed structured validation is stored |
| `runtime.py` | `data_version()` (memory rows, row versions, lifecycle events, conflicts and their resolutions, operational DB stat), `Harness.release`, `withheld_view` / `released_view` |
| `reliability.py`, `hooks.py`, `workers.py`, `strands_adapter.py` | Timeouts, late results, partial replies, context-overflow recovery, bounded provider workers; Strands `Agent` / `Model` / hooks used as the model-calling layer (Strands tool execution is not used; the Tool Selector still talks to MCP through its own toolbox) |
| `evidence_verifier.py`, `claim_extraction.py`, `claim_matching.py`, `clause_support.py` | Deterministic verification of every finding (below) |
| `approval.py`, `approval_store.py` | Persistent one-time human approval (below) |
| `memory_write_validator.py`, `temporal_conflict.py` | Pre-write checks on what the Memory Orchestrator proposes; temporal conflict detection |
| `telemetry.py`, `trace_recorder.py` | Per-request trace and metrics (calls, tokens, cache hits, compression, verification outcomes, approvals) |
| `evaluation/` | Repeatable evaluation (below) |

### Memory integrity boundary (`src/agents/memory/`)

Every durable memory mutation passes `integrity.py` (`MemoryIntegrityBoundary`), reached through the unchanged `MemoryStore` methods.

* **Identity.** Every write needs a sealed `Principal`: a verified person or a named in-process service (`ingestion`, `consolidation`, `orchestrator`, `curated_loader`, `ttl_expiry`), each limited to certain operations, memory types and clients.
* **Provenance and time.** A memory cites a real transcript turn of the right client; observation time comes from that turn, never from the caller. `valid_from` of semantic / procedural memories is bound to the cited turn; `valid_to` is set only by a trusted supersession.
* **Verification** (`support.py`). `verified` means the statement is supported by one cited source sentence and the class of memory is allowed to claim it. It is deterministic lexical / numeric / structural matching, **not semantic entailment**: it checks number, unit, magnitude and role; negation, hedges, conditions, attribution (told / recommended); `and` vs `or`; who acts (client, spouse, parent, colleague, advisor, named person); retraction anywhere in the excerpt. Anything it cannot decide stays `unverified`. A derived (consolidated) memory is `verified` only if a verified episode of the same turn states the same value.
* **Human corrections** take precedence and are protected from automatic overwrite; exactly one active version per semantic slot.
* **Slot identity.** (client, topic, subject) in canonical form (trimmed, lower-case, whitespace / hyphen -> `_`); topics must be in the planner's `TOPICS` vocabulary; whitespace-only subjects are refused. Unique index `uq_active_semantic_slot_canonical` (schema version 123). Legacy duplicates are never merged or deleted: `MemoryStore.slot_collisions()` reports them and new competing writes are refused.
* **Conflicts** (`memory_conflicts`, `memory_conflict_resolutions`) are append-only; only a verified, `lifecycle`-privileged person resolves them (`keep` / `dismiss` / `correct`).
* **Database guards.** 40 SQLite triggers (`MemoryStore._trigger_definitions()`) block raw writes and make audit tables append-only. `integrity_ready()` compares each trigger's exact definition, not its name; `init()` repairs a missing or modified one in one transaction and verifies it, otherwise durable writes are refused. Whoever owns the database file can still rewrite SQLite itself - this gives detection and fail-closed behaviour, not protection from the file owner.
* **TTL.** Working memory expires through `expire_working(principal)` on the store's own clock (`expires_at <= now`); there is no caller-supplied timestamp.

Migrations: `sql/120_cmf_memory_integrity.sql`, `121_cmf_memory_conflicts.sql`, `122_cmf_memory_conflict_resolutions.sql`, then the canonical slot index in `MemoryStore._ensure_slot_index()` (user_version 123). `MemoryStore.init()` applies them additively.

### What the Report Agent receives and how findings are checked

* Retrieved memories keep `verification`, `verification_basis`, `source_class` and `temporal_status` all the way into the report evidence (`agents/report/evidence.py`). Each item gets an `evidence_class`: `verified`, `unverified`, `contradicted` (status disputed), `open_conflict`, or `ambiguous` (exploratory / hypothetical / unconfirmed / recommendation). Non-verified items are labelled in their description, listed after the verified knowledge, and every one is named in the report's uncertainties.
* `agents/report/validate.py` withdraws a finding that cites an unverified / contradicted / open-conflict memory (the withheld text is not echoed); `claims.py` rejects structured claims on such evidence.
* **Evidence Verifier** (`evidence_verifier.py`), per finding: the finding cites evidence; the handle exists; its client is in the plan and in the caller's scope; the quotation is real text of the original turn (an agent's description is never proof); memory evidence is re-read from the store; a memory classed unverified / contradicted / open-conflict is refused (`EVIDENCE_NOT_VERIFIED`). Then the **figure check** (value, unit, period, modality, polarity, client) and the **clause check** (`clause_support.py`): every clause of a model-written finding must be accounted for by the source (actors, negation, conditions, hearsay, `and`/`or`, completed vs planned action, words the source does not support). The clause check can only downgrade.
* Outcomes: `VERIFIED` (kept), `AMBIGUOUS` (kept, prefixed `[Qualified: ...]`), `UNVERIFIED` / `CONTRADICTED` (withheld and listed under uncertainties). An optional entailment checker may only downgrade. Code-rendered findings are not clause-checked. The summary is still checked only for figures and dates.

### Human approval (`approval.py`, `approval_store.py`)

High-risk requests (`RiskCategory`: suitability conclusion, sensitive memory write, external action, irreversible operation, high-risk tool) are **withheld**: the API returns a content-free view and stores a pending approval in SQLite (`CMF_APPROVAL_DATABASE_URL`, default `build/cmf_approvals.db`). A supervisor / admin approves or rejects; the requester can then release it **once**, only while the approval has not expired (`HARNESS_APPROVAL_TTL_SECONDS`, default 14400) and the fingerprint (category, operation, clients, payload, `data_version`, requester) still matches - if the evidence changed after approval it is no longer releasable. A cache hit never bypasses this.

### API endpoints added for the harness (`api/main.py`)

| Route | Purpose |
|---|---|
| `POST /api/ask` | Runs the pipeline; a withheld answer returns only the content-free approval view |
| `GET /api/approvals`, `GET /api/approvals/{id}` | Pending approvals a supervisor / admin may decide (metadata only); one approval visible to its requester or a reviewer, otherwise 404 |
| `POST /api/approvals/{id}/decision`, `/approve`, `/reject` | Decide an approval |
| `POST /api/approvals/{id}/release` | The requester releases an approved answer, once |
| `POST /api/memories/{id}/lifecycle` | Change a memory's status (verified identity, `expected_version` for optimistic concurrency) |
| `POST /api/conflicts/{id}/resolve` | Resolve an open memory conflict (`keep` / `dismiss` / `correct`) |
| `GET /api/clients`, `/api/clients/{id}/brain`, `/api/memories/{id}`, `/api/health` | Existing read routes, now behind the permission gate (`/api/health` is the only unauthenticated route) |

### Configuration added for the harness

Defaults and validation are in `src/harness/config.py`; `.env.example` (production) and `.env.development.example` (local) list them. Besides the variables in the table above: `HARNESS_ENABLED`, `HARNESS_COMPRESSION_ENABLED`, `HARNESS_CACHE_ENABLED`, `HARNESS_CACHE_MAX_ENTRIES`, `HARNESS_CACHE_TTL_SECONDS`, `HARNESS_EVIDENCE_VERIFICATION`, `HARNESS_STRUCTURED_CLAIM_VERIFICATION`, `HARNESS_PERMISSION_GATE`, `HARNESS_MEMORY_WRITE_VALIDATION`, `HARNESS_APPROVAL_REQUIRED_FOR_HIGH_RISK`, `HARNESS_APPROVAL_TTL_SECONDS`, `HARNESS_APPROVAL_SENSITIVE_MEMORY_TOPICS`, `CMF_APPROVAL_DATABASE_URL`, `HARNESS_STRANDS_EXECUTION`, `HARNESS_TRACE_ENABLED`, `HARNESS_TRACE_STORE_PROMPTS`, the budget limits (`HARNESS_MAX_LLM_CALLS`, `HARNESS_MAX_TOOL_CALLS`, `HARNESS_MAX_RUNTIME_SECONDS`, `HARNESS_MAX_MODEL_CALL_SECONDS`, `HARNESS_MAX_INPUT_TOKENS`, `HARNESS_MAX_OUTPUT_TOKENS`, `HARNESS_MAX_TOTAL_TOKENS`, `HARNESS_MAX_CONTEXT_TOKENS`, ...) and the worker caps (`HARNESS_MAX_PROVIDER_WORKERS`, `HARNESS_MAX_ABANDONED_WORKERS`, `HARNESS_MAX_CANCEL_WORKERS`). In `production`, an attempt to switch off the permission gate, evidence verification, write validation, approval or the harness itself is ignored with a warning.

### Evaluation (`src/harness/evaluation/`)

```bash
PYTHONPATH=src python -m harness.evaluation.runner [--configs baseline,harness_full,no_cache] [--out build/harness_eval]   # P1.1 ablation runs
PYTHONPATH=src python -m harness.evaluation.p15 --out build/p15_eval                  # P1.5: claim-verification corpus, isolation, temporal, cache, compression, scenarios (scripted models)
PYTHONPATH=src python -m harness.evaluation.p15 --out build/p15_eval --real           # additionally ONE real-model request (smoke test only)
PYTHONPATH=src python -m harness.evaluation.p151                                      # P1.5.1: the 73 probes at the memory and report layers + 12 end-to-end scenarios (JSON)
PYTHONPATH=src python -m harness.evaluation.p151_mutations --out <dir>                # mutation check on a temp COPY of the repo (never touches the working tree)
```

Memory verification, report claim verification, finding qualification and approval release are separate boundaries and are measured separately. The corpora (`p15_corpus.py`, 70 cases; `p151_probes.py`, 73 probes) are **self-authored regression measurements, not independent validation** and not production accuracy; scripted models say nothing about a real model's accuracy.

### Status

P1.4.3 (memory integrity remediation), P1.5 (evaluation) and P1.5.1 (report verification, evaluation integrity) are implemented but **not independently accepted**; they were built and reviewed by the same engineer-agent. The tests named in each phase's report pass individually; a complete full-suite run after P1.5.1 is still pending. Known limits are listed in `docs/harness.md` (Known limitations; P1.4.3 and P1.5.1 sections).
