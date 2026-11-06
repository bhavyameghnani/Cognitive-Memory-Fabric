"""Trace helpers on top of the existing orchestration.tracing.Tracer (one request id, one JSONL file, shared by every agent and the harness).

Prompts are never written raw: the harness records hashes, token counts and IDs.  Retention is bounded by age and by file count.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Iterable, Optional

from orchestration.tracing import TRACE_DIR


def prune_traces(directory: Optional[Path] = None, retention_days: int = 30, max_files: int = 2000) -> int:
    """Delete trace files older than retention_days, then the oldest beyond max_files.  Returns how many were removed."""
    d = Path(directory or TRACE_DIR)
    if not d.is_dir():
        return 0
    files = sorted((p for p in d.glob("*.jsonl")), key=lambda p: p.stat().st_mtime)
    removed, cutoff = 0, time.time() - retention_days * 86400
    keep = []
    for p in files:
        if p.stat().st_mtime < cutoff:
            try:
                p.unlink()
                removed += 1
            except OSError:
                keep.append(p)
        else:
            keep.append(p)
    for p in keep[: max(0, len(keep) - max_files)]:
        try:
            p.unlink()
            removed += 1
        except OSError:
            pass
    return removed


def influence_record(plan, retrieved: dict, selection, report, verification=None, tools=None) -> dict:
    """Answers: which memories influenced the response, which were retrieved but excluded, which evidence supports which claim."""
    retrieved_ids = [m.memory_id for ctx in retrieved.values() for g in ("current", "history", "episodes", "procedures", "working", "unresolved") for m in getattr(ctx, g)]
    from agents.report.evidence import PROMPT_ITEMS
    retrieved_ids = list(dict.fromkeys(retrieved_ids))
    excluded = list(selection.excluded) if selection is not None else []
    # what the model could see: only the first PROMPT_ITEMS evidence items are put in the Report Agent's prompt
    shown = list(dict.fromkeys(e.memory_id for e in report.evidence[:PROMPT_ITEMS] if e.memory_id))
    cited = list(dict.fromkeys(m for c in (verification.claim_evidence() if verification is not None else []) for m in c["memory_ids"]))
    return dict(
        request_id=plan.request_id, client_ids=list(plan.client_ids), memories_retrieved=retrieved_ids,
        memories_excluded=[x for x in excluded], memories_shown_to_model=[m for m in shown if m in retrieved_ids],
        memories_cited_by_final_claims=cited,
        evidence=[dict(ref=e.ref, source_class=e.source_class, memory_id=e.memory_id, tool_execution_id=e.tool_execution_id, client_id=e.client_id) for e in report.evidence],
        claim_evidence=(verification.claim_evidence() if verification is not None else []),
        tool_calls=[dict(id=x.id, tool=x.tool, status=x.status) for x in (tools.executions if tools is not None else [])])
