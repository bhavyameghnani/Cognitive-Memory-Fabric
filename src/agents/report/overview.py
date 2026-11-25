"""D: the report's opening overview.  Pure code (data in, data out): no model, no store write, no inference.

It states only COUNTS and DATES taken from what the pipeline actually retrieved and classified (the Tool Selector's retrieval report, the B2B timeline), for the clients the plan was authorised
for.  It never says what a client wanted, decided or currently holds: those need the verified narrative, and an overview is not allowed to be a second, unchecked narrative.  What is established
(`verified`) and what is not (`unresolved`) are separate lists, so a reader cannot take a gap for a fact.  It carries no evidence handles and makes no claim about what anyone said."""
from __future__ import annotations

from typing import Optional

from schemas.report import ReportOverview, TimelineClaim

MAX_LISTED = 8          # uncovered meetings named one by one; beyond this only the count is given


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def build_overview(plan, tl: list[TimelineClaim], tools, evidence=(), coverage=None) -> Optional[ReportOverview]:
    """None when there is nothing to open with (no chronology and no retrieval report), e.g. a plain memory question."""
    rr = dict(getattr(tools, "retrieval_report", None) or {})
    tl = list(tl or [])
    if not tl and not rr.get("meetings_identified"):
        return None
    clients = list(plan.client_ids)
    cid = clients[0] if len(clients) == 1 else ", ".join(clients)
    name = None                                         # the report package reads no conversation store (tests/test_report_agent.py::Hygiene): the client id is the label

    retrieved = list(rr.get("retrieved") or [])
    identified = list(rr.get("meetings_identified") or [])
    not_retrieved = list(rr.get("not_retrieved") or [])
    unresolved_refs = list(rr.get("references_unresolved") or [])
    has_retrieval = bool(identified or retrieved)

    dates = sorted(r["date"] for r in retrieved if r.get("date")) or sorted({c.event_date for c in tl if c.event_date})
    first, last = (dates[0], dates[-1]) if dates else (None, None)
    turn_claims = [c for c in tl if c.source == "source_turn"]
    memory_only = [c for c in tl if c.source == "memory"]
    # a correction is shown when the record says so: a claim linked as correcting another, or one whose OWN wording is a correction (the B2B cue / modality); nothing else is called one
    corrections = sorted({(c.event_date or "", c.interaction_id or "") for c in tl
                          if any(r.get("type") == "corrects" for r in c.relations) or c.modality == "correction" or "correction" in c.cues})
    contradictions = sum(1 for c in tl for r in c.relations if r.get("type") == "contradicts") // 2
    n_turns = sum(len(r.get("turns") or []) for r in retrieved) if retrieved else len(turn_claims)

    verified: list[str] = []
    unresolved: list[str] = []
    verified.append(f"Client: {name} ({cid})." if name else f"Client: {cid}.")
    if has_retrieval:
        n_id, n_got = max(len(identified), len(retrieved)), len(retrieved)
        span = f", dated {first} to {last}" if first else ""
        verified.append(f"{n_got} of {_plural(n_id, 'meeting')} found for this subject were retrieved and read{span}; {_plural(n_turns, 'source turn')} in total.")
    elif turn_claims:
        span = f", dated {first} to {last}" if first else ""
        verified.append(f"{_plural(len(turn_claims), 'source turn')} were retrieved{span}.")
    verified.append(f"The chronology holds {_plural(len(tl), 'dated claim')}: {len(turn_claims)} from retrieved conversation turns, {len(memory_only)} from a stored memory only.")
    if corrections:
        shown = "; ".join(f"{d or 'undated'}" + (f" ({i})" if i else "") for d, i in corrections[:MAX_LISTED])
        verified.append(f"{_plural(len(corrections), 'correction')} stated in the retrieved source turns: {shown}.")
    if contradictions:
        verified.append(f"{_plural(contradictions, 'contradiction')} between retrieved statements is flagged in the chronology.")

    n_missing = len(not_retrieved)
    if n_missing:
        listed = ", ".join(f"{o['interaction_id']} ({o.get('date') or 'undated'})" for o in not_retrieved[:MAX_LISTED])
        more = f" and {n_missing - MAX_LISTED} more" if n_missing > MAX_LISTED else ""
        unresolved.append(f"{_plural(n_missing, 'meeting')} found for this subject {'was' if n_missing == 1 else 'were'} NOT retrieved (tool-call budget or a failed call): {listed}{more}. "
                          f"Nothing they say is in this report.")
        if not corrections:
            unresolved.append(f"No correction appears in the {_plural(len(retrieved), 'retrieved meeting')}, but the unretrieved meetings may hold one: the absence of a correction is not established.")
    if unresolved_refs:
        unresolved.append(f"{_plural(len(unresolved_refs), 'reference')} to another meeting could not be resolved, so no meeting was assumed.")
    refs_left = int((rr.get("counts") or {}).get("references_not_retrieved") or 0)
    if refs_left:
        unresolved.append(f"{_plural(refs_left, 'meeting')} named inside retrieved turns {'was' if refs_left == 1 else 'were'} not retrieved.")
    if memory_only:
        unresolved.append(f"{_plural(len(memory_only), 'claim')} rest only on a stored memory whose source turn was not retrieved; they are not stated as what was said.")
    errs = list(getattr(tools, "errors", None) or [])
    if errs:
        unresolved.append("A tool error occurred while retrieving evidence: " + "; ".join(str(e)[:120] for e in errs[:2]))

    ov = ReportOverview(client_id=cid, client_name=name, first_date=first, last_date=last,
                          meetings_identified=max(len(identified), len(retrieved)) if has_retrieval else None, meetings_retrieved=len(retrieved) if has_retrieval else None,
                          meetings_not_retrieved=n_missing if has_retrieval else None,
                          not_retrieved=[{"interaction_id": o["interaction_id"], "date": o.get("date"), "reason": o.get("reason")} for o in not_retrieved],
                          source_turns=n_turns, claims=len(tl), corrections=len(corrections), contradictions=contradictions,
                          references_unresolved=len(unresolved_refs), memory_only_claims=len(memory_only), verified=verified, unresolved=unresolved)
    return with_coverage(ov, coverage) if coverage is not None else ov


def coverage_lines(coverage) -> tuple[list[str], list[str]]:
    """(established, not established) lines about the verified narrative, from the coverage record alone: counts, dates and window numbers."""
    ok: list[str] = []
    gaps: list[str] = []
    rel, narr, nwin = coverage.relevant, len(coverage.narrated), len(coverage.windows)
    ok.append(f"{narr} of {_plural(rel, 'relevant source claim')} {'is' if narr == 1 else 'are'} narrated in prose that passed every check ({_plural(nwin, 'narrative window')}); the rest appear only in the code-written chronology.")
    left = rel - narr
    if left:
        parts = [f"{len(x)} {label}" for x, label in ((coverage.rejected, "had every sentence rejected by a check"), (coverage.not_cited, "were not used by the model"),
                                                          (coverage.withheld, "had a verified sentence withheld by a later safeguard (an unverified stored memory or the evidence verifier)"),
                                                          (coverage.window_failed, "belong to a window whose model call failed or was skipped")) if x]
        gaps.append(f"{_plural(left, 'relevant source claim')} {'is' if left == 1 else 'are'} not in the verified narrative: " + "; ".join(parts) + ".")
    for w in coverage.windows:
        if w.status in ("failed", "skipped", "empty"):
            gaps.append(f"Narrative window {w.index + 1} of {nwin} ({w.first_date or 'undated'} to {w.last_date or 'undated'}, {_plural(len(w.claim_ids), 'claim')}) could not be written ({w.status}"
                        + (f": {w.error}" if w.error else "") + "); its claims are shown only in the code-written chronology.")
    if coverage.unresolved:
        gaps.append(f"{_plural(len(coverage.unresolved), 'relevant claim')} {'is' if len(coverage.unresolved) == 1 else 'are'} left unresolved, disputed or corrected by the record itself.")
    return ok, gaps


def with_coverage(ov: ReportOverview, coverage) -> ReportOverview:
    """The overview with its narrative-coverage lines (re)computed from `coverage`; used again after a later safeguard has withheld sentences, so the overview never claims more than the report holds."""
    ok, gaps = coverage_lines(coverage)
    return ov.model_copy(update=dict(relevant_claims=coverage.relevant, narrated_claims=len(coverage.narrated), windows=len(coverage.windows),
                                     windows_incomplete=sum(1 for w in coverage.windows if w.status != "ok"), narrative_verified=ok, narrative_unresolved=gaps))
