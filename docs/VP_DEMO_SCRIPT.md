# CMF VP demo script (about 40 minutes)

Companion: [`VP_DEMO_CHEAT_SHEET.md`](VP_DEMO_CHEAT_SHEET.md) (one page of numbers and clicks).
Older, shorter outlines: [`DEMO_SCRIPT.md`](DEMO_SCRIPT.md), [`CMF_Demo_Script.docx`](CMF_Demo_Script.docx).

**The story:** a client's history is scattered across meetings, calls, email and chat. The CRM keeps a thin, partly hand-typed summary. CMF reads the original conversations and builds a memory in which every fact is dated, labelled by how settled it is, and traceable to the exact sentence it came from. Four agents answer advisor questions on top of that memory, inside a harness that decides what the AI may say.

**Three-part structure to give the VP:** data, then memory, then agents inside a harness.

---

## Before you present (do this the day before)

1. **Use a clean memory database.** The database the app was running on during development (`build/b6_demo/memory.db`) holds hundreds of leftover working-memory rows (Rebecca shows 533 memories there, against 70 in the seed). Copy `data/seed/cmf_memory_llm_first.db` to a new file and point `CMF_MEMORY_DATABASE_URL` at the copy. Do not edit the seed or operational databases.
2. **Run the Rebecca report once beforehand** and keep that browser tab open. A full report took 8 to 14 minutes on the development CPU (local Qwen2.5 3B). Do not wait for it live.
3. **Check ports.** Backend `http://127.0.0.1:8000`; UI is the Vite URL printed on start (5173 by default, 5174 if that was taken).
4. Open two tabs: the main page and `#/data`. Zoom to about 110 percent.
5. Confirm the client dropdown lists six clients.
6. **Verify before quoting:** the ingestion models and the 42% to 79% certainty-accuracy figure come from the older `DEMO_SCRIPT.md` and `docs/llm_first_ingestion.md`. They were not re-run for this script.

Rehearse once end to end. Keep the pre-run report as the fallback for every live Ask step.

---

## Act 0. Opening (2 min, no clicks)

> "Wealth advisors hold what they know about a client across meetings, calls, email and chat. The CRM keeps a thin summary, and when a client changes their mind, or an advisor writes something down wrong, nothing reconciles it. We built a memory layer that reads the original conversations. Every fact is dated, labelled by how settled it is, and traceable to the sentence it came from. On top of it, four AI agents answer advisor questions, inside a governance harness that decides what the AI is allowed to say."

---

## Act 1. The problem and the datasets (7 min), page `#/data`

Click **Learn about the data source** in the header.

**1. Four channels.** Read off the cards. All data is synthetic.

| Channel | Platform label | Size |
|---|---|---|
| Meetings | OneDrive and Teams | 100 |
| Calls | Telephony platform | 62 |
| Email | Exchange mailbox | 22 threads, 81 messages |
| Chat | Messaging platform | 28 conversations, 94 messages |

> "Different structures and ID schemes on every platform. That is the reality we start from."

- The base layer is **150 original conversations, 2,199 turns**: 6 clients, 3 advisors, January to June 2026 (88 meetings and 62 calls). We added 12 follow-up meetings, the emails and the chats, which cite earlier records, so the channels reference each other.
- Open the row browser. Show one email body and one chat message.

**2. The CRM layer is deliberately lossy.**
- 166 records: 153 automatic activity lines and 13 hand-typed notes or summaries.
- Some notes copy an advisor's assertion that the source later corrects, and the CRM is never updated (Rebecca "accepted").
> "That gap is what we are closing."

**3. Governance data.** 378 records (212 source plus 166 CRM), each with an owner, retention policy and access rule.
- Calls readable by the assigned advisor only.
- Three explicit third-party grants, one of them revoked.
- Four retention-pool records carry a narrower restriction.
- 22 access scenarios test cross-client denial, agent denial and revoked grants.
- Say plainly: this governance data is synthetic, not any vendor's policy.

**4. Operational data.** A separate structured database, 13 tables: 27 accounts, 73 holdings, 155 transactions, 396 snapshots, 45 goals, 5 liabilities, 35 instruments.
> "What clients said comes from conversations. What they own comes from this data. We never mix the two."

**5. Evaluation ground truth.** 23 planted storylines test certainty, corrections and currency. They live in a separate folder and the system never reads them.

---

## Act 2. One story across channels: Kenji (5 min), still on `#/data`

Step 3, "Follow one story across channels". In each card, point at three columns: what was said, what the CRM kept, the memory CMF extracted.

1. 9 Feb meeting: "leaning toward 60, not committing". CRM note: "would like to retire at 60". CMF: **exploratory**.
2. 10 Feb advisor email says "you'd like to retire at 60". Point at the lower confidence (the two models disagreed). Showing the limit builds trust.
3. 4 Mar call: Kenji corrects it. 5 Mar chat: confirmed in writing.
4. Apr to May: 63 appears as a maybe, then 1 May "We've decided: 63." CMF: **confirmed**, current value retirement age 63.

> "Seven records, four channels, three months. The CRM never shows the decision as a decision."

---

## Act 3. The brain (8 min), main page

Choose a client from the dropdown (Kenji, then Rebecca).

Explain the four regions and click one example in each:

| Region | Memory type | What it holds | Seed count |
|---|---|---|---|
| Prefrontal cortex | Working | Current task, tool results; short-lived (4 h expiry) | grows with every question |
| Hippocampus | Episodic | One event with its exact quote, turn and certainty | 365 |
| Cortex | Semantic | What we currently know; versioned, never overwritten | 55 |
| Cerebellum | Procedural | How the firm does things; curated, not client-specific | 3 |

> "The brain is not decoration. Each region obeys different rules."

- **Reading the picture:** solid = active, hollow = unconfirmed, dashed = superseded. Toggle superseded history on. Lines show supersedes, corrects, supports, derived-from, explores.
- **Certainty labels (seed):** confirmed 229, exploratory 97, unconfirmed 64, recommendation 20, correction 8, hypothetical 2.
> "An advisor's suggestion is a recommendation. It is never stored as the client's decision."
- **Kenji's retirement-age node, Memory Dossier tab:** value 63; version history 65, 60, 65, 63; evidence quote and turn; lifecycle audit trail; **Dispute** and **Forget** buttons (audited).
- **Conflicts:** 40 recorded in the seed. A conflict never overwrites anything. A person resolves it (keep, dismiss or correct) and the resolution is logged.
- **Sources & Lifecycle tab:** the evidence behind the selected memory.
- **Rebecca's two care values:** $6,000 per month and a $600,000 care reserve are two facts, not a contradiction.

---

## Act 4. How memories are written (3 min)

> "Memory is written through one gate. The extraction model proposes. Code then checks that the quote exists in the turn, the speaker is right and the number is in the quote. A second, different model labels certainty without seeing the first answer, and the more cautious label wins."

- **Verified** means the source turn exists, belongs to this client and supports the claim. It does not mean the claim is true.
- The database itself refuses writes that do not come through the integrity boundary, and rows cannot be physically deleted.
- A human correction is protected from automatic overwrite. An unverified memory can never replace a verified one.

---

## Act 5. The four agents (7 min), Ask CMF tab

Click **Ask CMF**. Name one client per question.

| # | Agent | Role | Model use |
|---|---|---|---|
| 1 | Planner | Turns the request into a plan: client, topics, task type, period | For single-client chronology requests code builds the plan, no model call |
| 2 | Memory Orchestrator | Retrieves the right memories, builds working memory for the request | Mostly code: ranks by certainty, recency, relevance |
| 3 | Tool Selector | Chooses which of 12 deterministic tools to call and when to stop | Model-driven; about 24 evidence calls on the Rebecca query, budget 30 |
| 4 | Report Agent | Writes the advisor report | Model writes sentences; code decides what is shown |

- The 12 tools are plain Python over the operational data (profile, portfolio, history, goals, liabilities, allocation, concentration, returns, liquidity, goal-funding gap, market data, source evidence). The model never runs SQL.
- **There is no fifth agent.** The harness is infrastructure around the four.
- Show the flow strip and the "How the agents performed" timing (Planner and Memory take milliseconds; the Report Agent takes almost all the time).

---

## Act 6. The harness (7 min)

Reference: `docs/harness.md`.

> "The agents have no authority of their own. Everything passes through the harness."

1. **Permission gate.** Identity comes from a signed token. An advisor sees only their own clients; a foreign client looks like it does not exist (404).
2. **Shared budget.** One budget per request covers model calls, tool calls (30), tokens and deadline. Exhaustion gives a labelled partial result, never an invented report.
3. **Context control.** Compression only deletes whole lines, never rewrites, so numbers and negations survive.
4. **Evidence verifier.** Sits between the draft and the reader. Quotes must be exact substrings of the source turn. Paraphrases must pass proposition checks: who said it, how firmly, nothing added, dates and figures match. Failures are withheld and listed.
5. **Approval gate.** Suitability-type answers are withheld until a supervisor approves; approval is single-use and bound to the requester.
6. **Trace.** One JSONL file per request records every decision. Prompts are hashed, not stored.

---

## Act 7. The live report (6 min)

Open the pre-run Rebecca report. Query: *"What is the history of Rebecca's decision about the Lakeshore company sale? Was the offer ever accepted, and what did Rebecca actually say versus what was recorded? Give the chronology with the source conversation turns, including any corrections."*

- **Header and executive summary.** A short cited answer.
- **How the history developed.** Plain prose by period. Open one "Supporting evidence" toggle to show the exact source turn behind a sentence.
- **Corrections and changes.** The 24 March correction, original and corrected positions side by side.
- **Current position and outcome.** The latest statement (22 April "I've decided") is shown; whether the sale was signed or completed is stated as not established unless a source says so. Code writes that outcome statement by scanning the retrieved turns.
- **Report metadata** (collapsed). Coverage statistics are secondary.

> "It would rather say 'here is the evidence and the open items' than state something it cannot verify."

---

## Act 8. Limits and roadmap (3 min). Say these before you are asked.

- Coverage is partial: in recent runs 19 to 34 of about 50 relevant records were written up as prose; the rest appear as recorded wording.
- It is slow on CPU (8 to 14 minutes for this report) because the live model is a local 3B Qwen via Ollama. A larger model is the realistic fix.
- The checks match words, not meaning. A pronoun role swap ("your accountant and I should speak" read as the client's) can pass.
- The executive summary is a selected digest of validated sentences, not original synthesis. It does not yet always answer the question asked.
- The current-position block does not yet separate "the client decided" from "the transaction completed".
- Everything is synthetic; SQLite only (PostgreSQL has not been run).

**Next steps:** a question-focused executive block; separate client decision from transaction status; pair a client question with the advisor's reply; cache validated sentences to cut runtime; try a larger narration model.

---

## Likely questions

| Question | Answer |
|---|---|
| Is this real client data? | No. Six fictional clients, three advisors. |
| What if the AI is wrong? | Every memory carries its quote and confidence; conflicts are flagged; advisors can dispute with an audit trail. It is a review aid, not an oracle. |
| Why not just read the CRM? | Rebecca's CRM note says "accepted" and was never corrected. |
| Why a harness instead of better prompts? | Safety is enforced in code, so it holds even when the model misbehaves. |
| Why are reports slow? | A local 3B model on CPU. The pipeline is model-agnostic. |
| Can one client's data leak into another's report? | Isolation is enforced at the permission gate and in storage, and is tested. It is still a prototype. |
| Why two models for memory? | Two copies of one model share blind spots; a blind second model makes disagreement informative. |

## Known rough edges (do not click into these live)

- Rebecca's 20 April chat has a memory filed as "retirement age = 2" (topic mistake).
- Some advisor read-outs are stored as current values (Rebecca $14,500 spending, Peg $210,000 cash).
- Sofia's house shows "2 years" and a $40k price; Grace's gift shows 15 instead of 15,000.
- The Memories-used list can show labels like "business_sale: business_sale: confirmed".

## If something goes wrong

| Problem | Fix |
|---|---|
| Blank page or "Could not load" | Backend not running; restart it. |
| Client list empty | `CMF_MEMORY_DATABASE_URL` points at the wrong or an empty database. |
| Working-memory count huge | You are on the development database, not the clean seed copy. |
| Ask takes minutes | Switch to the pre-run report. |
| You disputed or forgot a memory while practising | Re-copy the seed database. |
