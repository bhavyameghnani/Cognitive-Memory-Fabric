# Prioritised implementation roadmap on Bhavya's baseline

Derived from `docs/baseline_verification_report.md` and `docs/baseline_architecture_gap_analysis.md`. **Plan only: nothing here is implemented.** Evidence labels as in the report. AI-assisted analysis, not human validation. Ordering principle: first make the baseline reproducible, then close the gaps that **create wrong persistent memory today** (all demonstrable without an API key using scripted models), then provenance, then representation, then new capability.

Every milestone must keep the existing suite as its regression net (**all tests green except the environment-caused ones listed in the report**, then green once M0 removes them), must not weaken the integrity boundary or any approval requirement, and must be proven with deterministic fixtures before any real-LLM run.

## Status (updated after the M0/M1/M2 implementation task)
| Milestone | Status | Evidence | Caveat |
|---|---|---|---|
| M0 reproducible baseline | **Implemented** | `docs/m0_reproducibility_report.md`; temp-DB isolation, session guard, `.gitattributes` | Whole-suite pass count measured before M1/M2 only |
| M1 attribution and finality | **Implemented** | `docs/m1_attribution_safety_report.md`; 45 tests; full suite 1530 passed / 1 failed (pre-existing) / 1 xfailed at the M1 gate | Recall and wording-rule accuracy unmeasured; legacy seed rows not migrated |
| M2 staged formation and promotion | **Implemented as an opt-in mechanism; partially verified** | `docs/m2_staged_memory_report.md`; 22 tests; six sequential batches covering all 48 test files | Staged mode is opt-in (direct stays default); no review API/UI; approval tested only with a simulated Principal; **not** a universally enforced production approval boundary; no single uninterrupted full-suite run after M2 |
M3 onward: not started.

## M0. Reproducible baseline (small; do first)
* **Why.** A fresh checkout fails 73 tests (no `build/cmf_ops_smoke.db`) and the dataset hash lock (CRLF), and the suite creates `build/cmf_memory.db`; without a green baseline no later regression is interpretable. [report 2.3, 2.5]
* **Work.** (1) Documented/automatic creation of the operational smoke database in a temp or session fixture path; (2) `.gitattributes` pinning CSV line endings to LF (the hash locks assume LF), and verifying the lock tests pass on a fresh Windows checkout; (3) stop tests writing the default `build/cmf_memory.db` (set `CMF_MEMORY_DATABASE_URL` in `tests/conftest.py`); (4) minimal lint/format configuration.
* **Files.** `tests/conftest.py`, `.gitattributes`, a bootstrap note in `README.md`, optional `pyproject.toml`. No product code.
* **Acceptance.** A fresh worktree runs `pytest tests` with 0 failures, 0 errors; only the one documented xfail remains; no file appears in `build/`.
* **Risk.** Low. Needs the owner's OK to change test configuration.

## M1. Attribution and finality safety gates (**recommended first feature milestone**)
* **Why.** The only executed findings that create wrong, persistent, retrievable memory: an advisor-only recap becomes an active **confirmed semantic** value (Traces B, D), and "don't treat it as final" passes as `confirmed` (Trace C-5). Smallest change, highest safety value, testable without an API key. [gap G1, G2]
* **Work.** (1) A speaker-conditioned ceiling: advisor-spoken evidence caps certainty at `unconfirmed` unless a client-spoken supporting turn confirms it; (2) `Consolidator.eligible` and `MemoryWriteValidator.approve_semantic` require a client-spoken primary or supporting evidence row for any semantic version; (3) finality/deferral phrases added to the deterministic guards; (4) the semantic row keeps a visible link to the speaker of its evidence.
* **Files (existing, modified).** `src/agents/memory/rules.py` (`guard_certainty`, finality list), `src/agents/memory/consolidate.py` (`eligible`), `src/harness/memory_write_validator.py` (`review`, `approve_semantic`). **New tests.** `tests/test_memory_attribution_gates.py` with fixtures from Traces B, C-5, D plus the legitimate cases that must still pass (client decision; advisor recap **with** a client confirming reply; Kenji 65→63 chain).
* **Dependencies.** M0 recommended. None on later milestones.
* **Acceptance.** Traces B and D produce no semantic version and an `unconfirmed` episodic memory; Trace C-5 is downgraded; Trace A unchanged; the existing 209-test memory/integrity/approval set still passes; recall on his scripted fixtures does not drop below the measured baseline (his `scripts/eval_llm_first.py` **only if** an API key and budget are available; otherwise report as not measured).
* **Risks.** Recall loss where the dataset's only decisive record is an advisor recap; mitigated by the client-confirmation exception and by measuring before enabling by default (feature flag, default on in tests, owner decides for demo databases).
* **Stays disabled until it passes.** Any default change to `CMF_INGEST_MODE`; regeneration of the shipped `cmf_memory_llm_first.db`.

## M2. Formation staging and human approval
* **Why.** Formation writes straight to production; human approval covers only procedural proposals and unconfigured sensitive topics. [gap G3]
* **Work.** Choose route (a) or (b) of G3 with the owner. Route (a): default `approval_sensitive_memory_topics`, plus `CMF_MEMORY_FORMATION_MODE=direct|staged` where `staged` stores episodic rows as `candidate` and requires a role-gated lifecycle action; audit that `candidate` rows can never feed consolidation, retrieval, working memory or reports. Route (b): additive migration for proposals and decisions.
* **Files.** `src/agents/memory/agent.py` (`ingest_meeting`, `update_memory_lifecycle`), `src/agents/memory/ingest.py` (`classify_memory`), `src/agents/memory/config.py`, possibly a new `sql/130_*.sql` + `store._migrate_130`; `api/main.py` only to expose the approve action **if the owner asks** (UI out of scope here).
* **Tests.** staged rows invisible to retrieval/reports; approval requires a verified human principal different from the creator; no automatic activation; lifecycle append-only; existing suite unchanged in `direct` mode.
* **Acceptance.** In `staged` mode no memory becomes retrievable without a recorded human decision; `direct` mode behaviour is bit-for-bit unchanged.
* **Risks.** Largest design decision; touches a security-sensitive boundary. Migration only on copies first, never on seed databases.

## M3. Provenance hardening
* **Why.** Case-insensitive offset-free excerpt matching; no record of how a quote matched; configuration not pinned to the database. [gap G4, G5]
* **Work.** (1) A new excerpt-location function (exact / whitespace-normalised mapped back / unresolved; ambiguity reported; case-only never exact) beside `verify_excerpt`; (2) additive nullable columns for offsets and match kind on `memory_evidence`; (3) a formation-configuration stamp per database (mode, channels, model ids, prompt version, corpus hash) checked at open, with a clear error on mismatch.
* **Files.** `src/services/conversations.py` (new method), `src/agents/memory/store.py` + `sql/13x`, `src/agents/memory/integrity.py` (evidence validation, additive), `src/agents/memory/agent.py` (stamp check). **Tests.** pure-function tests (including whitespace-collapsed and ambiguous cases), migration idempotency on a copy, stamp mismatch refusal, existing evidence tests unchanged.
* **Acceptance.** Every new memory stores offsets or an explicit `unresolved` match; opening `cmf_memory_llm_first.db` without `CMF_MEMORY_CHANNELS=all` fails clearly.
* **Risks.** The excerpt rule must not reject currently valid memories (curly quotes, repaired near-miss excerpts): measure on the shipped seed database **read-only** first.

## M4. Representation: evidence class, confidence relabelling, unresolved interpretations
* **Why.** A numeric `confidence` calibrated on synthetic data can read as truth probability; unresolved interpretations are only note text. [gap G6, G7]
* **Work.** Derive an evidence class from the vote record and speaker; relabel/document `confidence` as a calibration bucket; structured `needs_review` and an `unresolved` candidate status with reason codes; expose in the API/UI **only if the owner asks**.
* **Files.** `src/agents/memory/llm_first.py` (`combine`), `src/agents/memory/ingest.py`, `memory_candidates` columns (additive migration), docs.
* **Acceptance.** No promotion path accepts an `ambiguous`/`inferred` class; documentation states what the number is and is not.

## M5. Use the relationships the sources state; temporal reasoning
* **Why.** Reply/correction relationships and temporal expressions are discarded. [gap G8, G9]
* **Work.** Carry `in_reply_to`/`references`/`reply_to_message_id`/subject into flattened rows and the LLM transcript; let corrections cite a target message and keep claim-level scope; parse temporal expressions as verbatim candidates resolved only against the message timestamp; expiry for time-bound commitments.
* **Files.** `src/services/conversations.py` (`channel_rows`), `src/agents/memory/llm_first.py` (`transcript`, prompts), `consolidate.py` (`_associate`), lifecycle for expiry. **Tests.** adversarial timelines (correction of one item of several, change of mind versus correction, same topic in two contexts), as MockLLM fixtures.
* **Risks.** Prompt changes alter extraction behaviour; any real-LLM re-measurement is a separate, budgeted step.

## M6. Evaluation discipline (starts in parallel with M1)
* **Why.** Current evaluation measures recall and certainty accuracy against an LLM-judged answer key; it does not measure unsupported or misattributed memories. [gap G10]
* **Work.** A deterministic adversarial fixture suite (no API key); a held-out review protocol (exclusion set, frozen order, explicit/contextual/ambiguous/unsupported labels, interval, "AI-assisted, not human-validated" label); an optional real-LLM run when budget allows, reported separately.
* **Files.** `tests/test_memory_adversarial.py`, `scripts/eval_*` additions, `docs/` protocol. **Acceptance.** every fixed failure mode has a regression fixture; reports separate structural from semantic claims.

## Decisions needed from the owner before M2 and M5
1. Should the shipped demo databases continue to be built by direct (operator-run) formation, or must staged approval become the default?
2. Is the structured evidence layer (`src/evidence/`) to be retired from this baseline, kept as a separate analysis tool, or later reconnected to memory? (This document assumes it stays out of the memory path.)
3. Is an API key and budget available for real-LLM re-measurement after M1, M3 and M5?

## Recommended next milestone
**M0 then M1.** M0 makes the baseline reproducible (it is small and unblocks every comparison). M1 closes the only demonstrated route by which an advisor's statement becomes a confirmed, persistent, retrievable semantic memory, entirely inside Bhavya's architecture, with deterministic tests. Do not begin M2 until the owner decides the staging question.
