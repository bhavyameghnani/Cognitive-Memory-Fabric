"""Code-built financial analysis and the feasibility guard.

The model may not conclude that a goal is feasible: no projection tool exists, so the only honest statement is the decision on record plus the simple
funding gap. Figures here are quoted from tool results (never computed or invented), each cited by evidence handle."""
from __future__ import annotations

import re
from typing import Any, Optional

from schemas.plan import ExecutionPlan
from schemas.report import EvidenceItem, Finding
from schemas.tools import ToolSelectorState

FEASIBILITY_Q = re.compile(r"\b(can|could|should|is it possible|able to|afford|enough|sufficient|feasib)\b", re.I)
ASSERTS = re.compile(r"\b(can|could|is able to|will be able to|is feasible|is affordable|is sufficient|on track|has enough|can afford|is possible)\b[^.]{0,40}", re.I)
HEDGE = re.compile(r"\b(cannot|can't|can not|not (?:be )?(?:confirmed|determined|established)|unable|no projection|not a projection|unconfirmed)\b", re.I)


def is_feasibility(plan: ExecutionPlan, request_text: str) -> bool:
    return plan.task_type == "forecast_feasibility" or bool(FEASIBILITY_Q.search(request_text) and plan.needs_calculation)


def _money(v: Any) -> str:
    return f"${float(v):,.2f}"


def _ref_for(evidence: list[EvidenceItem], execution_id: str) -> Optional[str]:
    return next((e.ref for e in evidence if e.tool_execution_id == execution_id), None)


def tool_findings(evidence: list[EvidenceItem], tools: ToolSelectorState) -> list[Finding]:
    """Deterministic financial-analysis findings quoted from successful tool results."""
    out: list[Finding] = []
    for ex in tools.successes():
        env, ref = ex.result, _ref_for(evidence, ex.id)
        if env is None or ref is None:
            continue
        d, asof = env.data, env.as_of or "the stated date"
        if env.tool == "calculate_goal_funding_gap":
            for g in d.get("goals", []):
                out.append(Finding(text=f"Simple funding gap for the {g.get('type', 'goal')} goal as of {asof}: target {_money(g['target'])}, "
                                        f"{d.get('resources_basis', 'resources').replace('_', ' ')} {_money(g['resources'])}, gap {_money(g['simple_gap'])}, "
                                        f"coverage {g['coverage_percent']}% (synthetic; not a projection).", refs=[ref]))
        elif env.tool == "calculate_liquidity_ratio":
            out.append(Finding(text=f"Liquidity as of {asof}: cash covers {d.get('cash_months')} months of expenses against a {d.get('target_months')}-month target "
                                    f"(shortfall {_money(d.get('shortfall_amount', 0))}) (synthetic).", refs=[ref]))
        elif env.tool == "calculate_portfolio_concentration":
            out.append(Finding(text=f"Concentration by {d.get('dimension', 'dimension')} as of {asof}: top {d.get('top_n')} holdings are {d.get('top_n_percent')}% "
                                    f"of the portfolio; Herfindahl index {d.get('herfindahl_index')} (synthetic).", refs=[ref]))
        elif env.tool == "calculate_asset_allocation" and d.get("allocation"):
            parts = ", ".join(f"{a['group']} {a['percent']}%" for a in d["allocation"][:5])
            out.append(Finding(text=f"Allocation as of {asof}: {parts} (synthetic).", refs=[ref]))
    return out


def feasibility_summary(evidence: list[EvidenceItem], fin: list[Finding]) -> str:
    cur = next((e for e in evidence if e.memory_type == "semantic" and e.status == "active" and not e.valid_to and e.value), None)
    gap = next((f for f in fin if f.text.startswith("Simple funding gap")), None)
    parts = []
    if cur:
        parts.append(f"The confirmed {cur.topic.replace('_', ' ')} is {cur.value}" + (f" (decided {cur.valid_from})" if cur.valid_from else "") + f" [{cur.ref}]")
    if gap:
        parts.append(gap.text.rstrip(".") + f" [{gap.refs[0]}]")
    parts.append("Whether this is feasible cannot be confirmed: no projection (growth, inflation, taxes, drawdown) is available")
    return ". ".join(parts) + "."


def guard_summary(summary: str, plan: ExecutionPlan, request_text: str, evidence: list[EvidenceItem], fin: list[Finding]) -> Optional[str]:
    """Return a replacement summary when a feasibility answer asserts feasibility, or fails to say it is unconfirmed; else None."""
    if not is_feasibility(plan, request_text):
        return None
    if ASSERTS.search(summary) and not HEDGE.search(summary):
        return feasibility_summary(evidence, fin)
    if not HEDGE.search(summary):
        return feasibility_summary(evidence, fin)
    return None
