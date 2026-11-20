# CMF structured evidence layer — design

Scope: channel-aware parsing and persistent, source-linked storage of the Dataset v1 communications. **No memories are created.** This layer produces evidence; a later phase may read it.

## 1. What is reused, and what is not
| Existing | Use |
|---|---|
| `datasets/source_catalog.py` (`discover_source_files`) | the only way ingestion enumerates files; evaluation files are refused by it, and ingestion re-asserts that |
| `datasets/source_manifest.csv` | validated Call/Meeting classification of the 150 original interactions (62 Calls, 88 Meetings); `channel` in `conversations.csv` is not used to decide this |
| `datasets/character_registry.csv` | speaker identity, category, attribution status and confidence |
| `datasets/governance/*` and `datasets/governance_check.py` | synthetic access fixtures; read-side authorization reuses `decide()` (deny by default) |
| `src/cmf_data/backend.py`, `MemoryStore` pattern | same conventions: `sqlite:///` URL, `?` placeholders, one module owns the driver, read-only handles for readers |
| `src/services/conversations.py` | read-only reader for `data/conversations.csv`; not modified |
| Memory DB / ops DB / approvals DB | **not touched**. Evidence goes in its own database, `build/cmf_evidence.db` (`CMF_EVIDENCE_DATABASE_URL`) |

Not reused: the memory schema (`memory_evidence` points *from* a memory *to* a turn; it is a consumer of evidence, not a store of it) and the existing `get_source_evidence` tool, which reads the CSV directly. It can later be re-pointed at `EvidenceRetriever` without a change in behaviour; this phase does not do it.

## 2. Common evidence representation (tables, `sql/200_cmf_evidence_schema.sql`)
| Logical entity | Table | Notes |
|---|---|---|
| SourceArtifact | `source_artifacts` | one per source container: file, source ref, source timestamp, ingest timestamp, content hash, parser version, status, error |
| CommunicationContainer | `containers` (+ `email_thread_details`) | email thread / meeting / call / chat conversation, native metadata |
| MessageTurn | `turns` (+ `email_message_details`) | one per email, speaker turn or chat message |
| ContextSegment | `segments`, `segment_turns` | segments always list their turns |
| EvidenceSpan | `evidence_spans` | exact original text of a turn; offsets only where reliable |
| Annotation | `annotations` | `provenance` = observed / rule_generated / model_generated; method and confidence stored |
| relations | `turn_relations` | reply, question→answer, correction; each with its basis |
| parties | `parties` | copy of the registry fields, so attribution confidence travels with the evidence |
| Derived CRM | `derived_artifacts`, `derived_sources` | separate pathway; never in `turns` |
| runs | `ingest_runs` | status and errors per run |

Hierarchy: container → segment → span, with FKs in both directions (`turns.container_id`, `segment_turns`, `evidence_spans.turn_id`). Full-text search uses SQLite FTS5 over `turns.original_text` if the build supports it, with a `LIKE` fallback. No embeddings, no vector store.

## 3. Parsers (`src/evidence/parsers/`)
All deterministic for IDs, boundaries, order, timestamps, recipients, client and channel. Pure functions from CSV rows to dataclasses; no database access.
* **email**: groups by thread, orders by `message_seq` (and checks the timestamps), keeps `Message-ID`, `In-Reply-To`, `References`, to/cc, attachments as named references only. Quoted text is detected only by explicit markers (`>` lines, `On … wrote:`, `-----Original Message-----`, `From:` header blocks); everything before the first marker is the authored text and the original body is always stored whole. Reply relations come only from `In-Reply-To`, and a reply to a message not in the thread is recorded as unresolved, not invented.
* **meeting** and **call**: turns from `meeting_turns.csv` (generated meetings) or `conversations.csv` (original interactions, classified through the manifest). Original turns have only a `Client`/`Advisor` label, so the speaker ID is derived from the interaction's client/advisor ID and marked `derived_from_role_label`. Turn timestamps exist only for generated meetings; for calls and original meetings none are stored.
* **chat**: messages in `message_seq` order, `reply_to_message_id` kept verbatim. Contextual grouping uses timing gaps and explicit replies; message boundaries are never merged.
* **crm**: derived-pathway parser (section 5).

## 4. Segmentation and annotation
* Topic segments: deterministic and hierarchical. A segment boundary is placed where the topic label changes between consecutive turns. Topic labels come from a fixed, reviewable keyword lexicon (retirement, portfolio allocation, liquidity, estate, tax, insurance, housing, business, follow-up…); turns with no topic cue join the surrounding segment. No fixed-size token chunks. Each segment records `method = "keyword_lexicon_v1"` and a confidence that reflects how many cues support the label, with `review_status = "unreviewed"`.
* Dialogue acts: rule-based (`dialogue.py`), `provenance = rule_generated`, `method = "rule_v1"`, a low-to-medium confidence. A suggestion or exploration is never labelled a decision: decision requires a first-person commitment cue from the **client** side or an advisor statement recording a decision already made; otherwise it is labelled proposal/suggestion. No LLM is used in this phase, and nothing generated overwrites source text.

## 5. CRM: separate and derived
`derived_artifacts` holds the CRM text, `derived_status = 'derived'`, the transfer method, and the layer flag from the dataset. `derived_sources` links it to its supporting containers (CRM `source_refs` in this dataset are container-level, so no turn-level support is invented). Retrieval returns the derived record and the original containers as separate objects. CRM rows never enter `turns`, `segments` or `evidence_spans`.

## 6. Idempotency and versioning
Every container gets a content hash over its canonical form plus the parser version. Re-ingest of an unchanged container is a no-op. A changed hash or a new parser version replaces the container's children inside one transaction and bumps `source_artifacts.revision`; the old hash and revision are recorded in `ingest_runs`. A failure rolls back that container only and is recorded; other containers proceed. Primary keys are the dataset IDs, so duplicates cannot occur.

## 7. Access
Writes happen only through `EvidenceStore.ingest_*`, an operator-side batch path with no agent or API route to it. It does not touch the approval lifecycle or the memory store. Reads go through `EvidenceRetriever(requester)`. Each request is checked with the synthetic reference evaluator `decide()` per container, and agents hold only their delegator's rights. Derived records are checked against their own control row, so a CRM record never widens access to its source and a source grant never opens a CRM record.

## 8. Test plan
`tests/test_evidence_layer.py`: structural counts per channel against the CSVs; thread reconstruction and per-message addressing; quoted-text parser on synthetic strings (the dataset has none); call/meeting classification against the manifest; segment→turn integrity; offsets reproduce text; CRM separation; client isolation and permission-aware retrieval; evaluation files refused; idempotency, deterministic output and rollback; retrieval metrics (P@K, R@K, MRR) on queries built from source text, with the evaluation scenarios used only by tests to build expectations.
