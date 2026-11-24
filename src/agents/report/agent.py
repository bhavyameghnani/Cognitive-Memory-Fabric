"""Report Agent: request + plan + memory contexts + tool state -> validated AdvisorReport. Data in, data out: no data-store or tool-protocol access,
no retrieval, no calculation, no memory writes."""
from __future__ import annotations

from typing import Optional

from agents.report.assemble import assemble
from agents.report.claims import render_claims, summary_from_claims, validate_claims
from agents.report.feasibility import feasibility_summary, guard_summary, is_feasibility, tool_findings
from agents.report.narrative import summary_conflict
from agents.report.timeline import build_timeline, significant, timeline_findings, timeline_summary, timeline_uncertainties
from agents.report.evidence import build_evidence, prompt_lines, select_for_prompt
from agents.report.semantic import NARRATIVE_SYSTEM, STORY_SYSTEM, build_checked, build_story, client_names, filter_summary, narrative_user, story_user, timeline_prompt
from agents.report.prompts import REPORT_SYSTEM
from agents.report.validate import (public_reason, authority_notes, authority_split, NumberChecker, check_prose, deterministic_sections, deterministic_summary, evidence_text,
                                    filter_findings, no_evidence_summary, source_record_findings, SOURCE_FINDINGS_LIMIT)
from llm.client import LLMClient, LLMError, StructuredOutputError, generate_structured
from orchestration.tracing import NullTracer, Tracer
from schemas.memory import MemoryContext
from schemas.plan import ExecutionPlan
from schemas.report import AdvisorReport, Finding, NarrativeLLMOutput, ReportLLMOutput, StoryLLMOutput, is_source_turn
from schemas.tools import ToolSelectorState


def _unverified_in(text: str, evidence: list) -> str:
    """A value that appears in `text`, is carried by a non-definitive memory and by NO definitive evidence."""
    import re
    good = {e.value for e in evidence if e.value and e.evidence_class in (None, "verified")}
    for e in evidence:
        v = e.value
        if v and e.evidence_class not in (None, "verified") and v not in good and re.search(r"(?<![\d.])" + re.escape(v) + r"(?![\d])", text):
            return v
    return ""


class ReportAgent:
    def __init__(self, llm: LLMClient, tracer: Optional[Tracer] = None) -> None:
        self.llm = llm
        self.tracer = tracer or NullTracer()

    # ------------------------------------------------------------------
    def write(self, request_text: str, plan: ExecutionPlan, memory: dict[str, MemoryContext], tools: ToolSelectorState) -> AdvisorReport:
        evidence, removed = build_evidence(plan, memory, tools)
        assumptions, limitations, uncertainties = assemble(plan, memory, tools, evidence)
        uncertainties = list(uncertainties) + [u for u in authority_notes(evidence) if u not in uncertainties]
        for o in removed.get("episodes_omitted", []):
            uncertainties.append(f"{o['client_id']}: {o['total']} stored episodes were retrieved but only {o['shown']} are in the evidence list ({len(o['ids'])} omitted, "
                                 f"{o['critical']} of them recording a decision, correction or superseded value); the omitted ones were not used.")
        tl = build_timeline(evidence, plan.memory_topics)                         # B2B: dated, classified claims; the narrative findings and the uncertainties below are written from them by code
        tl_unc = [u for u in timeline_uncertainties(tl) if u not in uncertainties]      # code-written, quote-bearing lines: they go into the REPORT, never into the model's prompt (its protected "known gaps" block must stay small)
        shown, hidden = select_for_prompt(evidence)
        if hidden:                                           # the model sees a bounded, prioritised part of the evidence list: say what it did not see
            kinds = {"source turns": sum(is_source_turn(e) for e in hidden), "episodes": sum(e.memory_type == "episodic" and not is_source_turn(e) for e in hidden)}
            kinds["other items"] = len(hidden) - sum(kinds.values())
            uncertainties.append(f"{len(hidden)} of {len(evidence)} evidence items were not shown to the model because of its context budget "
                                 f"({', '.join(f'{v} {k}' for k, v in kinds.items() if v)}); they remain in the evidence list, which the verifier checks.")
        n_turns = sum(1 for e in evidence if is_source_turn(e))
        if n_turns > SOURCE_FINDINGS_LIMIT:
            uncertainties.append(f"{n_turns} source turns were retrieved but only the first {SOURCE_FINDINGS_LIMIT} are written out as findings; all {n_turns} are in the evidence list.")
        incomplete = bool((getattr(tools, "retrieval_report", None) or {}).get("not_retrieved")) or any("may be incomplete" in m for m in tools.missing_information) \
            or bool(removed.get("episodes_omitted")) or n_turns > SOURCE_FINDINGS_LIMIT
        self.tracer.event("report", "evidence_built", items=len(evidence), by_class={c: sum(e.source_class == c for e in evidence) for c in {e.source_class for e in evidence}},
                          ownership_removed=len(removed["ownership_removed"]))
        rec: dict = {"model_called": False, "mode": "", "invalid_refs_removed": 0, "findings_dropped": [], "summary_replaced": None,
                     "claims_total": 0, "claims_accepted": 0, "claims_rejected": [], "ownership_removed": removed["ownership_removed"],
                     "evidence_items": len(evidence), "evidence_in_prompt": len(shown), "evidence_in_prompt_refs": [e.ref for e in shown],
                     "evidence_not_in_prompt_refs": [e.ref for e in hidden], "duplicate_source_turns": removed.get("duplicate_source_turns", [])}
        base = dict(request_id=plan.request_id, client_id=",".join(plan.client_ids), question=request_text, evidence=evidence,
                    assumptions=assumptions, limitations=limitations, timeline=tl)

        if not evidence:
            rec["mode"] = "no_evidence"
            self.tracer.event("report", "no_evidence_no_model_call")
            return AdvisorReport(**base, summary=no_evidence_summary(), uncertainties=(uncertainties + tl_unc) or ["No evidence of any kind was provided."],
                                 suggested_followups=self._followups(uncertainties, True), validation=rec)

        out = self._ask(request_text, plan, evidence, assumptions, limitations, uncertainties, rec)
        if out is None:
            return self._deterministic(base, plan, evidence, uncertainties + tl_unc, rec, "deterministic_model_failure", tools, request_text, tl, incomplete)

        nc = NumberChecker(evidence_text(evidence, request_text))
        claims, rejected = validate_claims(out.claims, evidence, plan)
        rec.update(claims_total=len(out.claims), claims_accepted=len(claims), claims_rejected=rejected)
        rendered = render_claims(claims, evidence)
        key = rendered["key_findings"] + filter_findings(out.key_findings, evidence, nc, rec)
        names = client_names(request_text)
        narr = self._narrate(request_text, plan, tl, names, rec)
        story = self._tell(request_text, plan, tl, names, rec)            # B6: a readable account for humans, labelled unverified; it never enters the findings the verifier checks          # follow-up to B4: a dedicated call; accepted paragraphs carry origin "narrative_checked" and the verifier re-checks them
        hist = narr + timeline_findings(tl, evidence) + rendered["historical_context"] + source_record_findings(evidence) + filter_findings(out.historical_context, evidence, nc, rec)
        fin = tool_findings(evidence, tools) + filter_findings(out.financial_analysis, evidence, nc, rec)
        key, m1 = authority_split(key, evidence, rec)
        hist, m2 = authority_split(hist, evidence, rec)
        fin, m3 = authority_split(fin, evidence, rec)
        if not (key or hist or fin):
            return self._deterministic(base, plan, evidence, uncertainties + tl_unc, rec, "deterministic_nothing_valid", tools, request_text, tl, incomplete)

        why = check_prose(out.summary, evidence, nc)
        summary = out.summary.strip()
        if why or not summary:
            rec["summary_replaced"] = public_reason(why) if why else "empty summary"
            summary = summary_from_claims(claims, evidence) or deterministic_summary(evidence, plan)
        leaked = _unverified_in(summary, evidence)
        if leaked:                                                                  # the summary may not state what only an unverified / contradicted / ambiguous memory says
            rec["summary_replaced"] = (rec["summary_replaced"] or "") + " authority guard: summary states a value from a non-verified memory"
            summary = deterministic_summary(evidence, plan)
        fixed = guard_summary(summary, plan, request_text, evidence, fin)
        if fixed:
            rec["summary_replaced"] = (rec["summary_replaced"] or "") + " feasibility guard: summary did not state that feasibility is unconfirmed"
            summary = fixed
        unc = list(uncertainties) + [m for m in tl_unc + m1 + m2 + m3 if m not in uncertainties]
        nstat = rec.get("narrative") or {}
        if nstat.get("attempted") and not narr:                                   # say plainly that no model narrative made it through; the chronology is still the code-written record
            unc.append("No model-written narrative passed verification, so the chronology in this report is the code-written record: every statement in it quotes its source turn.")
        for u in out.uncertainties:
            if not check_prose(u, evidence, nc) and u not in unc:
                unc.append(u)
        fol = [f for f in out.suggested_followups if not check_prose(f, evidence, nc)] or self._followups(uncertainties, False)
        tls = timeline_summary(tl, incomplete) if plan.task_type == "history" else ""
        held = {e.value for e in evidence if e.memory_type == "semantic" and e.status == "active" and not e.valid_to and e.evidence_class == "verified" and e.value}
        if plan.task_type == "history" and tl:
            summary, removed_why = filter_summary(summary, tl, names, held)
            if removed_why:
                rec["summary_replaced"] = ((rec["summary_replaced"] or "") + " semantic validation removed summary sentence(s): " + ", ".join(sorted(set(removed_why)))).strip()
                if not summary.strip():
                    summary = deterministic_summary(evidence, plan) if not tls else ""
        conflict = summary_conflict(summary, tl, held) if (plan.task_type == "history" and summary.strip()) else None
        if conflict:                                                              # B3: a summary may not state a value the timeline does not hold; the neutral timeline summary replaces it
            rec["summary_replaced"] = ((rec["summary_replaced"] or "") + " timeline consistency: " + conflict).strip()
            summary = deterministic_summary(evidence, plan) if not tls else ""
        if tls and tls not in summary:
            summary = f"{summary} {tls}".strip()
        rec["mode"] = "llm"
        self.tracer.event("report", "report_written", **{**_why(rec), "mode": "llm", "claims_rejected": len(rejected), "dropped": len(rec["findings_dropped"]),
                                                         "summary_replaced": bool(rec["summary_replaced"])})
        return AdvisorReport(**base, summary=summary, key_findings=_dedupe(key), historical_context=_dedupe(hist), financial_analysis=_dedupe(fin),
                             uncertainties=unc, suggested_followups=fol[:5], validation=rec, story=story)

    # ------------------------------------------------------------------
    def _narrate(self, request_text, plan, tl, names, rec) -> list:
        """The dedicated narrative call.  Shown ONLY the timeline lines and strict writing rules; returns paragraphs of sentences, each with the claim ids it rests on.  Code judges every sentence
        (agents/report/semantic.py) and keeps the survivors as findings with origin "narrative_checked".  A model or schema failure, or a narrative with no surviving sentence, leaves the report exactly
        as B3 wrote it: the deterministic chronology never depends on this call."""
        info = rec["narrative"] = {"attempted": False, "paragraphs_returned": 0, "sentences_returned": 0, "paragraphs_accepted": 0, "sentences_rejected": []}
        if plan.task_type != "history" or not any(c.source == "source_turn" and significant(c) for c in tl):
            return []
        block, shown, left = timeline_prompt(tl)
        info.update(attempted=True, claims_shown=[c.claim_id for c in shown], claims_not_shown=left)
        try:
            out = generate_structured(self.llm, "report", NARRATIVE_SYSTEM, narrative_user(request_text, block), NarrativeLLMOutput, retries=1, tracer=self.tracer)
        except (StructuredOutputError, LLMError) as e:
            self.tracer.event("report", "narrative_call_failed", error=str(e)[:200])
            info["error"] = "the narrative call failed; the deterministic chronology stands"
            return []
        except Exception as e:                                       # never let the optional narrative take the report down
            self.tracer.event("report", "narrative_call_failed", error=type(e).__name__)
            info["error"] = "the narrative call failed; the deterministic chronology stands"
            return []
        findings, rejected = build_checked(out.paragraphs, tl, names)
        info.update(paragraphs_returned=len(out.paragraphs), sentences_returned=sum(len(p.sentences) for p in out.paragraphs), paragraphs_accepted=len(findings), sentences_rejected=rejected)
        return findings

    def _tell(self, request_text, plan, tl, names, rec) -> list:
        """The readable story: one more model call over the same timeline lines, a free-prose account rather than quotations.  Every sentence names the claims it rests on; code removes what a story must not
        contain (semantic.story_problems) but cannot check a paraphrase, so the result is returned as UNVERIFIED model prose in its own field.  Failure of any kind returns nothing and changes nothing else."""
        info = rec["story"] = {"attempted": False, "paragraphs_returned": 0, "sentences_returned": 0, "paragraphs_accepted": 0, "sentences_rejected": []}
        if plan.task_type != "history" or not any(c.source == "source_turn" and significant(c) for c in tl):
            return []
        block, shown, left = timeline_prompt(tl)
        info.update(attempted=True, claims_shown=[c.claim_id for c in shown])
        try:
            out = generate_structured(self.llm, "report", STORY_SYSTEM, story_user(request_text, block), StoryLLMOutput, retries=1, tracer=self.tracer)
        except Exception as e:                                       # StructuredOutputError, LLMError (budget, timeout), anything: the story is optional
            self.tracer.event("report", "story_call_failed", error=type(e).__name__)
            info["error"] = "the story call failed"
            return []
        findings, rejected = build_story(out.lines, tl, names)
        info.update(paragraphs_returned=len(findings), sentences_returned=len(out.lines), paragraphs_accepted=len(findings), sentences_rejected=rejected)
        return findings

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

    def _deterministic(self, base, plan, evidence, uncertainties, rec, mode, tools, request_text, tl=(), incomplete=False) -> AdvisorReport:
        rec["mode"] = mode
        sec = deterministic_sections(evidence)
        sec["historical_context"] = timeline_findings(list(tl), evidence) + sec["historical_context"]
        self.tracer.event("report", "report_written", mode=mode, **_why(rec))
        sec["financial_analysis"] = tool_findings(evidence, tools) + sec["financial_analysis"]
        summary = deterministic_summary(evidence, plan)
        tls = timeline_summary(list(tl), incomplete) if plan.task_type == "history" else ""
        if tls:
            summary = f"{summary} {tls}"
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


def _why(rec: dict) -> dict:
    """What happened to the model's draft, for the trace: counts and the public reason codes only (never the model's sentences).  Without this a 'nothing valid' report cannot be explained."""
    def reasons(items):
        out: dict = {}
        for x in items:
            r = str(x.get("reason", ""))[:120]
            out[r] = out.get(r, 0) + 1
        return out
    return dict(model_called=rec.get("model_called"), evidence_items=rec.get("evidence_items"), evidence_in_prompt=rec.get("evidence_in_prompt"), claims_total=rec.get("claims_total"),
                claims_accepted=rec.get("claims_accepted"), claim_reject_reasons=reasons(rec.get("claims_rejected") or []), findings_dropped=len(rec.get("findings_dropped") or []),
                finding_drop_reasons=reasons(rec.get("findings_dropped") or []), model_error=rec.get("model_error"))


def _dedupe(items: list[Finding]) -> list[Finding]:
    seen, out = set(), []
    for f in items:
        if f.text not in seen:
            seen.add(f.text)
            out.append(f)
    return out
