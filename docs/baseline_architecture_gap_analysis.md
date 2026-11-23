# Baseline architecture gap analysis

Companion to `docs/baseline_verification_report.md` (same evidence labels: **[RUN]**, **[CODE]**, **[CLAIM]**, **[UNVERIFIED]**). AI-assisted analysis, not human validation. Nothing from the previous branch (semantic-rules-2, evidence contract/readiness, version guard, shadow memory store, evidence adapter, memory design documents) was copied or merged; this document lists **ideas and safeguards** worth **re-implementing independently inside Bhavya's architecture**, each with a compatibility analysis. Code transplantation is not recommended anywhere below unless stated.

## 1. The architectural fact that shapes everything
Bhavya's memory path is `data/*.csv → MultiChannelConversations (flattened rows) → LLM/regex extraction → fact checks → integrity boundary → production memory`. The structured evidence layer (`src/evidence/`, semantic rules v1) is **not part of that path** [CODE]. My previous branch built on the opposite assumption (evidence DB → read-only adapter → shadow candidates → human approval). The two designs differ in four ways:

| Dimension | Bhavya's baseline | Previous branch (design) |
|---|---|---|
| Evidence source for memory | flattened CSV rows, addressed by `(interaction_id, turn_index)` | structured evidence DB (containers, turns, spans, annotations), addressed by container/turn/span/offsets, pinned by version and file hash |
| Who proposes memories | an LLM reading the whole conversation (plus regex guards) | deterministic extractors over cited sentences; LLM only as an optional re-verified proposer |
| Where proposals go | straight to the production store (status `active`/`unconfirmed`) | a separate shadow store; promotion only after validation **and** a recorded human approval |
| How uncertainty is represented | certainty label + numeric `confidence` calibrated from a 150-meeting synthetic evaluation | evidence class and ordinal tier, certainty as a **ceiling** derived from evidence; no numeric confidence |

**Consequence.** Modules from the previous branch that depend on the evidence DB (the evidence adapter, semantic-rules-2, the version guard for evidence databases, the readiness policy over annotations) have **no consumer** in Bhavya's pipeline and should **not** be transplanted. What carries over is the set of *safety properties* those modules enforced, to be expressed in terms of his objects (`MemoryCandidate`, `Decision`, `memory_evidence`, `MemoryWriteValidator`, `Consolidator`).

## 2. Gaps and what to reimplement
Priority: **P1** = creates wrong persistent memory today (evidence in the verification report); **P2** = provenance/integrity weakness; **P3** = representation/clarity; **P4** = capability.

### G1 (P1). Speaker-conditioned certainty ceiling: advisor-reported decisions cannot be client-confirmed decisions
* **Gap.** `guard_certainty` receives `source_role` but only uses it for `recommendation`; `MemoryWriteValidator.review` checks that the candidate's role equals the source speaker but not that a *confirmed* item may be spoken by an advisor; `Consolidator.eligible`/`approve_semantic` never look at the speaker. Executed: an Advisor-sourced `confirmed` episode created an active semantic value, with the semantic row's `source_role` = `System` [RUN: Traces B, D].
* **Idea to reimplement.** A certainty **ceiling by evidence**: a statement spoken only by the advisor is evidence of the advisor's account (at most `unconfirmed`), unless a client turn confirms it (a supporting evidence row whose speaker is Client, which his `reanchor` already produces for short replies). A semantic version must be created only from an episode whose primary or supporting evidence is **client-spoken**.
* **Compatibility.** Fits his model: `Decision.final.certainty` (downgrade-only, already the convention), `rules.guard_certainty(certainty, excerpt, source_role)`, `MemoryWriteValidator.review/approve_semantic`, `consolidate.eligible`. No schema change required (evidence rows already carry `speaker`); a DB-level guard would need a migration and is optional.
* **Risk/trade-off.** The dataset's own generator writes the decisive record as an advisor recap ("You've decided..."), so a strict rule lowers recall where no client turn confirms; the rule must be measured (his harness already reports recall) and must distinguish *recap of a client decision stated earlier in the same conversation* (client turn exists) from *advisor-only* claims. This is the highest-value, smallest fix.

### G2 (P1). Negated-finality and "will confirm later" wording
* **Gap.** `rules.has_unconfirmed`/`has_exploratory` do not recognise "Please don't treat it as final yet" / "I'll confirm on Wednesday"; the sentence passed as `confirmed` when the (scripted) verifier agreed [RUN: Trace C-5]. The hedge veto worked on "leaning toward" and "Maybe" [RUN].
* **Idea.** A maintained, test-backed list of finality negations and deferral phrases in the deterministic guard, with adversarial fixtures drawn from the data; deterministic guards should not depend on the LLM verifier being right.
* **Compatibility.** Pure change inside `rules.py` plus tests; no schema or interface change.

### G3 (P1). A candidate stage and human approval for non-procedural memory
* **Gap.** Formation writes to production immediately: episodic `active`/`unconfirmed`, semantic via consolidation [CODE]. Human approval exists for procedural proposals and for semantic topics in `approval_sensitive_memory_topics`, whose default is empty. `memory_candidates` is an **audit of the filter**, not a staging area [CODE].
* **Idea (from the shadow design).** Separate *validated* from *approved*; let formation produce proposals that do not affect retrieval until approved; record the approving human; append-only decisions.
* **Compatibility analysis.** Do **not** transplant my shadow SQLite store: it is a separate database with its own evidence tables keyed to the evidence layer, which his pipeline does not use. Within his architecture there are two compatible routes: **(a) minimal**: set `approval_sensitive_memory_topics` to the sensitive topics by default (the harness approval gate already exists and is tested) and add a `CMF_MEMORY_FORMATION_MODE=direct|staged` switch where `staged` writes episodic rows with the existing status `candidate` and requires a lifecycle action (`update_memory_lifecycle`, role-gated) to activate; **(b) full**: a new additive migration (e.g. `memory_proposals` + decisions, appended after 122) holding unapproved proposals with their evidence and votes. Route (a) reuses existing statuses (`candidate` is already in the CHECK) but retrieval/consolidation must be audited so a `candidate` row can never feed a semantic version or a report; that audit is part of the work.
* **Decision needed from the owner**: whether direct formation by an operator is acceptable for the demo database, or whether staged mode must become the default.

### G4 (P2). Exact source offsets and an explicit "how was this excerpt matched" record
* **Gap.** `Conversations.verify_excerpt` is a boolean over a normalised, **case-insensitive** substring (`norm`); `memory_evidence` stores the excerpt but no offsets, no match kind, no source/artifact hash [RUN: "WE'VE DECIDED: 63." verifies; CODE].
* **Idea.** Locate the excerpt in the original text with a verified rule (exact, or whitespace-normalised mapped back to original positions, else **unresolved**), never treat a case-only match as an exact quotation, report ambiguity (the same string twice), and store the match kind and offsets.
* **Compatibility.** Add a **new** method next to `verify_excerpt` (keep its signature: the integrity boundary, harness validator and verifier call it) and additive nullable columns on `memory_evidence` via a new migration (the schema is migration-driven: `sql/120+`, `store._migrate_*`). Existing rows remain valid. The excerpt-location logic (a ~100-line pure function) is small enough to re-implement from the specification, not copy.

### G5 (P2). Pin the formation configuration to the database
* **Gap.** `data/seed/cmf_memory_llm_first.db` only works with `CMF_MEMORY_CHANNELS=all` ("must be set wherever this database is used"), and ingestion mode/model/prompt versions are not recorded per database; `ingestion_log` has `model` and `transcript_hash` per interaction only [CODE/CLAIM]. A mismatch fails silently: memories cite email/chat records that do not resolve with the default adapter.
* **Idea (from the version guard).** Stamp the database with the formation configuration (ingest mode, channels, extractor/verifier model ids, prompt version, source-corpus hash, schema version) and **refuse or warn** on open when the runtime configuration differs; expose it in the UI's data-source page.
* **Compatibility.** A small additive table or `schema_migrations`-style row; read in `MemoryStore.init`/`MemoryOrchestrator.__init__`. No transplant; the concept only.

### G6 (P3). Reframe numeric `confidence`; add an evidence class
* **Gap.** `memory_records.confidence` is a float in [0,1], filled from bucket accuracies measured on a synthetic set (0.96/0.82/0.86/0.2/0.3) or a fixed table (0.4–0.9), and semantic versions get 0.9; the UI/report layers may present it like a probability [CODE/CLAIM]. His own document warns the disagreement buckets are small samples.
* **Idea.** Keep the number if the UI needs it, but (i) rename/document it as a *calibration bucket*, (ii) add a categorical **evidence class** (explicit / contextual / inferred / ambiguous / unsupported) derived from the vote record and speaker, which is what gates promotion (G1/G3), and (iii) record unresolved interpretations structurally (a `needs_review` flag rather than text inside the audit note).
* **Compatibility.** Additive column(s); the audit note already records every vote, so the class can be derived without new model calls.

### G7 (P3). Abstentions and unresolved interpretations as data
* **Gap.** Rejected candidates are in `memory_candidates` with reasons [CODE]; ambiguous ones ("needs review") are only flagged in the note text, and the pipeline has no representation of "the model could not decide", only accept/downgrade/reject/merge [CODE].
* **Idea.** Preserve unresolved interpretations (with reason codes) as queryable rows that can never be approved without new evidence.
* **Compatibility.** Extends `memory_candidates` (additive columns: reason code, status `unresolved`).

### G8 (P4). Use the relationships the sources state
* **Gap.** The adapter drops `in_reply_to`, `references`, `message_id`, `reply_to_message_id`, recipients and subject [RUN]. Corrections are matched to earlier episodes by **value** (`consolidate._associate`, substring `_same`), not by the message they correct, and carry no scope [CODE].
* **Idea.** Carry the source-stated reply references into the flattened rows and the LLM transcript (extra dict keys, no pipeline change), and let a correction cite its target message; add claim-level (not message-level) correction scope later.
* **Compatibility.** `channel_rows()` already builds dicts; `llm_first.transcript` can show a "replying to turn n" marker. This is the one place where the structured evidence layer's *knowledge* (which fields are stated by the source) is useful; its code is not needed.

### G9 (P4). Temporal reasoning for plans and expressions
* **Gap.** `event_date` is the turn timestamp; "on June 10", "next month", "by Friday" are not parsed; episodic memories have no validity or expiry; planned actions never expire [CODE].
* **Idea.** Treat temporal expressions as verbatim candidates attached to an episode, resolved only against the message's own timestamp, with explicit `planned`/`historical` status and expiry for time-bound commitments.
* **Compatibility.** Needs episodic `valid_from/valid_to` use (columns exist) and a lifecycle rule for expiry; retrieval already supports `as_of` and stale detection.

### G10 (P2). Evaluation discipline
* **Gap.** His evaluation is rich (answer key = the generator's authored outcomes; blind judge `gpt-4.1`), but it measures **recall and certainty accuracy**, not the rate of *unsupported* or *wrongly attributed* memories, and uses no adversarial fixtures for the failure modes found here (advisor-only recap, negated finality, cross-context values, speaker confusion). Answer key and judge are LLM-based [CLAIM].
* **Idea (from the semantic audit).** Held-out samples with an explicit exclusion set, a frozen random order, labelling **explicit / contextual / ambiguous / unsupported**, reporting the unsupported-memory rate with an interval and a plain "AI-assisted, not human-validated" label; deterministic adversarial fixtures that need no API key.
* **Compatibility.** New test/eval modules only (`tests/`, `scripts/eval_*`); the MockLLM harness he built (`tests/test_llm_first.py`) is the right vehicle.

### G11 (P2). Reproducible baseline and test hygiene
* **Gap.** A fresh checkout fails 73 tests (no `build/cmf_ops_smoke.db`) and the dataset hash lock (CRLF from `core.autocrlf=true`); the suite creates `build/cmf_memory.db`; no lint config [RUN].
* **Idea.** A documented/automated bootstrap (create the smoke DB in a temp path or a session fixture), `.gitattributes` pinning CSV line endings to LF (the committed hashes assume LF), tests that never touch default `build/` paths, and a minimal lint/format configuration.
* **Compatibility.** Repository hygiene; independent of the architecture.

## 3. Ideas judged NOT worth carrying over (and why)
| Idea / artefact from the previous branch | Why not, or why later |
|---|---|
| semantic-rules-2, evidence contract/tiers, readiness policy over annotations | No consumer in his pipeline; his LLM-first path does not read annotations. Revisit only if the owner decides to rebuild memory evidence on the evidence layer (a different baseline). |
| Evidence adapter (`EvidenceSource`), evidence version guard | They read `cmf_evidence_v2.db`, which his path never uses. The *concepts* (version/config pin, exact offsets) are carried as G4/G5 against his stores. |
| Shadow SQLite store as a separate database | Duplicates tables he already has and keys evidence to a different model; see G3 for in-architecture routes. |
| Deterministic extractors replacing the LLM | A different product direction; his LLM-first extraction is the stated baseline. Guards (G1, G2) can be deterministic without replacing extraction. |
| "No numeric confidence" | Probably too strict for his UI; G6 keeps the number but relabels it and adds a categorical class. |
| Two-human-annotator gate | Not a precondition for any of the above; it only matters before enabling *automatic persistence without approval*, which is a later decision. |

## 4. Things Bhavya's baseline already does well (keep; do not rebuild)
Integrity boundary with sealed principals, SQLite guard triggers and an append-only `memory_write_log` (`integrity.py`); per-client scoping at every layer; downgrade-only certainty combination; deterministic, idempotent consolidation with historical versions and a human-correction precedence rule; a harness evidence verifier that re-reads sources and removes unverified claims; durable approval store; per-request trace and telemetry; working memory with TTL. These are **verified by passing tests** in this run (209 targeted tests; the rest of the suite apart from the environment-caused failures).
