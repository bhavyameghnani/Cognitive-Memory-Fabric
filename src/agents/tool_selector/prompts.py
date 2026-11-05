"""Selector system prompt and compact catalogue/prompt rendering."""
from __future__ import annotations

SELECTOR = """You are the Tool Selector of a financial-advisory assistant. You decide which read-only tools to call to collect the structured data a task needs.
Each turn you return JSON: {"action": "call_tools" | "finish", "calls": [{"tool", "arguments", "rationale"}], "missing_information": [], "reasoning": ""}.
Rules:
- Use only tools from the TOOL CATALOGUE and only the arguments listed there. Arguments marked * are required. client_id must be one of the plan's clients.
- Call at most 5 tools per turn. Independent calls go in the same turn; a call that needs another call's result waits for the next turn.
- Dates are YYYY-MM-DD and must be month-ends from 2026-01-31 to 2026-06-30. Omit optional arguments you do not need.
- Never repeat a call that already succeeded. If a call was rejected or failed, read the message and fix it or choose another tool.
- If MEMORY already answers the question, finish without calling tools. Do not call tools just in case.
- Never calculate or invent numbers yourself. If the data or a tool needed does not exist (for example there is no projection tool), do not guess: finish and describe the gap in missing_information.
- Finish as soon as the task has what it needs. Keep reasoning and rationales short."""


def _typ(p: dict) -> str:
    if "enum" in p:
        return "|".join(str(x) for x in p["enum"])
    if "anyOf" in p:
        for a in p["anyOf"]:
            if a.get("type") != "null":
                return _typ(a)
    return {"string": "str", "integer": "int", "boolean": "bool", "number": "num"}.get(p.get("type", ""), p.get("type", "any"))


def catalogue_line(tool: dict) -> str:
    sch = tool["input_schema"]
    req = set(sch.get("required", []))
    args = ", ".join(f"{k}{'*' if k in req else ''}: {_typ(p)}" + (f"={p['default']}" if "default" in p and p["default"] is not None else "")
                     for k, p in sch.get("properties", {}).items())
    first = (tool.get("description") or "").split(". ")[0].strip().rstrip(".")
    return f"- {tool['name']}({args}) - {first[:150]}"


def render_catalogue(tools: list[dict]) -> str:
    return "\n".join(catalogue_line(t) for t in tools)
