"""Report Agent: request + plan + memory contexts + tool state -> validated AdvisorReport. Data in, data out: no data-store or tool-protocol access,
no retrieval, no calculation, no memory writes."""
from __future__ import annotations

import os
import time
from typing import Optional

from agents.report.assemble import assemble
from agents.report.claims import render_claims, summary_from_claims, validate_claims
from agents.report.feasibility import feasibility_summary, guard_summary, is_feasibility, tool_findings
from agents.report.narrative import summary_conflict
from agents.report.timeline import build_timeline, significant, timeline_findings, timeline_summary, timeline_uncertainties
from agents.report.overview import build_overview
from agents.report import status as ST
from agents.report import synthesis as SY
from agents.report.answer import assemble_answer
from agents.report.corrections import correction_records
from agents.report.validate import source_record_problem
from agents.report.evidence import build_evidence, prompt_lines, select_for_prompt
from agents.report.semantic import (NARRATIVE_SYSTEM, STORY_SYSTEM, WINDOW_SIZE, build_checked, build_story, client_names, filter_summary, narrative_user_window, narrative_windows, story_user,
                                    timeline_prompt, window_block, window_context, window_example, reconcile_coverage, _order_key)
from agents.report.prompts import REPORT_SYSTEM
from agents.report.validate import (public_reason, authority_notes, authority_split, NumberChecker, check_prose, deterministic_sections, deterministic_summary, evidence_text,
                                    filter_findings, no_evidence_summary, source_record_findings, SOURCE_FINDINGS_LIMIT)
from llm.client import LLMClient, LLMError, StructuredOutputError, generate_structured
from orchestration.tracing import NullTracer, Tracer
from schemas.memory import MemoryContext
from schemas.plan import ExecutionPlan
from schemas.report import (AdvisorReport, ClausesLLMOutput, ExecutiveLLMOutput, Finding, NarrativeCoverage, NarrativeLLMOutput, NarrativeWindow, OutcomeStatement, ReportLLMOutput, StoryLLMOutput, SynthesisLLMOutput,
                            is_source_turn)
from schemas.tools import ToolSelectorState


MAX_WINDOW_ATTEMPTS = 2          # one try and ONE retry per window (a failed call, or a call none of whose sentences passed)
MAX_WINDOW_CALLS = 14            # and never more narrative calls than this in one report, whatever the number of windows


def report_mode() -> str:
    """CMF_REPORT_MODE: "synthesis" (default) = paraphrased, proposition-validated prose; "quotes" = the Phase 2 verified-quotation narrative.  Synthesis falls back to quotes when nothing survives."""
    return "quotes" if os.environ.get("CMF_REPORT_MODE", "synthesis").strip().lower() == "quotes" else "synthesis"


def story_enabled() -> bool:
    """The unverified model story is an optional extra (CMF_REPORT_STORY=1): it covers only part of a long history and the verified windowed narrative replaces it as the readable account."""
    return os.environ.get("CMF_REPORT_STORY", "0").strip().lower() in ("1", "true", "yes", "on")


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
                    assumptions=assumptions, limitations=limitations, timeline=tl, overview=build_overview(plan, tl, tools, evidence))

        if not evidence:
            rec["mode"] = "no_evidence"
            self.tracer.event("report", "no_evidence_no_model_call")
            return AdvisorReport(**base, summary=no_evidence_summary(), uncertainties=(uncertainties + tl_unc) or ["No evidence of any kind was provided."],
                                 suggested_followups=self._followups(uncertainties, True), validation=rec)

        out = self._ask(request_text, plan, evidence, assumptions, limitations, uncertainties, rec)
        if out is None and rec.get("main_call_timeout") and report_mode() == "synthesis" and plan.task_type == "history" and any(c.source == "source_turn" for c in tl):
            # The OVERVIEW call (summary, key findings) timed out: a model-call timeout does not stop later calls, and the checked narrative is written window by window by separate calls,
            # so it still runs.  The summary is then the code-written one, and the report says so.  Any other model failure keeps the deterministic report.
            out = ReportLLMOutput(summary="")
            uncertainties.append("The model's overview call did not finish within its time limit, so the summary is the code-written one; the narrative below was written and checked window by window.")
        if out is None:
            return self._deterministic(base, plan, evidence, uncertainties + tl_unc, rec, "deterministic_model_failure", tools, request_text, tl, incomplete)

        nc =NumberChecker(evidence_text(evidence, request_text))
        claims, rejected = validate_claims(out.claims, evidence, plan)
        rec.update(claims_total=len(out.claims), claims_accepted=len(claims), claims_rejected=rejected)
        rendered = render_claims(claims, evidence)
        key = rendered["key_findings"] + filter_findings(out.key_findings, evidence, nc, rec)
        names = client_names(request_text)
        coverage, outcome = None, None
        sentence_for: dict[str, str] = {}
        if report_mode() == "synthesis":
            narr, coverage, outcome, sentence_for = self._synthesize(request_text, plan, tl, names, rec, evidence, tools)
            if not [f for f in narr if f.window != -1]:                          # nothing survived proposition validation: the verified-quotation narrative is the bounded fallback
                rec["synthesis_fallback"] = "no paraphrased proposition passed the checks; the verified-quotation narrative was used instead"
                narr, coverage = self._narrate(request_text, plan, tl, names, rec, evidence)
        else:
            narr, coverage = self._narrate(request_text, plan, tl, names, rec, evidence)
        extras = self._answer_extras(request_text, plan, tl, names, sentence_for, rec, tools)
        if extras.get("executive") is not None:
            narr = [extras.pop("executive")] + narr                                  # the question-answering block's validated sentences, window -1: the Evidence Verifier re-checks them like every synthesised finding
        extras.pop("executive", None)
        if story_enabled():
            story = self._tell(request_text, plan, tl, names, rec)
        else:
            story, rec["story"] = [], {"attempted": False, "disabled": "the unverified story is off (CMF_REPORT_STORY=1 turns it on)", "paragraphs_returned": 0, "sentences_returned": 0,
                                       "paragraphs_accepted": 0, "sentences_rejected": []}            # B6: a readable account for humans, labelled unverified; it never enters the findings the verifier checks          # follow-up to B4: a dedicated call; accepted paragraphs carry origin "narrative_checked" and the verifier re-checks them
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
        if coverage is not None:
            coverage = reconcile_coverage(coverage, tl, hist, "withheld because it cites an unverified, contradicted or conflicting stored memory")
            base["overview"] = build_overview(plan, tl, tools, evidence, coverage)
            unc.extend(x for x in coverage_notes(coverage) if x not in unc)
        self.tracer.event("report", "report_written", **{**_why(rec), "mode": "llm", "claims_rejected": len(rejected), "dropped": len(rec["findings_dropped"]),
                                                         "summary_replaced": bool(rec["summary_replaced"])})
        return AdvisorReport(**base, summary=summary, key_findings=_dedupe(key), historical_context=_dedupe(hist), financial_analysis=_dedupe(fin),
                             uncertainties=unc, suggested_followups=fol[:5], validation=rec, story=story, coverage=coverage, outcome=outcome, **extras)

    # ------------------------------------------------------------------
    def _narrate(self, request_text, plan, tl, names, rec, evidence=None) -> tuple[list, Optional[NarrativeCoverage]]:
        """The verified narrative, written WINDOW BY WINDOW (Phase 2, B).  The relevant claims are cut into consecutive windows of about ten (semantic.narrative_windows); each window gets its own
        narrative call shown only its lines (plus the related lines of other windows, so a correction can name what it corrects).  Code judges every sentence exactly as before, and in addition
        rejects a sentence that cites outside its window, repeats claims or text already narrated, or goes back in date order across a window boundary.  A window whose call fails, or none of whose
        sentences pass, is retried ONCE; if it still yields nothing it stays a recorded gap (never silently dropped): the deterministic chronology still covers its claims and the report says so.
        Returns (findings in chronological order, coverage).  Coverage is None only when no narrative was attempted."""
        info = rec["narrative"] = {"attempted": False, "paragraphs_returned": 0, "sentences_returned": 0, "paragraphs_accepted": 0, "sentences_rejected": []}
        if plan.task_type != "history" or not any(c.source == "source_turn" and significant(c) for c in tl):
            return [], None
        windows = narrative_windows(tl)
        total = len(windows)
        cov = NarrativeCoverage(window_size=WINDOW_SIZE, relevant=sum(len(w) for w in windows), memory_only=[c.claim_id for c in tl if c.source == "memory"],
                                unresolved=[c.claim_id for w in windows for c in w if c.temporal_status in ("unresolved", "disputed", "corrected")])
        info.update(attempted=True, claims_shown=[c.claim_id for w in windows for c in w], claims_not_shown=0, windows=total)
        findings: list[Finding] = []
        narrated: set[str] = set()
        seen: set[str] = set()
        rejected_claims: set[str] = set()
        repairs: list = []
        after = None
        budget_stop = False
        risky = {e.ref for e in (evidence or []) if e.evidence_class in ("unverified", "contradicted", "open_conflict")}
        for wi, w in enumerate(windows):
            ctx = window_context(w, tl)
            entry = NarrativeWindow(index=wi, first_date=w[0].event_date, last_date=w[-1].event_date, claim_ids=[c.claim_id for c in w], context_ids=[c.claim_id for c in ctx])
            cov.windows.append(entry)
            block, ctx_block = window_block(w, ctx)
            example = window_example(w)
            t0 = time.perf_counter()
            for attempt in range(1, MAX_WINDOW_ATTEMPTS + 1):
                if budget_stop or cov.model_calls >= MAX_WINDOW_CALLS:
                    entry.status, entry.error = "skipped", entry.error or ("the request's model-call budget was used up" if budget_stop else "the cap on narrative calls was reached")
                    break
                cov.model_calls += 1
                entry.attempts = attempt
                user = narrative_user_window(request_text, block, ctx_block, wi, total, entry.first_date, entry.last_date, example, retry=attempt > 1)      # the retry is told what went wrong in general terms
                try:
                    out = generate_structured(self.llm, "report", NARRATIVE_SYSTEM, user, NarrativeLLMOutput, retries=1, tracer=self.tracer)
                except Exception as e:                                       # a window may fail; the report never does
                    entry.status, entry.error = "failed", f"{type(e).__name__}: {str(e)[:120]}"
                    self.tracer.event("report", "narrative_window_failed", window=wi, attempt=attempt, error=type(e).__name__)
                    if type(e).__name__ == "BudgetExceeded":                    # the request's budget is gone: no retry, and no further window is tried
                        budget_stop = True
                        break
                    continue
                acc: list = []
                n_rep = len(repairs)
                got, bad = build_checked(out.paragraphs, tl, names, repairs, window=entry.claim_ids, context=entry.context_ids, narrated=narrated, seen_texts=seen, after=after, accepted=acc, risky=risky)
                for r in repairs[n_rep:]:
                    r["window"] = wi
                entry.sentences_returned += sum(len(x.sentences) for x in out.paragraphs)
                entry.sentences_accepted += len(acc)
                entry.sentences_rejected += [{**r, "attempt": attempt} for r in bad]
                for r in bad:
                    rejected_claims.update(r.get("claims") or [])
                if acc:
                    for f in got:
                        f.window = wi
                    findings += got
                    after = max(_order_key(c) for c in tl if c.claim_id in {i for a in acc for i in a["primary"]})
                    entry.status = "ok" if not bad else "partial"           # judged on the attempt that was accepted (earlier attempts stay listed in sentences_rejected)
                    entry.error = None
                    break
                entry.status, entry.error = "empty", "the model answered but no sentence passed the checks"
            entry.duration_ms = int((time.perf_counter() - t0) * 1000)
            entry.quotes_repaired = sum(r.get("quotes", 0) for r in repairs if r.get("window") == wi)
        # ---- every relevant claim is in exactly one state
        failed_ids = {i for e in cov.windows if e.status in ("failed", "skipped") for i in e.claim_ids}
        for w in windows:
            for c in w:
                i = c.claim_id
                (cov.narrated if i in narrated else cov.window_failed if i in failed_ids else cov.rejected if i in rejected_claims else cov.not_cited).append(i)
        info.update(paragraphs_returned=sum(1 for _ in findings), sentences_returned=sum(e.sentences_returned for e in cov.windows), paragraphs_accepted=len(findings),
                    sentences_rejected=[{**r, "window": e.index} for e in cov.windows for r in e.sentences_rejected], quotes_repaired=sum(r.get("quotes", 0) for r in repairs),
                    repairs=repairs, model_calls=cov.model_calls, windows_ok=sum(1 for e in cov.windows if e.status == "ok"), windows_incomplete=sum(1 for e in cov.windows if e.status != "ok"),
                    claims_narrated=len(cov.narrated))
        if any(e.status in ("failed", "skipped") for e in cov.windows):
            info["error"] = "one or more narrative windows could not be written; they are listed in the coverage and the deterministic chronology stands for them"
        return findings, cov

    @staticmethod
    def _focus_terms(plan, tools) -> list:
        """The search phrases source discovery found in the client's own conversations, when the plan names NO topic (then they are the only grounded statement of what the question is about)."""
        if plan.memory_topics:
            return []
        return list((getattr(tools, "retrieval_report", None) or {}).get("terms") or [])

    def _focus_roots(self, request_text, plan, tools, names) -> tuple:
        """(all roots, main roots) of a plan with no topic: the grounded search phrases, and the best one (the subject itself, as against the relation words of the question)."""
        terms = self._focus_terms(plan, tools)
        return (ST.subject_roots(request_text, plan.memory_topics, names, terms=terms), ST.grounded_roots(terms[:1])) if terms else (set(), set())

    def _synthesize(self, request_text, plan, tl, names, rec, evidence=None, tools=None):
        """Phase 3: the report's prose, PARAPHRASED and validated proposition by proposition (agents/report/synthesis.py).  Chronological windows; per window one call for a sentence per source line, one
        retry (stricter) if the call fails or nothing passes, and ONE repair call for the lines the checks rejected when at least two were (it is told, in plain words, what each rule objected to).  A call
        cap and a request-budget stop bound the whole.  After the windows, ONE call writes the executive summary from the accepted propositions, judged the same way.  Returns (findings: the executive
        summary first (window -1), then each window's paragraphs; coverage; the code-written outcome)."""
        info = rec["narrative"] = {"attempted": False, "mode": "synthesis", "paragraphs_returned": 0, "sentences_returned": 0, "paragraphs_accepted": 0, "sentences_rejected": []}
        roots, main = self._focus_roots(request_text, plan, tools, names)
        if plan.task_type != "history" or not SY.synthesis_pool(tl, roots, main):
            return [], None, None, {}
        windows = SY.synthesis_windows(tl, roots=roots, main=main) if roots else SY.synthesis_windows(tl)
        total = len(windows)
        cov = NarrativeCoverage(window_size=SY.SYN_WINDOW, relevant=sum(len(w) for w in windows), memory_only=[c.claim_id for c in tl if c.source == "memory"],
                                unresolved=[c.claim_id for w in windows for c in w if c.temporal_status in ("unresolved", "disputed", "corrected")])
        cov.uninformative = [c.claim_id for c in tl if c.source == "source_turn" and c.relevant and SY.acknowledgement_only(c.wording)]
        info.update(attempted=True, claims_shown=[c.claim_id for w in windows for c in w], claims_not_shown=0, windows=total, repairs=0)
        findings: list[Finding] = []
        narrated: set[str] = set()
        seen: set[str] = set()
        rejected_claims: set[str] = set()
        after = None
        budget_stop = False
        risky = {e.ref for e in (evidence or []) if e.evidence_class in ("unverified", "contradicted", "open_conflict")}
        window_kept: dict = {}

        def call(system, user, schema):
            nonlocal budget_stop
            cov.model_calls += 1
            try:
                return generate_structured(self.llm, "report", system, user, schema, retries=1, tracer=self.tracer), None
            except Exception as e:                                       # a call may fail; the report never does
                if type(e).__name__ == "BudgetExceeded":                 # the request's budget is gone: no retry, and no further call is tried
                    budget_stop = True
                return None, f"{type(e).__name__}: {str(e)[:120]}"

        for wi, w in enumerate(windows):
            ctx = window_context(w, tl)
            entry = NarrativeWindow(index=wi, first_date=w[0].event_date, last_date=w[-1].event_date, claim_ids=[c.claim_id for c in w], context_ids=[c.claim_id for c in ctx])
            cov.windows.append(entry)
            by_id = {c.claim_id: c for c in w}
            block = "\n".join(SY.synthesis_line(c) for c in w)
            ctx_block = "\n".join(SY.synthesis_line(c) for c in ctx)
            start_after = after
            wk: list = []                                                # what this window has accepted, so that passes can be merged in the record's order
            window_kept[wi] = wk
            acc: list = []
            t0 = time.perf_counter()
            for attempt in range(1, MAX_WINDOW_ATTEMPTS + 1):
                if budget_stop or cov.model_calls >= MAX_WINDOW_CALLS:
                    entry.status, entry.error = "skipped", entry.error or ("the request's model-call budget was used up" if budget_stop else "the cap on narrative calls was reached")
                    break
                entry.attempts = attempt
                out, err = call(SY.SYNTHESIS_SYSTEM, SY.synthesis_user_window(request_text, block, ctx_block, wi, total, entry.first_date, entry.last_date, names, retry=attempt > 1), ClausesLLMOutput)
                if out is None:
                    entry.status, entry.error = "failed", err
                    self.tracer.event("report", "synthesis_window_failed", window=wi, attempt=attempt)
                    if budget_stop:
                        break
                    continue
                _, bad = SY.build_lines(out.lines, tl, names, window=entry.claim_ids, context=entry.context_ids, narrated=narrated, seen_texts=seen, after=start_after, accepted=acc, risky=risky, kept=wk)
                entry.sentences_returned += len(out.lines)
                entry.sentences_rejected += [{**r, "attempt": attempt} for r in bad]
                if wk:
                    entry.status, entry.error = ("ok" if not bad else "partial"), None
                    break
                entry.status, entry.error = "empty", "the model answered but no proposition passed the checks"
            # ---- one repair call for the lines the checks rejected (never when the window failed, never more than once)
            todo = [i for r in entry.sentences_rejected for i in (r.get("claims") or []) if i in by_id and i not in narrated]
            todo = list(dict.fromkeys(todo))
            if len(todo) >= 3 and entry.status in ("ok", "partial") and not budget_stop and cov.model_calls < MAX_WINDOW_CALLS:
                codes = {i: [c for r in entry.sentences_rejected if i in (r.get("claims") or []) for c in r["problems"]] for i in todo}
                rblock = "\n".join(SY.synthesis_line(by_id[i]) + "\n   NOTE: " + SY.repair_hints(codes[i], names) for i in todo)
                out, err = call(SY.SYNTHESIS_SYSTEM, SY.synthesis_user_repair(request_text, rblock, wi, total, names), ClausesLLMOutput)
                info["repairs"] += 1
                if out is not None:
                    n0 = len(wk)
                    _, bad2 = SY.build_lines(out.lines, tl, names, window=todo, context=(), narrated=narrated, seen_texts=seen, after=start_after, accepted=acc, risky=risky, kept=wk)
                    entry.sentences_returned += len(out.lines)
                    entry.sentences_rejected += [{**r, "attempt": "repair"} for r in bad2]
                    entry.attempts += 1
            # ---- the verifier's own source-record rule, applied here: a line it would refuse is not offered (and its claim is not counted as narrated)
            ref_item = {e.ref: e for e in (evidence or [])}
            for k in list(wk):
                if not all(self._source_ok(k[0], d, ref_item) for d, _ in k[1]):
                    wk.remove(k)
                    narrated.discard(k[0].claim_id)
                    entry.sentences_rejected.append({"line": None, "problems": ["source_record"], "claims": [k[0].claim_id], "attempt": "pre-check"})
            # ---- this window's findings: every accepted line, in the record's order
            if wk:
                got, badk = SY.findings_from_kept(wk, tl, names)
                for b in badk:
                    entry.sentences_rejected.append({**b, "attempt": "merge"})
                    narrated.difference_update(b["claims"])
                for f in got:
                    f.window = wi
                findings += got
                after = max(_order_key(c) for c, _, _ in wk if c.claim_id in narrated) if any(c.claim_id in narrated for c, _, _ in wk) else after
            entry.sentences_accepted = sum(1 for c, _, _ in wk if c.claim_id in narrated)
            for r in entry.sentences_rejected:
                rejected_claims.update(r.get("claims") or [])
            entry.duration_ms = int((time.perf_counter() - t0) * 1000)
        failed_ids = {i for e in cov.windows if e.status in ("failed", "skipped") for i in e.claim_ids}
        for w in windows:
            for c in w:
                i = c.claim_id
                (cov.narrated if i in narrated else cov.window_failed if i in failed_ids else cov.rejected if i in rejected_claims else cov.not_cited).append(i)
        # ---- the validated, dated sentence of every accepted turn the verifier's source-record rule accepts: what the question-answering block may use (Phase 4A; no further model call)
        allkept = [k for kk in window_kept.values() for k in kk if k[0].claim_id in narrated and not k[2]]
        ref_item = {e.ref: e for e in (evidence or [])}
        sentence_for = {ST.key_of(c): good[0][0] for c, good, _ in allkept if good and self._source_ok(c, good[0][0], ref_item)}
        props = {f"P{n}": 1 for n in range(1, sum(len(f.narrative or []) for f in findings) + 1)}
        info.update(paragraphs_returned=len(findings), sentences_returned=sum(e.sentences_returned for e in cov.windows), paragraphs_accepted=len(findings),
                    sentences_rejected=[{**r, "window": e.index} for e in cov.windows for r in e.sentences_rejected], model_calls=cov.model_calls,
                    windows_ok=sum(1 for e in cov.windows if e.status == "ok"), windows_incomplete=sum(1 for e in cov.windows if e.status != "ok"), claims_narrated=len(cov.narrated),
                    propositions=len(props), readability=SY.readability(findings))
        if any(e.status in ("failed", "skipped") for e in cov.windows):
            info["error"] = "one or more narrative windows could not be written; they are listed in the coverage and the excerpts stand for them"
        return findings, cov, OutcomeStatement(**SY.outcome_lines(tl)), sentence_for

    def _answer_extras(self, request_text, plan, tl, names, sentence_for: dict, rec: dict, tools=None) -> dict:
        """Phase 4A: the question-answering executive block, the four independent status facets and the correction records, all written by code from the timeline and the validated sentences (agents/report/answer.py,
        status.py, corrections.py).  No model call.  Only for a history request that has retrieved source turns.  Returns the AdvisorReport fields and, under "executive", the finding that carries the
        validated sentences the block uses (window -1), which the caller puts first in the narrative so that the Evidence Verifier re-checks them."""
        if plan.task_type != "history" or not any(c.source == "source_turn" for c in tl):
            return {}
        terms = self._focus_terms(plan, tools)
        block, executive = assemble_answer(request_text, tl, sentence_for, names, plan.memory_topics, terms=terms)
        txn = ST.transaction_status(tl, ST.subject_roots(request_text, plan.memory_topics, names, terms=terms))
        recs = correction_records(tl)
        info = rec.setdefault("narrative", {})
        info["executive"] = {"attempted": True, "model_call": False, "sentences": len(executive.narrative or []) if executive else 0, "slots": {s.key: s.status for s in block.slots}, "not_applicable": block.not_applicable}
        return {"answer": block, "transaction": txn, "corrections": recs, "executive": executive}

    @staticmethod
    def _source_ok(c, text: str, ref_item: dict) -> bool:
        """True when the verifier's source-record rule (speaker and turn named in the text; overreach terms only as attributed reported speech) accepts this sentence for this claim.  With no evidence
        item to check against (a unit test without one) the rule cannot be applied here and the verifier remains the judge."""
        e = ref_item.get(c.evidence_refs[0]) if c.evidence_refs else None
        return e is None or source_record_problem(SY.S.compose(text, [c]), [e], paraphrase=True) is None

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
        repairs: list = []
        findings, rejected = build_story(out.lines, tl, names, repairs)
        info.update(paragraphs_returned=len(findings), sentences_returned=len(out.lines), paragraphs_accepted=len(findings), sentences_rejected=rejected, quotes_repaired=sum(r.get('quotes', 0) for r in repairs), repairs=repairs)
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
            rec["main_call_timeout"] = "model_call_timeout" in str(e)          # the overview call ran out of time (a slow or looping local model); nothing else was refused
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


def coverage_notes(cov: NarrativeCoverage) -> list[str]:
    """Code-written uncertainty lines for the narrative windows that could not be written.  (Counts of narrated claims live in the structured coverage and the overview: they can change after a later safeguard withholds a sentence, a list of strings could not follow.)"""
    out = []
    for w in cov.windows:
        if w.status in ("failed", "skipped", "empty"):
            out.append(f"Narrative window {w.index + 1} of {len(cov.windows)} ({w.first_date or 'undated'} to {w.last_date or 'undated'}; claims {w.claim_ids[0]} to {w.claim_ids[-1]}) was not written ({w.status}"
                       + (f": {w.error}" if w.error else "") + "); its claims appear only in the code-written chronology.")
    return out


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
