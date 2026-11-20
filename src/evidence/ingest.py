"""Ingestion: parse every channel, add the deterministic semantic layer, and store.  Idempotent, per-container atomic, and safe to retry.

    python src/evidence/ingest.py [--db sqlite:///build/cmf_evidence.db]

Reads files ONLY through datasets/source_catalog.discover_source_files (evaluation ground truth is unreachable).  Governance fixtures are not
ingested.  This is an operator-side batch path.  It creates no memories."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Optional

if __package__ in (None, ""):                       # allow `python src/evidence/ingest.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    __package__ = "evidence"

from . import PARSER_VERSION, semantics
from .parsers.call import parse_calls
from .parsers.chat import parse_chats
from .parsers.crm import parse_crm
from .parsers.email import parse_email_threads
from .parsers.meeting import parse_meetings
from .sources import Sources
from .store import EvidenceStore


def parse_all(src: Sources):
    """Pure: (containers, derived, failures) for the whole dataset.  Deterministic: same input, same output."""
    reg = src.registry()
    names = {}
    for cid, r in reg.items():
        if r.get("first_name"):
            names.setdefault(r["first_name"], cid)
        names.setdefault(r["display_name"], cid)
    containers, failures = [], []
    for fn in (parse_email_threads, parse_meetings, parse_calls, parse_chats):
        ok, bad = fn(src)
        containers += ok
        failures += bad
    for k in containers:
        semantics.enrich(k, names)
    derived, bad = parse_crm(src)
    failures += bad
    containers.sort(key=lambda k: (k.channel, k.container_id))
    return containers, derived, failures, reg


def ingest(store: EvidenceStore, src: Optional[Sources] = None) -> dict:
    src = src or Sources()
    containers, derived, failures, reg = parse_all(src)
    run = store.begin_run()
    counts: Counter = Counter()
    by_channel: dict = {}
    store.upsert_parties(reg)
    for k in containers:
        try:
            r = store.ingest_container(k)
        except Exception as e:                       # one bad container is recorded and does not stop the run
            store.record_failure(k.container_id, k.channel, k.client_id, f"{type(e).__name__}: {e}", k.source_file)
            r = "failed"
        counts[r] += 1
        by_channel.setdefault(k.channel, Counter())[r] += 1
    for cid, channel, client, err in failures:
        store.record_failure(cid, channel, client, err)
        counts["failed"] += 1
    for d in derived:                                # derived pathway runs after the containers so source refs can resolve
        try:
            r = store.ingest_derived(d)
        except Exception as e:
            store.record_failure(d.crm_record_id, d.channel, d.client_id, f"{type(e).__name__}: {e}", d.crm_file)
            r = "failed"
        counts[r] += 1
        by_channel.setdefault("CRM(derived)", Counter())[r] += 1
    store.finish_run(run, counts)
    return dict(run_id=run, totals=dict(counts), by_channel={k: dict(v) for k, v in by_channel.items()}, failures=failures)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=None)
    a = ap.parse_args(argv)
    store = EvidenceStore(a.db)
    rep = ingest(store)
    print(json.dumps(rep, indent=2, default=str))
    print(f"parser {PARSER_VERSION}; database {store.path}")
    return 1 if rep["totals"].get("failed") else 0


if __name__ == "__main__":
    raise SystemExit(main())
