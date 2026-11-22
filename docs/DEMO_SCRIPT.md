# CMF demo script (about 15 minutes)

> **Current version: [`CMF_Demo_Script.docx`](CMF_Demo_Script.docx)**, about 12 minutes, with a screenshot for every step. It adds the conflicts and "why this memory" panels and the refused-resolve moment. This page is the earlier, longer outline.

The story you are telling: **a client's history is scattered across meetings, calls, emails and chats. The CRM keeps a thin, sometimes wrong summary. CMF reads the original conversations and builds a memory where every fact is dated, labelled by how settled it is, and traceable to the exact sentence it came from.**

Two clients carry the demo:

| Client | Storyline | Why it works |
|---|---|---|
| **Kenji** | Retirement age 65 → exploring 60 → 63 | One decision across 5 channels; the advisor's own note overstated it and had to be corrected |
| **Rebecca** | The Lakeshore company sale | The CRM still says "client accepted the offer"; the conversations say she was only inclined, until she decided |

---

## Before the audience arrives (5 min)

```bash
# terminal 1 (repo root)
PYTHONPATH=src .venv/bin/python -m api.main      # wait for "DEVELOPMENT PROFILE ACTIVE"
# terminal 2
cd ui/cmf-brain-ui && npm run dev                # http://localhost:5173
```

- Open **http://localhost:5173**. The **Client** dropdown (top right) should list 6 clients: Kenji 85, Rebecca 70, Sofia 77, Grace 59, Lucas 56, Peg 73 memories. If it's empty, the backend isn't running.
- Open a second tab on **http://localhost:5173/#/data** so you can switch views instantly.
- Zoom the browser to about 110% so the back row can read the tables.
- Don't use **Ask CMF** live unless you've rehearsed it (see Act 5).

---

## Act 1: The problem (1 min, no clicks)

> "An advisor's knowledge about a client lives in four places: meeting recordings, phone calls, an email inbox and a chat app. None of them talk to each other. What connects them is the CRM, and as you'll see, the CRM mostly says 'a meeting happened'. When a client changes their mind, or the advisor writes something down wrong, nothing reconciles it. CMF is our answer: a memory layer that reads the original conversations, not the summary."

---

## Act 2: The raw data (3 min), tab `#/data`

**Step 1, "Where the data comes from: four communication channels"**

- Point at the four cards: **100 meetings, 62 calls, 22 email threads (81 messages), 28 chats (94 messages)**, each captured by a different platform.
  > "Different formats, different ID schemes, different platforms. This is the reality we start from."
- Scroll to **"The actual data files: browse every row"**. Click **Email**, choose **Client → CLIENT-001**, and scroll a few rows of real email bodies. Then click **Chat** and show how short the messages are.
  > "This is the actual data we used: synthetic, but realistic. Every row here is something a person wrote or said."

**Step 2, "What the CRM keeps: a thin, partly manual summary"**

- Read the table: **166 CRM records; 153 are automatic log lines, only 13 are notes a person typed.**
  > "So the system of record mostly says 'meeting logged'. The few real notes were typed by hand, from memory. Keep that in mind; we're about to see one that's wrong."

---

## Act 3: Kenji's retirement age (4 min), still on `#/data`

**Step 3, "Follow one story across channels"**. Kenji's storyline, *"Retirement age: 65, exploring 60, then 63"*, is selected on the left, and its first card is open.

Walk the cards top to bottom. In each one, point at the three columns: ① what was said, ② what the CRM kept, ③ the memory CMF extracted.

1. **Meeting, Feb 9, INT-027**
   - ① Kenji: *"Let's say I'm leaning toward 60 for now, but I'm not committing to it."*
   - ② The CRM note, typed by the advisor: **"Kenji would like to retire at 60."**
   - ③ CMF: **exploratory**, "leaning toward 60 while 65 stays the formal planning date", confidence 0.96.
   > "Same conversation, two records. The CRM turned 'leaning, not committing' into a wish. CMF kept the hedge."
2. **Email, Feb 10, THR-002.** The advisor's summary email says *"you'd like to retire at 60"*.
   > "Here's an honest moment: the system trusted the advisor's written email too, and recorded 60. But look at the confidence, 0.82, lower than the others: the two AI models agreed, the rule check didn't. That's the signal a reviewer would use." *(Keep this. Audiences trust a demo that shows its limits.)*
3. **Call, Mar 4, INT-049.** Kenji corrects it: *"I should clarify that. I was only exploring the possibility."* The CRM gets a manual clarification note.
4. **Chat, Mar 5, CHT-001.** *"Can you confirm it says 65 is the planning date?"*: the correction is confirmed in writing, through a third channel.
5. **Meeting, Apr 16** → **Follow-up meeting, Apr 28 (MTG-004).** 63 comes up as a maybe: *"I'm not ready to say 63 is the answer."* CMF marks it **exploratory**, not decided.
6. **Meeting, May 1, INT-090.** *"We've decided: 63."* CMF: **confirmed**, confidence **0.96**, and a current value, **retirement age = 63, valid from May 1**.
   > "Seven records, four channels, three months. The CRM never shows the decision as a decision; it logs 'meeting happened'. CMF gives you the current answer and how it got there."

**Step 4, "The processed result"**: scroll down to the table.
> "This is the processed data: each memory with its type, status, value and the exact sentence and turn it came from. Nothing is a summary of a summary."

---

## Act 4: The memory brain (2 min), tab `localhost:5173`

- **Client → Kenji.** Point at the brain: each node is a memory, coloured by type. Working, episodic ("what happened"), semantic ("what we currently know") and procedural ("how to do this").
- Use the **Show superseded history** toggle (it reads **Hide superseded history** when on): solid = active, hollow = unconfirmed, dashed = superseded.
- Click Kenji's **retirement age (semantic)** node, then the **Memory Dossier** tab:
  - **Value 63**, Certainty, Confidence.
  - **Version history: 65 → 60 → 65 → 63.**
    > "Nothing is overwritten. The old values are superseded with dates, so you can always answer 'what did we believe in February?'"
  - **Evidence**: *"We've decided: 63."*, call INT-090, turn 2.
  - **Lifecycle audit trail**, and the **Dispute** / **Forget (expire)** buttons:
    > "An advisor can correct the record, and the correction is audited too."

---

## Act 5: Rebecca's company sale (3 min)

Back to the **`#/data`** tab → Step 3 → pick **"Lakeshore offer: inclined, not accepted; second thoughts; decision to sell"** (SCN-C2-01).

1. **Meeting, Mar 11.** *"I'm inclined to say yes, but I'm nervous about the earn-out and the rollover."* CMF: **exploratory**.
2. **Email, Mar 12, THR-006.** Open column ②. The CRM summary, typed by the advisor: **"Client accepted the offer."**
   > "This note is still in the CRM today. Nobody went back and fixed it."
   In column ③, CMF has *"inclined to accept; you have not signed the letter of intent"*, still **exploratory**.
3. **Meeting, Mar 24.** *"I said I was inclined to accept… I haven't signed."*: the correction.
4. **Meeting, Apr 17.** *"Below twenty-two I walk."* CMF records a $22M walk-away floor as her **current position, not a decision**.
5. **Call + chat, Apr 20.** Second thoughts: *"Is it crazy to wait two years?"*. She won't decide by text. CMF keeps it **exploratory**.
6. **Meeting, Apr 22.** *"I've decided. I'm going to sell to Lakeshore."* CMF: **confirmed**, confidence **0.96**.
   > "If an advisor or an AI assistant read the CRM before the next meeting, it would say she accepted in March. That's the wrong story, with money attached. CMF has the right one, with dates and quotes."

Then the brain tab → **Client → Rebecca** → open her **care** memories. There are **two separate** current values:
- **$6,000 / month** for her mother's care;
- a **$600,000 care reserve** set aside from the sale.
> "Same topic, different kinds of numbers. The system knows a monthly cost and a one-off reserve aren't the same fact, so one never overwrites the other."

**Optional, only if rehearsed: Ask CMF.** It takes about 25–30 seconds and calls OpenAI. Today it does **not** give a definite answer: its report agent refuses to state a current fact unless the memory is *verified*. That's word-for-word sentence matching, which most AI-written memories don't pass yet. If you show it, frame it as a feature:
> "It would rather say 'here is the evidence and the open items' than state something it can't verify."

---

## Act 6: How it works (1 min, no clicks)

> "For every conversation, one AI model, GPT-4o-mini, reads the whole thing and proposes memories. Code then checks the facts: the quote really exists, the speaker is right, the number is in the quote. A second model, GPT-4.1-mini, labels how certain each one is without seeing the first model's answer, and the lower label wins. Agreement becomes the confidence score. Only code decides what becomes a current value, and old values are never deleted."

The result to quote: on natural, unscripted wording, the old rule-based approach got **42%** of certainty labels right; this one gets **79%**, with **no** case of something tentative stored as decided.

---

## Likely questions

| Question | Answer |
|---|---|
| Is this real client data? | No, fully synthetic: 6 fictional clients, 3 advisors, Jan–Jun 2026. |
| What if the AI is wrong? | Every memory carries its quote and its confidence, disagreements are flagged for review, and an advisor can Dispute or Forget it with an audit trail. About 1 in 8 current values is still wrong, so it's a review aid, not an oracle. |
| Why two models? | Two copies of one model share blind spots. A different, blind second model makes disagreement informative. Measured: +5 points certainty accuracy. |
| Why not just read the CRM? | Act 5: the CRM says "accepted", and it was never corrected. |
| Does it use email and chat? | Yes: 59 email, chat and follow-up meeting records, alongside the 150 meetings and calls. A third party's messages (e.g. the CPA) are context only and can never be evidence. |

## Known rough edges (don't click into these live)

- Kenji's history shows 60 for a month (Act 3 explains it on purpose).
- Rebecca's chat (Apr 20) has a memory filed under "retirement age = 2"; that's a topic mistake.
- Rebecca $14,500 spending, Peg $210,000 cash: advisor read-outs stored as current values.
- Sofia's house: "2 years" and a $40k price; Grace's gift to Tobi shows 15 instead of 15,000.

## If something goes wrong

| Problem | Fix |
|---|---|
| Blank page or "Could not load" | The backend isn't running; restart terminal 1. |
| Client list empty | Check `.env` points at `build/cmf_memory_llm_first.db`. Recreate it with `cp data/seed/cmf_memory_llm_first.db build/`. |
| You disputed or forgot something while practising | Re-copy the seed database (same command) to reset. |
