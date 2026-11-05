"""Shared helpers for the Memory Orchestrator tests (no tests here).  Candidates quote GENUINE excerpts from data/conversations.csv; the helpers
verify every scripted excerpt against the CSV so a typo can never produce a fake quote."""
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from agents.memory.agent import MemoryOrchestrator  # noqa: E402
from agents.memory.config import OrchestratorConfig  # noqa: E402
from agents.memory.store import MemoryStore  # noqa: E402
from llm.client import MockLLM  # noqa: E402
from schemas.plan import ExecutionPlan  # noqa: E402
from services.conversations import shared  # noqa: E402

CONV = shared()
KENJI, REBECCA, SOFIA, GRACE, PEG = "CLIENT-001", "CLIENT-002", "CLIENT-003", "CLIENT-004", "CLIENT-006"


class Clock:
    def __init__(self, t=datetime(2026, 7, 1, 9, 0, 0)):
        self.t = t

    def __call__(self):
        return self.t

    def advance(self, **kw):
        self.t += timedelta(**kw)


def cand(turn, excerpt, topic, certainty, value=None, subject="", role="Client", corrects=None, proc=False, statement=None):
    return dict(topic=topic, subject=subject, statement=statement or excerpt[:200], value=value, certainty=certainty, speaker_role=role, turn_index=turn,
                excerpt=excerpt, corrects_value=corrects, reusable_procedure=proc)


class Script:
    """MockLLM handler: returns the scripted candidates for the meeting named in the prompt ('Meeting INT-012 on ...')."""

    def __init__(self, by_meeting):
        self.by_meeting, self.prompts = by_meeting, []
        for iid, cs in by_meeting.items():
            for c in cs:
                assert CONV.verify_excerpt(CONV.meeting(iid)[0]["client_id"], iid, c["turn_index"], c["excerpt"]), f"NOT A REAL EXCERPT: {iid}.{c['turn_index']}: {c['excerpt']}"

    def __call__(self, system, user, schema):
        self.prompts.append((system, user))
        iid = re.search(r"Meeting (INT-\d+)", user).group(1)
        return {"candidates": self.by_meeting.get(iid, [])}


def make(by_meeting=None, cfg=None, clock=None, url="sqlite:///:memory:"):
    script = Script(by_meeting or {})
    llm = MockLLM(handlers={"memory": script})
    clock = clock or Clock()
    store = MemoryStore(url=url, conv=CONV, clock=clock)
    orch = MemoryOrchestrator(llm, store=store, conv=CONV, config=cfg or OrchestratorConfig())
    orch.init()
    return orch, store, script, clock


def plan(text, clients, topics=(), cats=("semantic", "episodic"), task_type="retrieval", needs_history=False, time_period=None, rid="REQ-M1"):
    return ExecutionPlan(request_id=rid, plan_id="PLAN-" + rid, request_text=text, client_ids=list(clients), user_intent=text, task_type=task_type, time_period=time_period,
                         needs_history=needs_history, needs_calculation=False, subtasks=[], required_memory_categories=list(cats), memory_topics=list(topics), required_data=[],
                         required_tools=[], dependencies={}, execution_order=[], parallelizable_tasks=[], missing_information=[], success_criteria=["answered"])


# ---- genuine excerpts -----------------------------------------------------------------------------------------------------------------
K001_T13 = "The working plan I'm recording is retirement at 65 on about fifteen thousand a month after tax, with the mortgage cleared by 2032."
K012_T4 = "But it's got me thinking: what if I retired at 60 instead of 65?"
K012_T11 = "You're still weighing retiring at 60, which I've recorded as an idea and not an instruction."
K049_T17 = "I've corrected my own record: my earlier note that you wanted to retire at 60 was too strong; you are exploring it, and 65 remains the planning date."
K074_T13 = "You're still weighing retiring at 63 instead of 60, which I've recorded as an idea and not an instruction."
K090_T2 = "We've decided: 63."
K090_T11 = "You've decided to retire at 63, replacing the earlier plan of 65, because the role changed and Mariko wants to teach until 62."

KENJI_SCRIPT = {
    "INT-001": [cand(13, K001_T13, "retirement_income", "confirmed", None, role="Advisor")],   # the earlier build's failure: filed under retirement_income, empty value
    "INT-012": [cand(4, K012_T4, "retirement_age", "confirmed", "60", subject="retirement_age"),          # over-claimed: the guard must downgrade it
                cand(11, K012_T11, "retirement_age", "exploratory", "60", subject="retirement_age", role="Advisor")],
    "INT-049": [cand(17, K049_T17, "retirement_age", "correction", "65", subject="retirement_age", role="Advisor", corrects="60")],
    "INT-074": [cand(13, K074_T13, "retirement_age", "exploratory", "63", subject="retirement_age", role="Advisor")],
    "INT-090": [cand(2, K090_T2, "retirement_age", "confirmed", "63", subject="retirement_age"),
                cand(11, K090_T11, "retirement_age", "confirmed", "63", subject="retirement_age", role="Advisor")],
}
KENJI_MEETINGS = ["INT-001", "INT-012", "INT-049", "INT-074", "INT-090"]

REBECCA_SCRIPT = {
    "INT-061": [cand(11, "You've decided to decline the employee-ownership route, because a ten-year seller note would not give the certain cash needed for your mother's care.",
                     "business_sale", "confirmed", "decline employee ownership", subject="employee_ownership", role="Advisor")],
    "INT-099": [cand(15, "You've decided to set aside six hundred thousand dollars of the proceeds at closing as a care reserve for your mother, invested conservatively and reviewed yearly.",
                     "care_costs", "confirmed", "$600,000 at closing", subject="care_reserve", role="Advisor")],
    "INT-112": [cand(9, "On liquidity: you hold about $220,000 in cash and short-term holdings, which covers roughly 15 months of spending at the current level.",
                     "liquidity_reserve", "unconfirmed", "$220,000", subject="cash_on_hand", role="Advisor"),
                cand(11, "For you, given most of your net worth being inside the company and your mother's care costs, I'd want closer to 18 months, so that you're never forced to sell investments in a bad market to pay bills.",
                     "liquidity_reserve", "recommendation", "18 months", subject="cash_on_hand", role="Advisor")],
}
PEG_OPEN_SCRIPT = {
    "INT-063": [cand(11, "You've decided not to lend Brian eighty thousand dollars, because you need the money for Frank's care.", "family_support", "confirmed", "not to lend",
                     subject="brian_loan", role="Advisor"),
                cand(11, "You're still weighing offering Brian a few hundred dollars toward equipment, which I've recorded as an idea and not an instruction.", "family_support",
                     "exploratory", None, subject="brian_equipment", role="Advisor")],
    "INT-072": [cand(13, "You're still weighing moving to Maplewood Commons, with a three hundred eighty thousand dollar entrance fee, which I've recorded as an idea and not an instruction.",
                     "housing_property", "exploratory", None, subject="maplewood_commons", role="Advisor")],
    "INT-125": [cand(13, "You've decided not to pursue Maplewood Commons for now, because Frank wants to stay at home, with the option to revisit if his needs change.",
                     "housing_property", "confirmed", "not pursue", subject="maplewood_commons", role="Advisor")],
}
PEG_EQUITY_SCRIPT = {
    "INT-003": [cand(13, "The working plan I'm recording is keeping equities at about 35 percent of the portfolio.", "asset_allocation", "confirmed", None, role="Advisor")],
    "INT-057": [cand(13, "You're still weighing reducing equities from 35 percent to about 25 percent after Frank's hospitalisation, which I've recorded as an idea and not an instruction.",
                     "asset_allocation", "exploratory", None, role="Advisor")],
    "INT-060": [cand(13, "You've decided to cut the equity allocation from 35 percent to 25 percent after Frank's hospitalisation, with proceeds going to the short-term bond ladder.",
                     "asset_allocation", "confirmed", None, role="Advisor")],
    "INT-087": [cand(11, "You've decided to raise equities from 25 percent to 30 percent and add inflation-protected bonds, because inflation could erode a very conservative portfolio over a long retirement.",
                     "asset_allocation", "confirmed", None, role="Advisor")],
}
