# CMF harness (`src/harness/`)

The harness is **not a fifth agent**. It is request-scoped infrastructure around the four existing agents (Planner, Memory Orchestrator, Tool Selector,
Report Agent): one budget, one trace, one telemetry, one actor/client scope, plus governance checks that decide whether a memory, a claim or an answer is
allowed to exist. The four agents keep their domain logic and stay independently testable.

Safety invariant: *an inferred preference, uncertain statement, outdated memory or unsupported claim is never silently turned into a verified constraint or
durable fact.* It is enforced in code (write validator, evidence verifier, compression rules), not in a prompt.

## Where it plugs in

```
 API / CLI ──► run_pipeline(harness=, auth=)            one RequestRuntime per request, created next to request_id and Tracer
                  │
   PermissionGate ─┤ ask / read_memory / generate_report (before any model call that could spend budget on a forbidden client)
                  ▼
   Planner ──► Memory Orchestrator ──► Tool Selector ──► Report Agent        (stages follow plan.execution_order)
      │             │ retrieve                │ MCP tools                 │
      │   ContextBudgetController      HarnessToolbox                    │
      │   TemporalConflictDetector     (permission + tool budget +       │
      │   (ownership, demotion,         per-call timeout)                │
      │    token budget)                                                 ▼
      └──────── every model call ───► HarnessLLM ──► Strands Agent ──► CMF LLMClient (Ollama / OpenAI / Mock)
                                      fit/compress · cache · budget (BeforeModelCallEvent) · usage · trace
                                                                          │
                                         EvidenceVerifier ◄───────────────┘ drafted AdvisorReport
                                                │
                              Approval gate (fail closed) ──► released report, or withheld + approval id
                                                │
                       TraceRecorder: ONE jsonl per request (logs/traces/<request_id>.jsonl)
```

Memory *writes* are gated separately: `MemoryWriteValidator.review()` runs inside `MemoryOrchestrator.ingest_meeting` before an episode is stored, and
`approve_semantic()` runs inside `Consolidator._semantic` before a semantic version is created. Semantic memory is still written only by the Consolidator.

## Strands Agents SDK: what is actually used (strands-agents 1.57.1)

| Strands API | Use |
|---|---|
| `strands.Agent` | one single-turn Agent per model call (no tools, `retry_strategy=None`, no callbacks) built by `StrandsExecutor` |
| `strands.models.Model` | `CmfStrandsModel.stream()` delegates to the CMF `LLMClient`, so Ollama/OpenAI/Mock keep working unchanged |
| `strands.plugins.Plugin` + `@hook` | `CmfBudgetPlugin` |
| `BeforeModelCallEvent.cancel` | the shared request budget refuses a model call before the provider is contacted |
| `AfterModelCallEvent` | lifecycle completion + stop reason |
| `Agent(..., )(prompt, limits={turns, output_tokens, total_tokens})` | invocation limits: 1 turn, remaining output/total token budget |

Boundary decisions (deliberate):
* CMF agents call `LLMClient.complete()`, which Strands hooks cannot see. `HarnessLLM` is the adapter; with `HARNESS_STRANDS_EXECUTION=true` it routes every call
  through the Strands `Agent` above, otherwise it calls the client directly (same budget code, `direct` transport). Telemetry records which transport ran.
* A cancelled Strands call still returns an assistant message. The plugin therefore also stores the denial on `CallState` and the executor re-raises it, so a
  denial can never be mistaken for model output.
* **Not used:** Strands tool execution (the Tool Selector talks to MCP through its own `Toolbox`; tool budget/permission are enforced by `HarnessToolbox`) and
  Strands middleware (in 1.57.1 the registry is only reachable through the private `agent._middleware_registry`; it is not relied on).
* Strands' `limits` are soft (checked at turn boundaries) and per invocation; `RequestBudget` is the authority and is checked before every call.

## Shared request budget (`budget.py`)
One `RequestBudget` per request, handed to all four agents through the one `HarnessLLM`/`HarnessToolbox`. Limits: LLM calls, retries per request and per
operation, tool calls (total and per agent), tool iterations (→ `SelectorConfig`), input/output/total tokens, runtime deadline (`time.monotonic`), per-tool and
per-model-call timeouts, context size. `BudgetExceeded` subclasses `LLMError`, so every agent's existing fallback applies (heuristic plan, tool fallback,
deterministic report): exhaustion yields a controlled partial result with the reason in `report.limitations`, never a fabricated report. Cache hits do not
consume budget. The `SelectorConfig` limits are kept and derived from the shared caps.

## Token counting, context control, compression
* `TokenCounter`: tiktoken for OpenAI model names *when its vocabulary can be loaded*, otherwise `ceil(utf-8 bytes / 3)` (deliberately over-counts). Every result
  carries `exact`. Provider-reported usage (OpenAI `usage`, Ollama `prompt_eval_count`/`eval_count`) is read through `LLMUsage` via `pop_usage()`, and marked exact.
* `ContextBudgetController.fit()`: per-agent prompt ceiling (system + schema + user), compress at 85 %, fail with `ContextOverflow` if protected content does not fit.
* `compression.py` only **deletes whole lines** in fixed tiers (duplicates → failed tool attempts → old tool results → episodic evidence → superseded history).
  Nothing is rewritten, so negations, dates, amounts, units, certainty tags, client ids and evidence handles that survive are byte-identical. The omission marker lists
  at most four handles; the trace lists all.
* `select_memory()`: drops other-client / unauthorised memories, moves superseded or non-verified items out of the *current* group (never relabels them), ranks by
  certainty, status, recency, relevance, source quality, and enforces `memory_context_tokens`. Included, excluded, demoted are all traced with reasons.

## Prompt caching vs response caching
* **Provider prompt caching** (OpenAI prefix caching) is automatic and cannot be enabled by the harness. Telemetry reports `cached_input_tokens` only when the
  provider's usage block says so (`provider_prompt_cache_reported`).
* **Application response cache** (`cache.py`): key = model, agent, system hash (template version), schema hash, full user-prompt hash, actor, client scope,
  data version. Only validated output is stored (`accept_last()` from `generate_structured`). `tool_selector` is never cached; sampled (temperature > 0) output is
  never cached; authorization is checked before every lookup and never cached; TTL and size bounded; disable with `HARNESS_CACHE_ENABLED=false`.
  `data_version` changes when durable memory or the operational DB changes (working memory is excluded: every ask writes it).

## Evidence verifier: structured claim verification
Free-text findings are no longer compared with evidence by shared words. `harness/claim_extraction.py` reads each finding into **StructuredClaims** (`schemas/claims.py`, one per
quantity) and `harness/claim_matching.py` compares them, deterministically, with the facts of the cited evidence. `EvidenceVerifier(structured=True)` is the default and
`HARNESS_STRUCTURED_CLAIM_VERIFICATION` cannot be switched off in the production profile; `structured=False` keeps the audited lexical check **only** as the evaluation baseline.

**Structured claim** (`None` / `UNSPECIFIED` means "not stated", never a guess): `subject_client` (+ `subject_inferred`), `topic` (canonical, e.g. `retirement_age`, `cash_runway`,
`emergency_fund_target`, `allocation`) and `topic_terms`, `value` (Decimal), `unit` (years, months, weeks, days, percent, USD/INR/EUR/GBP; `unit_implied` when it comes from the topic's
default), `polarity` (POSITIVE/NEGATIVE), `modality` (CONFIRMED, REPORTED, ESTIMATED, RECOMMENDED, HYPOTHETICAL, EXPLORATORY, UNCERTAIN; none = plain assertion), `period` (CURRENT /
HISTORICAL / PLANNED / UNSPECIFIED + explicit dates), `evidence_handle`, `source_id`, `flags`.

**Where the evidence facts come from.** Only from (a) a quotation proven to be a substring of the original source turn (whitespace/quote-style normalised, case-sensitive, a trailing
ellipsis allowed) - the agent-written `description` is never proof, and a fabricated or paraphrased "excerpt" yields `QUOTE_NOT_IN_SOURCE`; (b) the memory record's own fields (topic, value,
certainty, validity), which can only make a fact *weaker or more specific*; (c) typed numeric leaves of a tool result and its arguments, unit read from the key name.

**Decision order** (the first failing dimension names the reason): subject -> unresolved qualifiers (`unless`, `except`, `depending on`, lists of alternatives, unclear negation) -> exact
value and unit -> polarity -> period -> modality.

| dimension | rule | reason / outcome |
|---|---|---|
| client | claim subject = named client, else the report's single client; must equal the evidence's client; unresolved or two clients -> not verified | `CLIENT_MISMATCH` (UNVERIFIED), `AMBIGUOUS_SUBJECT` (AMBIGUOUS) |
| value | exact `Decimal` equality (never substring: 6 != 63, 8% != 80%); only conversion is months <-> years | `VALUE_MISMATCH` (CONTRADICTED when the evidence is firm and current) |
| unit | percent / currency / duration are different families; same figure in different units (12 months vs 12 years) is not a match; a unit that cannot be established is not assumed | `UNIT_MISMATCH` |
| polarity | `not`, `never`, `no longer`, `isn't`, `cannot`, `without` flip polarity; two negations or a negation beside several figures are not guessed | `POLARITY_MISMATCH` (CONTRADICTED), `AMBIGUOUS_EVIDENCE` |
| period | historical vs current/planned must agree; a date or year in the claim must be supported by the evidence (text dates or the memory's validity window); a historical quotation does not verify a current claim unless the memory record is itself the current semantic memory | `TEMPORAL_MISMATCH` |
| modality | a claim may not be firmer than its evidence (`decided` vs `could consider`; `will` vs `would`/`might`/`wants`); a weaker claim than the evidence is AMBIGUOUS | `MODALITY_MISMATCH` |

Outcomes: **VERIFIED**, **UNVERIFIED**, **CONTRADICTED**, **AMBIGUOUS** (`schemas.claims.Outcome`). Report mapping: VERIFIED -> kept; AMBIGUOUS -> kept as `[Qualified: ...]`; UNVERIFIED and
CONTRADICTED -> removed from the body and listed under `uncertainties` with the reason codes. Exploratory / unconfirmed / superseded evidence can never produce more than a *qualified*
finding (`EVIDENCE_NOT_FINAL`). A finding with no checkable value is AMBIGUOUS (`NO_QUANTIFIABLE_CLAIM`) - qualitative statements cannot be verified deterministically.
`validation["harness_evidence"]` exposes `mode`, `counts`, `outcomes` and, per finding, the outcome, reason codes and structured claims (never a quotation or evidence text).

**Provenance of the finding** (`Finding.origin`, additive): `"rendered"` = prose assembled by code from the cited evidence item, `"tool_code"` = figures formatted from a tool result, `None` =
free text from a model. Code-built prose is not re-interpreted as language (its wording is a template): it is checked for whom it names (`Claim.subject` is model-supplied and was never
validated against the evidence's client) and for every figure and date occurring in the verified evidence. `origin` is hidden from the model's JSON schema and stripped from model output.

**What an entailment checker may do.** Optional (`EvidenceVerifier(entailment=...)`; none by default in structured mode). It can downgrade VERIFIED to qualified or reject on `contradicted`;
it can never promote and never override a deterministic failure (tested with a checker that always answers "supported").

**Not guaranteed.** The reader is a lexicon-and-grammar reader, not language understanding: tense is read from `was/were/earlier/until/no longer` and similar (not from every past-tense verb),
a qualitative claim is never VERIFIED, topics outside the small lexicon align only by near-identical wording, and the summary line is still checked by figure/date presence only.
Misreadings cost recall (a true claim shown qualified or withheld), not safety.

Before any text is compared (both modes): the finding cites evidence; the handle exists; its client is in the plan and in the actor's scope; the kind of evidence may support the kind of
statement; memory evidence is re-read from the store (exists, same client, a superseded memory cannot support a "current" statement).

## Memory write validator
* Writes: rejects missing/foreign turns, speaker mismatch, excerpt not in the turn, unknown topic, statements not supported by their excerpt. Downgrades hypothesis
  wording, reported preferences and advisor-side inferences from `confirmed`. Never upgrades. A semantic version can only be created from an active confirmed episode whose evidence
  re-verifies for the same client and whose wording is not a hypothesis/preference/inference. Sensitive topics (`HARNESS_APPROVAL_SENSITIVE_MEMORY_TOPICS`) additionally need a human decision.

## Memory integrity boundary (P1.4)
Every durable memory mutation passes ONE service, `agents/memory/integrity.py` (`MemoryIntegrityBoundary`, reached through the unchanged `MemoryStore` methods). No agent was added and no model is called.

**Durable write paths (inventory, all in `MemoryStore`, database `CMF_MEMORY_DATABASE_URL`, default `build/cmf_memory.db`).**

| Path | Caller | Boundary operation | Identity / scope |
|---|---|---|---|
| episodic + procedural proposals | `ingest.persist_decision` (from `MemoryOrchestrator.ingest_meeting`, API/CLI ingest) | `create` | service `ingestion` (origins `conversation_llm` / `conversation_rule`; types episodic, procedural), scope = client of the verified meeting row, never GLOBAL |
| semantic versions, supersession, relationships, supporting evidence, support counts, conflict records | `Consolidator` | `create` / `transition` / `link` / `support` | service `consolidation` (origin `consolidation`, type semantic; transitions only to `superseded`; **never touches a `human_correction` row**), scope = the one client being consolidated |
| working memory | `MemoryOrchestrator._working` (`pipeline` -> `store_working`) | `create` | service `orchestrator` (origins `orchestrator` / `tool_summary`; type working), scope = the plan's client; tool refs resolve only against THIS request's executions |
| curated procedures | `load_curated_procedures` | `create` | service `curated_loader`, scope GLOBAL only; the row must equal the `procedures.json` entry its evidence names (key, content hash, steps, name, approver) |
| lifecycle (dispute / expire / procedure approval) | `update_memory_lifecycle` <- `POST /api/memories/{id}/lifecycle` | `transition` / `approval` | verified JWT subject (`Principal.user`) whose client scope comes from the permission gate for THAT identity (+ GLOBAL), not from the row; optional `expected_version` (409 when stale) |
| working-memory TTL expiry (also fired on the *read* path in `retrieve`) | `MemoryStore.expire_working` | `transition` | service `ttl_expiry` (client-scoped by retrieval, any_client for the background job; ONLY active `working` rows whose `expires_at` is past THE STORE'S OWN CLOCK - there is no caller-supplied time anywhere - ONLY to `expired`); `expire_working(principal)` needs that Principal; deferred, not failed, on an un-migrated database |
| corrections / explicit supersession | `boundary.correct()`, `boundary.supersede()` | `correct` / `supersede` | authenticated person only (no HTTP route was added) |
| `ingestion_log`, `memory_candidates` | `start/finish_ingestion`, `add_candidate_audit` | not memory content (ingestion audit tables); unguarded |
| `init(reset=True)` | CLI `--reset` | drops the memory tables; **refused on a store that holds memories or audit rows** unless the operator sets `CMF_MEMORY_MAINTENANCE=1` |
| `utils/seed_demo_db.py` | offline tool | drops/recreates the triggers on its own copy, by design; a later `init()` restores any dropped trigger |

**Flow as implemented** (one `BEGIN IMMEDIATE` transaction; nested calls are SAVEPOINTs):
```
caller (rec, details, evidence, Principal - there is no implicit identity)
  -> memory_write_requested (event)
  -> Principal validation + client scope     _principal / _scope / _target     (before the transaction opens and BEFORE any target row is read; 'not yours' == 'does not exist')
  -> BEGIN IMMEDIATE                                                             (writers serialised, check-then-write cannot race)
  -> idempotency replay lookup, then duplicate (client, type, content_hash)      (a replay returns the ORIGINAL outcome; nothing is re-validated or re-written)
  -> record integrity                        structure, protected fields, identities embedded in generated text, referenced ids inside this client, detail rows
  -> provenance validation                   every cited source must RESOLVE in the client's scope; verification is computed here
  -> temporal classification + conflict detection   harness.temporal_conflict.detect + 3 write-time rules; conflicts never block or overwrite
  -> memory_write_validated (event)
  -> persistence with the write gate open     record + detail + evidence + lifecycle event + memory_write_log row, same transaction
  -> COMMIT
  -> memory_write_committed / _corrected / _superseded / _duplicate / conflict events   (fired only after the outermost COMMIT; a rolled-back caller gets memory_write_rolled_back)
rejections: structured reason code -> durable memory_write_log row (own transaction) -> memory_write_rejected | memory_scope_denied event
```
(The order differs slightly from the usual "validate then dedupe": the duplicate check runs first so a harmless re-submission is answered without re-running validation.)

**Database enforcement (defence in depth).** SQLite triggers abort any INSERT / UPDATE / DELETE on the memory content tables, `memory_evidence`, `memory_relationships`, `memory_lifecycle_events` and
`memory_write_log` unless `cmf_write_allowed()` is true. `MemoryStore` registers that function on its connection and the boundary opens it for the writing thread only, so raw `store.execute(...)` cannot write; any other
connection (another process, the `sqlite3` CLI) has no such function and therefore **fails closed**. Memory content columns are immutable even inside the gate (a correction creates a new row and supersedes the old one).
`memory_write_log` is append-only. The store's SQLite **authorizer** refuses schema changes (DROP/ALTER/CREATE TRIGGER|TABLE|INDEX, ATTACH, writable PRAGMA) through `execute()`/`query()` outside trusted maintenance (`init`, migrations).
**Not** protected: someone with write access to the file who drops the triggers, or code that reaches the private `_conn` / `_maintenance()` (the in-process trust boundary); readiness is re-checked on every durable write, so a database whose triggers are missing refuses writes until `init()` restores them.
Un-migrated databases refuse durable writes (`MemoryIntegrityUnavailable`) until `init()` has run (a store opened earlier recovers by itself once another process has migrated); the PostgreSQL path of `MemoryStore` has not been run and `init()` refuses it for this migration.

**Provenance and verification.** Existing vocabulary is reused: `status` already carries superseded / disputed and `certainty` already carries confirmed / unconfirmed. New columns on `memory_records`:
`verification` (`verified` | `unverified`), `verification_basis` (why), `source_class` (`observed_transcript` | `llm_extraction` | `derived` | `curated` | `tool_summary` | `human_correction` | `system` | `legacy`),
`observed_at` (when it happened/was said) vs the existing `created_at` (ingestion time), `temporal_status`, `created_by`, `request_id`, `operation_id`, `row_version`; and `resolution` on `memory_evidence`
(`resolved` | `unresolvable` | `unrecorded`). "Rejected" memories are not stored: they exist as `memory_write_log` rows (`outcome=rejected`, reason codes, actor, client, request).
**What `verified` means (P1.4.2).** `verified` = the cited transcript turn exists and belongs to the client, AND the memory's own claim is supported by it under deterministic matching in `agents/memory/support.py`. It is **never** "the claim is true" and **never** semantic entailment.
The matcher (no model, no network, no substring shortcut): every **sentence of the claim** must be matched by ONE source sentence (>= 75% of its content words AND at most one content word missing, so a long sentence cannot hide appended words; a claim needs at least two content words or numbers); **negation and hedging must agree**
between claim and source; every **number** the claim states and the extracted **value** must occur in that source sentence in a **current** role (not "earlier plan of 65", "weighing 60", "not 63", "65 was the plan" - unless the claim itself is hedged/replaced/negated), in the
same **unit class** (percent / money / months / bare age), and - when the sentence holds other numbers of that unit - next to a word the claim or the corrected subject shares ("Mariko wants to teach until 62" does not support a retirement age of 62). Spelled-out and decimal forms are
compared as numbers (`63` = `63.0` = `sixty-three`). Per class: `observed_transcript` (rule-extracted) - the statement IS one cited sentence and its value passes the number checks; `llm_extraction` - the paraphrase passes the matcher; `derived` (consolidation) - the value is in the source AND a *verified* episode of the
**same turn** states the **same value**; `human_correction` - the corrected value is stated in a cited source sentence as a current value of the same unit next to the corrected subject (otherwise the correction is **rejected**, `correction_value_unsupported`);
`curated` - the row equals the shipped `procedures.json` entry named by its evidence; `tool_summary` / `system` - always `unverified`. No value, no source, an unresolved source or anything the matcher cannot decide => `unverified` (or refused when the memory type requires evidence).
**What it does not guarantee:** that the speaker was right or sincere, that the transcript is accurate, that a faithful paraphrase with little word overlap will be recognised (it stays `unverified`), that wording the matcher has no pattern for (irony, "used to", a past-tense recounting such as "in January you told me 65")
is not mistaken for a current statement, or that a semantic version is the *latest* truth. It is a filter against unrelated, appended, negated and mis-attributed content, not a proof.
**Provenance classes are server-derived.** `source_class` comes from WHO writes (service identity) and the origin that identity is allowed to use - never from a caller-chosen label: only `ingestion` can produce `llm_extraction` / `observed_transcript`, only `consolidation` `derived`,
only `curated_loader` (GLOBAL) `curated`, only a person's `correct()` `human_correction`. A user Principal cannot call `create` at all. A syntactically valid handle that does not resolve for the client is refused. Callers can never set the protected columns.

**Client isolation and identity (P1.4.1).** There is **no implicit identity**: a missing, hand-built, edited (the Principal is sealed with a per-process key), unauthenticated Principal, a person named like a service or reserved word (`system`, `admin`, ...), or a service asked
for something outside its definition is refused *before* any target row is read; an `actor` string proves nothing. `Principal.user(..., authenticated=...)` has no default. Services are limited by operation, origin, memory type, client scope and **target** (see the inventory).
Every reference inside a write (`supersedes_id`, `derived_from_id`, relationship endpoints, related memory, evidence handles, the `interaction_id`/`turn_index` of an episodic detail row, a taken `memory_id`) must resolve **inside the Principal's client**; a foreign id and a missing id give
the **same** code and message (`memory_not_found` / `reference_not_found` / `memory_id_unavailable`), and raw driver errors (UNIQUE / FOREIGN KEY) are translated to `write_conflict`. The lifecycle API answers 404 for a memory outside the caller's scope exactly as for a missing one
(an identity without verified authentication gets 403 before any lookup). Client ids inside generated text are refused (`forged_identity`). Retries are authorized before the idempotency lookup, so a stranger can neither replay nor squat on a key.
Honest limit: code that can call `Principal.user(...)` / `Principal.service(...)` is trusted server code; a Python object cannot be authenticated by the module that receives it.

**Temporal behaviour.** `observed_at` is **derived from the cited transcript turn** (a caller-supplied `event_ts` / `event_date` that disagrees with it is refused: `event_time_mismatch` / `event_date_mismatch`; a memory with no transcript source has no observation time) and is stored separately from ingestion time; ordering never uses ingestion time. `temporal_status` is a conservative text heuristic (`planned` for intention wording, `historical` for completed
events or closed validity, `current` for a confirmed decision, `unknown` otherwise): a plan is never read as having happened, and a completed event is not made current. Conflicts are detected with the existing
`harness.temporal_conflict.detect` (contradiction, unconsolidated update, open exploration, changed over time) plus three write-time rules it cannot see: `contradiction_same_period`, `late_arriving_historical`,
`unverified_vs_verified`. A conflict never blocks, never overwrites and never auto-resolves: both observations are kept; every conflict is a **durable row in `memory_conflicts`** (migration 121; `MemoryStore.conflicts(client)`; append-only, linked to both memories) as well as a `memory_write_log` reason code and an event. Supersession of a semantic
version is still done only by the Consolidator (ordered by observation time, with `valid_to`); explicit supersession/correction require an authenticated person, an UNVERIFIED memory can never replace a VERIFIED one, and explicit supersession needs both observation times and the replacement strictly later (`supersession_chronology` / `supersession_time_unknown`); a replaced window never ends before it began.
**One active version per slot, enforced at the boundary (P1.4.2).** A semantic slot is (client, topic, subject or topic). `create` refuses a second active version (`active_version_exists`) and refuses any automatic write that names a live `human_correction` as `supersedes_id`
or competes with one (`human_correction_protected`); a declared `supersedes_id` that names the live previous version supersedes it in the SAME transaction, before the new row is inserted (chronology and verified-precedence checked first). A partial unique index
(`uq_active_semantic_slot`, migration 122) is the database backstop; on a legacy database that already holds duplicates it is skipped (never fails the migration) and the boundary check still applies.
**Valid time (P1.4.2).** `valid_from` of a semantic/procedural memory is DERIVED from the validated source turn (a different caller value is refused: `valid_time_mismatch`; a correction starts at the later of its evidence date and the corrected version's start); a writer can never supply `valid_to` (`valid_time_not_permitted`); `valid_to` is set only by a trusted supersession/transition and never
precedes `valid_from`; a caller-supplied `valid_to` argument to `set_status` is ignored. `created_at` (persistence), `observed_at` (source turn), `valid_from` / `valid_to` (validity window) stay distinct.
**Chronology (P1.4.2).** ONE rule for `supersede()`, `correct()`, `transition(superseded, related=...)` and a semantic create with `supersedes_id`: the replacement's primary source must be observed strictly later - by (meeting time, then turn order within the SAME meeting); the same turn, equal times in different
meetings, or an unknown time on either side are refused (`supersession_chronology` / `supersession_time_unknown`) and change nothing.
**Conflicts (P1.4.2).** Open conflicts of a client are returned by retrieval (`MemoryContext.conflicts` and, as `review required (...)` lines, `contradictions`, which the Report Agent already turns into uncertainties) for the kinds that need a person: `human_correction_vs_evidence`,
`contradiction_same_period`, `unverified_vs_verified`. A person (never a service) closes one with `boundary.resolve_conflict` / `POST /api/conflicts/{id}/resolve`: `keep`, `dismiss`, or `correct` (a new value that goes through the full `correct()` validation, chronology included). The resolution (who, when, why, decision,
resulting memory) is an append-only row in `memory_conflict_resolutions` (migration 122); conflict rows themselves can never be edited or deleted, a resolved conflict is not re-opened by re-running consolidation, and resolving changes no memory except through `correct()`.
**Existence oracles (P1.4.2).** `GET /api/memories/{id}`, `GET /api/clients/{id}/brain`, the lifecycle and the conflict-resolve routes answer **404 with the same body** for something outside the caller's client scope and for something that does not exist; an unauthenticated caller gets 401 before any lookup.
Store-level: foreign/same-client/missing targets give the same `memory_not_found` / `reference_not_found`. **Not closed:** `create` with a caller-chosen `memory_id` that is already taken (by any client) is refused (`memory_id_unavailable`) while a fresh id succeeds. Only in-process SERVICE principals can create (people cannot) and no API route accepts a caller-chosen id, so no tenant can reach this; it is a property of one global id namespace.
**Human corrections vs consolidation.** A live `human_correction` semantic version has precedence over every automatic version: the Consolidator (a) cannot transition it (`human_correction_protected`), (b) does not rebuild the slot's chain around it, and (c) records newer, *disagreeing* confirmed evidence as an open `human_correction_vs_evidence` conflict - a review requirement - instead of replacing it. A person resolves it with another correction/supersession.

**Idempotency.** Create takes an optional `idempotency_key`; the default key is the memory's `content_hash`. A key is scoped to (client, operation). Same key + same payload -> the original outcome is replayed
(`memory_ingestion_replayed`), including an original *rejection*; same explicit key + different payload -> `idempotency_key_conflict`. Identity/scope refusals never reserve a key. Because the whole mutation, its log row and its events
sit in one `BEGIN IMMEDIATE` transaction, a crash before COMMIT leaves nothing and the retry runs fresh; a crash after COMMIT is answered by the replay. Duplicates are by exact `(client, type, content_hash)`; no fuzzy
merging. The single shared connection is now serialised by a lock (the API shares one `MemoryStore` across threads).

**Corrections.** `boundary.correct(memory_id, principal, expected_version, new_value, evidence, reason)` requires an authenticated person in scope, the version the caller read (`stale_version` / `version_required`), a live
episodic/semantic target, at least one resolvable source, and a reason. The new row goes through the same create validation (`source_class=human_correction`); the old row is marked superseded (never edited or deleted), linked with
`corrects` + `supersedes`, and both lifecycle events record actor, time, reason. Dispute and expiry are logical status changes; **physical deletion is impossible** (trigger) and resurrecting an expired/superseded row is refused.
Concurrent corrections: the second sees a newer `row_version` and is refused.

**Migration 120** (`sql/120_cmf_memory_integrity.sql` + `MemoryStore._migrate_120`). Additive, idempotent, one transaction, recorded in `PRAGMA user_version` (written last; `schema_migrations` is untouched). Adds the columns above,
`memory_write_log`, indexes and the triggers. Existing rows are back-filled with described (not invented) provenance: `source_class` from `origin`, `created_by` from the first lifecycle event, `observed_at` from the episode, and
**`verification='unverified'`** with an explicit "legacy" basis - nothing is marked verified by the migration. Tested on a fresh DB, a legacy DB, a repeated run, and an interrupted run (rolled back, re-runnable).

**Audit events** (`harness/trace` component `memory.integrity`; ids, codes and counts only, never memory content): `memory_write_requested`, `memory_write_validated`, `memory_write_rejected`, `memory_write_committed`,
`memory_write_duplicate`, `memory_write_corrected`, `memory_write_superseded`, `memory_conflict_detected`, `memory_provenance_missing`, `memory_scope_denied`, `memory_ingestion_replayed`, `memory_conflict_resolved` (+ `memory_write_rolled_back` when a caller's transaction
failed after the write). Fields: `request_id`, `actor`, `actor_kind`, `client_id`, `operation_id`, `memory_id`, reason codes. Success events are emitted after COMMIT. Durable counterpart: `memory_write_log`
(attempts that created, duplicated, changed or were rejected; no-op relationship/support repeats are event-only to keep the log bounded). Stable reason codes: `missing_actor`, `client_scope_denied`, `missing_client_scope`, `memory_not_found`,
`operation_not_permitted`, `unauthenticated_actor`, `invalid_record`, `protected_field_supplied`, `forged_identity`, `provenance_missing`, `evidence_unresolved`, `certainty_requires_evidence`, `unverified_cannot_override_verified`,
`stale_version`, `version_required`, `target_not_correctable`, `invalid_transition`, `cross_client_relationship`, `slot_mismatch`, `reason_required`, `idempotency_key_conflict`.

**Limitations.** (1) Verification proves a source resolves in scope, not that a statement is true; the P1.1 claim verifier is still not production-grade and P1.2 findings H1, H2 and M2 are untouched. (2) `temporal_status` and the
tense/intention patterns are heuristics. (3) Tool evidence cannot be independently retrieved by the memory layer; it is recorded as `unresolvable` and such memories stay unverified. (4) Legacy rows are all `unverified`.
(5) Rejection log rows written inside a caller's transaction are lost if that transaction rolls back (the trace event is not). (6) The trigger guard stops application-level and foreign-connection writes, not a hostile file owner.
(7) No HTTP route exposes correct/supersede; they are service-level APIs. (8) SQLite only; the PostgreSQL path is unrun and refused. (9) Retrieval does not yet prefer verified memories - `verification` is recorded, not used for ranking.

## Permissions and identity
CMF does not authenticate people; it **verifies a signed token** minted by whatever does (`harness/auth_tokens.py`, standard library only).
* **The only identity source of the HTTP API** is `Authorization: Bearer <HS256 JWT>` verified against `CMF_AUTH_JWT_SECRET` (>= 32 characters). Actor and role come from the
  verified claims `sub` and `role`; `exp` is mandatory and enforced; `nbf` is honoured; `CMF_AUTH_ISSUER` / `CMF_AUTH_AUDIENCE` are enforced when set. The algorithm must be exactly
  HS256 (`none`/HS512 are refused) and the signature is checked, in constant time, before the payload is trusted.
* **`X-CMF-Actor-Id` / `X-CMF-Actor-Role` are never authentication.** Without a token they get 401 in every profile; with a token they are ignored. (Before this change
  `CMF_AUTH_TRUSTED_GATEWAY=true` made the API believe them, so any caller could claim `role=admin` or `supervisor`.)
* **`CMF_AUTH_TRUSTED_GATEWAY=true`** declares that identities arrive as signed tokens. It requires `CMF_AUTH_JWT_SECRET`; **the API refuses to start without it** (`ConfigError` from the
  ASGI lifespan and from `python -m api.main`). The secret is read by `config.load_auth_secret()` and is deliberately not a field of `HarnessConfig`, so it cannot reach a repr, a dump or a trace.
  Production with no secret at all starts but answers 401 to everything except `/api/health` (and logs an error).
* The role is *not* validated by the token layer: an unknown role in a correctly signed token is denied by the `PermissionGate` (403 `invalid_role`).
* An advisor's clients come from the data (`advisor_id` in `conversations.csv`), never from the request. Missing identity, foreign client, unknown operation, ungranted or unknown tool, and
  tool results owned by another client are all denied. The CLI runs as `AuthContext.local_operator`.
* **Operational assumption that remains:** CMF trusts whoever holds the signing secret. Issue short-lived tokens, rotate the secret, keep it out of source control, and terminate TLS in front of the API
  (a bearer token is a credential in transit). Revocation before `exp` is not implemented.

### Profiles and network exposure
| | production (default) | development / test |
|---|---|---|
| unauthenticated requests | 401 | served as the labelled `dev-anonymous` admin **only for loopback peers** |
| bind address | any | loopback only: `check_bind` fails start-up for `0.0.0.0`, LAN addresses, hostnames, empty; checked from the lifespan (`uvicorn api.main:app`) via `CMF_API_HOST` and from `python -m api.main` |
| start-up | error log if no secret | warning banner on the log and stderr |
| safety-control switches | ignored with a warning | honoured |
| `/docs`, `/redoc`, `/openapi.json` | 404 unless `CMF_API_DOCS_ENABLED=true`, then verified identity required | open |

`.env.example` is the production-safe default; local work uses `.env.development.example`, which selects the development profile explicitly. `HARNESS_ALLOW_ANONYMOUS_DEV`,
`HARNESS_ALLOW_ASSERTED_IDENTITY` and every safety switch are ignored (with a recorded warning) in the production profile.

### Route policy (enforced by `tests/test_p0_security.py::test_every_api_route_except_health_requires_identity_in_production`)
Every `/api/*` route except `GET /api/health` calls `authorize()` **before any lookup**, so unknown ids cannot be probed without credentials (always 401, never a 404, for anonymous callers).
`/api/health` returns only `{"ok": true}`. GLOBAL (curated) memories need a verified identity like any other memory. The lifecycle endpoint records the **verified** actor in the audit trail; the
`actor` field of the request body is accepted for compatibility and ignored.

## Runtime reliability (P1.3): hooks, timeouts, late results, context overflow, partial replies
**Mandatory Strands hooks.** `CmfBudgetPlugin` registers `BeforeModelCall` (charges the budget) and `AfterModelCall` (completion). *Registered is not executed*: each hook leaves proof on `CallState`
(`before_ok`, `strands_after_called`). The model adapter refuses to contact the provider unless the budget hook ran; a hook that raises is caught and **fails closed** (the call is cancelled, `hook_error`
is set); after the run `StrandsExecutor.verify_instrumentation()` requires both hooks, otherwise the text is discarded and `InstrumentationError(kind=hook_missing|hook_failed)` is raised. Before P1.3 a
plugin whose hooks never fired let the call run uncharged and return text. The failure is recorded as an incident, a `harness.reliability` event and an `error` call record, and the request is `degraded`.

**Provider timeouts.** For every call the harness computes `timeout = min(max_model_call_seconds, remaining request time)` and hands the provider a native timeout slightly *shorter* (`timeout - min(0.25 s, 10 %)`)
through the `CALL_TIMEOUT` context var (`llm.client.call_timeout`); `OllamaLLM` and `OpenAILLM` apply `min(own timeout, that)` as their urllib socket timeout (they previously ignored the request budget: 900 s / 120 s),
so a provider that honours it fails *first* with `LLMTimeoutError` and nothing is left running (`enforcement: provider_native`). urllib's timeout is a per-socket-operation limit, not a hard wall-clock cap: for the
non-streaming calls used here it bounds the wait for the response, but it is not a guarantee against a slow trickle. `MockLLM`/unknown clients have no native timeout (`native_timeout` attribute absent).

**Abandonment and late results.** The call runs in a lifecycle-tracked **daemon** worker (a `ThreadPoolExecutor` worker is joined at interpreter exit and could hold the process for the provider's full timeout).
If the deadline passes first, the call is *abandoned*: the request gets `BudgetExceeded("model_call_timeout")`; the estimated input tokens are **charged** (`charge_abandoned`) and the call stays counted, so abandoning
never refunds work; the provider is asked to cancel if it implements `cancel_call(call_id) -> bool` (incident `provider_cancel_ok|failed`, else `provider_cancel_unsupported`); a ghost entry is recorded. **A Python thread
is never killed**: for a provider without cancellation the worker keeps running until the provider returns. If it does, `_late()` records `ghost_call_late_result` (completed / error, tokens in `ghost_*` counters)
and *discards the text*: it cannot change the request's budget counters, telemetry call records, cache or response. An abandoned worker that has not started yet cannot charge the budget or contact the provider.
`outcome: "timeout"` is a distinct call outcome (it used to be recorded as `denied`).

**Context overflow.** Two sources, both `ContextOverflow` (a `BudgetExceeded`, so every agent degrades as before): the local pre-flight (`fit`) and the **provider** (`ContextLengthError`, classified from HTTP 400/413
bodies such as `context_length_exceeded`; an ordinary failure stays an ordinary `LLMError`). Provider overflow gets at most `HARNESS_MAX_CONTEXT_RECOVERIES` (default 1, 0-3) compress-and-retry attempts: the user part is
shrunk to `context_recovery_shrink` (0.6) of its size by the same deletion-only compression (protected lines are never cut); the retry is an ordinary retry (`attempt > 0`), so it also spends `max_retries`,
`max_retries_per_operation`, `max_llm_calls` and the deadline - recoveries are therefore also capped by `max_retries_per_operation` (2 by default). If recovery is impossible (protected content does not fit, no further
reduction, limit reached) the budget records `context_overflow` as exhausted and `ContextOverflow` is raised - never an endless loop, never a silent success.

**Partial replies.** A provider stop reason of `length`/`max_tokens` (`LLMUsage.finish_reason`, read from Ollama `done_reason` / OpenAI `finish_reason`) raises `PartialResponseError`: the (possibly parseable)
text is discarded, never cached, its tokens stay charged, outcome `partial`, incident `partial_response` (degraded). An exception after partial text returns nothing and caches nothing.

**Telemetry.** Incidents (`reliability.INCIDENT_KINDS`, closed vocabulary, structured fields only - agent, attempt, `call_id`, counts, enforcement, cancel result): `provider_timeout`, `provider_cancel_ok|failed|unsupported`,
`ghost_call_late_result`, `context_overflow`, `context_retry`, `partial_response`, `hook_missing`, `hook_failed`, `budget_exhausted`. Each is kept in `Telemetry.incidents` (and in the API's `harness.telemetry.incidents`) and emitted as a
`harness.reliability` trace event; the call record, the `harness.llm call` event and the provider (via `CALL_ID`) share one `call_id` (`<request_id>-cNNN`); trace events carry `request_id`. Error text goes through `safe_error` (class +
short message, bearer tokens / keys / JWT-shaped strings redacted); prompts are still hashed, never stored.
**Request outcome.** `harness.outcome` = `completed` | `degraded` (a degrading incident, an exhausted budget, or a report-agent fallback after a failed model call) | `abandoned` (an exception ended the pipeline; `finish(error=...)`,
event `request_abandoned`, still-running ghost calls reported as `ghost_pending`). Every report carries `validation["harness_runtime"] = {complete, outcome, degraded_reasons}` (also inside the approval draft) and a
limitation naming the causes; a degraded report is never presented as a normal success - it still follows the normal approval policy.
**P1.3.1 hardening (supersedes the corresponding statements above).**
* *Interpreter exit.* `Agent.__call__` runs its event loop in a `ThreadPoolExecutor` thread (`strands._async.run_async`) and `concurrent.futures` joins those threads at exit, as does `asyncio.to_thread`'s default
  executor: a provider call that never returned kept the whole process alive after the request had already timed out. The adapter now drives the public `Agent.invoke_async` with `asyncio.run` in HarnessLLM's daemon worker
  and runs the provider call in its own daemon thread. Nothing that can block forever is registered with an at-exit join; verified with a subprocess test on both transports (the child must exit).
* *Exactly-once charging.* `ModelCallGuard.settle()` charges a call's tokens once whatever path ends it (success, timeout, instrumentation failure). If the provider answered but the completion hook is missing/failed,
  the discarded text is still charged (actual usage, else the estimate) and the call stays counted; the text is never cached or returned.
* *Atomic abandonment.* The `abandoned` flag, the budget charge (`ModelCallGuard.before`) and the provider hand-over (`invoke_inner`) share one per-call lock, so a call is either charged before it was abandoned or refused;
  it never charges afterwards, and the request summary cannot disagree with later telemetry. The ghost record and the worker-slot "abandoned" mark are created inside the same critical section: every abandoned call has
  exactly one ghost record and one terminal outcome (`late_result` / `late_error`; `pending` only while the provider work genuinely still runs).
* *Process-wide worker cap.* Every call holds a slot in `harness.workers.REGISTRY` until its worker AND its provider thread have actually exited (returning to the caller releases nothing). New calls are refused
  before any charge when the process has `HARNESS_MAX_PROVIDER_WORKERS` (default 32) live workers (running + abandoned) or `HARNESS_MAX_ABANDONED_WORKERS` (default 8) abandoned ones still running. The refusal is a
  `worker_capacity_exhausted` incident plus `BudgetExceeded(provider_worker_capacity | abandoned_worker_capacity)` (degraded request). The counters are per PROCESS, shared by all requests and both transports, and appear in
  `harness.workers` of the request summary. The total cap is what bounds abandoned threads strictly (calls admitted at the same moment can all be abandoned together).
* *Cancellation is best effort and never blocks the request.* `cancel_call(call_id)` runs in a bounded pool (`HARNESS_MAX_CANCEL_WORKERS`, default 4) of daemon threads; the request does not wait. Incidents:
  `provider_cancel_started`, then exactly one of `provider_cancel_ok` (the provider ACKNOWLEDGED the request - not proof that its work stopped), `provider_cancel_failed`, `provider_cancel_timeout`
  (`HARNESS_CANCEL_TIMEOUT_SECONDS`, default 2; the hung hook keeps its pool slot until it exits); `provider_cancel_rejected` when the pool is full; `provider_cancel_unsupported` when the provider has no hook.
  `provider_timeout.cancel` is `dispatched | unsupported | rejected | not_needed` - never "cancelled".
* *Outcome vocabulary.* A request refused by the permission gate is `denied` (event `request_finished` with `outcome: denied`, plus `harness.reliability/request_denied`), not `completed`.
* *Approval digest.* `harness_runtime` and the harness-written degradation limitations are part of the report digest, so a degraded answer cannot be released under an approval granted for a complete one. This is NOT a fix
  for P1.2 finding H1: uncertainties, assumptions, follow-ups, validation results, P1.1 verification outcomes and evidence content/provenance are still outside the digest.
**Not guaranteed:** that an abandoned provider call stops (only providers implementing `cancel_call` can be cancelled; the shipped ones rely on their native socket timeout); that a ghost call is not billed by the provider;
that estimated input tokens charged for a timed-out call equal what the provider counted; telemetry for ghost calls that finish after the process exits.

## Human approval (persistent, one-time release - P1.2)
A report for a suitability-type question (`task_type=forecast_feasibility` or should/recommend/suitable/afford... wording) is **withheld** (`released=false`) until a supervisor/admin approves it, and
is then released **once**, to **its requester**. Approvals live in SQLite (`harness/approval_store.py`, schema `sql/110_cmf_approvals_schema.sql`) - they survive a restart. The in-memory store
exists only as `ApprovalStore()` for unit tests; `Harness` always opens the durable one and the production profile refuses `:memory:`.

**Lifecycle.** `pending` -> `approved` | `rejected` -> (approved only) `consumed`; `pending`/`approved` -> `expired` when `expires_at` passes. Every transition is one SQLite transaction (`BEGIN IMMEDIATE`) made of
a *conditional* UPDATE and its audit event; the database also enforces the legal transitions with triggers. Nothing moves backwards.
1. *Request.* The pipeline drafts the report, verifies it (P1.1), and - if the policy requires approval - stores the draft as `pending` and returns only the content-free `withheld_view()`.
   An identical request (same requester, content, client and data version) that is still pending or approved is the same approval (idempotent, so asking again does not multiply requests).
2. *Decision.* `POST /api/approvals/{id}/approve|reject` (or the older `/decision`), supervisor/admin only, reason mandatory (>= 3 characters). A rejected request's payload is deleted.
3. *Release.* `POST /api/approvals/{id}/release`, **the requester only**. Re-checks fingerprint, data version, expiry, requester, the recorded decision and the payload hash; flips `approved -> consumed`,
   clears the payload and writes the `released` event in the same transaction; the report is returned only after the commit. Any later release, or a concurrent one, gets the content-free `already_released` view.
   Asking the same question again after approval but before release releases and consumes in that one step (`require(consume=True)`), so an approval can never be replayed.

**Fingerprint** = SHA-256 over `category, operation, sorted client ids, payload, data_version, requester subject`. For reports the payload is `{question, report_digest}` (digest of summary, findings and evidence refs).
Role and request id are audit metadata, not part of it. A different payload, client, data version or requester gives a different fingerprint, so the old approval does not match. In addition `payload_hash`
(SHA-256 of the exact stored draft) is re-checked at release, so a draft altered in the database is refused.

**Identity rules.** The requester and the approver are the verified JWT `sub` (`AuthContext.actor_id`) - never a display name, a role string or anything in a request body (extra body fields are ignored).
Self-approval is refused on the stable subject (case- and whitespace-insensitive) in the gate, again inside the store transaction, and by a CHECK constraint. The approver must be authenticated (an unauthenticated
development identity can list but cannot decide), non-empty, have role `supervisor`/`admin`, and be in scope. Release requires the requester's own verified token.
Unknown ids and ids the caller may not see answer the same `404 unknown approval` (identity is checked first, so anonymous callers get 401 for everything).

**API** (all routes need a verified Bearer token; production serves nothing else):
| route | who | response |
|---|---|---|
| `GET /api/approvals` | supervisor/admin | pending approvals in scope: `approval_id, category, operation, status, created_at, expires_at, requested_by, client_ids, reason` (no report content, fingerprint or payload) |
| `GET /api/approvals/{id}` | the requester, or a reviewer in scope | `ApprovalRecord.public()` metadata (adds `decided_by, decided_at, decision_reason, consumed_at`) |
| `POST /api/approvals/{id}/approve` `{reason}` / `/reject` `{reason}` / `/decision` `{approve, reason}` | supervisor/admin, not the requester | the updated metadata; `403` self_approval / already_decided / reason_required / approval_expired, `404` unknown |
| `POST /api/approvals/{id}/release` | the requester only | the report (`released: true, status: released`) **once**; otherwise a content-free view with `status` `pending_approval`, `rejected`, `approval_invalidated`, `approval_expired` or `already_released` |
Any approval-store failure answers `503 {"detail": "approval store unavailable"}` and never contains report content, paths or driver text. `/api/ask` returns `503` (not a report) if the store cannot persist a request.

**Expiry.** `HARNESS_APPROVAL_TTL_SECONDS` (default 14400 = 4 h, allowed 60 s .. 30 days) counts from creation and covers both the decision and the release (a decision does not extend it). Expired requests can neither be
decided nor released; they are marked `expired` (event `expired`, actor `system`) and their payload is deleted the next time the store is written to, listed, or started.

**SQLite schema** (`sql/110_cmf_approvals_schema.sql`, version row in `approval_schema_migrations`, idempotent):
* `approval_requests` - `approval_id` (`APR-` + 128 random bits), `request_id`, `requester_subject`, `requester_role`, `client_id` (sorted, comma-joined), `category`, `operation`, `reason`, `report_payload` (JSON of the
  exact draft; NULL after reject/consume/expire and for non-report approvals), `payload_hash`, `data_version`, `fingerprint`, `status`, `created_at`, `expires_at`, `decided_by/role/at`, `decision_reason`, `consumed_at/by`.
  CHECKs: status set, decided_by != requester, a decision needs actor + time + reason, `approved` needs a supervisor/admin role, `consumed` <=> `consumed_at`. Triggers: no DELETE; bound columns immutable; legal status
  transitions only; no decision or consumption after `expires_at`. Indexes on fingerprint, (status, expires_at), requester.
* `approval_events` - append-only (`event_id`, `approval_id` FK, `actor_subject`, `actor_role`, `event_type`, `reason`, `fingerprint`, `created_at`); triggers forbid UPDATE and DELETE; a partial UNIQUE index allows one each of
  `requested/approved/rejected/released/expired` per approval. Refusals are recorded as `release_denied` / `decision_denied` with the reason **code** in `reason`; a refusal is never recorded as a success.

**Restart behaviour.** Nothing is held in memory that matters: pending, approved, rejected, consumed and expired states all come from the file, and a restart never approves or releases anything. If the database is corrupt or
cannot be opened, start-up fails (`ApprovalStoreError`) rather than running without durable approvals.

**What this guarantees:** one approval authorizes one exact report for one requester, one client set and one data version, decided by a different verified supervisor/admin subject, within its validity period, and releases
at most once even under concurrent requests (also across processes sharing the file).
**What it does not:** (a) one human with two different `sub` values (for example a personal and a service account) is **not detected** - there is no cross-identity key, and display names would not help; (b) the database
file is not encrypted and not tamper-proof against someone who can write to it (they can drop the triggers; `payload_hash` and the fingerprint check make a *changed payload* detectable at release, not impossible);
(c) the draft sits in the file in clear text while pending/approved - protect and back up the file like the client database (below); (d) the audit events are not hash-chained; (e) `sensitive_memory_write` approvals
are persistent and expire, but are not consumed on use (that belongs with the write-validator work); (f) `Harness.release(..., actor=None)` is a library call that trusts its caller - the API always passes the verified actor.
The decision is human: this is an authorization mechanism, not evidence that the report is correct (see the P1.1 limitation below).

**Deployment.** `CMF_APPROVAL_DATABASE_URL=sqlite:///path/to/cmf_approvals.db` (default `build/cmf_approvals.db`); put it on a volume with restricted permissions (the store requests mode 0600 where the OS supports it) and
exclude it from world-readable backups. Back it up with SQLite's online backup (`sqlite3 cmf_approvals.db ".backup out.db"`) or copy it while the API is stopped - never copy a live file mid-write. Restoring an old
backup restores old states: an approval consumed after the backup would become releasable again, so restore only with the API stopped and treat anything released since the backup as released.
Run one API process per database file on a local disk (SQLite locking is unreliable on network file systems).

## Configuration
`HARNESS_*` and `CMF_AUTH_*` variables (see `.env.example` for production and `.env.development.example` for local work), validated at start-up (`ConfigError`). `HARNESS_PROFILE=production` (default) ignores any attempt to disable
`permission_gate`, `evidence_verification`, `memory_write_validation`, `approval_required_for_high_risk` or `enabled` and records a warning; `development`/`test` allow it
(used by the ablation runs). A missing variable never weakens a control.
Approvals: `HARNESS_APPROVAL_TTL_SECONDS` (default 14400; 60..2592000) and `CMF_APPROVAL_DATABASE_URL` (`sqlite:///path`, default `build/cmf_approvals.db`; production refuses an in-memory store; anything but SQLite is a `ConfigError`).

## Commands (Windows PowerShell or bash, repo root)
```
python -m pytest tests -q                                           # 284 tests; set PYTHONUTF8=1 if your console codepage is not UTF-8
PYTHONPATH=src python -m api.main                                   # API; production needs CMF_AUTH_JWT_SECRET + a Bearer token, local work uses .env.development.example
PYTHONPATH=src python -m harness.auth_tokens --actor ADVISOR-001 --role advisor    # mint a signed token for testing (secret from CMF_AUTH_JWT_SECRET)
PYTHONPATH=src python -m orchestration.cli ask "Why did Kenji's retirement age change?"
PYTHONPATH=src python -m orchestration.cli ingest --client CLIENT-001      # write validator active
PYTHONPATH=src python -m orchestration.cli trace <request_id>
PYTHONPATH=src python -m harness.evaluation.runner [--configs baseline,harness_full,no_cache] [--out build/harness_eval]
```
PowerShell: set variables with `$env:PYTHONPATH="src"; $env:PYTHONUTF8="1"`.

## Evaluation and optimization (P1.5)
`harness/evaluation/p15.py` evaluates the REAL code paths - the memory integrity boundary, retrieval, the harness pipeline (the four agents), the response cache, the compressor, the approval gate - with scripted models, real CSV excerpts and in-memory SQLite. It extends the existing
`harness.evaluation` package (fixtures, `runner`, `adversarial`, `metrics`); it is not a parallel framework. Everything below is reproduced by one command:

    PYTHONPATH=src python -m harness.evaluation.p15 --out build/p15_eval            # deterministic, no network, no credentials  (writes results.json + report.md)
    PYTHONPATH=src python -m harness.evaluation.p15 --out build/p15_eval --real     # additionally ONE controlled request through the configured model (CMF_LLM_BACKEND, default ollama / qwen2.5:3b)
    python -m pytest tests/test_p15_evaluation.py                                    # pins every number and invariant reported here

**Corpus** (`p15_corpus.py`, version `p15-1`): 70 hand-labelled one-sentence cases - negation 10, wrong-client 8, numeric/unit 16, paraphrase 14, temporal 10, modality 12 (27 should verify, 43 should not); 25 contradiction cases, 3 ambiguity cases; clients CLIENT-001..006
(40 / 9 / 6 / 6 / 5 / 4 cases - Kenji dominates, which is a stated bias). The module imports nothing but `dataclasses`: no label is computed by any verifier. A label says what the sentences MEAN, so a faithful paraphrase is `VERIFIED`-labelled even though a deterministic matcher is expected to miss it - that miss is reported as a false negative, not tuned away.
**Bias disclosure:** the labels were written by the same engineer-agent that built the systems, after reading the P1.4 audit findings; they are NOT a second reviewer's labels, the sentences are synthetic (not transcripts), and 70 cases cannot establish production accuracy.

**Metrics** (per category AND overall): TP/FP/FN/TN; precision = TP/(TP+FP); recall = TP/(TP+FN); F1; false-positive rate = FP/(FP+TN) (an unsupported claim presented as verified); false-negative rate = FN/(FN+TP); unverified rate = share not presented as verified; contradiction-detection rate = share of contradiction-labelled cases NOT verified;
ambiguity-detection rate likewise. Isolation, temporal, cache and compression results are counts of attempts vs successes / checks passed. Four systems are scored on the same labels: `none` (no verification), `memory_p141_baseline` (a reconstruction of the P1.4.1 matcher), `memory_matcher_rules_off` (the CURRENT matcher with the P1.5 / P1.4.3 rules switched off via `support.FLAGS` - it is NOT the matcher as shipped in P1.4.2, whose source was not preserved), `memory_current`, and `report_current`/`report_before_p151` (the P1.1 report-layer verifier).

| system | TP | FP | FN | TN | precision | recall | F1 | FPR | FNR | contradiction det. |
|---|---|---|---|---|---|---|---|---|---|---|
| none (nothing verified) | 27 | 43 | 0 | 0 | 0.386 | 1.000 | 0.557 | 1.000 | 0.000 | 0.00 |
| memory, P1.4.1 matcher (a reconstruction) | 24 | 30 | 3 | 13 | 0.444 | 0.889 | 0.592 | 0.698 | 0.111 | 0.24 |
| memory, current matcher with the P1.5 / P1.4.3 RULES switched off (NOT the shipped P1.4.2) | 19 | 6 | 8 | 37 | 0.760 | 0.704 | 0.731 | 0.140 | 0.296 | 0.84 |
| memory, derived semantic path | 19 | 0 | 8 | 43 | 1.000 | 0.704 | 0.826 | 0.000 | 0.296 | 1.00 |
| **memory, current** | 19 | **0** | 8 | 43 | 1.000 | 0.704 | 0.826 | 0.000 | 0.296 | 1.00 |
| report layer before P1.5.1 (clause check off) | 20 | 6 | 7 | 37 | 0.769 | 0.741 | 0.755 | 0.140 | 0.259 | 0.92 |
| **report layer, current (P1.5.1)** | 18 | **0** | 9 | 43 | 1.000 | 0.667 | 0.800 | 0.000 | 0.333 | 1.00 |

**These are regression measurements on a self-authored corpus, not independent validation, and not production accuracy.** The memory layer and the report layer are different boundaries and are scored separately: memory (current) has FP 0 / FN 8 (missed: d04 d09 d11 d13 e05 e08 f05 f12); the report layer had FP 6 on this corpus before P1.5.1 (monthly vs annual income, three appended-clause paraphrases, "plans" read as "has retired", "unknown whether" read as a decision - audit finding H1) and has FP 0 / FN 9 now (missed: d04 d08 d09 d11 d13 e01 e05 e08 f12). A zero in the memory row says nothing about the report row, and neither says anything about finding qualification or approval release (see the P1.5.1 section).

**Optimizations kept (each measured before/after on the same corpus; the "before" is reproducible through `support.FLAGS`):**
1. *Magnitude words are part of the unit* (`lakh` != `crore`, `thousand` != `million`; `unit_class` returns e.g. `money:million`). Fixed c03/c14-type confusions.
2. *Intent / tense parity* (`support.MODAL`): "plans / wants / proposes / hopes ..." and the past copula "was / were" next to a number must AGREE between claim and source, in both directions (fixes f01, f02, f10, c06, e02, e09: a wish is not a decision, "was 65" is not "is 65").
   An intermediate version (treating "was" as a replaced-value marker) was measured, caused a new false negative (e03) and was REPLACED by the windowed parity rule. The parity is checked in the number's own 4-token window, not sentence-wide (sentence-wide broke the legitimate "decided 63 ... Mariko wants to teach until 62").
3. *`data_version` includes conflicts, their resolutions and every `row_version`* (runtime.py). Defect found by the cache evaluation: recording or resolving a conflict changed no memory row and no lifecycle event, so the version - part of EVERY cache key and EVERY approval fingerprint - did not move: an approval granted before a conflict appeared stayed releasable. Regression: `test_regression_a_recorded_or_resolved_conflict_...`.
4. *Compression keeps caveats longest* (compression.py, tier `episode_critical`): episode lines that carry a human correction, an exploratory/hypothetical/unconfirmed/recommended certainty, a disputed status, or a negated statement are dropped only after plain episodes AND superseded history. See the trade-off below.
5. *A finding that names a client outside the plan is withheld and not echoed* (evidence_verifier.py, structured path): found by end-to-end scenario 2 - "Rebecca decided ..." citing Kenji's evidence was shown as "[Qualified ...]", and later echoed in the uncertainties. It is now unsupported (`CLIENT_MISMATCH`), the uncertainty keeps the reason code but says "text not shown".
**Considered and rejected:** loosening the matcher to recover the 7 paraphrase misses (forbidden: it would trade false negatives for false positives); counting "plan" the noun as an intent marker (breaks "replacing the earlier plan of 65"); protecting plain confirmed financial lines in compression (see below).

**Cache** (`harness/cache.py`, application-level; provider prompt caching is separate and only reported). Key = model | agent | hash(system) | hash(output schema) | hash(user prompt) | actor | sorted client scope | data version | salt. Invalidation: a different key (any component), TTL (default 900 s), LRU bound, and a data-version change. Authorization is never cached: the permission gate runs before every lookup.
Only validated output is stored; the Tool Selector (stateful) is never cached; a cache hit still goes through verification, the approval gate and ownership filters (they run on the pipeline's output, not inside the cache). The 12 requested situations are covered by `evaluate_cache()`: all pass (repeat -> hit; two clients and two actors -> no shared entry; evidence, correction, supersession,
conflict record and conflict resolution -> new version; a different time context -> miss; a hit on a request that needs approval is still withheld; a caller without permission is denied before any lookup; model/system/schema/user prompt each change the key). Working-memory changes deliberately do NOT change the version (per-request scratch that never reaches a prompt).
Repeat-heavy mock workload: 5 requests -> 4 hits / 6 misses (hit rate 0.40) before and after P1.5 (the fixes change invalidation, not hit rate). A hit rate says nothing about correctness. Limits: the cache is in-process and per `Harness`; the version is a coarse "anything changed" string (it invalidates more than strictly necessary, never less); `updated_at` has one-second resolution (row_version covers same-second changes).

**Compression** (`harness/compression.py`): deletion-only - a retained line is byte-identical to the original; protected lines (current knowledge, unresolved items, headers, approval/procedure text, conflict indicators) are never touched; if they do not fit the request fails safely (`ContextOverflow`). Measured on a 41-line / 2601-token prompt corpus (14 hand-tagged critical lines; `evaluate_compression`), position order (P1.4) vs critical-last (P1.5):

| budget | ratio old / new | critical facts kept | negated kept | uncertain kept | corrections kept | critical probe claims still checkable | plain financial lines kept | altered lines |
|---|---|---|---|---|---|---|---|---|
| 85% | 0.819 / 0.819 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 | 1.00 / 1.00 | 0.65 / 0.65 | 0 / 0 |
| 70% | 0.689 / 0.683 | 0.86 / 1.00 | 1.00 / 1.00 | 0.78 / 1.00 | 1.00 / 1.00 | 0.82 / 1.00 | 0.50 / 0.40 | 0 / 0 |
| 55% | 0.539 / 0.523 | 0.43 / 1.00 | 1.00 / 1.00 | 0.33 / 1.00 | 0.00 / 1.00 | 0.27 / 1.00 | 0.50 / 0.10 | 0 / 0 |
| 40% | 0.383 / 0.370 | 0.21 / 0.93 | 0.00 / 1.00 | 0.11 / 0.89 | 0.00 / 1.00 | 0.00 / 0.91 | 0.35 / 0.00 | 0 / 0 |

**The trade-off is real and is a policy choice:** the caveat-keeping order protects corrections, negations and uncertainty, and pays for it with plain confirmed lines (e.g. routine income / holdings read-outs: 35% -> 0% kept at the 40% budget). It was kept because losing a caveat can make the report sound more certain than the memory is, while a dropped plain line only means a fact the report cannot state
(the omitted handles are listed in the trace); no integrity metric regressed (0 altered lines, all protected lines kept, provenance 100%). "Downstream claim verification" here is the share of critical-line probe claims whose cited line survived - it is NOT a model-in-the-loop accuracy. If a deployment values routine figures over caveats, set `compression.CRITICAL_LAST = False`.
Not measured: information loss in free-form text (there is no rewriting, so none occurs), and the token counter is an estimate unless the provider returns usage.
Also observed, NOT changed: the Report Agent's prompt does not carry `source_class` or `verification`, so a human-corrected current value is not labelled as such inside the model's prompt (retrieval, the conflict disclosure and the post-model assembly do carry it).

**End-to-end scenarios** (`run_scenarios`, through `run_pipeline` with the four agents): (1) correct client memory, (2) cross-client contamination, (3) contradictory temporal evidence, (4) human correction + repeated consolidation + conflicting evidence, (5) approval, rejection and cache reuse, (6) budget pressure, (7) conflict resolution. All pass; each result records input, agent sequence,
memory operations (write-log counts), tool operations, evidence references, approval decisions, cache and compression telemetry, final output, expected vs actual. In scenario 4 the correction is made at INT-090 turn 11 before the meeting that states 63 is ingested; the "newer conflicting evidence" is a real later turn (INT-149 turn 3, "In January you told me you'd retire at 65") - which the matcher cannot tell is a recounting.

**Mocked vs real model.** Everything above used scripted models, except the one real run reported at the end of this paragraph: it measures how deterministic controls behave on fixed inputs, not the accuracy of any language model; scripted token figures are tokenizer estimates (`usage_is_exact: false`). `--real` runs ONE request ("Why did Kenji's retirement age change?", CLIENT-001, ADVISOR-001) with the Planner and Report agents on the configured model and the scripted real-excerpt memory;
it never falls back to a mock (a mock backend or an unreachable model produces `ran: false` with the reason). A single request is an existence check, not an accuracy measurement. Memory ingestion (extraction) was NOT run through a real model, and clients CLIENT-002..006 were evaluated only through the scripted/deterministic paths.
**The one real-model run made while implementing P1.5** (2026-11-06, local Ollama, `qwen2.5:3b`, CPU, client CLIENT-001 only, Planner + Report agents; provider-reported usage): released = true, 2 model calls, 93 s end to end, 1768 input / 827 output tokens (exact, from the provider), independent verification of the released findings: 2 verified, 3 qualified, 0 unsupported.
The qualified findings are legitimate qualifications (a superseded value, an exploratory episode). This shows that the pipeline, verifier and approval gate work with a real model; it says nothing about the model's accuracy or about any other client. Real-model coverage of CLIENT-002..006, of memory extraction and of repeated runs is PENDING.

**Known evaluation limitations.** Labels by one author; synthetic single sentences; Kenji over-represented; one-sentence evidence cannot exercise multi-turn context; the corpus was inspected while the rules were being written (the rules were fitted to its failure modes - there is no held-out set); the temporal and isolation checks are scenario assertions, not statistics; latency figures from scripted models say nothing about a real model; the report-layer verifier's 6 false positives remain.

## Known limitations
* **P1.4.2 known limits:** the claim matcher is deterministic text/number matching (see "What `verified` means"): it can mark a past-tense recounting as current and it leaves faithful paraphrases `unverified`; `create` with a taken `memory_id` is distinguishable from a fresh id for in-process services (see "Existence oracles"); the `CMF_MEMORY_MAINTENANCE=1` environment variable is the only authority needed for an offline reset of a populated store, and in-process code can set it; readiness checks trigger NAMES, not bodies; conflict kinds outside the review set stay audit-only.
* **P1.4.1 known limits:** `verified` is lexical/numeric support, not entailment (a faithful LLM paraphrase with little word overlap is stored `unverified`); newer evidence that contradicts a human correction waits for a person (nothing auto-resolves); every write still scans its topic's rows (L6); repeated
  scope-denied attempts each add an audit row (L1, unbounded); the temporal heuristic is still a regex (mixed sentences such as "received a quote and might retire" are read as historical; subject spelling variants are not unified) (L4 partly); PostgreSQL remains refused and unrun; an operator or code with the private connection can still change the schema.
* **P1.4 memory integrity** protects the write boundary, provenance, client scope, history and idempotency of durable memory (see "Memory integrity boundary (P1.4)"); it does not make stored statements true, does not fix P1.2 H1/H2/M2, and does not make the P1.1 verifier production-grade.
* Scripted-model evaluation measures how controls react to fixed model output, not LLM quality; nothing here was run against a live OpenAI/Ollama model.
* Provider usage capture for OpenAI/Ollama is implemented from their documented response fields but was not exercised against a live endpoint; the mock reports no usage (estimates are flagged).
* tiktoken could not load its vocabulary in the development environment (offline TLS failure), so token counts there are estimates.
* Claim verification is deterministic and structured (see above), not semantic entailment: no language model judges a claim. Phrasings outside its lexicon cost recall, never safety.
  Measured on 63 hand-labelled scripted cases (`python -m harness.evaluation.runner`): structured 0 false positives / 0 false negatives; the audited lexical verifier 28 / 4; no verifier 49 / 0.
  The structured result is partly tuned (the lexicon was written while looking at similar phrasings); round 1 on 20 unseen phrasings gave 1 false positive and 1 false negative before two
  extraction fixes, so expect misses on new wording. This is not an evaluation of any live LLM.
* **P1.1 claim verification is NOT production-grade and is not semantic entailment.** An independent audit found plausible wrong claims it marks VERIFIED (tense: "retired" vs "will retire"; bounds: "under 12 months";
  negation verbs it does not know: "ruled out"; the code-annotation shortcut "is not a decision"; subjects that are not directory clients or whose directory lookup fails; tool-leaf matching on one shared word).
  It is conservative in many cases and incomplete in others; do not treat a VERIFIED label as proof, and do not rely on it for client-facing financial decisions without human review. The approval workflow below
  authorizes a *person's decision*; it does not make the report correct.
* Approvals are durable and single-use (see "Human approval") but: one person with two different authenticated subject ids is not detected; the SQLite file is unencrypted and not tamper-proof against someone who can write it;
  audit events are not hash-chained; sensitive-memory-write approvals are not consumed on use; the in-process `Harness.release(actor=None)` library call trusts its caller.
* A timed-out model call abandons its worker (a Python thread cannot be killed); with the shipped providers the native socket timeout ends it, but a provider without a native timeout or `cancel_call` can keep running and be billed. See "Runtime reliability (P1.3)". Strands `limits` are soft.
* Plan `parallelizable_tasks` is not exploited: the Tool Selector consumes the memory summary, so memory and tools run sequentially in plan order.
* Only SQLite was exercised; the harness uses the existing store abstraction and adds no engine-specific SQL beyond `{S}`-prefixed queries.


## Memory integrity remediation (P1.4.3)

P1.4.3 addresses four findings reproduced by an adversarial review of P1.4.2 (F1-F4) and one test gap. **That review was written by the same person who implemented P1.4.2/P1.5; it was adversarial but it was not independent, and P1.4.3 is not independently accepted.** The next acceptance review must be done by a different reviewer.

### F1 - evidence verification is still deterministic matching, not entailment

`agents/memory/support.py` decides whether a memory's statement is supported by ONE cited source excerpt. It is a conservative lexical / numeric / structural matcher. **It is not semantic entailment** and no model can upgrade a result to `verified`. What it checks now, in addition to the P1.4.2 rules (sentence coverage, number role / unit / magnitude, hedge and modal parity):

* **Clause structure** (`structure_ok`): the sentence is split into clauses (`, ` `; ` and / but / or / then / while). Every clause of the claim must be matched, in order, by a clause of the source with the **same actors** (the speaker, a relation noun such as wife / father / colleague / advisor, a pronoun, or a capitalised name), the **same negation, hedge and attribution flags**, the **same connective** (`and` is not `or`), and the numbers the claim states. "I will retire at 63" is not supported by "My wife will retire at 63"; "my wife retires before me" is not supported by "I retire before my wife".
* **Rejection / refusal words** (rejected, declined, refused ...) count as negation; **conditions** (unless, except, otherwise, either) count as hedges; **attribution** (told, recommended, advised, suggested, asked ...) marks hearsay or third-party advice.
* **Retraction anywhere in the excerpt** (just kidding, scratch that, never mind, I changed my mind ...) disables the whole excerpt, even when it is in another sentence.
* Anything the rules cannot decide stays `unverified`. Only (1) a source-backed verbatim sentence and (2) a claim whose every clause is matched by the rules above can be `verified`.
* A derived (consolidated) memory is `verified` only when a **verified** episode of the same turn states the same value; it can never outrank its evidence, across repeated consolidation and reopen.

Measured only on the 73-probe regression corpus (`tests/p143_probes.py`, copied verbatim from the review's artifact, run through the real integrity boundary by `tests/test_p143_memory_integrity.py`): before the change 11 of the 73 were stored `verified` although unsupported; after, 0. The 4 probes whose source does support the claim (B16 "No, I will ...", D10, F6, F8) stay `unverified` and are recorded as known false negatives rather than rescued by loosening the matcher. In the P1.5 70-case corpus one more false negative appears (f05, a *rejected* value is no longer a current value). These numbers describe those corpora only; they are not a general accuracy claim.

### Client-turn context window (`support.SourceContext`)

A client's short reply ("We've decided: 63.") cannot carry the claim "Kenji has decided to retire at 63" on its own: the topic is in the advisor's question and "we" is the client. When the strict single-sentence check has **already failed**, `claim_supported` makes one more attempt with a `SourceContext`, built by `integrity._source_contexts` only when ALL of these hold: the memory is an `llm_extraction`; the cited turn is a **Client** turn; the turn directly before it (same interaction) is an **Advisor** turn that is not a retraction and shares a content stem with the memory's topic/subject. The context lends exactly two things: those topic stems and the client's first name (claim actor `NAME:<client>` is read as `SELF`; any other name still mismatches). The cited sentence itself must still share a content word with the claim, and negation, hedging, modality, attribution, structure and the value/unit/role checks are evaluated on the cited sentence alone. A pass is labelled in `verification_basis` ("... plus the topic named in the advisor turn just before it"). Switch: `support.FLAGS["context_window"]` (off in `p15.shipped_p142()`, so the before/after stays reproducible). Tests: `tests/test_context_verification.py` (positive case, nine negative controls, flag off, seed rows).

### F2 - verification travels with the memory to the report

`MemoryItem` now carries `verification`, `verification_basis`, `source_class`, `temporal_status` (default `unverified`). `EvidenceItem` carries them plus `memory_source_class`, `evidence_class` and `conflict_ids`; provenance (`interaction_id`, `turn_index`, `excerpt`) was already carried. `evidence_class` is one of:

| class | meaning | may support a definitive finding |
|---|---|---|
| `verified` | verified by the boundary, not disputed, not in an open conflict, not exploratory | yes |
| `unverified` | the boundary could not verify it | no |
| `contradicted` | status `disputed` | no |
| `open_conflict` | one side of an open durable conflict | no (review required) |
| `ambiguous` | verified but exploratory / hypothetical / unconfirmed / a recommendation | only as exploratory wording; never as a `current` / `superseded` claim |

Policy (conservative): non-verified memories stay visible but are listed **after** the verified knowledge with an explicit label in the description (`UNVERIFIED - needs confirmation`, `CONTRADICTED`, `AMBIGUOUS`, `OPEN CONFLICT`); a finding citing an unverified / contradicted / open-conflict memory is withdrawn and replaced by a code-written "withheld pending confirmation" line (the withheld text is **not** echoed); structured claims citing them are rejected; the summary may not state a value that only such a memory carries; each such memory is listed in `uncertainties`. Compression only deletes whole lines, so a surviving line keeps its label byte-identical, and labelled lines are dropped after the plain episodes.

### F3 - triggers are checked by definition

`MemoryStore._trigger_definitions()` is the single canonical source of the 40 integrity triggers (38 guard / append-only triggers from P1.4.x plus the two lifecycle append-only triggers). `integrity_ready()` now requires every one to have exactly that definition (table, event, WHEN clause, body; whitespace-insensitive), not just its name. `init()` replaces a missing or modified trigger (`DROP TRIGGER` then `CREATE`, because `CREATE TRIGGER IF NOT EXISTS` keeps a tampered same-name trigger), inside the one init transaction, and verifies the result before it may commit; any failure rolls the whole repair back and writes stay refused. **Threat boundary:** whoever owns the database file can rewrite SQLite itself; this gives reliable detection and fail-closed behaviour at startup and on every durable write, not protection from the file owner. There are no client-scope triggers: client scope is enforced by the boundary (`Principal` scopes), not by SQLite.

### F4 - canonical semantic slot identity

A slot is (client, canonical topic, canonical subject). Canonical = trimmed, lower-cased, runs of whitespace / hyphens -> `_`. The topic must be in the approved vocabulary, which is the planner's own `TOPICS` list (17 topics including `other`; every topic in `data/seed/cmf_memory_demo.db` is in it). A whitespace-only subject or topic is refused; a semantic memory with an empty subject is stored with its topic as the subject. Migration **123** replaces the raw-spelling unique index with `uq_active_semantic_slot_canonical`, an index on the canonical expressions, so even a write that skipped Python collides. **Legacy data is never merged, deleted or rewritten:** if canonicalisation reveals duplicates the index is not created, `MemoryStore.slot_collisions()` lists them, and the boundary refuses every new competing active version (`active_version_exists`).

### TTL and the trusted-code boundary

`expires_at <= now` is covered at `<`, `==` and `>`, for timezone-aware clocks (converted to UTC; previously the offset was dropped without converting), reopened databases, client-scoped and any-client principals, non-working memory, repeated invocation and unauthorised principals. The clock is the store's constructor-injected clock; there is no caller-supplied timestamp parameter.

F5 (what is guaranteed): semantic and procedural `valid_from` is bound to the cited turn; episodic observation time (`event_ts`) must equal the cited turn's time, but a caller-supplied episodic or working `valid_from` is **not** bound to the source (it is not used as a validity window for those types - known limitation, not fixed here); working-memory `expires_at` is set by the orchestrator service from the store clock; a memory with no transcript source, or whose source turn has a blank datetime, has no observed time (`None`) rather than an invented one. F6: the process-wide `Principal` seal and the store's `clock` attribute protect against **external** callers (API users, other services), not against malicious Python already running in the same process; that was not redesigned.


## Report verification and evaluation integrity (P1.5.1)

**Status: implemented, NOT independently accepted.** P1.4.3 and P1.5 have not been independently accepted either; everything here uses the current P1.4.3 contracts and does not prove them correct. The reviews that found these problems were written by the same engineer-agent that implemented the work.

### H1 - the report boundary has its own clause check
The P1.5 review showed that a finding supported on its figure could be *extended* ("... is 63 and he has a terminal illness", "... which his wife also chose") and still be presented as VERIFIED: the structured verifier compares figures, and the extension has none. `harness/clause_support.py` adds the second half: every clause of a model-written finding must be accounted for by the verified source quotation, and the structure around the figure must match - who acts (client / spouse / parent / colleague / advisor / named person, and in which order), negation, hedges and conditions, hearsay or a third party's recommendation, `and` versus `or`, retraction anywhere in the excerpt, and completed versus planned action. It reuses the memory matcher's building blocks (`agents.memory.support`), can only DOWNGRADE (one unsupported word -> AMBIGUOUS/qualified, two or more or a structural mismatch -> UNVERIFIED/withheld), skips code-rendered findings, and is deterministic lexical/structural matching, **not semantic entailment**. The verifier also refuses, on its own, any finding that cites a memory the memory layer classed unverified, contradicted or in an open conflict (`EVIDENCE_NOT_VERIFIED`).

73 adversarial probes (`harness/evaluation/p151_probes.py`, copied verbatim from the P1.4.2 review; self-authored), run through the real structured report verifier:

| boundary | TP | FP | FN | TN | false positives | false negatives |
|---|---|---|---|---|---|---|
| memory_episodic_llm | 7 | 0 | 4 | 62 | - | B16 D10 F6 F8 |
| memory_derived_semantic | 7 | 0 | 4 | 62 | - | B16 D10 F6 F8 |
| report_before_p151 (clause check switched off) | 8 | 16 | 3 | 46 | A4 A5 A6 B7 B18 C4 C5 C8 E1 E2 E3 E4 M1 M3 M5 M9 | B16 D10 F8 |
| report_current | 8 | 0 | 3 | 62 | - | B16 D10 F8 |

Known conservative false negatives at the report layer (the source supports the claim, the checker declines): B16 D10 F8. A faithful paraphrase that is downgraded is accepted on purpose; an unsupported claim presented as verified is not.

### Four boundaries, never one number
1. *Memory verification* (`agents/memory`): is the claim stored `verified`? 2. *Report claim verification* (`harness/evidence_verifier`, `clause_support`): is a finding presented as VERIFIED? 3. *Finding qualification* (`agents/report/validate.authority_split`, the `[Qualified: ...]` label, the code-written uncertainties): is a finding that rests on non-verified evidence withheld or labelled? 4. *Approval release* (`harness/approval`): is a sensitive answer withheld until approved, and released once, to its requester, only while the evidence is unchanged? The earlier headline "false positives 6 -> 0" described boundary 1 only.

### M1 - what the evaluation now covers
The 70-case corpus is unchanged. Added: the 73 probes at both layers (above); the derived-semantic path (a consolidation-class memory citing the same turn: it never outranks its episode); and 12 end-to-end scenarios (`harness/evaluation/p151.py`), each recording storage / retrieval / context / report evidence / finding / approval / release:

| # | scenario | outcome by layer | |
|---|---|---|---|
| 1 | unverified memory reaches the Report Agent | storage: unverified / retrieval: verification=unverified / context: labelled UNVERIFIED / report_evidence: unverified / finding: not presented / release: released=True | pass |
| 2 | contradicted memory reaches the Report Agent | storage: status=disputed / retrieval: retrieved with status disputed / context: labelled CONTRADICTED / report_evidence: contradicted / finding: not presented / release: released=True | pass |
| 3 | ambiguous (exploratory) memory reaches the Report Agent | storage: exploratory episodes stored verified / retrieval: retrieved as episodes / context: labelled AMBIGUOUS / report_evidence: 3 ambiguous / finding: outcomes={'VERIFIED': 0, 'UNVERIFIED': 1, 'CONTRADICTED': 0, 'AMBIGUOUS': 0} / release: released=True | pass |
| 4 | retraction and conditional claims | storage: retraction=NOT_VERIFIED, conditional=NOT_VERIFIED / finding: conditional finding presented=False; outcomes={'VERIFIED': 0, 'UNVERIFIED': 1, 'CONTRADICTED': 0, 'AMBIGUOUS': 0} | pass |
| 5 | derived memory built on unverified evidence | storage: unverified source -> derived unverified; false-positive-prone source -> derived NOT_VERIFIED | pass |
| 6 | human correction reaches the Report Agent | storage: human_correction/verified / retrieval: active / context: unlabelled (verified) / report_evidence: verified / finding: outcomes={'VERIFIED': 0, 'UNVERIFIED': 1, 'CONTRADICTED': 0, 'AMBIGUOUS': 0} / release: released=True | pass |
| 7 | open conflict reaches the Report Agent | storage: 6 open conflict(s) / retrieval: conflicts listed in the context / context: labelled OPEN CONFLICT / report_evidence: 2 open_conflict / finding: withheld pending review / release: released=True | pass |
| 8 | unsupported appended clause in a finding | storage: verified source / report_evidence: verified / finding: presented=False; outcomes={'VERIFIED': 0, 'UNVERIFIED': 1, 'CONTRADICTED': 0, 'AMBIGUOUS': 0} / release: released=True | pass |
| 9 | subject mismatch in a finding | storage: verified source / report_evidence: verified / finding: presented=False; outcomes={'VERIFIED': 0, 'UNVERIFIED': 1, 'CONTRADICTED': 0, 'AMBIGUOUS': 0} / release: released=True | pass |
| 11 | cache hit while approval is pending | approval: pending:yes / release: first released=False, repeat released=False, cache_hits=2 | pass |
| 10 | approval rejection | approval: rejected by SUP-1 / release: withheld:rejected | pass |
| 12 | approval release after the evidence changed | approval: approved by SUP-1, then the source memory became disputed / release: denied:approval_invalidated | pass |

Still NOT measured: a real model's extraction or report accuracy, multi-client robustness beyond the scripted worlds, the curated-procedure path as a verification subject, and any held-out set written by someone else. The corpus was developed alongside the matcher rules, so it measures regression, not generalisation.

### M2 - the "P1.4.2 baseline"
`memory_p142_shipped` was not the shipped P1.4.2 matcher. Its switches turned off the P1.5 / P1.4.3 rules but the later negation / hedge vocabulary stayed on, and the historical source was not preserved. It is renamed **`memory_matcher_rules_off`** ("the current matcher with the P1.5 / P1.4.3 rules switched off"). No exact P1.4.2 reproduction is claimed. The report-layer "before" is **`report_before_p151`**: the current verifier with `clause_support.ENABLED = False` (the other P1.5.1 verifier changes - the not-verified-evidence guard and the evidence-status note on qualified findings - are not switched off, but neither affects these probes).

### M3 - the numbers in this document
The tables above are generated from `p15.evaluate_claims()` / `p151.evaluate_probes()`; `tests/test_p151_report_verification.py::test_documented_headline_numbers_equal_the_computed_ones` fails if the headline rows drift from the computed values.

### M4 - reproducible mutation evidence
`PYTHONPATH=src python -m harness.evaluation.p151_mutations --out <dir>` copies the repository to a temp directory, applies each mutation there, runs the named tests, and records KILLED / SURVIVED / NOT-APPLIED (the real working tree is never touched). Result of the run recorded with P1.5.1:

| ID | Component | Intended defect | Detecting tests | Result | Evidence |
|---|---|---|---|---|---|
| M01 | report verifier clause matching | clause-level check disabled: an appended unsupported clause passes | `tests/test_p151_report_verification.py -k probe or categories or pipeline` | **KILLED** | 1 failed, 19 deselected in 3.45s |
| M02 | report verifier subject consistency | actor comparison disabled: a spouse's value verifies the client's | `tests/test_p151_report_verification.py -k probe or categories` | **KILLED** | 1 failed, 24 deselected in 3.27s |
| M03 | report verifier subject consistency | a person that the source never mentions may be added to a finding | `tests/test_p151_report_verification.py -k clause_unit` | **KILLED** | 1 failed, 138 deselected in 3.20s |
| M04 | report verifier numeric / unit matching | any unit word is forgiven: 'a month' accepted for 'a year' | `tests/test_p151_report_verification.py -k probe or categories` | **KILLED** | 1 failed, 24 deselected in 3.19s |
| M05 | report verifier negation / modality | negation parity disabled in the report check | `tests/test_p151_report_verification.py -k probe or categories` | **KILLED** | 1 failed, 24 deselected in 3.24s |
| M06 | report verifier negation / modality | hearsay / attribution parity disabled | `tests/test_p151_report_verification.py -k clause_unit` | **KILLED** | 1 failed, 1 passed, 138 deselected in 3.20s |
| M07 | report verifier temporal meaning | completed-versus-planned action check disabled | `tests/test_p151_report_verification.py -k probe or categories` | **KILLED** | 1 failed, 84 passed, 24 deselected in 3.55s |
| M08 | report verifier retraction | retraction anywhere in the excerpt ignored | `tests/test_p151_report_verification.py -k probe or categories` | **KILLED** | 1 failed, 24 deselected in 3.28s |
| M09 | cache key isolation | the actor is dropped from the cache key | `tests/test_p15_evaluation.py -k cache` | **KILLED** | 1 failed, 29 deselected in 4.43s |
| M10 | cache key isolation | the client scope is dropped from the cache key | `tests/test_p15_evaluation.py -k cache` | **KILLED** | 1 failed, 29 deselected in 4.69s |
| M11 | cache invalidation | conflict creation / resolution no longer changes the data version | `tests/test_p15_evaluation.py -k cache` | **KILLED** | 1 failed, 29 deselected in 4.72s |
| M12 | approval withholding | the releasing actor is no longer checked | `tests/test_p151_report_verification.py -k unauthenticated` | **KILLED** | 1 failed, 140 deselected in 3.56s |
| M13 | compression retention | compression drops caveat-bearing lines first again | `tests/test_p151_report_verification.py -k default_compression` | **KILLED** | 1 failed, 140 deselected in 3.24s |
| M14 | compression disclosure | the omission marker no longer names what was omitted | `tests/test_p151_report_verification.py -k markers` | **KILLED** | 1 failed, 140 deselected in 3.49s |
| M15 | evidence provenance / authority | every memory is classed verified in the report evidence | `tests/test_p143_report_authority.py` | **KILLED** | 1 failed, 2 passed in 3.67s |
| M16 | report authority split | findings on non-verified memory are no longer withdrawn | `tests/test_p143_report_authority.py` | **KILLED** | 1 failed, 2 passed in 3.74s |
| M17 | report authority (verifier guard) | the report verifier ignores the memory layer's verdict | `tests/test_p151_report_verification.py -k refuses_any_finding` | **KILLED** | 1 failed, 140 deselected in 3.88s |

Attempted 17, killed 17, survived 0, not applied 0. Not mutation-tested: planner / tool-selector behaviour, cache TTL and LRU eviction, the compression tier order beyond M13/M14, and the memory-layer matcher (covered by the P1.4.3 mutation run, which is not part of this table).

**Real-model run.** The one local Ollama `qwen2.5:3b` request reported with P1.5 predates P1.4.3 and P1.5.1, left no stored artifact, and is a smoke test only (pipeline, verifier and approval gate ran with a real model). No real-model run was made for P1.5.1; none is claimed.

### Cache and compression (narrow changes)
Cache keys serialise the client scope as a JSON list instead of a comma-joined string (`('a,b',)` no longer collides with `('a','b')`). Compression omission markers now also name the caveat kinds of the omitted lines (`kinds: unverified, correction`), so a reader can tell a budget omission from absence of evidence; the code-written uncertainties (built from the full evidence list) are unchanged. An omitted value is still checked against the full evidence, where an unverified or contradicted memory cannot support a finding. Cache semantics and approval checks were not changed.

### Known limitations
Lexical/structural matching, not entailment; false negatives on paraphrase; the report-layer check only knows the relation nouns, pronouns and capitalised names it lists; one-word differences are qualified, not withheld; the human-correction finding "current age is 63" is conservatively withheld because the quotation also says "replacing the earlier plan of 65" (pre-existing P1.1 behaviour); self-authored corpora; no independent acceptance of P1.4.3, P1.5 or P1.5.1.
