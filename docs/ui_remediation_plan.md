# Remediation plan for the defects found in the real-model end-to-end test

Worktree `...\Cognitive Memory Fabric - Bhavya Baseline`, branch `feature/llm-first-ingestion`, HEAD `8081eaa`, nothing staged. **This document is a plan only. No source, test, schema, migration, configuration or database was changed to produce it.** Investigation used read-only inspection of the code, the saved traces from `docs/ui_end_to_end_test_report.md`, read-only (`mode=ro`) queries on scratchpad database copies, and pure-function replays of `support.claim_supported` and `rules.*`. Nothing here is human semantic ground truth: every finding below is a deterministic replay or a code reading, and the end-to-end outputs came from a single run of a 3B model.

## 0. Summary of root causes

| # | Defect | Root cause in one line |
|---|---|---|
| D1 | Simulated reviewer recorded as human | `staging.py` writes the constant `"human"` for every approve/reject/expire, and `Principal` has no identity-source field, so "authenticated test principal" and "verified person" are indistinguishable. |
| D2 | No budget slot / current value / supersession | The extractor returned **no value** for every Maya budget statement; there is no deterministic value fallback outside two numeric topics; the slot subject was the coarse `investment_trade`; so consolidation (which needs a value) never ran. |
| D3 | Client's own statement stored as advisor-sourced | `llm_first.fact_check` **re-anchored** the client's declarative sentence onto the advisor's earlier *question* because the model's advisor-voiced statement overlapped that question more, and then switched `speaker_role` to Advisor. |
| D5 | Staged validation rejected the client's correction and risk exception | The staged check (`staging.Proposals.checks`) turns `support.claim_supported` into a **hard reject**; in direct mode the same function only sets a `verification` label. It fails on first-person→third-person paraphrase and on a correction cue and its value living in two different sentences. |
| D6 | Kenji report had no key findings, 3 tool errors, 404 s | (a) Findings were **demoted by the report authority guard** because 365 of 365 episodic memories in the shipped seed are `unverified`; (b) the tool-selector model combined `search_text` with other selectors, which the service forbids; (c) latency is CPU inference of three LLM stages. |
| D4 | No synthetic clients | `Conversations.__init__` defaults to the constant `CSV_PATH`, `shared()` is the single factory, and nothing allows a test-only override. |

D2, D3, D5 and D6 share one thread: **a memory without an extracted value can never reach `verified`**, and `verified` is what the report agent and the staged validator need.

## 1. D1. Approval identity

**Trace (approval request to audit row).**
1. `api/main.py:544,574` builds `Principal.user(actor, scopes, authenticated=bool(...))` from an `AuthContext`; `AuthContext.source` (`signed-token`, `test`, `dev-anonymous`, `header-asserted`, `local-cli`, `src/harness/permission_gate.py:47-62`) is **dropped** at this point. `harness/evaluation/p15.py:193` and `p151.py:87` also build `Principal.user(..., authenticated=True)` for fixtures.
2. `integrity.Principal` (`integrity.py:103-137`) has `kind` (`user|service`), `authenticated`, `label`, but no source. A person and a test fixture are the same object.
3. `staging.Proposals.approve/reject/expire` (`staging.py` around lines 313-334) pass the literal `"human"` as `actor_kind` and `reviewer_kind`; `auth_ref` is `f"{who.kind}:{who.actor_id}:{request_id or 'session'}"`, which records nothing about how identity was established.
4. `sql/130_cmf_memory_proposals.sql`: `memory_proposal_events.actor_kind IN ('human','service')`, `memory_proposal_decisions.reviewer_kind = 'human'`, plus a reserved-name list. These CHECKs guarantee only that a *service name* is not recorded as human; they cannot tell a simulated person from a real one.
5. `staging.approve` has **no role check by default**: `authorize` is an optional callback; the harness `PermissionGate` operation `approve` is not applied. Any in-scope `authenticated=True` user can approve.

**Evidence.** The end-to-end audit for the promoted proposal showed `approved, human, TEST-REVIEWER-simulated` and decision `reviewer_kind human`, `auth_ref user:TEST-REVIEWER-simulated:session`.

**Proposed minimal change.**
* Add `identity_source` to `Principal` (sealed, set only by `Principal.user(..., source=...)`), populated from `AuthContext.source` in `api/main.py`. Map: `signed-token`, `gateway` = **verified_person**; `test` = **simulated**; `local-cli` = **operator** (a local OS user, not an approver); `dev-anonymous`, `header-asserted` = **unverified** (already refused: `authenticated=False`).
* Record it. Two options (decision A below): add nullable `identity_source` columns to events and decisions with `ALTER TABLE ... ADD COLUMN` (migration **131**, no table rebuild, no data rewrite), and change the recorded kind values to `human` | `simulated` | `operator` | `service`.
* Database enforcement by trigger, not CHECK (SQLite cannot alter a CHECK without a table rebuild): `proposal_approved_needs_decision` additionally requires the latest approve decision to have `identity_source IN ('signed-token','gateway')` **unless** the store carries an explicit `allow_simulated` flag that can only be set in a development/test store (one-way like the formation-mode latch is not appropriate here; it must be settable off in tests: decision A).
* Apply the harness role check: `Proposals.approve` calls `PermissionGate.check(auth, "approve", [client])` (supervisor/admin per the existing operation matrix) instead of an optional callback. The `authorize` callback stays for tests.
* Tests and fixtures mint `Principal.user(..., source="test")`; audit rows then say `simulated` and a report cannot count them as human approvals.

**Regression tests.** (1) a `source="test"` approval is recorded as `simulated`, never `human`; (2) in a store without `allow_simulated`, promotion of a simulated-approved proposal is refused by the **trigger** (raw SQL path too); (3) `signed-token` approval records `human` and promotes; (4) `dev-anonymous`/`header-asserted`/`local-cli` cannot approve; (5) a user without the `approve` role is refused; (6) migration 131 on a disposable 130 database is idempotent, additive, preserves existing rows (their `identity_source` NULL is treated as `unverified`, not human); (7) the existing 22 M2 tests still pass after changing their principals to `source="test"` **plus** an explicit allow flag.

**Acceptance.** No approval row can carry `human` unless `identity_source` is a verified-person source; zero rows in a production-profile store with `simulated`; the report generator lists approvals by kind.

**Risks.** Existing 130 rows have no source: they were written only by tests/this session's runs, so treat NULL as unverified. Changing recorded values affects the 22 M2 tests (expected).

## 2. D3. Client statement stored as advisor-sourced (do before D2)

**Root cause.** `llm_first.fact_check` (lines ~226-236) re-anchors when `supports(statement, value, excerpt) < REANCHOR_BELOW (0.6)`. Intended case (comment at line 226): a bare confirmation ("Yes, I think so.") is re-anchored to the earlier sentence that states the content. For T6a the model's statement was advisor-voiced ("Maya, we should plan around an investment budget of ₹65 lakh"), the value was empty (so no value short-circuit in `supports`), and the client's declarative "My investment budget is ₹65 lakh." scored below 0.6 against it. `reanchor()` then scanned the three previous turns and picked the advisor's **question** "What budget should we plan around?" (higher word overlap); `fact_check` then **replaced** `turn_index`, `speaker_role` and `excerpt` with the advisor's (`row, role, excerpt = prev, prev["speaker"], sent`).

**Evidence.** Direct-mode candidate note: "evidence re-anchored to turn 1 (content); turn 2 kept as supporting evidence"; stored memory content "Maya, we should plan around ...", `source_role=Advisor`, `event_type=advisor_interpretation`, evidence `MAYA-T6a#1/Advisor, #2/Client`. Staged proposal excerpt = "What budget should we plan around?". M1 then (correctly) capped it at `unconfirmed`, so this is a **fidelity** defect, not a safety bypass.

**Proposed minimal change** (in `llm_first.py`, the existing function; no new pipeline):
1. Only re-anchor when the cited sentence is a **bare confirmation** (reuse `attribution._AFFIRM` / short-reply test), never when it is a declarative with its own content.
2. Never re-anchor onto a sentence ending in `?` (a question states nothing).
3. Never change the speaker from `Client` to `Advisor` through re-anchoring; if the better match is an advisor sentence, keep the client turn primary and add the advisor sentence only as supporting evidence.
4. Post-check on the extractor output: a client-turn candidate whose statement addresses the client in the second person or uses advisor voice ("we should", "Maya, ...") is flagged in the candidate note and its statement replaced by the cited sentence (verbatim), so the stored text is what was said. Extractor prompt (`llm_first.py` extraction prompt, ~line 60-66) gets one line stating that a statement must be written about the client in the third person and must not be advice.

**Regression tests.** A synthetic two-turn fixture with an advisor *question* then a client declarative (the T6a pair) must store `source_role=Client` with the client sentence as primary evidence; a genuine "Yes, let's do that" confirmation after an advisor proposal must still re-anchor (existing `test_llm_first` cases stay green, unmodified); a question is never re-anchored onto; speaker is never switched Client to Advisor. All with `MockLLM`, plus the real-model scenario re-run as a separate measured check.

**Acceptance.** For the T6a/T6b pair the stored primary evidence is the client turn and `source_role=Client`; zero Client-to-Advisor switches in the existing suite; M1 tests unchanged.

**Risks.** Tightening re-anchoring could lower recall for genuine confirmations; the existing confirmation tests are the guard. The prompt change affects real-model behaviour and needs a measured before/after on the same scenarios.

## 3. D2. Budget slot, current value, supersession

**Chain (each step verified).**
1. *Extraction.* All 8 Maya episodes have `value_text = NULL`. Normalisation is fine (replayed: `normalise_value("investment_trade","₹65 lakh") = "₹65 lakh"`, `value_supported` true), so the **model returned no value**, and `rules.infer_value` (rules.py, deterministic fallback) only handles `retirement_age` and `asset_allocation`. In the shipped seed, 233 of 365 episodic rows carry "no extracted value".
2. *Slot.* The model used subject `investment_trade` for the budget (the prompt asks for a specific subject for non-fixed topics, `llm_first.py:64-66`). Slot key `consolidate._slot` = `topic|subject` = `investment_trade|investment_trade`, which is not a budget slot. There is no budget topic in `agents/planner/topics.py:TOPICS` and no keyword for "budget".
3. *Certainty.* The correction ("I want to correct that...") was stored `confirmed`, not `correction`, and `corrects_value` was not set.
4. *Consolidation.* `consolidate.eligible` requires a value, certainty confirmed/correction, status not unconfirmed, and client-attributable evidence. The 63 episode had no value, so ineligible; the 65 episode was advisor-sourced, so (correctly, M1) ineligible. Result: no semantic version, so no supersession, and `retrieve` `current` is empty.

**Proposed minimal change.**
* **Value.** Add a deterministic fallback in `rules.infer_value` for **money amounts**: when the excerpt has exactly one currency amount (`₹|Rs|$|£|€` + number + optional `lakh|crore|k|m|million`), return it; if two amounts and a correction cue (`not X`, `instead of`, `rather than`) return the one not introduced by the negation and set `corrects_value` to the other. The existing `value_supported` check still applies, so a value is only kept if it is literally in the excerpt.
* **Slot.** No new topic if decision B chooses subject-based slots: keep topic `investment_trade` for budgets? That is semantically off. Recommended: add one topic `investment_budget` to `TOPICS` (+ `TOPIC_KEYWORDS`: budget, invest, allocate, lakh), keep it **non-numeric** (not in `NUMERIC_TOPICS`, value is a short amount phrase) and make it a fixed-subject topic (`FIXED_SUBJECT_TOPICS`) so every budget statement lands in one slot. Touches `planner/topics.py`, `rules.py`, `config.py`, both extraction prompts (they interpolate `TOPICS`), and any test that pins the topic list (none found by grep for `len(TOPICS)`).
* **Correction certainty.** `rules.guard_certainty` already downgrades; add the symmetrical *classification* on client turns: a client sentence with correction wording and a changed value is `correction`. (Downgrade-only rule is preserved: this is applied to the extractor's own `confirmed`/`correction` choice only when both the cue and a differing earlier value exist, never upgrading from exploratory/unconfirmed.)
* Consolidation, `fold`, supersession and retrieval stay unchanged: with a value and a stable slot they already produce `65 -> 63`, as the M2 test 15 demonstrates with scripted data.

**Regression tests.** (1) rule-level: `infer_value` on the two Maya sentences and on negations ("not ₹65 lakh") returns `₹63 lakh`, `corrects_value ₹65 lakh`; no amount returned for sentences with two unrelated amounts and no cue; (2) end-to-end with `MockLLM` returning no value: ingest T6a then T6b (client sentences) => semantic chain v1 65 superseded, v2 63 active, `retrieve` `current` = 63, history keeps 65; (3) an advisor-only 65 statement never creates a semantic version (M1 stays); (4) same-day conflicting values still raise a conflict (existing tests); (5) staged mode: the same two meetings produce proposals that validate (depends on D5), and after approval+promotion consolidation yields the chain.

**Acceptance.** Retrieval for the two-meeting scenario returns `current = 63` with the 65 version present as superseded history and no memory presenting 65 as current; both source turns remain traceable via evidence rows.

**Risks.** Currency parsing is locale-sensitive (lakh/crore vs thousand/million): keep the **text form**, do not convert units. A new topic changes planner/retrieval vocab (backward compatible: old rows keep old topics). Money fallback must not fire on, e.g., account numbers: require a currency marker or an explicit unit word.

## 4. D5. Staged validation rejecting genuine client statements

**Exact reasons (replayed on the stored statements).**
* "Maya corrected the investment budget to ₹63 lakh." against "I want to correct that. My current investment budget is ₹63 lakh, not ₹65 lakh." => `part of the claim is not found in any single source sentence`. The act ("correct") is in source sentence 1, the budget and number in sentence 2; `support._claim_sentence_supported` (support.py:249-278) demands coverage >= 0.75 from **one** sentence.
* "Maya is comfortable taking higher risks for a small portion of her portfolio." against "I am comfortable taking higher risks for a small portion of my portfolio." => `who acts ... differs` (`structure_ok` / `_actors`, support.py:167-225): the claim's actor is `NAME:maya`, the source's is `SELF`. Replays: dropping "Maya" to the first person passes; "Maya's current investment budget is ₹63 lakh" also fails; "Maya prefers conservative investments." passes (the actor logic tolerates a leading name in some forms only).
* Where it is applied: `staging.Proposals.checks` calls `support.claim_supported` and maps failure to `fail` (reject). In **direct** mode the same function is called only in `integrity._verification` (integrity.py:704) to set `verification` = `verified`/`unverified`; accept/reject is decided by the much looser `MemoryWriteValidator._support` (memory_write_validator.py:65-73: value supported, or topic inferred, or 25% word overlap). So staged mode is **stricter than direct mode** by construction. This is an M2 design choice of mine, not Bhavya's behaviour.

**Preserving the distinction between direct, advisor-reported, paraphrase and inference.** Keep the evidence classes; do not loosen what counts as `verified`. Proposed:
1. Split the staged `unsupported_inference` check into graded outcomes recorded in `validation_json`: `quote` (statement is a source sentence), `supported_paraphrase` (current strict pass), `person_shifted_paraphrase` (passes after symmetric first-person/third-person normalisation of **both** sides, same speaker, same value, same negation/hedge), `adjacent_sentence_support` (claim satisfied by two adjacent sentences of the **same turn and speaker**, where one is a bare correction/confirmation cue), `unsupported`. Only `unsupported` rejects; the two new classes pass as `validated` **with a visible warning** that the reviewer sees (they are not `verified`).
2. Implement person normalisation inside `support._actors` (map `SELF` and the client's own name to one actor when the cited speaker is the client), a pure, tested function.
3. Never normalise across speakers: an advisor sentence about "Maya" can never support a client-attributed statement, so advisor-reported stays advisor-reported (M1 unchanged).
4. Better still (cheap): for client-spoken evidence, store the **verbatim cited sentence** as `statement` (display paraphrase kept separately in `details_json`), so the strict gate compares like with like (the D3 fix, step 4, delivers this).

**Regression tests.** The 4 replayed pairs above (pass with the right class and warning); a changed number, dropped negation, hedge removed ("might" -> "will"), changed subject ("my wife") and advisor-spoken source still **fail**; the M2 tests 8-10 stay green unchanged; a new test proves validation never returns `validated` for `unsupported`.

**Acceptance.** All four Maya statements validate in staged mode (first two with the new classes plus warnings); every negative mutation still rejects; staged and direct verdicts for the same input differ only by the documented graded classes.

**Risks.** This is the most safety-sensitive change; the plan adds graded passes with warnings instead of relaxing thresholds. Needs your decision (decision D) on whether the new classes may auto-advance to `pending_approval` or must carry a mandatory reviewer acknowledgement.

## 5. D6. Report agent: empty findings, tool errors, latency

**What the trace shows (`t8k.json`).** Retrieval: 12 memories (1 current, 3 history, 8 episodes) cited. The model **did** write findings: `validation.findings_demoted` lists 11 findings, each with `classes: ["unverified"]`; `summary_replaced`: "authority guard: summary states a value from a non-verified memory"; `claims_total 0`; `key_findings`, `financial_analysis` empty; 38 uncertainties and 12 limitations. Code: `agents/report/agent.py:70-87` (`authority_split`, `_unverified_in`) moves findings that cite non-verified memory out of the findings and replaces the summary. This is the **designed fail-safe**, not a bug.

**Why everything is unverified (upstream cause).** In the shipped memory database (433 `unverified` vs 3 `verified` curated procedures): episodic 365 of 365 unverified (basis counts: 233 "no extracted value to check against the cited source", 105 "part of the claim is not found in any single source sentence", 25 "who acts / negation / how the clauses are joined differs"), semantic 55 of 55 unverified ("no verified episode of the cited turn states this value"). So with this data **no memory-sourced finding can ever be shown**. The same missing-value and strict-paraphrase gaps as D2/D5.

**Tool errors.** `mcp_server/services.py:393-396` raises "search_text cannot be combined with entity_type, entity_id, interaction_id, turn_index or turn_indices". The selector model supplied both in 3 calls, then repeated identical failing calls 9 more times, which the loop rejected (`status rejected`/`duplicate`), ending with `stop_reason no_new_valid_call` after 5 iterations: 20 tool executions, 11 not useful.

**Latency (404 s).** Harness telemetry by agent: tool_selector 225 s (5 LLM calls, 12,248 input tokens), report 141 s (2,928 in, 932 out), planner 38 s; tools themselves took milliseconds. It is CPU inference of a 3B model, not a hang.

**Not established.** I did not capture the report prompt text or the model's raw output (the API response only contains counts and the demotion records), so whether the 11 findings were faithful is **unverified**; the plan below includes capturing it.

**Proposed minimal changes.**
1. No change to the authority guard. Fix the supply of verified evidence (D2, D3, D5) and decide how to treat the **legacy seed** (decision E): a re-verification pass on a *copy* that re-runs the existing verification on stored rows (read-only on the seed).
2. `get_source_evidence`: resolve the conflict by **precedence** instead of failing (when `search_text` is given with exact selectors, prefer the exact selectors and note the ignored field), or tighten the tool schema with `oneOf` so the selector model cannot form the call. Plus a stop rule in `tool_selector` after the first repeated *identical failing* call (currently it burns iterations).
3. Report observability: add the prompt/raw-output hash and the per-finding demotion reason to the harness trace (already partly present) so the next investigation does not need reconstruction.

**Regression tests.** Tool: combined-argument call returns the exact-selector result plus a warning (or is impossible by schema); identical failing call is not repeated more than once. Report: with a verified memory set the same scripted model output yields key findings; with unverified memory it still demotes (guard stays); mixed sets split correctly. Data: after D2/D3/D5 the Maya scenario's current budget memory is `verified`.

**Acceptance.** A run over a store that contains verified memories shows non-empty `key_findings` citing them; zero tool errors from selector argument conflicts across the existing selector test set; tool loop stops after one repeated identical failure. (Latency is not an acceptance item with a 3B CPU model; measure separately with the intended model.)

**Risks.** Precedence changes tool semantics (low, additive). Do not relax the guard to make the demo look better.

## 6. D4. Synthetic clients in an isolated environment

**Root cause.** `services/conversations.py:13` `CSV_PATH`; `Conversations.__init__(path=CSV_PATH)`; `shared()` (lru_cached, line ~174) is the one factory used by planner, memory, store, permission gate, tool selector and API. No environment override exists. `directory()` derives clients and first names from the CSV (advisor greetings in turns 1-3), the advisor id from the data, so a CSV alone makes a client resolvable by planner and gate. The operational tools read the operational DB (`CMF_DATABASE_URL`); a synthetic client has no profile/portfolio rows there, so tool results for it will be empty, not an error (to verify).

**Smallest safe change.**
* Add `CMF_CONVERSATIONS_PATH` read by `shared()` only: **ignored (with a warning) when `HARNESS_PROFILE=production`**, so authorization and the production data path cannot be redirected. Default behaviour unchanged.
* Add `scripts/make_synthetic_clients.py` (new file, not in the app path) that writes a synthetic CSV (Maya Rao, Arjun Mehta; fictional text; `consent_status` set) and a temp memory DB, printing the env lines to export. No ingestion endpoint, no UI change, no new architecture.
* Alternative with **zero source change** (decision F): copy the repository tree to a scratch directory, replace `data/conversations.csv` there, and launch from the copy. Heavier, but never touches this worktree.

**Regression tests.** `shared()` honours the variable in development/test; ignores it in production; a synthetic client is resolvable by the planner; an advisor assigned to Maya cannot read Arjun (the existing gate tests, parametrised on the synthetic CSV); the default `shared()` still returns the original 6 clients.

**Acceptance.** Run backend and frontend with the variable set: `/api/clients` lists Maya and Arjun only; production profile with the variable set still lists the original clients; seed and `build/` hashes unchanged.

**Risks.** `lru_cache` means the variable is read once per process (intended); a mismatch between the CSV and the memory DB `client_id`s gives "unknown client" (fail closed).

## 7. Order and dependencies

1. **D4** synthetic clients (enables every later acceptance test in the real app; independent).
2. **D1** approval identity (independent; migration 131).
3. **D3** re-anchoring/attribution fidelity (prerequisite for correct provenance).
4. **D2** money value + budget slot + correction classification (needs D3 for correct speaker).
5. **D5** graded staged validation (needs D3's verbatim statements and D2's values; otherwise it would validate bad data).
6. **D6** tool-argument precedence and report observability; legacy re-verification on a copy (decision E).
7. Re-run: unit suite in batches, then the Maya scenarios on the real model, in direct **and** staged mode, recording results separately from mock-based tests.

## 8. Decisions that need your approval

* **A. Identity model.** Recorded kinds `human | simulated | operator | service`; whether simulated approvals can ever promote (my recommendation: only in a store carrying an explicit development/test flag, refused by trigger otherwise); acceptance of **migration 131** (additive columns, trigger changes).
* **B. Budget representation.** New topic `investment_budget` (recommended) vs subject-based slots under `investment_trade`.
* **C. Amount format.** Keep the text form ("₹63 lakh"), no unit conversion (recommended) vs a normalised numeric.
* **D. Staged support policy.** Graded outcomes (`person_shifted_paraphrase`, `adjacent_sentence_support`) pass with a reviewer-visible warning, or require explicit reviewer acknowledgement before `pending_approval`.
* **E. Legacy seed.** Re-verify the shipped seed rows on a **copy** (so the report agent can show findings in the demo), or leave the seed as the honest "unverified legacy" example and build verified demo data from new ingestion.
* **F. Synthetic-client mechanism.** Environment override gated off in production (recommended) vs copy-the-tree.
* **G. Real-model evaluation.** The 3B model is both extractor and verifier; decide whether to measure the fixes with a stronger or second model (no key is available now), since agreement of one model with itself is weak evidence.

## 9. Cross-cutting risks and compatibility

* Direct mode stays the default; none of the above makes staged mode universal. The M2 caveats in `docs/m2_staged_memory_report.md` remain true.
* Existing databases: only additive migration 131; old rows keep their values; no legacy rewriting without decision E.
* M1 policy (downgrade-only ceilings, advisor statements never client-confirmed) is not touched; every change above must keep `test_m1_attribution_safety.py` green and unmodified.
* Verification of every fix on the real model remains a *measurement on one small scenario set*, not evidence of general accuracy, and no AI output here is treated as human semantic validation.
* The backend (port 8000) and frontend (port 5174) started for the end-to-end test are still running on disposable database copies; stop them if not needed.
