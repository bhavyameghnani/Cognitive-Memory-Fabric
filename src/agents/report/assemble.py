"""Step 2: code-assembled assumptions, limitations and uncertainties. The model never writes these sections' facts."""
from __future__ import annotations

import re
from typing import Any, Optional

from schemas.memory import MemoryContext
from schemas.plan import ExecutionPlan
from schemas.report import EvidenceItem
from schemas.tools import ToolSelectorState

SYNTHETIC_STATEMENT = ("Operational data and tool calculations come from a synthetic dataset; they are not facts disclosed by the client. "
                       "Conversation evidence and memories are the client's own statements.")
NO_PROJECTION = ("No projection exists: only a simple funding-gap style calculation was available; there is no market-return, tax or "
                 "longevity projection, so feasibility cannot be confirmed.")
FEASIBILITY_RX = re.compile(r"\b(can|could|should|afford|feasib\w*|enough|sufficient|on track|possible)\b", re.I)


def _dedupe(xs: list[str]) -> list[str]:
    return list(dict.fromkeys(x for x in xs if x))


def _find_key(obj: Any, rx: re.Pattern) -> Optional[Any]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if rx.search(str(k)) and isinstance(v, (int, float, str)) and str(v).strip():
                return v
        for v in obj.values():
            r = _find_key(v, rx)
            if r is not None:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _find_key(v, rx)
            if r is not None:
                return r
    return None


RETIRE_KEY = re.compile(r"retire\w*_?age|target_retirement|retirement_age", re.I)


def _int(v: Any) -> Optional[int]:
    m = re.search(r"\d+", str(v))
    return int(m.group()) if m else None


def retirement_discrepancies(memory: dict[str, MemoryContext], tools: ToolSelectorState) -> list[str]:
    out = []
    for ex in tools.successes():
        env = ex.result
        if env is None:
            continue
        op = _find_key(env.data, RETIRE_KEY)
        op_age = _int(op) if op is not None else None
        if op_age is None:
            continue
        ctx = memory.get(env.client_id or "") or (next(iter(memory.values())) if len(memory) == 1 else None)
        if ctx is None:
            continue
        for m in ctx.current:
            if "retire" in (m.topic + " " + m.subject).lower() and m.value_text and _int(m.value_text) is not None and _int(m.value_text) != op_age:
                out.append(f"Discrepancy for {ctx.client_id}: memory says the retirement age is {m.value_text} (valid from {m.valid_from or 'an unstated date'}, "
                           f"memory {m.memory_id}) but the operational data ({env.tool}, as of {env.as_of or 'an unstated date'}) shows {op_age}. "
                           f"Both are reported and not reconciled; the confirmed conversation decision should be checked against the operational profile.")
    return _dedupe(out)


def assemble(plan: ExecutionPlan, memory: dict[str, MemoryContext], tools: ToolSelectorState, evidence: list[EvidenceItem]) -> tuple[list[str], list[str], list[str]]:
    assumptions: list[str] = []
    limitations: list[str] = []
    unc: list[str] = []
    if any(e.synthetic for e in evidence):
        assumptions.append(SYNTHETIC_STATEMENT)
    for ex in tools.successes():
        env = ex.result
        if env is None:
            continue
        a = env.data.get("assumptions") if isinstance(env.data, dict) else None
        if isinstance(a, list):
            assumptions += [f"{env.tool}: {x}" for x in map(str, a)]
        elif isinstance(a, str):
            assumptions.append(f"{env.tool}: {a}")
        if env.provenance.note and env.provenance.note != "All operational values are synthetic.":
            assumptions.append(f"{env.tool}: {env.provenance.note}")
        limitations += [f"{env.tool}: {x}" for x in env.limitations]
    if plan.task_type == "forecast_feasibility" or FEASIBILITY_RX.search(plan.request_text) and plan.needs_calculation:
        limitations.append(NO_PROJECTION)

    for cid, ctx in memory.items():
        for u in ctx.unresolved:
            unc.append(f"Unresolved ({cid}): {u.content}" + (f" [{u.memory_id}]" if u.memory_id else ""))
        unc += [f"Contradiction ({cid}): {c}" for c in ctx.contradictions]
    unc += [f"Missing information: {m}" for m in plan.missing_information]
    unc += [f"Missing information: {m}" for m in tools.missing_information]
    unc += [f"Tool error: {e}" for e in tools.errors]
    for ex in tools.executions:
        if ex.status != "success":
            unc.append(f"Tool {ex.tool} ({ex.id}) did not return a result: {ex.status}" + (f" ({ex.error})" if ex.error else ""))
    unc += retirement_discrepancies(memory, tools)
    wanted_tools = plan.needs_calculation or bool(plan.required_tools)
    if wanted_tools and not tools.successes():
        unc.append("No tool results were available, so no financial figures can be reported; the question's calculation part is unanswered.")
    return _dedupe(assumptions), _dedupe(limitations), _dedupe(unc)
