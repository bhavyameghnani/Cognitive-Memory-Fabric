# CMF end-to-end test report (application launch, UI path, Maya Rao scenarios)

Date 2026-11-23. Worktree `...\Cognitive Memory Fabric - Bhavya Baseline`, branch `feature/llm-first-ingestion`, HEAD `8081eaa`. Nothing staged or committed; no source, schema, UI or database-design change. AI-assisted testing; **no human reviewed any output and no semantic accuracy is claimed.** Raw evidence (JSON dumps, logs) is in the session scratchpad `...\scratchpad\e2e\` (`t1.json`, `t8k.json`, `maya_direct.json`, `maya_staged.json`, `backend.log`, `frontend.log`).

## Verdict up front
The application **launches and runs end to end** (backend, frontend, proxy, harness, four agents, real local LLM). **However**, three things limit what the UI can show: (1) the app has **no ingestion endpoint or screen** and its client directory is the fixed `data/conversations.csv`, so **Maya Rao and Arjun Mehta cannot exist in the running app**; (2) **I could not control or see the browser** (no browser automation was available), so UI behaviour was exercised through the same HTTP endpoints the UI calls; (3) the backend scenario run exposed **real defects** in correction/supersession handling (Test 6) and in staged validation (Test 10). Recommendation: **needs targeted fixes before further testing** (section G).

## A. Environment
| Item | Value |
|---|---|
| Frontend | `http://localhost:5174` (Vite dev; 5173 is taken by an unrelated project's dev server, left untouched) - started `VITE_DEV_PORT=5174 npm run dev` in `ui/cmf-brain-ui` (after `npm install`; `node_modules` is git-ignored) |
| Backend | `http://127.0.0.1:8000` - `PYTHONPATH=src python -m api.main`; FastAPI/uvicorn; `GET /api/health` -> `{"ok": true}`; frontend proxy `5174/api/health` also OK |
| Profile / auth | `HARNESS_PROFILE=development` (loopback, unauthenticated = admin for all clients; the app prints this warning). A separate **production-profile** instance (port 8001, signed JWTs, temporary random secret, now stopped) was used for the isolation test |
| Model | **Real local model**: Ollama `qwen2.5:3b` (CPU), `CMF_LLM_BACKEND=ollama`. No OpenAI key present; no model substitution. The llm_first verifier reuses the same model (the app's documented behaviour for non-OpenAI clients), so extractor and verifier are **not independent** here |
| Databases | **Disposable copies** of `data/seed/cmf_memory_llm_first.db` and `cmf_ops_demo.db` in the scratchpad; Maya scenarios used fresh temp SQLite stores. The copy of the memory DB was migrated 123 -> 130 by the app on startup (the seed itself is still 123 and untouched) |
| Test identities | Dev profile: `dev-anonymous` (admin). Production profile: `ADVISOR-001`, `ADVISOR-002` (role advisor). Staged test: `TEST-REVIEWER-simulated` (a **simulated** Principal, not a person) |
| Browser opened? | A launch of `http://localhost:5174/` in the default browser was **requested** (Windows `Start-Process`, no error). I **cannot confirm it rendered**; no screenshots, no clicks. UI rendering is therefore **NOT VERIFIED** |
| Config / secrets | No `.env` created (settings passed via process environment); no keys printed |

Pre-state recorded: branch/HEAD above; git status (36 entries, identical afterwards); seed hashes (3) and `build/cmf_memory.db` hash identical afterwards; an unrelated Vite server (another project) and Ollama were already running.

### What the app is (from the code)
Frontend: React + Vite (`ui/cmf-brain-ui`): brain map, memory dossier, **Ask CMF**. Backend routes: `GET /api/health, /api/clients, /api/clients/{id}/brain, /api/memories/{id}, /api/datasource*`, `POST /api/ask`, `GET/POST /api/approvals*` (high-risk **report-release** approvals, not memory proposals), `POST /api/memories/{id}/lifecycle`, `POST /api/conflicts/{id}/resolve`. Four agents (README, code): Planner -> Memory Orchestrator -> Tool Selector -> Report Agent, wrapped by the harness. **Not in the UI/API:** conversation or file ingestion, multichannel upload, staged-proposal inspection/approval, the M2 candidate lifecycle.

## B. Test results
Legend: UI = through the app's HTTP API/proxy (the calls the UI makes; **I did not drive the browser**); BACKEND = Python harness against the same memory code with a synthetic CSV, temp DB and the real `qwen2.5:3b`.

| Test | Channel | Expected | Actual | Status |
|---|---|---|---|---|
| 1 Connectivity | UI-path (`POST /api/ask` via 5174) | Request accepted, planner decides workflow, no invented facts, harness telemetry | HTTP 200 in 58 s. Planner ran (LLM call, 57 s). Memory ran (0 retrieved: no client named). Tool Selector ran and stopped: `no_client_in_plan`, "ask the advisor which client". Report ran **without a model call** (`mode: no_evidence`) and said it cannot answer. Harness: outcome completed, `ask`/`read_memory` permission decisions allowed, telemetry and budget present. No facts invented. (The planner's `topics: ["family_support"]` is an odd label for the request.) | **PASS** |
| 2 Preference extraction | BACKEND | Maya as speaker, meaning preserved, not an allocation, evidence kept, right client | Episodic `statement`, topic `asset_allocation`, content verbatim "I prefer relatively conservative investments...", `source_role=Client`, CLIENT-901, evidence `MAYA-T2#2/Client`. Certainty **unconfirmed** (write validator: "a reported preference is kept as unconfirmed"). No `value_text`; no allocation produced | **PASS** (topic label coarse) |
| 3 Tentative decision | BACKEND, direct mode | Tentative, not a commitment, source Maya | Episodic `exploration`, certainty `exploratory`, `source_role=Client`; votes extractor=verifier=regex=exploratory | **PASS** |
| 4 Advisor-reported decision | BACKEND, direct mode | Advisor attribution kept; no fabricated client confirmation | `source_role=Advisor`, `event_type=reported_decision`, certainty and status `unconfirmed`; candidate reason "advisor's account ... without client-spoken confirmation"; the only evidence row is the advisor turn | **PASS** (M1 behaviour confirmed with a real model) |
| 5 Negated finality | BACKEND, direct mode | Not a confirmed decision; uncertainty kept | `exploratory`, `source_role=Client`, content verbatim "Please don't treat ... final yet. I am still evaluating..." | **PASS** |
| 6 Correction / supersession | BACKEND | Both traceable; current = 63; 65 not silently erased/presented as current; supersession if supported | Both stored and traceable, **but**: no budget slot exists (both under `investment_trade`, `value_text` NULL), **no semantic version, no supersession link, `current` retrieval is empty**. Nothing marks 63 as current; the 65 memory is `unconfirmed` and the correction `confirmed` but unlinked. Also the client's own "My investment budget is ₹65 lakh" was stored as an **advisor-sourced** paraphrase ("Maya, we should plan around ₹65 lakh", `advisor_interpretation`, client turn only supporting). In **staged** mode the correction was **rejected** by validation | **FAIL** |
| 7 Contradictory info | BACKEND | Not treated as exclusive; ambiguity preserved | Both kept as separate episodes (`risk_tolerance` unconfirmed; exception under `investment_trade`), nothing merged or resolved. But the exception was stored as `event_type=decision`, certainty **confirmed**, which overstates "comfortable with" and no relationship/ambiguity is recorded | **PASS with defect D4** |
| 8 Retrieval | BLOCKED for Maya via UI/pipeline; BACKEND retrieval + UI-path analogue on Kenji | Current budget 63; tentative items; advisor vs client distinction; evidence-backed | **Maya blocked**: `run_pipeline` reports "client not identified" for CLIENT-901 (planner resolves only the fixed directory). Store retrieval for Maya returns 8 episodes (client-sourced and advisor-sourced labelled), `current`=0, so **a current budget cannot be derived**. Kenji analogue through the UI path: 404 s, all four agents ran, memory returned 12 (1 current, 3 history, 8 episodes) incl. retirement_target_age 63, but the report had **no key findings** (summary generic; `claims_total` 0), 3 `get_source_evidence` calls errored (`search_text cannot be combined with ...`), tools `partial`, harness evidence `unsupported: 1` | **BLOCKED** (and the analogue shows weak report quality) |
| 9 Client isolation | UI-path with auth, on shipped clients (Maya/Arjun impossible) | No cross-client access | Production profile: no token 401; ADVISOR-001 sees CLIENT-001/004 only, ADVISOR-002 CLIENT-002/005 only; cross-advisor brain read **404** `unknown client`, cross-advisor memory read **404**, own read 200, cross-advisor `/api/ask` about Kenji **403** `client_not_in_scope`. Store-level (BACKEND): CLIENT-902 retrieval returned **no** Maya memories | **PASS** (substituted clients) |
| 10 Staged lifecycle | BACKEND (not exposable via UI/API) | Candidate != production; unauthorised promotion refused; no human claim | See D. 8 proposals, **0** production memories after ingestion; 5 `pending_approval`, 3 `rejected` by validation. Refused: promote while pending, approve as `ingestion`, as unauthenticated user, as out-of-scope user, promote as `ingestion`. Approved by **simulated** `TEST-REVIEWER-simulated`, promoted by `promotion`; repeat promote idempotent; one production memory (`unconfirmed`, `source_role=Advisor`). Raw SQL insert of a conversation-derived memory refused by the database trigger | **PASS** (mechanism; **no human approval occurred**) |

## C. Four-agent verification (from the actual traces, `t1.json`, `t8k.json`)
| Agent | Evidence it executed | Notes |
|---|---|---|
| Planner | `llm_calls_by_agent.planner = 1`, timings 57 s / 38 s, a structured plan with subtasks and clients (`['CLIENT-001']` for Kenji) | Generic query got an odd topic; client resolved only from the fixed directory |
| Memory Orchestrator | `timings.memory` 1-10 ms; `memory.total` 0 (Test 1) and 12 with groups (Kenji) | Deterministic retrieval, **no LLM call** at query time |
| Tool Selector | Test 1: ran, `stop_reason no_client_in_plan`. Kenji: 5 LLM calls, tools `get_client_profile`, `get_client_goals`, `get_portfolio_history`, `get_source_evidence` x3 | 3 tool calls errored on invalid argument combinations (model-built); status `partial` |
| Report | Test 1: ran with `model_called: false`. Kenji: 1 LLM call, `mode llm` | Output weak: no key findings despite 12 memories (3B model) |
| Harness | `harness.permission_decisions`, `budget`, `telemetry`, `outcome: completed`, `released: true` | Permissions, budget and telemetry recorded per request |

## D. Memory and staged verification (representative synthetic examples, real model)
* **Client-attributed:** `I prefer relatively conservative investments...` -> episodic `statement`, Client, unconfirmed.
* **Tentative:** `I am considering investing ₹50 lakh next quarter, but I have not made a final decision yet.` -> `exploratory`, Client.
* **Advisor-reported:** `Maya has decided to invest ₹50 lakh next quarter.` -> `reported_decision`, Advisor, `unconfirmed`; candidate note: advisor's account without client-spoken confirmation. Same in staged mode: proposal `attribution=advisor_reported_client`, exact offsets 0..49; promoted only after the simulated approval, still `unconfirmed`/`Advisor`.
* **Negated finality:** `Please don't treat the investment decision as final yet...` -> `exploratory`, Client.
* **Correction:** stored as confirmed `decision` "Maya corrected the investment budget to ₹63 lakh." (Client) next to an unconfirmed advisor-paraphrased ₹65 memory; **no link between them, no current value** (defect D2).
* **Staged lifecycle events (promoted proposal):** extracted -> validated -> pending_approval (service `ingestion`) -> approved (actor kind `human`, id `TEST-REVIEWER-simulated`) -> promoted (service `promotion`).

## E. Governance
* **Authentication:** development profile = unauthenticated loopback admin (by design, with warnings); production profile verified: 401 without a token, scope derived from the advisor's clients.
* **Client isolation:** verified at API level on shipped clients and at store level for Maya/Arjun data. Cross-client probes returned the same "not found" style answers (404) or 403 for `/api/ask`.
* **Promotion controls (staged only):** verified as listed in Test 10. **Does production memory require the gate?** Only in a store latched to staged mode (`CMF_MEMORY_FORMATION_MODE=staged`). The running app and the default are **direct mode**, where extraction writes memories immediately (Tests 2-7 ran that way). So M2 is **not a universally enforced approval boundary**.
* **Direct vs staged:** direct for Tests 2-7; staged for Test 10. Staged mode cannot be enabled or inspected from the UI/API.
* **Bypasses found:** none for the stated gates; raw SQL and non-promotion identities were refused. Caveat D10 below.

## F. Limitations
* UI rendering, the brain map, dossiers and the Ask CMF screen were **not viewed or clicked**; only their HTTP endpoints were called.
* Ingestion, Maya/Arjun, and M2 staged tests are **BACKEND-only**; the app exposes no way to feed them in.
* The real LLM was a **3B local model**; extractor and verifier are the same model, so agreement is weak evidence. No mock was used for these results. No OpenAI-based test was possible (no key).
* Single run per scenario; LLM output is non-deterministic, so none of this is a stable benchmark; recall was not measured (Arjun's budget statement was dropped: `no recognisable topic`, D7).
* The approval in Test 10 is a simulated test Principal. **Nothing here required or received a human approval.**
* Not tested: file/multichannel upload (no UI), report-release approvals, memory lifecycle/conflict endpoints, mobile layout.

## Defects and observations (reproduction notes; no fix attempted)
| # | Component | Expected / Actual / Repro |
|---|---|---|
| D1 | App scope | No ingestion path and fixed client directory: Maya/Arjun cannot be tested in the app. Repro: `POST /api/ask` for `CLIENT-901` -> "client not identified" |
| D2 | Memory topics / consolidation | Corrections need a slot to supersede. Maya budget has no topic; episodes stored with `value_text` NULL; no semantic version, no supersession, `current`=0. Repro: ingest the two budget meetings (`maya_backend.py direct`) and read `memory_records` |
| D3 | llm_first extraction | The client's own sentence was rewritten as an advisor sentence (`Maya, we should plan around...`) and attributed Advisor; the client turn is only supporting evidence. Fidelity problem (the M1 policy then correctly capped it at unconfirmed) |
| D4 | Extraction labelling | "comfortable taking higher risks for a small portion" stored as `decision`/`confirmed` |
| D5 | **Staged validation** | The client's correction (₹63, "not ₹65") and the higher-risk exception were **rejected** with `unsupported_inference` ("part of the claim is not found in any single source sentence" / "clauses joined differently"). Over-strict lexical support gate: legitimate client statements cannot be promoted, only the advisor-reported item and verbatim preferences passed |
| D6 | Tool Selector / Report | `get_source_evidence` called with `search_text` combined with other selectors (3 errors); Kenji report produced no findings despite retrieved memory |
| D7 | Extraction recall | Arjun's `My investment budget is ₹20 lakh.` rejected as "no recognisable topic" |
| D8 | Planner | Generic request labelled topic `family_support` |
| D9 | Side effect | The running app created a new git-ignored file `build/cmf_approvals.db` (approval store). Existing build/seed hashes unchanged |
| D10 | Audit | A simulated Principal is recorded in the audit as `actor_kind human`; the database cannot tell a test identity from a person (the id string is the only marker) |

## Final safety audit
Branch `feature/llm-first-ingestion`, HEAD `8081eaa`; 0 staged; git status identical to the pre-state; no commit/push/merge/rebase/reset/stash/clean/branch switch; original repo and sibling worktree untouched. The three `data/seed` databases and the existing `build/cmf_memory.db` are byte-identical; seed memory DB still `user_version` 123 (migration to 130 ran only on the scratchpad copy). One new untracked, git-ignored file: `build/cmf_approvals.db`; `ui/cmf-brain-ui/node_modules/` (ignored). No `.env`/`.venv` created. Processes left running: backend `127.0.0.1:8000` and frontend `localhost:5174` (stop them when finished); the production-profile instance on 8001 was stopped.

## G. Recommendation
**Needs targeted fixes before further testing.** The app starts and the M1 attribution behaviour holds with a real model (Tests 3-5, advisor attribution in 4 and 10), isolation works, and the staged gates hold. But: a client correction cannot supersede anything (D2), staged mode rejects genuine corrections (D5), Maya-style testing needs an ingestion path (D1), and the final answer quality on the 3B model is poor (D6). Suggested order, each needing separate authorisation: (1) topic/slot for budget plus correction linkage; (2) relax or rework the staged support gate for corrections; (3) a supported way to load a synthetic client for UI testing; (4) re-run with a stronger model.
