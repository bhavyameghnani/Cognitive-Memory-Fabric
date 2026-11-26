# CMF VP demo: cheat sheet (print this)

Full script: [`VP_DEMO_SCRIPT.md`](VP_DEMO_SCRIPT.md)

## One-line pitch
CMF reads the original conversations and builds a dated, certainty-labelled, evidence-linked memory. Four agents answer questions on it, inside a harness that decides what the AI may say.

## Numbers to know
| Item | Value |
|---|---|
| Clients / advisors | 6 / 3 (synthetic), Jan to Jun 2026 |
| Original conversations | 150 (88 meetings + 62 calls), 2,199 turns |
| Meetings / Calls | 100 / 62 |
| Email | 22 threads, 81 messages |
| Chat | 28 conversations, 94 messages |
| CRM records | 166 (153 automatic, 13 hand-typed) |
| Governance records | 378 (212 source + 166 CRM) |
| Access scenarios / planted storylines | 22 / 23 |
| Operational tables | 13 (27 accounts, 73 holdings, 155 transactions, 396 snapshots, 45 goals, 5 liabilities) |
| Seed memories | 365 episodic, 55 semantic, 3 procedural |
| Certainty (seed) | confirmed 229, exploratory 97, unconfirmed 64, recommendation 20, correction 8, hypothetical 2 |
| Conflicts recorded | 40 |
| Seed memories per client | Kenji 85, Rebecca 70, Sofia 77, Grace 59, Lucas 56, Peg 73 |
| Tools / tool-call budget | 12 / 30 per request |
| Agents | 4 (no fifth; harness is not an agent) |
| Live model / seed extraction | local Qwen2.5 3B (Ollama) / verify in `docs/llm_first_ingestion.md` |
| Rebecca report runtime | about 8 to 14 min on CPU (pre-run it) |
| Rebecca coverage | 19 to 34 of about 50 relevant records written up |

## Brain regions
| Region | Type | Meaning |
|---|---|---|
| Prefrontal cortex | Working | current task, short-lived (4 h) |
| Hippocampus | Episodic | one event, quote, turn |
| Cortex | Semantic | what we know now, versioned |
| Cerebellum | Procedural | how the firm does things |

Node style: solid = active, hollow = unconfirmed, dashed = superseded.

## Agents in one line each
1. **Planner**: request to plan (code-built for single-client chronology).
2. **Memory Orchestrator**: retrieves the right memories.
3. **Tool Selector**: picks deterministic tools, stops within budget.
4. **Report Agent**: writes sentences; code verifies and shows only what passes.

## Harness in one line each
Permission gate, shared budget, context control, evidence verifier, approval gate, trace.

## Click path (about 40 min)
| Min | Where | Click | Say |
|---|---|---|---|
| 0 | Main | nothing | pitch, three-part structure |
| 2 | `#/data` | Learn about the data source | four channels, CRM thin, governance, operational data |
| 9 | `#/data` | Step 3, Kenji storyline, cards top to bottom | said vs CRM vs memory |
| 14 | Main | Client: Kenji, then each brain region | what each region holds |
| 18 | Main | retirement-age node, Memory Dossier | versions 65, 60, 65, 63; Dispute/Forget |
| 22 | Main | Sources & Lifecycle tab; Client: Rebecca | conflicts; two care values |
| 25 | Main | Ask CMF tab | four agents, 12 tools |
| 32 | Ask | pre-run Rebecca report | summary, chronology, evidence toggle, corrections, outcome |
| 36 | any | nothing | harness, limits, roadmap, questions |

## Rebecca question (use exactly)
What is the history of Rebecca's decision about the Lakeshore company sale? Was the offer ever accepted, and what did Rebecca actually say versus what was recorded? Give the chronology with the source conversation turns, including any corrections.

## Key facts for the Rebecca story
- 12 Mar email, advisor summary: "client accepted the offer" (never corrected in the CRM).
- 24 Mar meeting: advisor corrects his own record: inclined to accept, not signed.
- 20 Apr: second thoughts. 22 Apr: "I've decided. I'm going to sell to Lakeshore." with conditions.
- Signing or completion of the sale: not established by the retrieved turns.

## Say these before they ask
- Partial coverage, slow on CPU, word-level checks (not meaning), summary is a digest, synthetic data, SQLite only.

## Pre-flight checklist
- [ ] Fresh copy of the seed memory DB in use (not the development DB)
- [ ] Backend up (port 8000); UI URL noted (5173 or 5174)
- [ ] Six clients in the dropdown
- [ ] Rebecca report pre-run and tab open
- [ ] `#/data` tab open; zoom 110 percent
- [ ] Ingestion-model and accuracy claims verified or left out
- [ ] Do not click: Rebecca "retirement age = 2", $14,500, $210,000, Sofia house, Grace gift

## Recovery
Blank page: restart backend. Empty list: wrong DB URL. Huge working count: wrong DB. Slow Ask: use pre-run tab. Practised Dispute/Forget: re-copy the seed DB.
