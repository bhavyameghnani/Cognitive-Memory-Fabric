"""CLI.  Run from src/:  python -m orchestration.cli init-memory --reset | ingest --client CLIENT-001 | ask "..." | trace <request_id>

Needs a running Ollama (CMF_LLM_*) or an OpenAI key for ingest and ask; init-memory and trace do not.

The CLI uses the SAME harness path as the API (budget, permission gate, evidence verification, approval, write validation).  It runs as the local operator
(AuthContext.local_operator): whoever can run this command already has the data files, which is a statement of fact, not an authentication scheme.
Harness settings come from HARNESS_* variables; in the production profile the harness cannot be switched off.
"""
from __future__ import annotations

import argparse
import json
import os

from agents.memory.agent import MemoryOrchestrator
from agents.report.render import render_report
from harness import AuthContext, ConfigError, Harness, HarnessConfig, PermissionDenied
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
    p.add_argument("--actor", default=os.environ.get("CMF_CLI_ACTOR", "local-operator"), help="actor id recorded in the trace and approvals")
    p.add_argument("--no-harness", action="store_true", help="development/test profiles only: run the original pipeline without the harness")
    p = sub.add_parser("reverify", help="re-check legacy (pre-integrity-boundary) memories against their transcript turns; the ones that pass become 'verified' (append-only)")
    p.add_argument("--client", help="only this client (default: every client and the GLOBAL procedures)")
    p.add_argument("--actor", default=os.environ.get("CMF_CLI_ACTOR", "local-operator"), help="operator id recorded in the audit table")
    p = sub.add_parser("trace")
    p.add_argument("request_id")
    a = ap.parse_args(argv)

    if a.cmd == "trace":
        print(render_trace(a.request_id))
        return 0
    if a.cmd == "reverify":                                   # offline operator maintenance: needs neither the LLM nor the harness
        from agents.memory.config import GLOBAL_CLIENT
        from agents.memory.integrity import Principal
        from agents.memory.store import MemoryStore
        store = MemoryStore()
        store.init()
        clients = [r["client_id"] for r in store.query("SELECT DISTINCT client_id FROM {S}memory_records")] + [GLOBAL_CLIENT]
        print(json.dumps(store.reverify_legacy(principal=Principal.user(a.actor, clients, authenticated=True), client_id=a.client), indent=2, sort_keys=True))
        store.close()
        return 0
    try:
        cfg = HarnessConfig.from_env()
    except ConfigError as e:
        print(f"harness configuration error: {e}")
        return 2
    for w in cfg.warnings:
        print(f"warning: {w}")
    harness = Harness(cfg) if cfg.enabled else None
    if getattr(a, "no_harness", False):
        if cfg.profile == "production":
            print("--no-harness is refused in the production profile (set HARNESS_PROFILE=development to experiment)")
            return 2
        harness = None
    llm = make_llm()
    mem = MemoryOrchestrator(llm, write_validator=harness.write_validator() if harness else None)
    if a.cmd == "init-memory":
        print(json.dumps(mem.init(reset=a.reset)))
    elif a.cmd == "ingest":
        mem.init()
        for r in mem.ingest_client(a.client):
            print(json.dumps(r, default=str))
    elif a.cmd == "ask":
        mem.init()
        try:
            res = run_pipeline(a.question, llm, mem, harness=harness, auth=AuthContext.local_operator(a.actor))
        except PermissionDenied as e:
            print(f"denied: {e}")
            return 3
        print(res.report.model_dump_json(indent=2) if a.json else render_report(res.report))
        if res.harness:
            b, t = res.harness["budget"], res.harness["telemetry"]
            print(f"\n(harness: {b['llm_calls']} model calls, {b['tool_calls']} tool calls, {b['input_tokens']}+{b['output_tokens']} tokens "
                  f"[{'exact' if t['exact_usage_calls'] and not t['estimated_usage_calls'] else 'estimated'}], cache hits {t['cache_hits']}"
                  + (f", BUDGET EXHAUSTED: {', '.join(e['reason'] for e in b['exhausted'])}" if b["exhausted"] else "") + ")")
        if not res.released:
            print(f"\nThis answer is withheld pending human approval: {res.harness['approval']['approval_id']}")
        print(f"\n(trace: python -m orchestration.cli trace {res.plan.request_id})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
