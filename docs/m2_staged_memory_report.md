# M2 report: staged memory formation and controlled promotion

Worktree `C:\Users\Mohor\Documents\Cognitive Memory Fabric - Bhavya Baseline`, branch `feature/llm-first-ingestion`, HEAD `8081eaa` (nothing staged, committed or pushed). AI-assisted engineering, not human validation. **Kept distinct throughout: structural correctness, evidence traceability, semantic correctness, human approval, production promotion.** This milestone delivers the lifecycle and its enforcement; it makes **no** claim about semantic accuracy, and no real person approved anything (approval is simulated by an authenticated test Principal).

## 1. Architecture before and after
**Before.** `MemoryOrchestrator.ingest_meeting` → LLM/rules → fact checks → (M1 policy) → `persist_decision` → integrity boundary → **production memory**, immediately, under the `ingestion` identity. Human approval existed only for procedural proposals and optional sensitive semantic topics.

**After (opt-in `staged` mode; `direct` stays the default so nothing existing changes).**
```
LLM/rules -> fact checks + M1 evidence policy -> PROPOSAL (extracted) -> validate -> validated | rejected | unresolved
          -> submit -> pending_approval -> HUMAN approve -> approved -> PROMOTE (identity `promotion`, via the integrity boundary) -> promoted -> production memory
```
Semantic memory is still written **only by consolidation**, from promoted client-attributable episodes (M1); it is never proposed.

## 2. What was built
| Piece | Where |
|---|---|
| Additive migration **130**: `memory_proposals`, `memory_proposal_events` (append-only), `memory_proposal_decisions` (append-only), `memory_formation_mode` (one-way latch). Follows the existing conventions (SQL script + `PRAGMA user_version`, one transaction, idempotent, boundary refuses writes until a store is migrated). | `sql/130_cmf_memory_proposals.sql`, `store.py` (`_migrate`, `MIGRATION = "130"`) |
| 16 canonical **database triggers** for the lifecycle, installed, verified and **fail-closed on every write** exactly like the existing guard triggers (kept as a separate set, so existing guard tests keep their meaning) | `store.py` (`_proposal_trigger_definitions`, `proposal_trigger_mismatches`, `_install_proposal_guards`, `integrity_ready`) |
| State machine shared by Python and SQL | `proposal_states.py` |
| Service: propose, validate (11+1 checks), submit, approve, reject, expire, promote, reads | `staging.py` |
| Boundary rules: new `promotion` service identity; `ingestion` may only propose and is **refused a direct write in staged mode** (`direct_formation_disabled`); public `authenticate()` for identity/scope checks | `integrity.py` |
| Orchestrator: `CMF_MEMORY_FORMATION_MODE=staged` (config `formation_mode`) latches the store and routes kept decisions to proposals | `config.py`, `agent.py` |
| `memory_rows()` factored out of `persist_decision` so direct and staged paths build **byte-identical** rows | `ingest.py` |

## 3. The candidate lifecycle
States: `extracted, validated, rejected, unresolved, pending_approval, approved, promoted, superseded, expired`. Legal transitions (enforced by a database trigger **and** the service; a test keeps them equal): extracted→validated|rejected|unresolved|expired; validated→pending_approval|rejected|unresolved|expired; unresolved→rejected|expired (**no approval path**: new evidence means a new proposal); pending_approval→approved|rejected|expired; approved→promoted|rejected|expired; promoted→superseded|expired; rejected/superseded/expired terminal. Every transition inserts an append-only audit event **first** (a trigger refuses a state change without it). `validated` means "passed the structural checks", never "authorised".

**What a proposal keeps:** client id, memory type, proposed content, source (interaction, turn), the **exact excerpt with offsets computed against the original turn text** (`exact`, `whitespace_normalised`, or `unresolved` with NULL offsets: never invented), every cited evidence row with its own offsets, speaker attribution class, certainty **ceiling**, extraction model/config and notes (calibration confidence stored for audit only), formation time, validation results, unresolved issues, the exact production row a promotion would write, and the target memory id.

## 4. Validation (deterministic; no model; confidence never an input)
Structural: source existence, client ownership, evidence traceability + offsets, speaker attribution (turn speaker equals the stated role and the recomputed attribution class), **certainty ceiling** (M1 policy recomputed from the data), temporal consistency (event date = the cited turn's date, not in the future), memory-type requirements, duplicates (an already-stored memory rejects; another open proposal warns), supersession safety (a proposal may not carry supersession). Semantic-support gate (labelled `semantic`): the statement and value must be supported by the cited text. Advisory: contradiction with an existing current value (warn, shown to the reviewer). Outcomes: any fail → `rejected`; unresolvable offsets → `unresolved`; else `validated`.

## 5. Approval and promotion
* **Approval:** an authenticated **person** (`Principal.user`, authenticated, not a reserved name, in the client's scope) records an `approve` decision bound to the **exact content hash** reviewed; the approver cannot raise certainty (the ceiling was set at extraction). A model, a script, an unauthenticated user, a reserved name, or **any service identity** is refused by the service **and** by database CHECKs (`reviewer_kind = 'human'`, a reserved/`service:`/`model:` blocklist kept equal to `RESERVED_ACTORS` by a test); the creator cannot review. An optional `authorize(principal, proposal)` callback is where a caller applies its role check (the harness `PermissionGate` operation `approve`: supervisor/admin); **wiring that to the API/UI was not done**.
* **Promotion:** only the `promotion` service, only for an `approved` proposal whose latest decision is a human approval of the same content, after **re-running the validation checks**, in **one transaction** (the memory, its evidence, the state change and the audit event commit together or roll back together; tested). Idempotent: a repeat returns the same memory (`created = False`).
* **Advisor-reported decisions** keep `source_role = Advisor`, `attribution = advisor_reported_client`, `event_type = reported_decision`, `certainty = unconfirmed` through approval and promotion; no approval can make them client-confirmed (M1's boundary gate also re-checks at promotion).

## 6. Production-write protection (what stops each bypass)
| Attempt | Stopped by |
|---|---|
| Extraction writes a memory in staged mode | boundary: `direct_formation_disabled` for the `ingestion` identity |
| Any identity inserts a conversation-derived memory without an approved proposal | **database trigger** `proposal_gate_memory_insert` (needs an `approved` proposal for exactly that memory id, content hash and client); raw SQL is also blocked by the existing guard triggers |
| Mark a proposal approved without a decision, skip the audit event, edit or delete a proposal/event/decision, make an illegal transition | database triggers (tested with raw SQL) |
| Leave staged mode | `formation_mode_latch` trigger (one-way; leaving it is offline maintenance) |
| Drop a proposal trigger | `integrity_ready()` fails closed until `init()` restores it (tested) |
| Re-promote | idempotent; one `promoted` event |

## 7. Tests
`tests/test_m2_staged_formation.py`: **22 tests**, all 17 required cases (1 extraction writes nothing; 2 validation writes nothing and approves nothing; 3 unapproved cannot be promoted, also at the database; 4 non-human approval rejected everywhere; 5 unauthorised actor; 6 cross-client access refused identically to "not found"; 7 advisor attribution survives promotion; 8 a person cannot raise an advisor decision, a forged `confirmed` proposal is rejected by validation; 9 negated decision not promotable as final; 10 unsupported claim rejected and unpromotable; 11 exact offsets and the matcher's four outcomes; 12 unresolved evidence stays unresolved; 13 rejected proposals auditable and undeletable; 14 duplicate promotion idempotent; 15 promoted episodes consolidate into a 65→63 chain that keeps v1 superseded; 16 failed promotion rolls back everything and can be retried; 17 direct persistence paths blocked), plus readiness fail-closed, migration additive/idempotent/leaves data alone, an interrupted migration rolling back and rerunning, state machine parity, and the reviewer blocklist parity. All on temporary stores. Existing tests changed: four that pin the migration version (123→130) or the exact trigger set (now also the proposal set).

## 8. Results (what was actually run)
* Targeted: `tests/test_m2_staged_formation.py` **22 passed**; the three integrity files that pin version/trigger sets, 183 passed.
* **No single uninterrupted full-suite run was completed after M2.** The first attempt was killed by the harness because the machine was low on memory (not a test failure). The suite was then run as **six sequential batches of 8 files each (`python -m pytest <8 files> -q -p no:cacheprovider -rfEsx`), covering all 48 test files**:

| Batch | Files | Exit | Result |
|---|---|---|---|
| 1 | `test_api.py` .. | 0 | 241 passed, 1 xfailed |
| 2 | `test_harness_integration.py` .. | 0 | 192 passed |
| 3 | `test_memory_channels.py` .. | 0 | 155 passed |
| 4 | `test_p12_approvals.py` .. `test_p143_report_authority.py` | 1 | 10 failed, 369 passed |
| 5 | `test_p143_slot_identity.py` .. `test_report_agent.py` | 1 | 1 failed, 365 passed |
| 6 | `test_report_feasibility.py` .. | 1 | 1 failed, 219 passed |
| **Initial total** | | | **1541 passed, 12 failed, 1 xfailed, 0 skipped, 0 errors** |

* The 12 failures: **11 stale schema-version assertions** (`user_version == 123`; the schema is now 130): 10 parametrised cases of `test_p141_memory_remediation.py::test_m3_ordinary_callers_cannot_change_the_schema_through_execute_or_query` and `test_p143_slot_identity.py::test_an_existing_p142_database_is_migrated_to_the_canonical_index`; and **1 pre-existing failure** `test_semantic_eval.py::test_committed_package_is_not_stale` (present before M2; untouched).
* The two stale assertions were corrected (123 to 130, nothing else). **Reruns, reported separately:** the two affected files: 87 passed; batch 4: 379 passed (exit 0); batch 5: 366 passed (exit 0). Batch 6 was not rerun, so the pre-existing failure there stands. The 37 other files were not rerun.
* Consequently the best statement is: every test file was executed after M2, and the only known failing test is the pre-existing one; this is **not** a single full-suite pass.

## 9. Limitations and risks
1. **No API/UI/CLI for review.** Approval is a Python service call; nothing exposes it to a reviewer yet, and the harness role check is a caller-supplied callback, not wired.
2. **`staged` is opt-in.** `direct` remains the default, so the shipped demo flows still write directly. The latch makes staging irreversible per store; there is no supported way back.
3. **Existing databases** are migrated (additively) only when `init()` runs on them. Never run it on `data/seed/*.db`; use a working copy. Legacy rows are not re-verified by M2.
4. **Procedural proposals** use the same lifecycle but their content check is only lexical support of one sentence.
5. **The validation checks are mechanisms, not meaning**: lexical support, wording rules (M1) and structure. They do not establish truth; the `contradiction` check is advisory.
6. **Approval auth:** the database records an `auth_ref` string and refuses non-human identity names; it cannot verify authentication. Verification is the Principal seal plus the caller's real authentication.
7. **Promoted/superseded states:** the `promoted → superseded|expired` transitions exist in the machine, but nothing yet moves a proposal when its promoted memory is later superseded by consolidation.
8. Throughput: every ingested statement now needs a human decision in staged mode; no batching or prioritisation exists.

## 10. Files
**Created:** `sql/130_cmf_memory_proposals.sql`, `src/agents/memory/{staging,proposal_states}.py`, `tests/test_m2_staged_formation.py`, `docs/m2_staged_memory_report.md`.
**Modified:** `src/agents/memory/{store,integrity,ingest,config,agent}.py`; tests `test_p14_memory_integrity.py`, `test_p142_final_remediation.py`, `test_p143_trigger_integrity.py`, `test_p141_memory_remediation.py`, `test_p143_slot_identity.py` (version/trigger-set constants only).

## 11. What M2 is not
* Bhavya's direct memory-formation pipeline remains available, and **direct mode is the default**; staged mode is opt-in (`CMF_MEMORY_FORMATION_MODE=staged`) and latches per store.
* There is **no review API or UI**; approval is a Python call.
* Approval has been tested only with a **simulated, authenticated test Principal**; no real person approved anything and no real authentication system is wired in.
* The staged workflow is **not a universally enforced production approval boundary**: it holds for stores latched to staged mode.
* Structural correctness, evidence traceability, semantic correctness, human approval and production promotion remain distinct; nothing here establishes semantic accuracy.
