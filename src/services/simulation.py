"""Demo simulation: replay pre-generated interactions (data/simulation/sim_conversations.csv, written by scripts/generate_synthetic_conversations.py)
as if they had just happened, one at a time, through the REAL ingestion path.

Nothing here is a shortcut around the memory pipeline.  `ingest(iid)` does exactly two things:
  1. adds that interaction's rows to the shared conversation source (until then the system has never seen them: retrieval, the brain view and the
     evidence checks cannot reach them), and
  2. runs MemoryOrchestrator.ingest_meeting on it: LLM-first extraction, blind verifier, fact checks, M1 evidence policy, harness write validator,
     integrity boundary, consolidation.
State lives in the memory database itself (its ingestion_log), so a restart re-adds exactly the simulated interactions that were ingested, and
re-copying the demo database resets the simulation.

Enabled only outside the production profile (`enabled()`), and the API requires the `ingest` permission.
"""
from __future__ import annotations

import csv
import os
import threading
from pathlib import Path
from typing import Optional

from services.conversations import ROOT, Conversations

SIM_CSV = ROOT / "data" / "simulation" / "sim_conversations.csv"
_LOCK = threading.Lock()


def enabled(profile: str) -> bool:
    return profile != "production" and os.environ.get("CMF_SIMULATION", "on").strip().lower() not in ("off", "false", "0") and SIM_CSV.exists()


def _load(path: Optional[Path] = None) -> dict[str, list[dict]]:
    path = path or SIM_CSV                  # resolved at call time (a default argument would freeze the module value at import)
    if not path.exists():
        return {}
    out: dict[str, list[dict]] = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            out.setdefault(r["interaction_id"], []).append(dict(r, turn_index=int(r["turn_index"])))
    for rows in out.values():
        rows.sort(key=lambda r: r["turn_index"])
    return out


def restore(store, conv: Conversations, path: Optional[Path] = None) -> list[str]:
    """At startup: re-add the simulated interactions this memory database has already ingested (their memories cite them as evidence)."""
    pending = _load(path)
    if not pending:
        return []
    done = {r["interaction_id"] for r in store.query("SELECT interaction_id FROM {S}ingestion_log WHERE interaction_id LIKE 'SIM-%'")}
    todo = [i for i in sorted(done) if i in pending and i not in conv.meetings]
    with _LOCK:
        if todo:
            conv.add_rows([r for i in todo for r in pending[i]])
    return todo


DONE = ("completed", "degraded")      # ingestion_log statuses that count as ingested ("failed" / "running" can be retried)


def queue(conv: Conversations, store, client_id: Optional[str] = None, path: Optional[Path] = None) -> list[dict]:
    """Every simulated interaction (optionally of one client), in date order: its ingestion status (from the memory database) and the transcript."""
    out = []
    for iid, rows in _load(path).items():
        r0 = rows[0]
        if client_id and r0["client_id"] != client_id:
            continue
        status = store.ingestion_status(iid)
        out.append(dict(interaction_id=iid, client_id=r0["client_id"], interaction_datetime=r0["interaction_datetime"], channel=r0["channel"],
                        meeting_context=r0["meeting_context"], turns=len(rows), status=status, ingested=status in DONE,
                        transcript=[dict(turn_index=r["turn_index"], speaker=r["speaker"], utterance=r["utterance"]) for r in rows]))
    return sorted(out, key=lambda x: (x["interaction_datetime"], x["interaction_id"]))


def next_for(conv: Conversations, store, client_id: str, path: Optional[Path] = None) -> Optional[dict]:
    return next((q for q in queue(conv, store, client_id, path) if not q["ingested"]), None)


def activate(conv: Conversations, store, interaction_id: str, path: Optional[Path] = None) -> dict:
    """Make one simulated interaction part of the live source (idempotent for an earlier attempt that failed).  Refused unless it is the client's NEXT
    one in date order: memory is built in time order, and ingesting a later interaction first would show consolidation the story out of sequence."""
    rows = _load(path).get(interaction_id)
    if not rows:
        raise KeyError(interaction_id)
    with _LOCK:
        if store.ingestion_status(interaction_id) in DONE:
            raise ValueError("already ingested")
        nxt = next_for(conv, store, rows[0]["client_id"], path)
        if nxt is None or nxt["interaction_id"] != interaction_id:
            raise ValueError(f"not the next simulated interaction for this client (next: {nxt and nxt['interaction_id']})")
        if interaction_id not in conv.meetings:
            conv.add_rows(rows)
    return dict(interaction_id=interaction_id, client_id=rows[0]["client_id"], turns=len(rows))
