"""Extraction prompt for the Memory Orchestrator.  The model sees only meeting snippets and the topic list: no SQL, no credentials."""
from __future__ import annotations

from agents.planner.topics import TOPICS

EXTRACTION_SYSTEM = f"""You extract durable client memory candidates from ONE wealth-advisory meeting. You do not answer questions and you never invent facts.
You are shown selected sentences of selected turns, each labelled with its turn number and speaker. Return JSON with a list "candidates" (at most 12; usually 0 to 4).
Only include something worth remembering later: a decision, an exploration the client is still weighing, a correction of an earlier record, an advisor recommendation, or a stated fact or request.
Do not include greetings, acknowledgements, scheduling, portfolio read-outs or spending averages (those come from operational data, not from the client).

For each candidate:
- topic: exactly one of {", ".join(TOPICS)}. Use retirement_age for when someone retires (a target age or date) and retirement_income for how much income they need. Avoid "other".
- subject: a short slot name; for retirement_age and asset_allocation use the topic name itself.
- statement: one sentence in your own words.
- value: a short normalised value taken from the text (for example "63" or "35"), or null. Never put a sentence in value.
- certainty: confirmed (the client decided or agreed a plan), exploratory ("still weighing", "idea and not an instruction", "what if", "maybe"), hypothetical (an if-scenario),
  recommendation (the advisor proposes and the client has not accepted), correction (an earlier record is being corrected), unconfirmed (unclear). If unsure choose the weaker one.
- speaker_role: Client or Advisor, as labelled. turn_index: the turn number shown. excerpt: ONE sentence copied exactly from that turn.
- corrects_value: for a correction, the value being corrected, else null.
- reusable_procedure: true only when an advisor states a reusable way of working, never for a client and never for an ordinary instruction.
Numbers from tools or portfolio read-outs are system data, not what the client said. Output JSON only."""


def build_user_prompt(client_label: str, meeting_date: str, interaction_id: str, context: str, snippets: list[tuple[int, str, str]]) -> str:
    lines = [f"Meeting {interaction_id} on {meeting_date} with {client_label}. Meeting context: {context}", "Selected turns:"]
    lines += [f"[turn {t} | {sp}] {txt}" for t, sp, txt in snippets]
    return "\n".join(lines)
