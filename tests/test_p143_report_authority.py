"""P1.4.3 F2 - the integrity boundary's verdict (verification, basis, source class, provenance, temporal status, open conflicts) must reach the Report Agent, and a memory that is not
verified must never be presented as an established client fact.  Everything runs through the real retrieval -> evidence -> report path on in-memory SQLite."""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from agents.memory.integrity import Principal  # noqa: E402
from agents.report.claims import validate_claims  # noqa: E402
from harness.evaluation import fixtures, p15  # noqa: E402
from schemas.memory import MemoryItem  # noqa: E402
from schemas.report import Claim  # noqa: E402

K = "CLIENT-001"
Q = "Why did Kenji's retirement age change?"
ING = Principal.service("ingestion", [K])
FAB = "We've decided: 63. The client has also been diagnosed with a serious illness."
EVD = dict(source_type="conversation", interaction_id="INT-090", turn_index=2, speaker="Client", excerpt="We've decided: 63.")
DET = dict(interaction_id="INT-090", turn_index=2, event_ts="2026-05-01T08:15:00", speaker_role="Client", event_type="decision")
USER = Principal.user("audit-advisor", [K], authenticated=True)
ILLNESS = "Kenji has been diagnosed with a serious illness"


def _fabricated(world, mid="MEM-FAB"):
    rec = dict(memory_id=mid, client_id=K, memory_type="episodic", topic="retirement_age", subject="audit_slot", content=FAB, value_text="63", certainty="confirmed", source_role="Client",
               status="active", origin="conversation_llm", event_date="2026-05-01", content_hash=mid)
    world.store.insert_memory(rec, dict(DET), [dict(EVD)], principal=ING)
    return mid


def _wants(text, status="current", pattern=r"\[(E\d+)[^\]]*\][^\n]*serious illness"):
    """A report model that WANTS to state `text` as a finding, a claim and the summary, citing the memory its prompt line matches (else the current knowledge)."""
    def h(system, user, schema):
        m = re.search(pattern, user) or re.search(r"\[(E\d+)[^\]]*\] Current knowledge", user)
        ref = m.group(1)
        return {"summary": text, "key_findings": [{"text": text, "refs": [ref]}], "historical_context": [], "financial_analysis": [], "uncertainties": [], "suggested_followups": [],
                "claims": [{"subject": "Kenji", "topic": "retirement_age", "value": "63", "status": status, "evidence_ref": ref, "text": text}]}
    return h


def _definitive(report):
    return " ".join([report.summary] + [f.text for f in report.key_findings + report.historical_context + report.financial_analysis])


def test_the_verification_fields_travel_with_the_retrieved_memory():
    w = p15.build_world()
    mid = _fabricated(w)
    ctx = w.retrieve(p15.plan_for(K, ("retirement_age",)))[K]
    item = next(m for m in ctx.current + ctx.history + ctx.episodes + ctx.unresolved if m.memory_id == mid)
    assert (item.verification, item.source_class) == ("unverified", "llm_extraction") and item.verification_basis and item.temporal_status
    ok = next(m for m in ctx.current if m.status == "active")
    assert ok.verification == "verified" and ok.source_class


def test_a_memory_built_without_verification_information_defaults_to_unverified():
    assert MemoryItem(memory_id="x", client_id=K, memory_type="episodic", topic="t", content="c").verification == "unverified"


def test_unverified_fabricated_clause_never_becomes_a_definitive_finding():
    w = p15.build_world()
    mid = _fabricated(w)
    res = p15.ask(w, Q, p15.make_harness(), report=_wants(ILLNESS))
    item = next(e for e in res.report.evidence if e.memory_id == mid)
    assert (item.verification, item.evidence_class, item.memory_source_class) == ("unverified", "unverified", "llm_extraction") and "UNVERIFIED" in item.description
    assert item.interaction_id == "INT-090" and item.turn_index == 2 and item.excerpt                      # provenance is carried
    assert "serious illness" not in _definitive(res.report)                                               # not a finding, not a claim, not the summary
    assert "serious illness" not in p15.visible(res.report)                                               # the withheld text is not echoed anywhere visible
    assert any("unverified" in u and item.ref in u for u in res.report.uncertainties)                     # the review requirement IS visible


def test_the_unverified_memory_is_listed_after_the_verified_knowledge():
    w = p15.build_world()
    mid = _fabricated(w)
    res = p15.ask(w, Q, p15.make_harness(), report=_wants(ILLNESS))
    order = [(e.memory_id, e.evidence_class) for e in res.report.evidence if e.memory_id]
    assert order.index((mid, "unverified")) > order.index(next(x for x in order if x[1] == "verified"))


def test_contradicted_evidence_cannot_support_a_finding():
    w = p15.build_world()
    cur = w.store.records(K, "semantic", statuses=["active"], topics=["retirement_age"])[0]
    w.store.set_status(cur["memory_id"], "disputed", "checking", "reviewer", principal=USER)
    res = p15.ask(w, Q, p15.make_harness(), report=_wants("Kenji's retirement age is settled at 63", pattern=r"\[(E\d+)[^\]]*\][^\n]*CONTRADICTED"))
    item = next(e for e in res.report.evidence if e.memory_id == cur["memory_id"])
    assert item.evidence_class == "contradicted" and "CONTRADICTED" in item.description
    assert not any(f.refs == [item.ref] for f in res.report.key_findings + res.report.historical_context)
    assert any("contradicted" in u for u in res.report.uncertainties)


def test_ambiguous_exploratory_evidence_cannot_back_a_current_or_superseded_claim():
    w = p15.build_world()
    res = p15.ask(w, Q, p15.make_harness())
    amb = [e for e in res.report.evidence if e.evidence_class == "ambiguous"]
    assert amb
    plan = fixtures.plan() if hasattr(fixtures, "plan") else p15.plan_for(K, ("retirement_age",))
    for status in ("current", "superseded"):
        ok, rej = validate_claims([Claim(subject="Kenji", topic="retirement_age", value=amb[0].value, status=status, evidence_ref=amb[0].ref, text="Kenji's retirement age is settled")],
                                  res.report.evidence, plan)
        assert not ok and rej, status


def test_structured_claims_citing_unverified_contradicted_or_conflicted_memory_are_rejected_directly():
    """validate_claims itself (not only the later finding filter) refuses them; a claim on verified evidence is accepted."""
    w = p15.build_world()
    mid = _fabricated(w)
    res = p15.ask(w, Q, p15.make_harness())
    plan = fixtures.plan() if hasattr(fixtures, "plan") else p15.plan_for(K, ("retirement_age",))
    ev = res.report.evidence
    bad = next(e for e in ev if e.memory_id == mid)
    good = next(e for e in ev if e.evidence_class == "verified" and e.status == "active" and e.memory_type == "semantic")
    mk = lambda e, st="current": Claim(subject="Kenji", topic="retirement_age", value=e.value, status=st, evidence_ref=e.ref, text="Kenji's retirement age")      # noqa: E731
    ok, rej = validate_claims([mk(bad)], ev, plan)
    assert not ok and "unverified" in rej[0]["reason"]
    for cls in ("contradicted", "open_conflict"):
        bad.evidence_class = cls
        ok, rej = validate_claims([mk(bad)], ev, plan)
        assert not ok and cls in rej[0]["reason"]
    ok, rej = validate_claims([mk(good)], ev, plan)
    assert ok and not rej


def test_a_human_correction_is_verified_evidence_and_keeps_precedence():
    w, cor = p15.corrected_world(with_conflict=False)
    res = p15.ask(w, Q, p15.make_harness())
    item = next(e for e in res.report.evidence if e.memory_id == cor)
    assert (item.memory_source_class, item.verification, item.evidence_class) == ("human_correction", "verified", "verified")
    assert w.store.get(cor)["status"] == "active"


def test_both_sides_of_an_open_conflict_are_labelled_review_required():
    w, cor = p15.corrected_world()
    assert w.store.conflicts(K, "open")
    res = p15.ask(w, Q, p15.make_harness())
    flagged = [e for e in res.report.evidence if e.evidence_class == "open_conflict"]
    assert flagged and all(e.conflict_ids and "OPEN CONFLICT" in e.description for e in flagged)
    assert any("open conflict" in u for u in res.report.uncertainties)


def test_verified_memory_is_unchanged_and_still_supports_findings():
    w = p15.build_world()
    res = p15.ask(w, Q, p15.make_harness())
    cur = [e for e in res.report.evidence if e.evidence_class == "verified" and e.status == "active"]
    assert cur and res.report.key_findings and all("UNVERIFIED" not in e.description for e in cur)


def test_a_foreign_clients_memory_is_still_excluded_from_the_evidence():
    w = p15.build_world(rebecca=True)
    res = p15.ask(w, Q, p15.make_harness())
    assert {e.client_id for e in res.report.evidence if e.client_id} <= {K}


def test_compression_keeps_verification_labels_byte_identical_and_drops_labelled_review_lines_last():
    """Compression only deletes whole lines.  A surviving line keeps its [evidence class] tag and its UNVERIFIED / OPEN CONFLICT label exactly; lines the integrity boundary labelled as
    not-verified are treated as review requirements (dropped after the plain episodes)."""
    from agents.report.evidence import prompt_lines
    from harness import compression
    from harness.token_counter import TokenCounter
    w = p15.build_world()
    mid = _fabricated(w)
    res = p15.ask(w, Q, p15.make_harness(), report=_wants(ILLNESS))
    lines = prompt_lines(res.report.evidence)
    labelled = [ln for ln in lines if "UNVERIFIED" in ln]
    assert labelled and all(", unverified]" in ln for ln in labelled)                       # the prompt tag AND the description label
    assert compression.classify(labelled[0]) in ("episode_critical", "protected")           # never in the cheap-to-drop tier
    text = "\n".join(lines)
    tc = TokenCounter()
    out = compression.compress(text, max(20, tc.count(text).tokens // 2), tc)
    kept = set(out.text.splitlines())
    assert all(ln in lines for ln in kept if ln.startswith("[E"))                           # surviving evidence lines are byte-identical originals
    assert mid in {e.memory_id for e in res.report.evidence}
