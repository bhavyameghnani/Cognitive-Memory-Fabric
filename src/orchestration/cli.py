"""CLI.  Run from src/:  python -m orchestration.cli init-memory --reset | ingest --client CLIENT-001 | ask "..." | trace <request_id>

Needs a running Ollama (CMF_LLM_*) for ingest and ask; init-memory and trace do not.
"""
from __future__ import annotations

import argparse
import json

from agents.memory.agent import MemoryOrchestrator
from agents.report.render import render_report
from llm.client import make_llm
from orchestration.pipeline import run_pipeline
from orchestration.tracing import render_trace


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="orchestration.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init-memory")
    p.add_argument("--reset", action="store_true")
    p = sub.add_parser("ingest")
    p.add_argument("--client", required=True)
    p = sub.add_parser("ask")
    p.add_argument("question")
    p.add_argument("--json", action="store_true")
    p = sub.add_parser("trace")
    p.add_argument("request_id")
    a = ap.parse_args(argv)

    if a.cmd == "trace":
        print(render_trace(a.request_id))
        return 0
    llm = make_llm()
    mem = MemoryOrchestrator(llm)
    if a.cmd == "init-memory":
        print(json.dumps(mem.init(reset=a.reset)))
    elif a.cmd == "ingest":
        mem.init()
        for r in mem.ingest_client(a.client):
            print(json.dumps(r, default=str))
    elif a.cmd == "ask":
        mem.init()
        res = run_pipeline(a.question, llm, mem)
        print(res.report.model_dump_json(indent=2) if a.json else render_report(res.report))
        print(f"\n(trace: python -m orchestration.cli trace {res.plan.request_id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
