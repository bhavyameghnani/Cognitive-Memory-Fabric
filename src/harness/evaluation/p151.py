"""P1.5.1 - evaluation additions: the 73-probe corpus at BOTH verification boundaries (kept separate), the derived-memory path, and the P1.4.3 / P1.5.1 end-to-end scenarios
with an outcome recorded at every layer.

Four boundaries are measured separately and must never be merged into one headline number:
  1. MEMORY verification        - does the integrity boundary store the claim as `verified`?            (agents.memory.integrity / support)
  2. REPORT claim verification  - does the Evidence Verifier present a finding as VERIFIED?             (harness.evidence_verifier / clause_support)
  3. FINDING qualification      - is a finding that rests on non-verified evidence withheld / labelled?  (agents.report.validate.authority_split)
  4. APPROVAL release           - is a sensitive answer withheld until approved, and only released once? (harness.approval)

Everything here is a SELF-AUTHORED regression measurement with scripted models and in-memory SQLite.  It is not independent validation, says nothing about a real model's accuracy, and
no number here is a production accuracy.  Run:  PYTHONPATH=src python -m harness.evaluation.p151
"""
from __future__ import annotations

import json
import re
import sys
from typing import Any

from agents.memory.errors import MemoryWriteRejected
from agents.memory.integrity import Principal
from harness import ApprovalRequired, AuthContext, PermissionDenied, clause_support
from harness.evaluation import fixtures, p15
from harness.evaluation.adversarial import present
from harness.evaluation.p151_probes import P
from harness.evaluation.p15_corpus import N, V, Case15

K = "CLIENT-001"
Q = "Why did Kenji's retirement age change?"


def _case(row) -> Case15:
    pid, claim, ev, val, exp, op = row
    return Case15(pid, "probe", K, claim, ev, val, exp, "-", open_item=bool(op))


def derived_verdict(c: Case15) -> str:
    """Episode (as in the memory layer runner) and then a consolidation-class SEMANTIC memory citing the SAME turn with the same value: what does the derived memory get?"""
    run = p15.MemoryLayerRunner()
    ep, _ = run.verdict(c)
    if not run.conv.rows:
        return N
    idx = max(i for (_, i) in run.conv.rows)
    rec = dict(memory_id="SEM-" + c.id, client_id=c.client, memory_type="semantic", topic="retirement_age", subject="derived", title="t", content="retirement age = " + (c.value or ""),
               value_text=c.value or None, certainty="confirmed", status="active", origin="consolidation", content_hash="sem" + c.id, event_date="2026-06-01", valid_from="2026-06-01", version_no=1)
    ev = dict(source_type="conversation", interaction_id="INT-P15", turn_index=idx, speaker="Client", excerpt=c.evidence)
    try:
        run.store.insert_memory(rec, dict(slot_key="retirement_age|derived", basis="b", supporting_episodes=1), [ev], principal=Principal.service("consolidation", [c.client]))
    except MemoryWriteRejected:
        return N
    return V if run.store.get("SEM-" + c.id)["verification"] == "verified" else N


def evaluate_probes() -> dict:
    """The 73 probes through: memory (episodic, LLM-origin), memory (derived semantic), report verifier BEFORE P1.5.1 (clause check off) and report verifier NOW."""
    mem = p15.MemoryLayerRunner()
    systems = {"memory_episodic_llm": lambda c: mem.verdict(c)[0],
               "memory_derived_semantic": derived_verdict,
               "report_before_p151 (clause check switched off)": None,
               "report_current": None}
    out: dict = {}
    for name in systems:
        rows = []
        for r in P:
            c = _case(r)
            if name.startswith("report_before"):
                clause_support.ENABLED = False
                try:
                    actual = V if present(p15._report_case(c), "structured")["presented_as_verified"] else N
                finally:
                    clause_support.ENABLED = True
            elif name == "report_current":
                actual = V if present(p15._report_case(c), "structured")["presented_as_verified"] else N
            else:
                actual = systems[name](c)
            rows.append(dict(id=c.id, category="probe", expected=c.expected, actual=actual, correct=actual == c.expected, contradiction=False, ambiguity=False))
        out[name] = dict(overall=p15.confusion(rows), false_positive_ids=[r["id"] for r in rows if r["expected"] == N and r["actual"] == V],
                         false_negative_ids=[r["id"] for r in rows if r["expected"] == V and r["actual"] == N])
    return out


# ------------------------------------------------------------------------------------------------------------------------------ scenarios
ING = Principal.service("ingestion", [K])
FAB = "We've decided: 63. The client has also been diagnosed with a serious illness."
EVD = dict(source_type="conversation", interaction_id="INT-090", turn_index=2, speaker="Client", excerpt="We've decided: 63.")
DET = dict(interaction_id="INT-090", turn_index=2, event_ts="2026-05-01T08:15:00", speaker_role="Client", event_type="decision")
USER = Principal.user("eval-advisor", [K], authenticated=True)
CUR = re.compile(r"\[(E\d+)[^\]]*\] Current knowledge")


def _fabricated(world, mid="MEM-FAB"):
    world.store.insert_memory(dict(memory_id=mid, client_id=K, memory_type="episodic", topic="retirement_age", subject="eval_slot", content=FAB, value_text="63", certainty="confirmed",
                                   source_role="Client", status="active", origin="conversation_llm", event_date="2026-05-01", content_hash=mid), dict(DET), [dict(EVD)], principal=ING)
    return mid


def _finding(text, pattern=None):
    def h(system, user, schema):
        m = (re.search(pattern, user) if pattern else None) or CUR.search(user)
        return {"summary": "Kenji's retirement age changed from 65 to 63.", "key_findings": [{"text": text, "refs": [m.group(1)]}], "historical_context": [], "financial_analysis": [],
                "uncertainties": [], "suggested_followups": [], "claims": []}
    return h


def _presented(res, needle: str) -> bool:
    return any(needle.lower() in f.text.lower() for f in res.report.key_findings + res.report.historical_context + res.report.financial_analysis) or needle.lower() in res.report.summary.lower()


def _outcomes(res) -> dict:
    return res.report.validation.get("harness_evidence", {}).get("outcomes", {})


def _layers(**kw) -> dict:
    base = dict(storage="n/a", retrieval="n/a", context="n/a", report_evidence="n/a", finding="n/a", approval="n/a", release="n/a")
    base.update(kw)
    return base


def run_scenarios() -> list:
    out: list = []

    def rec(n, name, layers, passed, expected):
        out.append(dict(scenario=n, name=name, layers=layers, passed=bool(passed), expected=expected))
    plan = p15.plan_for(K, ("retirement_age",))

    # 1 unverified memory reaching the Report Agent
    w = p15.build_world()
    mid = _fabricated(w)
    ctx = w.retrieve(plan)[K]
    it = next(m for m in ctx.current + ctx.history + ctx.episodes + ctx.unresolved if m.memory_id == mid)
    res = p15.ask(w, Q, p15.make_harness(), report=_finding("Kenji has been diagnosed with a serious illness", r"\[(E\d+)[^\]]*\][^\n]*serious illness"))
    e = next(x for x in res.report.evidence if x.memory_id == mid)
    rec(1, "unverified memory reaches the Report Agent", _layers(storage=w.store.get(mid)["verification"], retrieval=f"verification={it.verification}", context="labelled UNVERIFIED" if "UNVERIFIED" in e.description else "unlabelled",
                                                              report_evidence=e.evidence_class, finding="not presented" if not _presented(res, "serious illness") else "PRESENTED", release=f"released={res.released}"),
        w.store.get(mid)["verification"] == "unverified" and e.evidence_class == "unverified" and not _presented(res, "serious illness") and "serious illness" not in p15.visible(res.report),
        "stored unverified; labelled; never a finding; text not echoed")

    # 2 contradicted memory
    w = p15.build_world()
    cur = w.store.records(K, "semantic", statuses=["active"], topics=["retirement_age"])[0]
    w.store.set_status(cur["memory_id"], "disputed", "eval", "x", principal=USER)
    res = p15.ask(w, Q, p15.make_harness(), report=_finding("Kenji's retirement age is settled at 63", r"\[(E\d+)[^\]]*\][^\n]*CONTRADICTED"))
    e = next(x for x in res.report.evidence if x.memory_id == cur["memory_id"])
    rec(2, "contradicted memory reaches the Report Agent", _layers(storage="status=disputed", retrieval="retrieved with status disputed", context="labelled CONTRADICTED" if "CONTRADICTED" in e.description else "unlabelled",
                                                                report_evidence=e.evidence_class, finding="not presented" if not _presented(res, "settled") else "PRESENTED", release=f"released={res.released}"),
        e.evidence_class == "contradicted" and not _presented(res, "settled"), "contradicted evidence supports no finding")

    # 3 ambiguous memory + a finding that firms it up
    w = p15.build_world()
    res = p15.ask(w, Q, p15.make_harness(), report=_finding("Kenji has decided to retire at 60", r"\[(E\d+)[^\]]*\] Episode;[^\n]*exploratory"))
    amb = [x for x in res.report.evidence if x.evidence_class == "ambiguous"]
    rec(3, "ambiguous (exploratory) memory reaches the Report Agent", _layers(storage="exploratory episodes stored verified", retrieval="retrieved as episodes", context="labelled AMBIGUOUS", report_evidence=f"{len(amb)} ambiguous",
                                                                           finding=f"outcomes={_outcomes(res)}", release=f"released={res.released}"),
        bool(amb) and not _presented(res, "decided to retire at 60") or any(t.startswith("[Qualified") for t in [f.text for f in res.report.key_findings]),
        "an exploratory value is never presented as a decision")

    # 4 retraction / conditional
    run = p15.MemoryLayerRunner()
    stored, _ = run.verdict(Case15("R1", "x", K, "I will retire at 63.", "I will retire at 63. Just kidding.", "63", N, "-"))
    cond, _ = run.verdict(Case15("R2", "x", K, "I will retire at 63.", "I will retire at 63, unless things change.", "63", N, "-"))
    w = p15.build_world()
    res = p15.ask(w, Q, p15.make_harness(), report=_finding("Kenji's current retirement age is 63 unless things change"))
    rec(4, "retraction and conditional claims", _layers(storage=f"retraction={stored}, conditional={cond}", finding=f"conditional finding presented={_presented(res, 'unless things change')}; outcomes={_outcomes(res)}"),
        stored == N and cond == N and not _presented(res, "unless things change"), "neither a retracted nor a conditional statement is stored / presented as verified")

    # 5 derived memory based on unverified evidence
    w = p15.build_world()
    unver = w.store.get(_fabricated(w))
    try:
        w.store.insert_memory(dict(memory_id="SEM-D70", client_id=K, memory_type="semantic", topic="liquidity_reserve", subject="eval_derived", title="t", content="reserve = 70", value_text="70",
                                   certainty="confirmed", status="active", origin="consolidation", content_hash="d70", event_date="2026-05-01", valid_from="2026-05-01", version_no=1),
                              dict(slot_key="liquidity_reserve|eval_derived", basis="b", supporting_episodes=1), [dict(EVD)], principal=Principal.service("consolidation", [K]))
        dv = w.store.get("SEM-D70")["verification"]
    except MemoryWriteRejected as ex:
        dv = "rejected:" + ex.code
    probe_d = derived_verdict(Case15("D1", "x", K, "I will retire at 63.", "My wife will retire at 63.", "63", N, "-"))
    rec(5, "derived memory built on unverified evidence", _layers(storage=f"unverified source -> derived {dv}; false-positive-prone source -> derived {probe_d}", retrieval="n/a", finding="n/a"),
        dv != "verified" and probe_d == N and unver["verification"] == "unverified", "a derived memory never outranks its evidence")

    # 6 human correction reaches the Report Agent
    w, cor = p15.corrected_world(with_conflict=False)
    res = p15.ask(w, Q, p15.make_harness())
    e = next(x for x in res.report.evidence if x.memory_id == cor)
    rec(6, "human correction reaches the Report Agent", _layers(storage=f"{w.store.get(cor)['source_class']}/{w.store.get(cor)['verification']}", retrieval="active", context="unlabelled (verified)", report_evidence=e.evidence_class,
                                                              finding=f"outcomes={_outcomes(res)}", release=f"released={res.released}"),
        e.memory_source_class == "human_correction" and e.evidence_class == "verified" and w.store.get(cor)["status"] == "active",
        "verified evidence that keeps precedence.  KNOWN conservative false negative (pre-existing P1.1 behaviour, unchanged): the finding 'current age is 63' is withheld because the quoted sentence also says "
        "'replacing the earlier plan of 65' (TEMPORAL_MISMATCH / MODALITY_MISMATCH)")

    # 7 open conflict reaches the Report Agent
    w, cor = p15.corrected_world()
    res = p15.ask(w, Q, p15.make_harness())
    flagged = [x for x in res.report.evidence if x.evidence_class == "open_conflict"]
    rec(7, "open conflict reaches the Report Agent", _layers(storage=f"{len(w.store.conflicts(K, 'open'))} open conflict(s)", retrieval="conflicts listed in the context", context="labelled OPEN CONFLICT",
                                                          report_evidence=f"{len(flagged)} open_conflict", finding="withheld pending review", release=f"released={res.released}"),
        bool(flagged) and any("open conflict" in u for u in res.report.uncertainties) and not any(f.refs and f.refs[0] in {x.ref for x in flagged} for f in res.report.key_findings), "review requirement visible; no finding rests on it")

    # 8 unsupported appended clause / 9 subject mismatch in a model-generated finding
    for n, name, text, needle in ((8, "unsupported appended clause in a finding", "Kenji's current retirement age is 63 and he has a terminal illness", "terminal illness"),
                                  (9, "subject mismatch in a finding", "Kenji's wife will retire at 63", "wife")):
        w = p15.build_world()
        res = p15.ask(w, Q, p15.make_harness(), report=_finding(text))
        rec(n, name, _layers(storage="verified source", report_evidence="verified", finding=f"presented={_presented(res, needle)}; outcomes={_outcomes(res)}", release=f"released={res.released}"),
            not _presented(res, needle) and _outcomes(res).get("VERIFIED", 0) == 0, "the unsupported clause is never presented as verified")

    # 10 approval rejection / 11 cache hit while pending / 12 release after the evidence changed
    sens = "Should Kenji retire at 60?"
    plan5 = fixtures.planner_json(task="forecast_feasibility")
    kadv = AuthContext.for_test("ADVISOR-001", "advisor")
    sup = AuthContext.for_test("SUP-1", "supervisor")

    def outcome_of(fn):
        try:
            fn()
            return "released"
        except ApprovalRequired as e:
            return "withheld:" + e.record.status
        except PermissionDenied as e:
            return "denied:" + e.code
    w = p15.build_world()
    h = p15.make_harness()
    r1 = p15.ask(w, sens, h, planner=plan5)
    apr = r1.harness["approval"]
    dvn = next(e["payload"]["data_version"] for e in r1.trace if e["event"] == "request_started")
    r2 = p15.ask(w, sens, h, planner=plan5)
    leak11 = bool(r2.report.key_findings) or "63" in p15.visible(r2.report)
    rec(11, "cache hit while approval is pending", _layers(approval=f"pending:{apr['approval_id'] and 'yes'}", release=f"first released={r1.released}, repeat released={r2.released}, cache_hits={r2.harness['telemetry']['cache_hits']}"),
        (not r1.released) and (not r2.released) and r2.harness["telemetry"]["cache_hits"] > 0 and not leak11, "a cached model answer is still withheld until approved")
    h.decide_approval(apr["approval_id"], sup, False, "not suitable")
    rel_after_reject = outcome_of(lambda: h.release(apr["approval_id"], dvn, actor=kadv))
    rec(10, "approval rejection", _layers(approval="rejected by SUP-1", release=rel_after_reject), rel_after_reject.startswith(("withheld", "denied")), "a rejected answer cannot be released")
    w = p15.build_world()
    h = p15.make_harness()
    r1 = p15.ask(w, sens, h, planner=plan5)
    apr = r1.harness["approval"]
    dvn = next(e["payload"]["data_version"] for e in r1.trace if e["event"] == "request_started")
    h.decide_approval(apr["approval_id"], sup, True, "reviewed")
    cur = w.store.records(K, "semantic", statuses=["active"], topics=["retirement_age"])[0]
    w.store.set_status(cur["memory_id"], "disputed", "eval", "x", principal=USER)                  # the evidence the approval rested on changes
    from harness.runtime import data_version
    rel = outcome_of(lambda: h.release(apr["approval_id"], data_version(w.store), actor=kadv))
    rec(12, "approval release after the evidence changed", _layers(approval="approved by SUP-1, then the source memory became disputed", release=rel), rel.startswith(("withheld", "denied")),
        "an approval granted before the evidence changed is no longer releasable")
    return out


def run() -> dict:
    return dict(probes=evaluate_probes(), scenarios=run_scenarios())


def main(argv=None) -> int:
    doc = run()
    print(json.dumps(doc, indent=1, default=str))
    return 0 if all(s["passed"] for s in doc["scenarios"]) else 1


if __name__ == "__main__":
    sys.exit(main())
