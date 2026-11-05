"""Report Agent: request + plan + memory contexts + tool state -> validated AdvisorReport. Data in, data out: no data-store or tool-protocol access,
no retrieval, no calculation, no memory writes."""
from __future__ import annotations

from typing import Optional

from agents.report.assemble import assemble
from agents.report.claims import render_claims, summary_from_claims, validate_claims
from agents.report.feasibility import feasibility_summary, guard_summary, is_feasibility, tool_findings
from agents.report.evidence import build_evidence, prompt_lines, PROMPT_ITEMS
from agents.report.prompts import REPORT_SYSTEM
from agents.report.validate import (NumberChecker, check_prose, deterministic_sections, deterministic_summary, evidence_text,
                                    filter_findings, no_evidence_summary)
from llm.client import LLMClient, LLMError, StructuredOutputError, generate_structured
from orchestration.tracing import NullTracer, Tracer
from schemas.memory import MemoryContext
from schemas.plan import ExecutionPlan
from schemas.report import AdvisorReport, Finding, ReportLLMOutput
from schemas.tools import ToolSelectorState


class ReportAgent:
    def __init__(self, llm: LLMClient, tracer: Optional[Tracer] = None) -> None:
        self.llm = llm
        self.tracer = tracer or NullTracer()

    # ------------------------------------------------------------------
    def write(self, request_text: str, plan: ExecutionPlan, memory: dict[str, MemoryContext], tools: ToolSelectorState) -> AdvisorReport:
        evidence, removed = build_evidence(plan, memory, tools)
        assumptions, limitations, uncertainties = assemble(plan, memory, tools, evidence)
        self.tracer.event("report", "evidence_built", items=len(evidence), by_class={c: sum(e.source_class == c for e in evidence) for c in {e.source_class for e in evidence}},
                          ownership_removed=len(removed["ownership_removed"]))
        rec: dict = {"model_called": False, "mode": "", "invalid_refs_removed": 0, "findings_dropped": [], "summary_replaced": None,
                     "claims_total": 0, "claims_accepted": 0, "claims_rejected": [], "ownership_removed": removed["ownership_removed"],
                     "evidence_items": len(evidence), "evidence_in_prompt": min(len(evidence), PROMPT_ITEMS)}
        base = dict(request_id=plan.request_id, client_id=",".join(plan.client_ids), question=request_text, evidence=evidence,
                    assumptions=assumptions, limitations=limitations)

        if not evidence:
            rec["mode"] = "no_evidence"
            self.tracer.event("report", "no_evidence_no_model_call")
            return AdvisorReport(**base, summary=no_evidence_summary(), uncertainties=uncertainties or ["No evidence of any kind was provided."],
                                 suggested_followups=self._followups(uncertainties, True), validation=rec)

        out = self._ask(request_text, plan, evidence, assumptions, limitations, uncertainties, rec)
        if out is None:
            return self._deterministic(base, plan, evidence, uncertainties, rec, "deterministic_model_failure", tools, request_text)

        nc = NumberChecker(evidence_text(evidence, request_text))
        claims, rejected = validate_claims(out.claims, evidence, plan)
        rec.update(claims_total=len(out.claims), claims_accepted=len(claims), claims_rejected=rejected)
        rendered = render_claims(claims, evidence)
        key = rendered["key_findings"] + filter_findings(out.key_findings, evidence, nc, rec)
        hist = rendered["historical_context"] + filter_findings(out.historical_context, evidence, nc, rec)
        fin = tool_findings(evidence, tools) + filter_findings(out.financial_analysis, evidence, nc, rec)
        if not (key or hist or fin):
            return self._deterministic(base, plan, evidence, uncertainties, rec, "deterministic_nothing_valid", tools, request_text)

        why = check_prose(out.summary, evidence, nc)
        summary = out.summary.strip()
        if why or not summary:
            rec["summary_replaced"] = why or "empty summary"
            summary = summary_from_claims(claims, evidence) or deterministic_summary(evidence, plan)
        fixed = guard_summary(summary, plan, request_text, evidence, fin)
        if fixed:
            rec["summary_replaced"] = (rec["summary_replaced"] or "") + " feasibility guard: summary did not state that feasibility is unconfirmed"
            summary = fixed
        unc = list(uncertainties)
        for u in out.uncertainties:
            if not check_prose(u, evidence, nc) and u not in unc:
                unc.append(u)
        fol = [f for f in out.suggested_followups if not check_prose(f, evidence, nc)] or self._followups(uncertainties, False)
        rec["mode"] = "llm"
        self.tracer.event("report", "report_written", mode="llm", claims_accepted=len(claims), claims_rejected=len(rejected), dropped=len(rec["findings_dropped"]),
                          summary_replaced=bool(rec["summary_replaced"]))
        return AdvisorReport(**base, summary=summary, key_findings=_dedupe(key), historical_context=_dedupe(hist), financial_analysis=_dedupe(fin),
                             uncertainties=unc, suggested_followups=fol[:5], validation=rec)

    # ------------------------------------------------------------------
    def _ask(self, request_text, plan, evidence, assumptions, limitations, uncertainties, rec) -> Optional[ReportLLMOutput]:
        gaps = "\n".join(f"- {u}" for u in uncertainties[:8]) or "- none"
        lims = "\n".join(f"- {x}" for x in limitations[:6]) or "- none"
        user = (f"REQUEST: {request_text}\nCLIENTS: {', '.join(plan.client_ids) or 'unknown'}\nTASK TYPE: {plan.task_type}\n"
                f"SUCCESS CRITERIA: {'; '.join(plan.success_criteria)}\nEVIDENCE:\n" + "\n".join(prompt_lines(evidence)) +
                f"\nKNOWN GAPS (code adds these to the report; do not repeat as facts):\n{gaps}\nTOOL LIMITATIONS:\n{lims}")
        rec["model_called"] = True
        try:
            return generate_structured(self.llm, "report", REPORT_SYSTEM, user, ReportLLMOutput, retries=2, tracer=self.tracer)
        except (StructuredOutputError, LLMError) as e:
            self.tracer.event("report", "llm_failure_deterministic_report", error=str(e)[:300])
            rec["model_error"] = str(e)[:300]
            return None

    def _deterministic(self, base, plan, evidence, uncertainties, rec, mode, tools, request_text) -> AdvisorReport:
        rec["mode"] = mode
        sec = deterministic_sections(evidence)
        self.tracer.event("report", "report_written", mode=mode)
        sec["financial_analysis"] = tool_findings(evidence, tools) + sec["financial_analysis"]
        summary = deterministic_summary(evidence, plan)
        if is_feasibility(plan, request_text):
            summary = feasibility_summary(evidence, sec["financial_analysis"])
        return AdvisorReport(**base, summary=summary, uncertainties=uncertainties,
                             suggested_followups=self._followups(uncertainties, False), validation=rec, **sec)

    @staticmethod
    def _followups(unc: list[str], no_evidence: bool) -> list[str]:
        out = []
        if no_evidence:
            out.append("Provide or retrieve client memory and tool results, then re-run the report.")
        if any(u.startswith("Discrepancy") for u in unc):
            out.append("Reconcile the operational profile with the client's confirmed decision.")
        if any(u.startswith("Unresolved") for u in unc):
            out.append("Review the unresolved items with the client at the next meeting.")
        if any(u.startswith(("Missing", "Tool error", "No tool results", "Tool ")) for u in unc):
            out.append("Obtain the missing information or tool results listed under uncertainties.")
        return out[:5]


def _dedupe(items: list[Finding]) -> list[Finding]:
    seen, out = set(), []
    for f in items:
        if f.text not in seen:
            seen.add(f.text)
            out.append(f)
    return out
