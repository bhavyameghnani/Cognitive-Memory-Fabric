# Dataset Design (revision 2)

Revision 2 is a targeted quality revision of the first dataset. The original is preserved in `backup/v1/`
(CSV, manifest, validation report, the v1 source tree and the v1 design doc). See "Revision 2" below for what changed.

## Methodology

**Generation method: authored text plus deterministic Python. No LLM was used.** There is no model dependency
and no network access.

1. **Storylines.** Each client has a 25-step storyline (`src/cmfgen/clients/cN.py`). A step is a meeting topic plus
   an ordered list of segments. These v1 files are unchanged except for a few intentional text fixes.
2. **Revision overlay.** `src/cmfgen/revisions.py` and `rev_c1.py` .. `rev_c6.py` replace or extend individual steps.
   The v1 storyline stays readable beside what changed.
3. **Segment types** (`src/cmfgen/beats.py`):
   - `S`: hand-written scripted dialogue for pivotal moments. It can register outcomes (decision, plan, tentative idea,
     deferral, correction, advisor or client action), open or close action items, and set client state.
   - `Check`: a follow-up on an earlier action item (done or still pending), phrased from the action's owner.
   - `G`: parameterised generic segments (portfolio snapshot, market commentary, cash flow, liquidity, document
     request, suitability explanation, advisor-advised delay, scenario promise, concept explanation, circumstances check).
4. **Voices** (`src/cmfgen/voice.py`). Routine scaffolding is drawn from per-client and per-advisor pools:
   Kenji decisive and analytical; Rebecca decisive and blunt; Sofia fast, anxious, fluent in tech but new to insurance;
   Grace hesitant, needs plain explanations; Lucas informal and challenging; Peg plain-spoken and wary of jargon.
   Priya (advisor) is analytical, Daniel direct, Elena warm.
5. **Per-client state** persists across the six months: equity target, anxiety level, cash, portfolio value (tracked
   against one shared fictional market path), the open-action ledger, which template variants have been used, which
   casual remarks have been used, and the previous meeting's context.
6. **Assembly.** Opening (greeting plus agenda citing the real previous meeting date) -> segments -> closing summary built
   from what that meeting logged. Adjacent turns by the same speaker are merged; scripted lines are never rewritten
   when merged. No stand-alone filler turns are inserted.
7. **Scheduling.** Unchanged from v1 (own seeds): per client, 25 distinct business days from a fixed monthly allocation,
   at least 2 days apart. Advisor time slots never overlap. Interaction IDs are chronological.
8. **Seeding.** One seed (default `20260101`). Output is byte-identical across runs.

The closing summary separates **decisions** ("You've decided...") from **tentative ideas** ("You're still weighing...")
and **corrections**, so uncertain intentions are not recorded as instructions.

## Clients

| Client | Advisor | Profile | Main longitudinal arcs |
|---|---|---|---|
| CLIENT-001 Kenji Tanaka, 58 | Priya Nair | Senior logistics executive. Spouse Mariko, daughter Yuki (24), son Ren (19). | Retirement age 65 -> exploring 60 -> note corrected -> 63. Equity 75% -> 60% after the March sell-off. Vested-share sale, deferred-comp election, graduate-tuition cap of $30k a year. |
| CLIENT-002 Rebecca Holloway, 52 | Daniel Okafor | Owner of a packaging manufacturer. Husband David, son Ethan (17), mother Dorothy (81) in memory care. | Curious -> offer -> "accepted" note corrected -> second thoughts -> decision to sell. Declines employee ownership because she needs certain cash for her mother. |
| CLIENT-003 Sofia Alvarez, 34 | Elena Rossi | Product manager. Partner Mateo, daughter Lucia (born 3 March). | 90% equity -> discomfort -> 75% with a safe house fund. House purchase "might" -> mis-recorded as decided -> corrected -> postponed. |
| CLIENT-004 Grace Adeyemi, 61 | Priya Nair | Hospital administrator, widowed Nov 2025. Son Tobi, daughter Folake. | No-risk stance -> gradual move to about a quarter in stocks (first step 1 June). Mortgage payoff wish -> deferred -> keep. Rejects an annuity. Retire 65 -> 62? -> 63. |
| CLIENT-005 Lucas Moreau, 39 | Daniel Okafor | Software founder, irregular income. Partner Camille, daughter Amélie. | "Double down on Vantora" -> loses customer -> nine-month reserve. Sells half the secondary offer, trims Vantora, sells crypto. |
| CLIENT-006 Margaret "Peg" Sullivan, 74 | Elena Rossi | Retired. Husband Frank (Parkinson's), daughter Colleen, son Brian. | Equity 35% -> 25% -> 30%. Declines Brian's loan. Maplewood Commons: interest -> declined for now. |

All people, employers and figures are fictional.

## Revision 2: what changed

| Request | What was done |
|---|---|
| Reduce repeated filler | Removed all stand-alone bridging turns and most acknowledgements; opening is 2 turns, closing 2-4. Routine reactions now come from client/advisor voice pools. Transitions between segments are merged into the speaker's turn and name the topic. Exact-repeated turns: 58.5% -> 28.6% (definitions in the comparison report). |
| Thin meetings | All 36 previously thin meetings were read. 29 were rewritten and 1 extended with client reasoning, advisor follow-up and an outcome or an explicit open question; the 6 half-year reviews kept their recap and gained a short unresolved/forward-looking item. INT-148 now records an explicit pending status for the disability-insurance follow-up (no decision manufactured), so every meeting has at least one logged outcome. |
| Communication styles | Per-client and per-advisor voice pools, plus new scripts written in those voices. |
| Corrections | 4 -> 11 correction meetings; every client now has at least one (see below). |
| Relative dates | The v1 audit flagged 10 inaccurate or ambiguous references (born "last week" two days after a meeting, and similar); all were fixed, along with casual remarks that were reused or out of order. The audit now reports 0 issues. |
| Longitudinal consistency | Action ledger identical (48 opened, 40 completed, 8 outstanding). Fixed a v1 timeline error (Grace's investing steps), stale holdings text, reused casual remarks, and cash balances that disagreed with scripts. |
| Preservation | IDs, clients, advisors, datetimes, channels and columns identical. 479 of 500 (95.8%) hand-written v1 script lines survive verbatim; the other 21 are intentional edits (listed in the comparison report). |

### Correction scenarios (all preserve the original statement and add a later clarification)

| Client | Meeting | Scenario |
|---|---|---|
| 001 | existing (March) | advisor's note "wanted to retire at 60" was too strong; it was exploring |
| 001 | step 20 (late May) | advisor filed Ren's 14k as extra tuition; it covers the program fee, flights and housing |
| 002 | existing (March) | advisor's note said she accepted the offer; she only leaned toward accepting |
| 002 | step 24 (late June) | advisor assumed salary stops at closing; the draft keeps her as a paid consultant for 12 months |
| 003 | existing (March/April) | house purchase recorded as decided; it was only a possibility |
| 003 | INT-139 (22 June) | advisor treated the house fund as a childcare cushion; it remains house money |
| 004 | INT-050 (9 March) | advisor reported the pension survivor payment as about half; the form says 60% |
| 004 | INT-122 (5 June) | advisor labelled the insurance payout as for the mortgage; that was a wish, not a purpose |
| 005 | existing (March) | advisor's note said he intended to sell tender shares; he had not decided |
| 006 | INT-098 (8 May) | note said she would help Brian with equipment costs; it was only a thought |
| 006 | INT-119 (2 June) | care model assumed 4 aide days a week; it has been 5 since Frank's fall |

### New facts introduced

Revision added details that were not in v1, in each case to support a scenario. They are listed in
`reports/manual_review_notes.json` under `new_facts_introduced_by_revision` so a reviewer can check them against
the storylines. One example is Lucia's birth date, which makes the 4 March meeting a call from hospital.

## Quality checks

- **Automated validator** (`src/validate_dataset.py`): 33 checks. 30 carry over by name or intent; `turns_within_15_to_32`
  became `turns_within_12_to_32`; three were added (exact-repeat rate <= 40%, no known stand-alone filler, relative and
  cross-reference dates consistent).
- **Date audit** (`src/date_audit.py`): relative-phrase rules against each meeting's real gap to the previous meeting;
  agenda "last time, on <date>" and "pencil in <weekday>, <date>" are matched to the true previous and next meeting.
- **Comparison** (`src/compare_quality.py` -> `reports/dataset_quality_comparison.json`): v1 vs v2 metrics with their
  definitions, preservation checks, and a clearly separated manual-review section.
- **Manual review** (`reports/manual_review_notes.json`): all 36 previously thin meetings and ten others were read in full.
  This was done by the assistant that wrote the revision, so it is not independent.

## Known limitations

1. **Shorter meetings.** Mean 14.7 turns (v1: 21.95); 92 of 150 are under 15; the shortest has 12. Removing filler
   exposed how brief many scripted cores are. Total words fell from 63k to 51k. Meetings were padded only up to 12 turns.
2. **Scaffolding is still templated.** 28.6% of turns are exact repeats and 31.6% are repeats after normalising names,
   dates and numbers. Portfolio snapshots, cash-flow checks, the circumstances check and market commentary are still
   templates, and every meeting still ends with the same recap-plus-next-date structure.
3. **Distinct strings did not rise.** The count of distinct utterances is about the same as v1 (1,773 -> 1,800).
   The repeat rate fell because repeated filler turns were removed, and not because much new unique text was added.
4. **Uniform voice in preserved scripts.** The 96% of v1 script lines that were kept verbatim are still uniformly polite
   and articulate. Voices differ most in scaffolding and the rewritten meetings.
5. **Half-year reviews remain recap-driven** (INT-145 to INT-150). They do not list every unresolved thread; see `docs/final_dataset_acceptance.md`.
6. **Proxy metrics are weak.** The automated "no substantive new information" proxy flags only 2 meetings in v2 and 1
   in v1, so it does not discriminate. The authoring-side metric (no scripted outcome) goes from 36 to 0.
7. **Advisor commitments beyond 30 June** (Priya's conditional call before the 1 July purchase) are outside the window
   and are recorded in the manifest under `commitments_beyond_observation_window`, not in the 48/40/8 ledger.
8. **No semantic uniqueness was measured.** Embeddings are out of scope; the report says so.
9. **`meeting_context` changed for 24 meetings** where the label described a template segment (e.g. "Market check")
   rather than the new content. They are listed in the comparison report.

## Final QA pass

A final targeted audit corrected four inconsistencies (INT-009, INT-115, INT-119, INT-148) and froze the dataset; see
`docs/final_dataset_acceptance.md` and `reports/final_acceptance_audit.json`.
