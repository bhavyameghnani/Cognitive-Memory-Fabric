# Client-readable report: Stage A (architecture and gap assessment)

Worktree `...\Cognitive Memory Fabric - Bhavya Baseline`, branch `feature/llm-first-ingestion`, HEAD `8081eaa`, 52 uncommitted entries preserved, nothing staged. **Stage A is read-only: no source, test, schema, migration, configuration or database was changed to produce this document.** Statements marked *(observed)* come from code I read or from runs and data I inspected in this project; statements marked *(proposal)* do not exist yet. Nothing here is human semantic ground truth.

## 0. Honest verdict

**The system is not close to a client-readable report; it is close to a safe building site for one.**

* **What already works and is worth keeping:** attribution capture at memory formation (M1: speaker role, advisor-reported vs client-direct, certainty ceilings), verbatim source quotes with turn-level provenance, a deterministic evidence verifier, an authority guard that refuses to state unverified values, scope/permissions, and approval gating. These are the hard parts and they are real.
* **What is missing is almost everything between "safe evidence" and "readable report":**
  1. The Report agent's output type is an *advisor-facing findings bundle* (`AdvisorReport`: summary, three lists of one-sentence findings with `E7`-style handles, uncertainty bullets, limitations bullets, an evidence list with internal ids). There is no section structure, no narrative, no chronology object, no preparation context.
  2. The **only** thing that reliably survives the safety layers for a chronology question is a list of raw, code-rendered "Synthetic source record: INT-001 turn 6 … recorded speaker Client, contains: “…”" lines. That is an evidence dump, not a report.
  3. The model's synthesis (key findings, analysis, summary) is withheld or replaced on this question for three independent, *correct-by-design* reasons (section 4), so even a perfect model could not produce the chronology today.
  4. **Temporal reconciliation does not exist as a computation anywhere in the report path.** Retrieval returns a capped, relevance-ranked pile of memories; relationships (`supersedes`, `supports`, `follows`) exist in the database and are not carried into the report.
  5. The UI renders the raw structure and the debug panel together; there is no report view.
* **Distance:** large on synthesis, chronology, retrieval for history questions and presentation; small-to-moderate on the safety policy, which needs a clarified, documented distinction (section 5), not loosening. I will not put a percentage on it: it would be invented.

## 1. Current end-to-end information flow *(observed)*

```
source conversations (data/conversations.csv + multichannel datasets)   [read-only; MultiChannelConversations adapter]
   |  ingestion (rules or llm_first): extract -> fact-check -> blind verify -> M1 attribution/ceiling -> write validator
   v
memory store: episodic (per turn: speaker role, certainty, event_type, excerpt+turn evidence), semantic (versioned per slot by consolidation), procedural
              + relationships (supersedes / supports / follows / explores / derived_from / related_to) + integrity verdict per memory (verified | unverified)
   |
   |  QUESTION  ->  Planner (LLM) -> plan {clients, topics, task_type, required tools}
   v
Memory Orchestrator.retrieve(plan)   deterministic: current + history (superseded) + episodes (cap 8, ranked by relevance) + unresolved + conflicts + relations
Tool Selector                         deterministic source discovery for chronology questions (search_text, then per-meeting turn retrieval, 12 execution cap) + model-driven tool loop
   v
Report Agent.write(request, plan, memory, tools)
   build_evidence     -> numbered E1.. items (memories, tool results, retrieved source turns)
   model drafts       -> ReportLLMOutput {summary, key_findings, historical_context, financial_analysis, uncertainties, followups, claims}
   code validates     -> claims, findings (numbers, evidence refs), summary (authority guard), source records (rendered by code)
   v
Harness finalize_report -> Evidence Verifier (structured claims) -> withheld statements listed as uncertainties; approval gate (suitability keywords / forecast tasks)
   v
API /api/ask -> AdvisorReport JSON (+ agent_run telemetry)  ->  UI: AdvisorQuery (summary, lists, citations) + AgentRunPanel
```

## 2. Existing Report agent contract *(observed)*

* **Inputs** (`ReportAgent.write`, `src/agents/report/agent.py`): request text; `ExecutionPlan`; per-client `MemoryContext` (current, history, episodes, procedures, working, **relations**, contradictions, conflicts, unresolved, notes); `ToolSelectorState` (executions incl. retrieved source turns).
* **What actually reaches the model** (`agents/report/evidence.py`): at most `PROMPT_ITEMS = 16` evidence items, each trimmed to 300 characters; memory items carry topic/value/status/certainty/validity/verification class and a description string. **Not carried as structured fields:** the memory's `source_role` (the speaker appears only inside the description text "said by Advisor in …"), `event_type`, `supersedes_id`/version number, and none of `MemoryContext.relations`.
* **Output** (`schemas/report.py`): `AdvisorReport` = `summary`, `key_findings`, `historical_context`, `financial_analysis` (lists of `Finding{text, refs, origin}`), `evidence[]` (internal handles, memory ids, interaction ids), `assumptions`, `uncertainties`, `limitations`, `suggested_followups`, `validation` (debug dict). `ClaimStatus` for structured claims is `current | superseded | exploratory | recommendation`: **there is no "client stated", "advisor reported/recorded" or "contradicted" status**, so the single mechanism that links a sentence to a typed fact cannot express the distinction the task cares about most.
* **Who writes what:** the model writes free text (summary, findings, claims); **code** writes `origin="rendered"` findings (source records, claims rendered from evidence fields, tool figures), assumptions, limitations and authority notes.
* **Is it suitable for a client?** No: handles (`E7`), memory ids, `validation`, tool limitations ("get_source_evidence: cue 'correction' is a vocabulary hint…"), and reason codes are all in the primary payload.

## 3. Evidence from the Kenji run (what really happened)

From your last output (REQ-13620587), and the data:

| Observation | Source |
|---|---|
| 36 source turns retrieved; 24 rendered as findings, 12 only in the evidence list | `SOURCE_FINDINGS_LIMIT = 24` (`report/validate.py:317`) |
| 13 matching meetings not retrieved because "the tool-call budget of 12 was used" | tool budget; plus search phrases "retirement", "change", "including" (framing words not in the stop list) |
| **INT-049 (4 March 2026, "correcting the retirement-age record") not retrieved** | meeting ranking in `source_discovery.plan_meetings`; earlier/later ends and strongest matches outrank it |
| 4 model statements withheld, all `SOURCE_NOT_PERMITTED` | `evidence_verifier.py` ~lines 241-254 |
| Summary replaced: "authority guard: summary states a value from a non-verified memory" | `report/agent.py` `_unverified_in` |
| 12 retirement-age memories shown, all "unverified - needs confirmation" | `verification = unverified` on 365 of 365 seed episodic rows |
| One model sentence passed: "The exact retirement age remains uncertain as Kenji has not provided a clear decision." | not verified against the data by anything; see below |

**The data themselves** (seed copy, memories for Kenji's `retirement_age`; all `unverified`):

| Date | Speaker role | Value | Certainty / type | Source turn |
|---|---|---|---|---|
| 2026-01-02 | Client | 65 | confirmed decision (recorded as a "current plan, not a fixed decision" by the advisor in the same meeting) | INT-001#4 |
| 2026-01-16, 02-09 | Client | 60 | exploratory | INT-012#4, INT-027#8 |
| 2026-02-10 | **Advisor** | 60 | confirmed decision | THR-002#1 |
| 2026-02-10 | System | semantic v2 = 60 (valid 02-10 to 03-05) | derived from the advisor-spoken episode | THR-002#1 |
| **2026-03-04** | Client | 65 | exploratory (the client says: only exploring 60, not decided, 65 remains the planning date; the advisor says the file note "was too strong" and will be corrected) | **INT-049#4** |
| 2026-03-05 | Client / Advisor | 65 / 60 | confirmed / exploratory | CHT-001#1, #2 |
| 2026-04-16 to 04-28 | Client, Advisor | 63 | exploratory | INT-074, INT-082, MTG-004 |
| 2026-05-01 | Client | 63 | confirmed decision (semantic v4 = 63, current) | INT-090#2 |
| 2026-06-03 to 06-12 | Client, Advisor | 63 | confirmed | THR-010, INT-120, MTG-008, CHT-021 |

This is precisely the case the report must handle: an **advisor-recorded "decision" (60)** that the client later corrects in a client-spoken turn, a **semantic version derived from that advisor episode** (legacy; M1 would no longer create it), and a later number (63) that must not be presented as established merely because it is later. 27 episodes exist for this slot; **retrieval caps episodes at 8 (`EPISODE_CAP`) ranked by relevance**, so for a chronology question the correction episode may simply not be in the pile.

I have not verified whether the "63 is current" semantic version is right; I only note the records say so, and every one of them is marked unverified by the integrity boundary.

## 4. Why valid historical information disappears *(observed)*

1. **Source turns can support only a "what the turn records" sentence in `historical_context`.** A model-written key finding or financial-analysis statement that rests only on source turns is refused (`SOURCE_NOT_PERMITTED`), and a model-written historical sentence must name the recorded speaker, interaction id and turn number and avoid an "overreach vocabulary" (`validate.source_record_problem`). The model usually writes a synthesis, so it is refused. Code-rendered source records always pass, which is why the output is a quote list.
2. **Any finding that cites a memory is refused while the memory is unverified** (`EVIDENCE_NOT_VERIFIED`). All 365 episodic and 55 semantic seed rows are unverified (basis counts: 233 "no extracted value", 105 "claim not found in any single source sentence", 25 structure mismatch, 55 "no verified episode states this value"). So *no* memory-based statement can ever appear, **including a plain historical attribution**.
3. **The summary authority guard** replaces any summary that states a value carried only by unverified memories.
4. **Volume caps:** 8 episodes, 16 prompt items, 24 rendered source records, 12 tool executions, `ReportLLMOutput` list limits (8 per section).
5. **Retrieval for history questions is not chronology-aware:** relevance-ranked caps, search phrases picked from request wording, meeting ranking that can drop the correction meeting.

These are safety behaviours, not defects, except 4 and 5 which are capacity/design gaps.

## 5. Proposed evidence and attribution policy (document first, implement only after your approval)

The current verifier has one dimension ("may this evidence support this kind of statement?"). The task needs two: **what kind of statement is being made** and **what kind of evidence is cited**. Proposed claim classes:

| Class | Example sentence | Evidence required | May say | May **not** say |
|---|---|---|---|---|
| **H1 Attributed historical statement** | "On 4 March 2026 Kenji said he had only been exploring 60 and that 65 remained the planning date." | A **verbatim quote proven to be real text of that source turn** (`conv.verify_excerpt`), with date and recorded speaker, from a source turn *or from the quote stored with a memory*. The memory's own verification status is **irrelevant** to H1 because the claim is about the words, not their truth. | who said what, when, quoted | that it is true, a decision, agreed, or current |
| **H2 Advisor recorded / interpreted / summarised** | "In the 10 February record the advisor noted 60 as a decision." | same as H1, speaker = Advisor, `attribution` advisor-reported/interpretation (M1) | what the advisor recorded | that the client decided it (only a client-spoken confirmation row, M1 `client_confirms`, can raise this) |
| **H3 Tentative / exploratory** | "Kenji described 63 as something he was considering." | H1 plus certainty wording check | that it was being considered | that it was a plan or decision |
| **D Client-confirmed decision** | "Kenji decided on 63 on 1 May 2026." | client-spoken turn with decision wording **and** a **verified** memory (or the deterministic wording rule, see decision B) | a decision, dated, attributed | that it is *currently valid* unless a verified current semantic version exists |
| **C Current semantic fact** | "Kenji's current retirement age is 63." | **verified** semantic version only (unchanged) | current value | anything otherwise: say "no verified current value" |
| **S Superseded / outdated** | "65 was the recorded planning age until 10 February; it was later replaced by 60 in the advisor's record." | relationship (supersedes) plus H-class evidence on both sides, with validity window | the replacement relationship **as recorded** | that the replacement was correct |
| **X Contradiction / not reconciled** | "The advisor recorded 60 as a decision on 10 February; on 4 March Kenji said he had not decided it. The records do not settle this; a later clarification is dated …" | two H-class claims with opposite attribution and an explicit "not reconciled" label unless a later client-spoken clarification exists (then H1 states that clarification) | both sides, with dates | a winner, unless evidence names one |

**Implication.** H1-H3 and X do **not** weaken the verifier: they let the report say what is provable (the words, speaker, date) while D and C keep the current requirements. The "chronology" is then a sequence of H/S/X entries with an explicit **reconciliation status** per slot (`established`, `not established`, `contradicted`, `superseded`), and "established" requires class C or D evidence. A later number is never promoted automatically.

**Smallest safe verifier change** (to be implemented in Stage B only after approval): let a finding whose `origin` is code-rendered from H-class fields (quote + date + speaker + attribution label) cite **unverified memories' quotes** as H evidence, because those findings are assembled by code from the verified quote and cannot overreach (the verifier already trusts `origin="rendered"`). The refusal of model-written statements about truth, decisions or current state stays exactly as it is. Alternatively the memory's quote can be re-proven against the source turn at report time (the verifier already does this for quotes).

## 6. Proposed client-facing report contract *(proposal)*

New, additive type next to `AdvisorReport` (backward compatible; the existing fields and tests stay):

```
ClientReport
  title, prepared_for (client first name), prepared_on, question (as asked), scope_note ("based on N meetings between … and …")
  sections: [ ReportSection{ id, heading, paragraphs[text with plain-language source cites], entries[ChronologyEntry]?, items[ActionItem]? } ]
  disclosures: [plain-language statements of what is unverified / missing / not reconciled]
  sources: [ SourceNote{ plain label "Meeting of 4 March 2026 (advisor and client)", ref -> internal handle (hidden by default) } ]
ChronologyEntry { date, meeting_label, speaker ("Kenji" | "Advisor"), class (H1/H2/H3/D/S/X), quote, value?, relation_to_previous?, verification_label }
```

* **Never shown to the client:** memory ids, tool names/arguments, evidence handles (`E7`), reason codes, validation dict, confidence scores, agent telemetry. They stay in a separate `evidence_index` / `agent_run` for the debug panel and drill-down.
* **Source references** are rendered as "Meeting of 4 March 2026, Kenji (client)" and resolve to the internal handle only inside the UI's drill-down.

**Section rules (conditional on the question and the evidence; no section may be invented):**

| Section | Generated when | Content source |
|---|---|---|
| 1 Executive summary | always | code-composed from the sections below (see 7) |
| 2 Current financial profile | profile/portfolio tool results exist **or** a verified current fact exists | tool figures (as-of dates, "synthetic"), verified C facts; otherwise omitted or "no verified current value" line |
| 3 Goals and planning history | the question or plan concerns goals/timelines (e.g. retirement age) | chronology entries H/S/X per slot |
| 4 Decisions and changes | at least one D or S entry | class D with who/when/what it superseded |
| 5 Investment and portfolio context | portfolio/allocation topics requested **and** tool data or H entries exist | tool results, H entries |
| 6 Pending actions and open questions | unresolved memories, conflicts, advisor/client action items present in evidence | memory unresolved list, conflicts, action lines from retrieved recaps (attributed) |
| 7 Conflicts and uncertainties | always (an empty-state sentence when none) | X entries, unverified flags, retrieval omissions in plain words ("meetings not reviewed because the review was limited") |
| 8 Overall review summary | always, after sections exist | code-composed synthesis (see 7) |

## 7. Who writes the prose *(design decision, recommendation)*

Three options, with consequences:

* **A. Fully deterministic renderer.** Safest and testable; reads stiffly; no model dependence.
* **B. Model writes the narrative; verifier checks each sentence.** Reads best; with the local 3B model, rejection rates will be high (observed: all four synthesis statements withheld).
* **C. Hybrid (recommended).** Code builds the structure and every factual sentence from typed fields (dates, speakers, quotes, relationships, labels); the model may only write **connective, non-factual framing** per section, checked against the existing overreach vocabulary; on any failure the code's own wording stands. The existing mechanism for this is already present (`origin="rendered"`, `render_claims`, deterministic fallbacks); the change is to give it a section/chronology shape.

## 8. Temporal reconciliation: what must be built *(proposal)*

1. **Chronology retrieval mode** for history questions: for the requested slot(s), fetch **all** episodes and versions in time order (bounded, with an explicit "more exist" note), not the 8 most relevant. Relationships (`supersedes`, `supports`, `follows`, `explores`) come along.
2. **Slot timeline builder (pure code):** order by event date; attach speaker role, attribution class, certainty wording, the quote, and the verification label; link entries by recorded relationships **and** by client-spoken clarification cues in the *same-slot, later* turn (the INT-049 pattern); never infer a relationship from number order.
3. **Reconciliation status per slot** (`established` only with class C/D evidence; otherwise `not established`/`contradicted`).
4. **Retrieval fixes that the chronology depends on:** exclude request-framing words from source search phrases; always include meetings whose own text states a correction/clarification of the slot (the INT-049 case) ahead of generic matches.

## 9. Gaps by area, and the files that would change

| Area | Exists | Missing | Files (Stage B unless noted) |
|---|---|---|---|
| Attribution capture | yes (M1) | carried into the report as structured fields (`source_role`, `event_type`, `attribution`) | `schemas/report.py` (EvidenceItem fields), `agents/report/evidence.py` |
| Verification policy | strict single dimension | H/S/X claim classes; rendered H entries from memory quotes; documented rules | `harness/evidence_verifier.py` (only after approval), `agents/report/validate.py`, `schemas/claims.py`/`report.py` (`ClaimStatus`) |
| Chronology / temporal reconciliation | relationships in DB | timeline builder, reconciliation status, chronology retrieval mode | new `agents/report/chronology.py`; `agents/memory/retrieve.py` (a mode, not a rewrite); `orchestration/pipeline.py` wiring |
| Source retrieval for history | code-driven discovery | framing-word filter, correction-aware meeting priority | `agents/tool_selector/source_discovery.py` |
| Report contract | `AdvisorReport` | `ClientReport` + sections + disclosures + sources | `schemas/report.py`, new `agents/report/client_report.py`, `agents/report/agent.py`, `agents/report/render.py`, `agents/report/prompts.py` |
| Governance | verifier, authority guard, approval | none new; must stay intact; withheld client report must remain content-free | `harness/runtime.py` (finalize_report scrubs `ClientReport` the same way) |
| API | `/api/ask` returns `AdvisorReport` + `agent_run` | additive `client_report` field | `api/main.py`, `api/agent_run.py` |
| UI | `AdvisorQuery` (summary + lists), `AgentRunPanel` (debug) | a dedicated report view; debug panel secondary | Stage D: new `ClientReportView.tsx`, `types/index.ts`, `AdvisorQuery.tsx` |
| Evaluation | `harness/evaluation` (p15/p151), structured-claims eval, semantic eval | report-level deterministic cases (below) | new `tests/test_client_report_*.py`, new fixtures |

## 10. Deterministic evaluation framework *(proposal; no human ground truth)*

Scripted models, synthetic conversation CSVs, temporary stores, and **structural** assertions authored by me (AI-assisted, explicitly *not* human semantic validation). Cases:

1. client vs advisor attribution: the same number spoken by each gets different classes and wording;
2. tentative vs confirmed: "considering 60" never renders as a decision;
3. advisor-recorded decision later corrected by the client (INT-049 pattern): both sides appear, status `contradicted`/`not reconciled` until a client-spoken clarification, which is quoted;
4. supersession: the older value is rendered as "was … until …" only via a recorded relationship;
5. contradictory historical records: both shown, no winner;
6. missing evidence: the section says so; no invented sentence;
7. unverified memories: H1 entries render; D/C sentences do not;
8. current fact unestablishable: the report states "no verified current value" instead of a number;
9. suitability-style requests stay subject to approval and the withheld output stays content-free;
10. **Kenji chronology query as the primary end-to-end case**: required properties (not a golden text): INT-049 present and attributed to Kenji (client) on 4 March 2026; the 10 February advisor-recorded 60 attributed to the advisor; no sentence states a "final/confirmed retirement age" unless class C/D evidence exists; every date/quote in the report appears in the source data; retrieval omissions disclosed in plain words; no ids/tool names/handles in the client view; every withheld claim listed with its reason.

## 11. Risks and compatibility

* Additive types and fields keep all current tests meaningful; `AdvisorReport` remains the harness/verifier contract.
* Verifier changes are the highest risk; the plan keeps model-written truth/decision/current claims exactly as strict as today.
* The local 3B model cannot be relied on for prose; design C does not depend on it.
* Legacy seed rows remain unverified (not touched). The chronology will honestly show everything as unverified until a separate, approved re-verification milestone; the report will still be readable because H-class entries do not need memory verification.
* A **legacy semantic version derived from an advisor-spoken episode** (v2 = 60) will appear in the data; the report must mark it as derived from the advisor's record, not as a client-established value (policy S/X above covers it).
* UI work is small only if the backend returns a stable `ClientReport`; Stage D must not start earlier.

## 12. Decisions I need from you before Stage B

* **A.** Approve the claim classes in section 5, in particular that **H-class (attribution) sentences may be rendered from the verbatim quote stored with an unverified memory**, because they assert only what was said. If you prefer, restrict H evidence to retrieved source turns only (then retrieval must always fetch them).
* **B.** For class D (a "decision"): require a **verified** memory only (today's rule; with the unverified seed, no decision can ever be stated), or also allow a **deterministic client-spoken wording rule** over the verbatim turn (stricter than the model, weaker than a verified memory). My recommendation: allow it **only as a labelled "recorded as a decision by Kenji on …" attribution**, not as a verified fact.
* **C.** Prose option (section 7): A, B or C (recommend C).
* **D.** `ClientReport` as an additive field next to `AdvisorReport` (recommended) vs replacing the output.
* **E.** Permission to change retrieval for history questions (chronology mode, episode cap lifted for the requested slot, correction-aware meeting priority, framing-word filter).
* **F.** Whether the evaluation fixtures may include a **copy of Kenji's real synthetic records** (INT-001, INT-012, INT-027, THR-002, INT-049, CHT-001, …) as a frozen CSV in `tests/`, or only invented conversations.

## 13. Proposed order of work

Stage B: (1) report contract types and plain-language renderers (no model); (2) carry attribution/relations into evidence; (3) slot timeline builder + reconciliation; (4) retrieval fixes (framing words, correction-aware priority, chronology mode); (5) H/S/X verification policy (after your approval of A and B); (6) section assembly with conditional rules; (7) optional model framing under the existing guards; (8) tests per the evaluation list. Stage C: run the Kenji case and show the generated report plus every withheld claim. Stage D: `ClientReportView` and a toggle that keeps the debug panel separate.

## 14. What I did and did not do

Read: `schemas/report.py`, `agents/report/{agent,evidence,validate,prompts}.py`, `harness/evidence_verifier.py`, `orchestration/pipeline.py`, `agents/memory/{retrieve,config}.py`, `schemas/memory.py`, the tool-selector discovery code, the UI components and types, the Kenji seed rows (read-only on a scratchpad copy) and `INT-049` (read-only). Not done: any code, test, schema, migration or data change; any UI browser testing (none was performed); any claim about semantic correctness of the records.
