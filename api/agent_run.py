"""Builds the 'agent run' summary the UI shows under an answer: stage timings, plan, which memories were retrieved / cited, tool calls and any degradation.
Pure function over a PipelineResult; defensive (getattr) so a missing piece never breaks /api/ask."""
from __future__ import annotations

from typing import Any

GROUPS = ("current", "history", "episodes", "procedures", "working", "unresolved")
DEGRADED = ("failure", "fallback", "unavailable")


def _g(o: Any, name: str, default: Any = None) -> Any:
    return getattr(o, name, default)


def build_agent_run(res: Any) -> dict:
    plan, tools, report = _g(res, "plan"), _g(res, "tools"), _g(res, "report")
    cited = {e.memory_id for e in (_g(report, "evidence", []) or []) if _g(e, "memory_id")}
    retrieved: list[dict] = []
    seen: set[str] = set()
    by_group = dict.fromkeys(GROUPS, 0)
    contradictions: list[str] = []
    for cid, ctx in (_g(res, "memory", {}) or {}).items():
        contradictions += list(_g(ctx, "contradictions", []) or [])
        for grp in GROUPS:
            for m in _g(ctx, grp, []) or []:
                by_group[grp] += 1
                mid = _g(m, "memory_id")
                if mid in seen:
                    continue
                seen.add(mid)
                retrieved.append(dict(memory_id=mid, client_id=cid, group=grp, memory_type=_g(m, "memory_type"), topic=_g(m, "topic"),
                                      label=_g(m, "value_text") or _g(m, "title") or "", status=_g(m, "status"), cited=mid in cited))
    execs = [dict(tool=e.tool, status=e.status, duration_ms=e.duration_ms, rationale=e.rationale, error=e.error) for e in (_g(tools, "executions", []) or [])]
    degraded = sorted({f"{e['component']}: {e['event']}" for e in (_g(res, "trace", []) or []) if any(k in e["event"] for k in DEGRADED)})
    return dict(
        timings_ms=_g(res, "timings_ms", {}) or {},
        plan=dict(intent=_g(plan, "user_intent"), task_type=_g(plan, "task_type"), topics=_g(plan, "memory_topics", []),
                  memory_categories=_g(plan, "required_memory_categories", []),
                  subtasks=[dict(id=t.id, agent=t.agent, description=t.description) for t in (_g(plan, "subtasks", []) or [])]),
        memory=dict(total=len(retrieved), cited=sum(1 for r in retrieved if r["cited"]), by_group=by_group, contradictions=contradictions, items=retrieved),
        tools=dict(status=_g(tools, "completion_status"), stop_reason=_g(tools, "stop_reason"), iterations=_g(tools, "iterations", 0),
                   fallback_used=_g(tools, "fallback_used", False), executions=execs, missing_information=_g(tools, "missing_information", [])),
        report=dict(validation=_g(report, "validation", {}) or {}, evidence=len(_g(report, "evidence", []) or [])),
        degraded=degraded,
        harness=_g(res, "harness", None) or None,
    )
