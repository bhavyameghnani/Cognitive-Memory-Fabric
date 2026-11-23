# Baseline verification report: Bhavya's `feature/llm-first-ingestion`

**Scope.** Audit and execution-based testing only. No source, test, schema, UI or agent code was modified; nothing was committed or pushed; no existing database was ingested into or altered. All execution-based investigation used `:memory:` stores, a temporary operational database in the scratchpad, or read-only queries. This report was written by one AI reviewer (Claude). It is **AI-assisted verification, not human validation**, and it makes no claim of semantic accuracy.

**Evidence labels used throughout.** **[RUN]** = observed output of a command executed in this session. **[CODE]** = read from source, not executed. **[CLAIM]** = stated in Bhavya's documents or reports, not re-measured here. **[UNVERIFIED]** = neither executed nor inspected to the needed depth.

## 1. Repository state
| Item | Value | Basis |
|---|---|---|
| Directory | `C:\Users\Mohor\Documents\Cognitive Memory Fabric - Bhavya Baseline` | [RUN] |
| Branch | `feature/llm-first-ingestion` (tracks `origin/feature/llm-first-ingestion`) | [RUN] |
| HEAD | `8081eaadeedcfd2589383246b60a248f80abfc5f` ("Surface conflicts and the reasoning behind every memory; demo script with screens", 2026-11-22) | [RUN] |
| `git status` | clean (0 entries) before and after the investigation; only git-ignored artefacts appeared (section 2.5) | [RUN] |
| Original repository and sibling worktree | not touched | [RUN] (status and database hashes compared earlier) |

## 2. Test suite
### 2.1 Configuration inspected
`requirements.txt` (pydantic, strands-agents, mcp, jsonschema, pytest, fastapi, uvicorn, httpx); `tests/conftest.py` forces `HARNESS_PROFILE=development`, `CMF_MEMORY_CHANNELS=""`, `CMF_INGEST_MODE=rules` and gives every test its own approval database. There is **no linter or static-analysis configuration** in the repository (no `pyproject.toml`, `setup.cfg`, `tox.ini`, ruff, flake8, mypy or pre-commit file) and no Makefile; none was run, and none was invented. [CODE/RUN]

### 2.2 Command and result (as found, fresh worktree)
```
python -m pytest tests -q -p no:cacheprovider -rfEsx --durations=8
```
**64 failed, 1363 passed, 33 skipped, 1 xfailed, 10 errors in 240.5 s** (exit 1). [RUN]
The suite is **not green in a fresh checkout**. Every failure was classified by cause (2.3); none was fixed, edited around or deselected.

### 2.3 Every failure and its cause
| Group | Count | Cause | Evidence |
|---|---|---|---|
| `tests/test_tool_selector.py` | 26 failed | `build/cmf_ops_smoke.db` does not exist in a fresh worktree (git-ignored; created by `python src/opdata/load_db.py --sqlite build/cmf_ops_smoke.db`); the MCP tools raise `FileNotFoundError` | [RUN] |
| `tests/test_lakeshore_remediation.py` | 18 failed | same | [RUN] |
| `tests/test_mcp_tools.py` | 16 failed | same | [RUN] |
| `tests/test_cmf_data.py` | 10 errors | same: "build\cmf_ops_smoke.db not found; create it with: python src/opdata/load_db.py --sqlite ..." | [RUN] |
| `tests/test_harness_pipeline.py` | 2 failed | same (tool results empty) | [RUN] |
| `tests/test_pipeline.py` | 1 failed | same (`[] != ['instrument', 'issuer', 'sector']`) | [RUN] |
| `tests/test_multichannel_datasets.py::test_multichannel_dataset_validation` | 1 failed | check "3. original Calls data not modified: sha256 differs from the value recorded at build time". The committed file is LF (`git ls-files --eol`: `i/lf w/crlf`) but this machine's global `core.autocrlf=true` wrote CRLF into the new worktree (`data/conversations.csv`: 2,200 extra CR bytes, sha256 `787d70fb...` here versus the committed `fd77a128...`). Environment, not a data change. | [RUN] |
| skipped | 33 | all in `tests/test_semantic_eval.py`: "build/cmf_evidence.db not present" | [RUN] |
| xfail | 1 | `tests/test_e2e_api.py::test_e2e_03_1_...` a documented known residual: a first NAME ("Kenji") still distinguishes an inaccessible client from an unknown word | [RUN] |

**Confirmation of the causes.** The operational database was rebuilt in a **temporary location** (scratchpad, not `build/`) with the repository's own loader, and the seven affected files were re-run with `CMF_DATABASE_URL` pointing at it: **164 passed, 1 failed**; the single failure is the CRLF hash lock. [RUN]
**Pre-existing or introduced by the branch?** The 73 missing-database failures and the CRLF failure are caused by artefacts that are git-ignored or by local git configuration, not by code in the branch's six commits, which changed nothing in those code paths [CODE: `git diff 4dd1307 8081eaa` touches no file under `src/cmf_data`, `src/mcp_server`, `src/opdata`]. I did not run the suite at the parent commit in a clean tree, so "pre-existing" is shown by the cause, not by a before/after comparison. **No test failure was attributable to the new LLM-first or multichannel code**: `tests/test_llm_first.py` and `tests/test_memory_channels.py` pass.

### 2.4 Targeted results (key files, no ops database needed)
`test_llm_first`, `test_memory_channels`, `test_memory_ingest`, `test_memory_walkthroughs`, `test_p14_memory_integrity`, `test_p12_approvals`, `test_evidence_layer`: **209 passed**. [RUN]

### 2.5 Did the tests create or modify persistent databases?
* **Yes, one.** The run created `build/cmf_memory.db` in the worktree (241,664 bytes, schema only: 0 memory records, 0 evidence rows, 0 write-log rows, 1 migration row), at the time the suite started. It is git-ignored and empty; it is consistent with the API's lazy `store()` helper (`api/main.py`: `MemoryStore()` then `init()` on first use, against `CMF_MEMORY_DATABASE_URL`, default `sqlite:///build/cmf_memory.db`), which API tests call; I did not identify the exact test. This is test pollution of the default development path, not a write to a seed database. [RUN/CODE]
* The three seed databases in `data/seed/` and `data/conversations.csv` have identical hashes before and after. [RUN]
* Approval databases are per-test temporary files (conftest). [CODE]
* Nothing else under the worktree changed (`git status --ignored` shows only `__pycache__` and `build/`). [RUN]

## 3. Parsing architecture
**There are two unrelated parsing pipelines in this branch.** [CODE: grep for imports of the `evidence` package outside `src/evidence/` finds only `scripts/inspect_evidence.py` and `scripts/semantic_eval.py`; `src/agents`, `src/orchestration`, `src/harness` and `api` import nothing from it]

### 3.1 Pipeline A: the structured evidence layer (`src/evidence/`, committed earlier, **unchanged by Bhavya**; not used by memory)
* Channels: Meetings, Calls, Email, Chat (parsers `src/evidence/parsers/{meeting,call,email,chat}.py`) plus CRM derived records (`parsers/crm.py`, kept separate). Common representation: `ParsedContainer`/`ParsedTurn` (`src/evidence/model.py`), stored by `src/evidence/store.py` (schema `sql/200_cmf_evidence_schema.sql`: containers, turns, evidence spans, segments, annotations, relations). Conversation/message/turn ids, source file, native source reference, content hash and parser version are stored; per-turn timestamps and source-stated speakers are kept; original interactions keep role-label attribution flagged `derived_from_role_label` and no per-turn time. [CODE]
* Semantics: `src/evidence/semantics.py` (214 lines, rules v1): keyword-lexicon segments, adjacent-question and correction-cue relations, dialogue-act annotations with a numeric `confidence`, provenance `observed`/`rule_generated`. [CODE]
* Determinism and idempotency: content-hash per container; unchanged containers are skipped; tests `test_parsing_is_deterministic`, `test_a_failing_container_rolls_back_alone_and_is_reported` etc. pass. [RUN: `test_evidence_layer.py` in 2.4; the database-dependent evaluation tests were skipped]
* **Known weaknesses of this layer** (from my earlier audit, `semantic_v2_independent_assessment` on the other branch, not re-measured here): over-fragmented segments, adjacency-based answer links (692 links, many wrong), no abstention (every question gets an answer link), no evidence tiers, v1 methods carry no version marker. [CLAIM from earlier AI-assisted review]
* It is **not consumed** by memory formation, retrieval, the harness or the API. [CODE]

### 3.2 Pipeline B: the multichannel memory adapter (`src/services/conversations.py`, Bhavya's "revised parsing")
* `Conversations` reads `data/conversations.csv`; `MultiChannelConversations` (`channel_rows`) adds the generated meetings (`meeting_turns.csv`), email (`emails.csv`) and chat (`chats.csv`) rows, each flattened to the conversations.csv row shape: `interaction_id` = meeting/thread/chat id (`MTG-`/`THR-`/`CHT-`), `turn_index` = turn/message sequence, `speaker` in {Client, Advisor, "Third party"} derived from the sender-id **prefix** (`_role`), `speaker_id`, `speaker_name`, `utterance` = body text, `interaction_datetime` = the row's own timestamp, `meeting_context` = subject. [CODE/RUN: 209 interactions, 2,526 rows]
* Preserved: text, per-message timestamp (email/chat/new meetings), sender id/name, client, advisor, consent/source type. **Dropped**: `message_id`, `in_reply_to`, `references`, recipients, attachments, email `subject` beyond context, chat `reply_to_message_id`, meeting participant lists, call/meeting metadata files, and every cross-message relationship; CRM records are never read. [RUN: the row carries 13 keys; `emails.csv` has 25 columns]
* Restricted records (`SYN-ACC-RESTRICTED`) are excluded from the new channels (`restricted_records`: `CHT-018`, `INT-121`, `MTG-012`, `THR-012`), which is why 209 interactions are loaded where Pipeline A has 212 containers. **`INT-121` is restricted but is still loaded from `conversations.csv`** (Bhavya documents this gap). [RUN]
* No segmentation, no relation detection, no annotation: structure is recovered later by the LLM reading the whole transcript. Corrections and uncertainty are not parsed; they appear only as model output and regex guards (`rules.py`). [CODE]
* Determinism: the adapter is deterministic (CSV read, stable sort). Idempotency: ingestion is per interaction, recorded in `ingestion_log`; a completed interaction is skipped, and a forced re-run stores 0 new rows (section 4.3). [RUN]
* Source text preservation: the original text stays in the CSV; memory evidence stores a verbatim excerpt, verified by `Conversations.verify_excerpt`, which **normalises case, whitespace and curly quotes** (`norm`), so "WE'VE DECIDED: 63." verifies. No offsets are stored. [RUN]

### 3.3 Concrete weaknesses and missing capabilities
1. The two pipelines are disconnected: the semantic/structural work is not used by memory (and vice versa). [CODE]
2. Pipeline B discards reply/correction relationships the sources state explicitly (email headers, chat `reply_to`). [CODE/RUN]
3. Excerpt verification is case-insensitive, normalised and offset-free; an excerpt can match more than one place. [RUN]
4. Speaker role comes from an id prefix; there is no party registry or attribution status (`derived_from_role_label` is lost). [CODE]
5. Original interactions carry one timestamp per interaction, so every turn of a call has the same time; this is not flagged on the memory. [CODE]
6. The two corpora differ (209 vs 212 interactions) without a reconciliation check. [RUN]

## 4. Memory formation
### 4.1 Workflow [CODE]
`MemoryOrchestrator.ingest_meeting` (`src/agents/memory/agent.py`) → **rules mode** (`ingest.select_salient_turns` → `extract_memory_candidates` + `rule_candidates` → `filter_memory_candidates`) **or `llm_first` mode** (`llm_first.run`: extract from the whole transcript → `fact_check` + `reanchor` → blind `verify` → `combine` (lower label wins, regex hedge veto) → `check_value` → `check_slot`) → optional harness `MemoryWriteValidator.review/apply` (reject or downgrade only) → `ingest.classify_memory` → `persist_decision` (`store.insert_memory` through `MemoryIntegrityBoundary`) → `Consolidator.consolidate_client` (the only writer of semantic memory). Extraction is performed by an LLM (default gpt-4o-mini in llm_first, local Qwen in rules mode); code performs fact checks, certainty guards and consolidation.

### 4.2 Representation, evidence, versions [CODE]
* Types: `memory_records.memory_type` ∈ {episodic, semantic, procedural, working} with detail tables (`sql/100`). Episodic: one memory per accepted candidate (`persist_decision`). Semantic: versioned slot values written by `consolidate.py`. Procedural: curated global procedures from `procedures.json` (approved) and advisor-proposed `candidate` procedures awaiting human approval. Working: `MemoryOrchestrator.store_working` with a TTL (default 4 h), session-scoped, never from conversations.
* Evidence: `memory_evidence` (interaction id, turn index, speaker, verbatim excerpt, role primary/supporting), re-verified at write time by the boundary; semantic versions copy the creating episode's evidence and add supporting rows.
* Temporal: episodic `event_date` = the cited turn's timestamp; semantic `valid_from`/`valid_to`/`version_no`/`supersedes_id`; `validate_chains` checks overlaps. Historical versions are kept (`superseded`), lifecycle events and `memory_write_log` are append-only. No temporal-expression parsing, no validity window on episodic memories, no expiry of planned actions.
* Certainty/confidence: a label (`confirmed`, `correction`, `unconfirmed`, `recommendation`, `exploratory`, `hypothetical`) **and** a numeric `confidence` in [0,1]: in llm_first the measured accuracy of the vote-agreement bucket (0.96, 0.82, 0.86, 0.2, 0.3), in rules mode a fixed table (`rules.confidence_for`), 0.9 for semantic versions. The bucket values come from a 150-meeting synthetic evaluation [CLAIM] and are stored as if they were a property of each memory.
* Duplicates: `content_hash` over (client, type, interaction, turn, topic, subject, value, certainty, excerpt) with a unique index: the same candidate is never stored twice, but the same claim in a different turn or channel is stored as a separate episode and merged only at the semantic slot level (`supports`).
* Contradictions/corrections: `consolidate._associate` (`follows`, `explores`, `corrects` by **value** match within a slot); same-day conflicting confirmed values → a `related_to` "conflict" relationship; a live human correction outranks automatic versions (`_respect_correction` records an open `human_correction_vs_evidence` conflict in `memory_conflicts`); retrieval-time `harness/temporal_conflict.py` reports replacement, contradiction, stale, open exploration (information only).
* Validation/approval: validation exists (code guards + optional write validator + integrity boundary). **Human approval exists only for procedural proposals** and for semantic writes in topics listed in `HarnessConfig.approval_sensitive_memory_topics`, whose default is **empty**. [CODE: `harness/runtime.py:99`, `harness/config.py:102`]
* Automatic creation: **yes, as an operator action.** `python -m orchestration.cli ingest --client ...` (role `ingest`: admin/operator/service) writes episodic and, through consolidation, semantic memories **directly to the production store** with status `active`/`unconfirmed`. There is no shadow/candidate stage and no promotion step. It is not triggered by the API or by a request. [CODE/RUN]

### 4.3 Traced examples (executed on `:memory:` stores with a **scripted** model, `MockLLM`)
Important limitation: the extractor and verifier were scripted (the verifier always answers "confirmed"), so these traces show what **the code** does with given candidates. They say nothing about what a real LLM would propose or how well it votes. Script: scratchpad `trace_bl.py`; full output kept in the scratchpad (`trace_bl_out.txt`).

**Trace A: Kenji, retirement age 65 → 63** (`data/conversations.csv`, llm_first). Source: `INT-001` turn 4 (Client, 2026-01-02) "Honestly, the plan has always been 65." and `INT-090` turn 2 (Client, 2026-05-01) "We've decided: 63." Result: two episodic memories (`certainty=confirmed`, `source_role=Client`, `confidence` 0.82 and 0.96 from the scripted votes, each with a primary evidence row `INT-001#4`/`INT-090#2` and the verbatim excerpt); consolidation created semantic v1 `65` (valid 2026-01-02 → 2026-05-01, superseded) and v2 `63` (valid from 2026-05-01, active, `supersedes_id` = v1); `validate_chains` returned `[]`. Re-running `ingest_meeting("INT-090")` → `skipped`; `force=True` → 0 stored, 0 versions created (idempotent). Note the audit line for `INT-001#4`: `regex=unconfirmed`, i.e. the code's own regex did not see decision wording in "the plan has always been 65", yet the two (scripted) LLM votes made it `confirmed` (bucket `agree_llms`, 0.82). *Verified: mechanics, provenance and versioning work as designed. Not verified: that a real model would behave this way.*

**Trace B: email thread `THR-010` (multichannel)**: client hedge versus advisor recap. Source: turn 3 (Client, 2026-06-01) "I'm leaning toward five installments, but I'll confirm on Wednesday's call. Please don't treat it as final yet." and turn 4 (**Advisor**, 2026-06-03) "your decision to elect five annual installments starting at 63 is recorded." The scripted extractor over-claimed both as `confirmed`. Result: turn 3 was **downgraded to `exploratory`** by the regex hedge veto (`votes: ... regex=exploratory -> exploratory (regex_hedge, confidence 0.3); needs review`): the safeguard works. Turn 4 was **accepted as `confirmed` with `source_role=Advisor`**, stored as an episodic memory, and **consolidation created an active semantic version from it** (`origin=consolidation`, `source_role=System`, `certainty=confirmed`, primary evidence = the advisor's sentence), identical **with and without** the harness write validator. *Finding: an advisor's recap alone can become a confirmed semantic value; the semantic row no longer shows that no client statement supports it.*

**Trace C: bad candidates.** (1) `INT-057` turn 4 (Client) "Maybe down to twenty-five percent.", scripted `confirmed` → downgraded to `exploratory`, confidence 0.3 (hedge veto) and no semantic version. (2) `THR-010` turn 2 (Third party, the CPA) → **rejected** "a third-party message cannot be cited as client memory evidence". (3) A cited sentence not in the turn → **rejected** "excerpt does not exist in the cited turn". (4) A value (`40`) not in the excerpt → **dropped** ("value '40' dropped: not in the excerpt"), memory stored with no value. (5) **Gap:** the sentence "Please don't treat it as final yet." (turn 3), scripted `confirmed`, was **accepted as `confirmed`** (`votes: ... regex=unconfirmed -> confirmed (agree_llms)`), stored with the model-invented subject `x`: the hedge/finality regexes (`rules.has_unconfirmed`, `has_exploratory`) do not recognise "don't treat it as final", so only the (scripted) verifier stood between this sentence and a confirmed memory.

**Trace D: advisor recap as sole evidence.** Scripted candidate on `THR-010` turn 4 (Advisor) with topic `retirement_age`, value `63`, `confirmed`: accepted; an active semantic `retirement_age = 63` was created whose only evidence is "your decision to elect five annual installments starting at 63 is recorded". The code checks only that the value "63" occurs in the excerpt, not that the excerpt states a retirement age; the topic and the "client's agreed retirement_age" judgement come from the model.

### 4.4 Direct answers
| Question | Answer | Basis |
|---|---|---|
| Can advisor statements be confused with client decisions? | **Yes.** `source_role=Advisor` is recorded on the episode but nothing blocks an Advisor-sourced `confirmed` episode from creating a semantic version; the semantic row has `source_role=System`. | [RUN] Traces B, D; [CODE] `consolidate.eligible`, `MemoryWriteValidator.approve_semantic` (no speaker check) |
| Can unsupported inferences become persistent memories? | Partly guarded: invented values are dropped, absent excerpts and third-party citations rejected, hedges downgraded, `_support` rejects unsupported statements in the write validator. Negated-finality phrasing passed the code guards (Trace C-5) and a scripted verifier "confirmed" it. | [RUN] |
| Does validation exist? | Yes (code guards, write validator, integrity boundary, value/slot LLM checks). | [CODE/RUN] |
| Does approval exist? | Only for procedural proposals and optionally for configured sensitive semantic topics (default none). | [CODE] |
| Are historical versions preserved? | Yes: semantic versions superseded with `valid_to`, lifecycle events and write log append-only. | [RUN] Trace A; [CODE] |
| Can memory creation happen automatically? | Yes, by operator command, straight into the production store. | [CODE/RUN] |
| Quality of a real model's extraction | Bhavya reports 84.4% recall and 89.4% certainty accuracy (150 meetings, own answer key, blind LLM judge) and about 6 of ~50 current values wrong across all clients. Not re-measured here (needs an API key and spend). | [CLAIM] |

## 5. Four-agent workflow and harness
* **Pipeline** (`src/orchestration/pipeline.py`): Planner → Memory Orchestrator (retrieve) → Tool Selector → Report Agent, with a shared tracer and `RequestRuntime`; the harness permission gate runs before any model call and again for the validated plan. [CODE]
* **Planner / Tool Selector / Report Agent** are unchanged by Bhavya's six commits (no diff under `src/agents/planner`, `tool_selector`, `report`). [CODE]
* **Memory Agent**: the only component that reads or writes memory (`agents/memory/retrieve.py` for reads; `ingest_meeting` for writes; `store_working` per request). It is not triggered by the API; ingestion is a CLI/operator action. [CODE]
* **Permissions and client scope**: `harness/permission_gate.py` (deny by default; roles; client scope from data, never from the request); the integrity boundary re-checks scope with a sealed `Principal`; retrieval queries are client-scoped. [CODE; tests `test_p0_security`, `test_p14_memory_integrity`, `test_e2e_api` pass: counted in the 1363]
* **Evidence verification**: `harness/evidence_verifier.py` with structured-claim matching re-reads memory evidence and quoted source turns; unverified or contradicted findings are removed. [CODE; tests `test_structured_claims*`, `test_p151_report_verification` pass]
* **Memory write validation**: `MemoryWriteValidator` (fact, speaker, excerpt, topic, support, certainty downgrades; `approve_semantic` re-verifies evidence). It does not check who spoke. [CODE]
* **Approval lifecycle**: durable approval store (`harness/approval_store.py`); report release requires a human approval for sensitive reports; `lifecycle` and `approve` operations restricted to supervisor/admin/operator. Not used for episodic/semantic memory writes by default. [CODE; `test_p12_approvals` passes]
* **Trace/telemetry**: `Tracer` (per-request JSONL), `harness/telemetry.py`, memory audit events after commit, `memory_write_log`. [CODE]
* **Coherence**: with an operational database present, the four-agent pipeline tests pass (164 of 165 in the re-run; the failure is the CRLF hash lock). The system works as a coherent whole **for the memories it has**; it consumes `MemoryContext` and is indifferent to how memories were formed. The structured evidence layer is outside this loop. [RUN/CODE]

## 6. Objectives scorecard
| # | Objective | Classification | Evidence |
|---|---|---|---|
| 1 | Multichannel ingestion | **Implemented and verified** (as two disconnected pipelines) | Memory path: `services/conversations.py:channel_rows/MultiChannelConversations`, `tests/test_memory_channels.py` [RUN pass]; evidence layer: `src/evidence/ingest.py`, `tests/test_evidence_layer.py` [RUN pass] |
| 2 | Common structured representation | **Partially implemented** | Exists only in `src/evidence/model.py`/`sql/200` (unused by memory); the memory path flattens to conversations.csv-shaped rows and drops relations/metadata |
| 3 | Source-level provenance | **Partially implemented** | `memory_evidence` (interaction, turn, speaker, excerpt), `Conversations.verify_excerpt` (case-insensitive, no offsets), `ingestion_log.transcript_hash`; no artifact/source-file hash on a memory; excerpts verified by `tests/test_source_turn_evidence.py` [RUN] |
| 4 | Cross-channel context reconstruction | **Partially implemented** | Only slot-level merging in `consolidate.py`; reply/correction relations of the sources unused; CRM excluded; whole-transcript LLM context within one conversation only |
| 5 | Episodic memory formation | **Implemented and verified (mechanics)** | `ingest.persist_decision`; Traces A–C [RUN]; extraction quality is Bhavya's [CLAIM] |
| 6 | Semantic memory formation | **Implemented; unsafe for advisor-sourced evidence** | `consolidate.fold/_semantic`; versions verified in Trace A; advisor recap alone creates a confirmed version (Traces B, D) |
| 7 | Procedural memory | **Partially implemented** | curated approved procedures (`agent.load_curated_procedures`, `procedures.json`) and advisor-proposed candidates needing approval (`ingest.classify_memory`, `persist_decision`); one statement = one step; no client-specific working agreements |
| 8 | Working memory | **Implemented and verified** | `agent._working/store_working/expire_working`, TTL tests (`test_p143_ttl_boundary.py`, `test_memory_retrieval.py`) [RUN pass] |
| 9 | Contradiction and correction handling | **Partially implemented** | value-level `corrects` and automatic supersession (`consolidate._associate/_semantic`), human-correction precedence + `memory_conflicts` (`_respect_correction`), retrieval-time `harness/temporal_conflict.py`; no message-level target or scope; context separation depends on an LLM slot verdict (`llm_first.check_slot`) |
| 10 | Temporal reasoning | **Partially implemented** | `event_date`, semantic validity windows, `as_of` retrieval, `validate_chains`, stale detection; no temporal-expression parsing, no episodic validity/expiry of plans |
| 11 | Client-level isolation | **Implemented and verified** | `integrity.py` (Principal, scope, guard triggers), `permission_gate.py`, client-scoped `retrieve.py`; `test_p14*`, `test_p0_security`, `test_e2e_api` [RUN pass]; one documented residual (first-name disclosure, xfail) |
| 12 | Evidence-backed memory retrieval | **Implemented and verified** | `retrieve.retrieve_context` + `MemoryItem.evidence`, `harness/evidence_verifier.py`, `test_structured_claims*`, `test_p151_report_verification` [RUN pass] |
| 13 | Safe memory updates and promotion | **Partially implemented / unsafe at the formation edge** | strong write boundary, append-only history, write log; but formation writes straight to production, no candidate stage, human approval not required for episodic/semantic by default |
| 14 | Four-agent integration | **Implemented and verified** | `orchestration/pipeline.py`; `test_pipeline`, `test_harness_pipeline` pass with an operational database present [RUN]; fail without it |

## 7. What this audit did not establish
* **Semantic quality of a real LLM run**: no model was called (no API key; cost); all numbers about recall and certainty accuracy are Bhavya's [CLAIM].
* The blind verifier's behaviour was not tested (scripted).
* The UI and the API's HTTP behaviour beyond what the existing tests cover.
* The baseline suite was not run at the parent commit in a clean tree, so "pre-existing" failures are identified by cause, not by comparison.
* Lint/static checks: none configured; none run.
