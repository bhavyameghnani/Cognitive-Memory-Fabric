"""Harness governance: permission gate, evidence verifier, temporal conflicts, memory write validator (adversarial), human approval."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tests"))

from test_memory_support import (CONV, KENJI, KENJI_MEETINGS, KENJI_SCRIPT, REBECCA, cand, make, plan)  # noqa: E402

from agents.memory import ingest as ing  # noqa: E402
from agents.memory.consolidate import Version  # noqa: E402
from agents.report.evidence import build_evidence  # noqa: E402
from harness import (ApprovalGate, ApprovalPolicy, ApprovalProposal, ApprovalRequired, AuthContext, HarnessConfig, PermissionDenied, PermissionGate,  # noqa: E402
                     RiskCategory, resolve_identity)
from harness.evidence_verifier import EvidenceVerifier  # noqa: E402
from harness.memory_write_validator import MemoryWriteValidator, classify_statement  # noqa: E402
from harness.temporal_conflict import detect, publish  # noqa: E402
from schemas.memory import MemoryCandidate, MemoryContext, MemoryItem  # noqa: E402
from schemas.report import AdvisorReport, EvidenceItem, Finding  # noqa: E402
from schemas.tools import Envelope, Provenance, ToolExecution, ToolSelectorState  # noqa: E402

PROD = HarnessConfig()                      # production defaults
DEV = HarnessConfig.for_test()
K_ADV, R_ADV = "ADVISOR-001", "ADVISOR-002"   # Kenji's and Rebecca's advisors (data/conversations.csv)


def gate(cfg=PROD):
    return PermissionGate(cfg, CONV.directory)


# ==================================================================================================================== permission gate
def test_advisor_cannot_read_another_advisors_client():
    g = gate()
    a = AuthContext.for_test(K_ADV, "advisor")
    assert g.check(a, "read_memory", [KENJI]).allowed
    with pytest.raises(PermissionDenied) as e:
        g.check(a, "read_memory", [REBECCA])                         # Client A's advisor requesting Client B's memories
    assert e.value.code == "client_not_in_scope"
    with pytest.raises(PermissionDenied):
        g.check(a, "read_memory", [KENJI, REBECCA])                  # one foreign id poisons the whole request


def test_client_id_in_the_request_is_not_proof_of_access():
    g = gate()
    assert g.authorized_clients(AuthContext.for_test(K_ADV, "advisor")) == {KENJI, "CLIENT-004"}
    assert g.authorized_clients(AuthContext.for_test("ADVISOR-999", "advisor")) == frozenset()     # unknown advisor: nothing
    assert g.authorized_clients(AuthContext.for_test(K_ADV, "advisor", scope=[KENJI])) == {KENJI}   # scope can only narrow


def test_missing_identity_invalid_role_and_unauthenticated_are_denied():
    g = gate()
    for auth, code in ((None, "missing_identity"), (AuthContext("", "advisor", True), "missing_identity"), (AuthContext.for_test("x", "root"), "invalid_role"),
                       (AuthContext(K_ADV, "advisor", authenticated=False, source="header-asserted"), "unauthenticated")):
        with pytest.raises(PermissionDenied) as e:
            g.check(auth, "ask")
        assert e.value.code == code
    assert gate(DEV).check(AuthContext(K_ADV, "advisor", False, source="header-asserted"), "ask").allowed   # dev/test accept asserted identities


def test_role_and_operation_matrix():
    g = gate()
    adv, sup = AuthContext.for_test(K_ADV, "advisor"), AuthContext.for_test("SUP-1", "supervisor")
    for op in ("approve", "lifecycle", "ingest"):
        with pytest.raises(PermissionDenied):
            g.check(adv, op)
    assert g.check(sup, "approve", [KENJI]).allowed
    with pytest.raises(PermissionDenied):
        g.check(sup, "delete_everything")                             # unknown operations are denied


def test_unauthorized_tool_execution_is_denied():
    g = gate()
    a = AuthContext.for_test(K_ADV, "advisor", tools=["get_client_goals"])
    assert g.check_tool(a, "get_client_goals", KENJI).allowed
    with pytest.raises(PermissionDenied) as e:
        g.check_tool(a, "get_client_portfolio", KENJI)               # not granted to this actor
    assert e.value.code == "tool_not_permitted"
    with pytest.raises(PermissionDenied):
        g.check_tool(AuthContext.for_test(K_ADV, "advisor"), "transfer_funds", KENJI)   # unknown / write-capable tools never run by default
    with pytest.raises(PermissionDenied):
        g.check_tool(AuthContext.for_test(K_ADV, "advisor"), "get_client_goals", REBECCA)   # right tool, wrong client


def test_data_ownership_and_identity_resolution():
    g = gate()
    a = AuthContext.for_test(K_ADV, "advisor")
    assert g.owns(a, KENJI) and not g.owns(a, REBECCA) and not g.owns(a, None)
    with pytest.raises(PermissionDenied):
        resolve_identity(PROD, None, None)
    with pytest.raises(PermissionDenied):
        resolve_identity(PROD, "someone", None)
    anon = resolve_identity(DEV, None, None)
    assert anon.source == "dev-anonymous" and anon.authenticated is False
    assert resolve_identity(PROD, K_ADV, "advisor", authenticated_by="gateway").authenticated


def test_every_decision_is_traced():
    seen = []
    g = PermissionGate(PROD, CONV.directory, emit=lambda c, n, **p: seen.append((c, n, p)))
    g.check(AuthContext.for_test(K_ADV, "advisor"), "ask")
    with pytest.raises(PermissionDenied):
        g.check(AuthContext.for_test(K_ADV, "advisor"), "read_memory", [REBECCA])
    assert [s[1] for s in seen] == ["allow", "deny"] and seen[1][2]["code"] == "client_not_in_scope"


# ==================================================================================================================== evidence verifier
@pytest.fixture(scope="module")
def kenji_world():
    orch, store, script, clock = make(KENJI_SCRIPT)
    for i in KENJI_MEETINGS:
        orch.ingest_meeting(i)
    p = plan("Why did Kenji's retirement age change?", [KENJI], ["retirement_age"], needs_history=True)
    mem = orch.retrieve(p)
    tools = ToolSelectorState(request_id="REQ-M1")
    ev, _ = build_evidence(p, mem, tools)
    return dict(orch=orch, store=store, plan=p, mem=mem, tools=tools, ev=ev)


def _report(world, findings, summary="Kenji's retirement age is 63.", evidence=None, **sections):
    return AdvisorReport(request_id="REQ-M1", client_id=KENJI, question="q", summary=summary, evidence=evidence or world["ev"],
                         key_findings=findings, **sections)


def _ref(world, **match):
    for e in world["ev"]:
        if all(getattr(e, k) == v for k, v in match.items()):
            return e.ref
    raise AssertionError(f"no evidence matching {match}")


def _verifier(world, **kw):
    return EvidenceVerifier(CONV, world["store"], **kw)


def test_supported_finding_is_verified_and_keeps_its_references(kenji_world):
    cur = _ref(kenji_world, memory_type="semantic", status="active")
    rep = _report(kenji_world, [Finding(text="Kenji's current retirement age is 63", refs=[cur])])
    new, res = _verifier(kenji_world).verify_report(rep, [KENJI], kenji_world["tools"])
    assert res.counts() == {"verified": 1, "qualified": 0, "unsupported": 0}
    assert new.key_findings[0].refs == [cur] and new.validation["harness_evidence"]["claim_evidence"][0]["memory_ids"]


def test_unsupported_claims_are_removed_and_listed(kenji_world):
    cur = _ref(kenji_world, memory_type="semantic", status="active")
    rep = _report(kenji_world, [Finding(text="Kenji's current retirement age is 61", refs=[cur]),          # wrong figure
                                Finding(text="Kenji plans to retire at 63", refs=[]),                       # no evidence
                                Finding(text="Kenji plans to retire at 63", refs=["E99"]),                  # unknown handle
                                Finding(text="Kenji decided on 2026-02-30 to retire at 63", refs=[cur])])   # date absent from evidence
    new, res = _verifier(kenji_world).verify_report(rep, [KENJI], kenji_world["tools"])
    assert res.counts()["unsupported"] == 4 and new.key_findings == []
    assert len([u for u in new.uncertainties if u.startswith("Withheld unsupported")]) == 4


def test_llm_assertion_of_support_is_not_proof(kenji_world):
    class Yes:
        def check(self, text, evidence_texts):
            return "supported", "the model says so"
    cur = _ref(kenji_world, memory_type="semantic", status="active")
    rep = _report(kenji_world, [Finding(text="Kenji's current retirement age is 61", refs=[cur])])
    _, res = _verifier(kenji_world, entailment=Yes()).verify_report(rep, [KENJI], kenji_world["tools"])
    assert res.counts()["unsupported"] == 1            # deterministic number check wins over any entailment verdict


def test_cross_client_evidence_is_rejected(kenji_world):
    foreign = EvidenceItem(ref="E1", source_class="memory", description="Current knowledge; retirement_age; value 63", memory_id="MEM-X", client_id=REBECCA,
                           memory_type="semantic", status="active", certainty="confirmed", value="63")
    rep = _report(kenji_world, [Finding(text="Retirement age is 63", refs=["E1"])], evidence=[foreign])
    _, res = _verifier(kenji_world).verify_report(rep, [KENJI], kenji_world["tools"])
    assert res.verdicts[0].verdict == "unsupported" and "outside the plan" in res.verdicts[0].reasons[0]
    # a client the plan names but the ACTOR may not read is rejected too
    own = lambda cid: cid != KENJI      # noqa: E731
    rep2 = _report(kenji_world, [Finding(text="Kenji's current retirement age is 63", refs=[_ref(kenji_world, memory_type="semantic", status="active")])])
    _, res2 = EvidenceVerifier(CONV, kenji_world["store"], owns=own).verify_report(rep2, [KENJI], kenji_world["tools"])
    assert res2.counts()["unsupported"] == 1


def test_superseded_evidence_cannot_support_a_current_statement(kenji_world):
    old = _ref(kenji_world, memory_type="semantic", status="superseded")
    rep = _report(kenji_world, [Finding(text="Kenji's retirement age is 65", refs=[old])], historical_context=[Finding(text="Kenji earlier planned to retire at 65", refs=[old])])
    new, res = _verifier(kenji_world).verify_report(rep, [KENJI], kenji_world["tools"])
    assert [v.verdict for v in res.verdicts] == ["unsupported", "qualified"]       # as key finding: no; as history: yes, labelled
    assert new.historical_context[0].text.startswith("[Qualified:")


def test_unconfirmed_evidence_only_supports_a_qualified_statement(kenji_world):
    expl = next(e for e in kenji_world["ev"] if e.certainty in ("exploratory", "hypothetical", "unconfirmed"))
    rep = _report(kenji_world, [Finding(text=f"Kenji discussed retiring at {expl.value or '60'}", refs=[expl.ref])])
    new, res = _verifier(kenji_world).verify_report(rep, [KENJI], kenji_world["tools"])
    assert res.verdicts[0].verdict == "qualified"
    assert new.key_findings[0].text.startswith("[Qualified:") and "not a verified fact" in new.key_findings[0].text


def test_fabricated_excerpt_is_rejected(kenji_world):
    e = next(x for x in kenji_world["ev"] if x.interaction_id)
    fake = e.model_copy(update=dict(ref="E1", excerpt="We have fully decided to retire at fifty."))
    rep = _report(kenji_world, [Finding(text="Kenji decided something", refs=["E1"])], evidence=[fake])
    _, res = _verifier(kenji_world).verify_report(rep, [KENJI], kenji_world["tools"])
    assert res.verdicts[0].verdict == "unsupported" and "excerpt not found" in " ".join(res.verdicts[0].reasons)


def test_tool_figures_are_checked_against_the_full_tool_result(kenji_world):
    env = Envelope(tool="calculate_portfolio_concentration", client_id=KENJI, as_of="2026-06-30", data={"top_n_percent": 41.25, "herfindahl_index": 0.1834, "padding": ["x" * 400]},
                   provenance=Provenance(source="test"))
    ex = ToolExecution(id="EX-1", iteration=1, tool=env.tool, arguments={"client_id": KENJI, "dimension": "issuer"}, status="success", result=env)
    tools = ToolSelectorState(request_id="REQ-M1", executions=[ex])
    item = EvidenceItem(ref="E1", source_class="tool_calculation", description="calculate_portfolio_concentration(...) as of 2026-06-30: " + "x" * 300, tool_execution_id="EX-1",
                        synthetic=True, client_id=KENJI)
    good = Finding(text="Concentration as of 2026-06-30: top holdings are 41.25% of the portfolio.", refs=["E1"])
    bad = Finding(text="Concentration as of 2026-06-30: top holdings are 52.5% of the portfolio.", refs=["E1"])
    rep = _report(kenji_world, [], evidence=[item], financial_analysis=[good, bad], summary="Concentration was calculated.")
    new, res = _verifier(kenji_world).verify_report(rep, [KENJI], tools)
    assert [v.verdict for v in res.verdicts] == ["verified", "unsupported"] and new.financial_analysis == [good]


def test_client_state_cannot_rest_on_tool_results_alone(kenji_world):
    item = EvidenceItem(ref="E1", source_class="operational_data", description="get_client_goals: 63", tool_execution_id="EX-9", synthetic=True, client_id=KENJI)
    rep = _report(kenji_world, [Finding(text="Kenji wants to retire at 63", refs=["E1"])], evidence=[item])
    _, res = _verifier(kenji_world).verify_report(rep, [KENJI], kenji_world["tools"])
    assert res.verdicts[0].verdict == "unsupported"


def test_summary_with_unsupported_figures_is_replaced(kenji_world):
    cur = _ref(kenji_world, memory_type="semantic", status="active")
    rep = _report(kenji_world, [Finding(text="Kenji's current retirement age is 63", refs=[cur])], summary="Kenji will retire at 58 with $9,999,999.")
    new, res = _verifier(kenji_world).verify_report(rep, [KENJI], kenji_world["tools"], fallback_summary=lambda: "FALLBACK")
    assert new.summary == "FALLBACK" and res.summary_replaced


# ==================================================================================================================== temporal conflicts
def mi(mid, **kw):
    base = dict(memory_id=mid, client_id=KENJI, memory_type="semantic", topic="retirement_age", subject="retirement_age", content="c", value_text="63", certainty="confirmed",
                status="active", event_date="2026-05-01", valid_from="2026-05-01", origin="consolidation")
    base.update(kw)
    return MemoryItem(**base)


def test_replacement_is_reported_and_history_is_preserved(kenji_world):
    ctx = kenji_world["mem"][KENJI]
    before = ctx.model_dump()
    found = detect(ctx)
    assert any(c.kind == "replacement" for c in found) and not any(c.kind == "contradiction" for c in found)
    assert ctx.model_dump() == before                                           # detection never changes a memory
    assert any(m.status == "superseded" and m.value_text == "65" for m in ctx.history)


def test_genuine_contradiction_is_flagged_and_published():
    ctx = MemoryContext(client_id=KENJI, current=[mi("A", value_text="63"), mi("B", value_text="65")])
    found = detect(ctx)
    c = next(x for x in found if x.kind == "contradiction")
    assert c.severity == "high" and set(c.memory_ids) == {"A", "B"} and "63 vs 65" in c.reason
    assert publish(ctx, found) >= 1 and any("contradiction" in x for x in ctx.contradictions)
    assert publish(ctx, found) == 0                                              # idempotent


def test_a_newer_exploration_does_not_replace_the_current_value():
    ctx = MemoryContext(client_id=KENJI, current=[mi("A", value_text="65", valid_from="2026-01-02", event_date="2026-01-02")],
                        episodes=[mi("E", memory_type="episodic", value_text="60", certainty="exploratory", event_date="2026-03-10", valid_from=None)])
    found = detect(ctx)
    assert any(c.kind == "open_exploration" for c in found) and ctx.current[0].value_text == "65"


def test_a_newer_confirmed_statement_is_surfaced_not_assumed_correct():
    ctx = MemoryContext(client_id=KENJI, current=[mi("A", value_text="65", valid_from="2026-01-02", event_date="2026-01-02")],
                        episodes=[mi("E", memory_type="episodic", value_text="58", certainty="confirmed", event_date="2026-04-01", valid_from=None)])
    c = next(x for x in detect(ctx) if x.kind == "unconsolidated_update")
    assert c.severity == "high" and "not assumed correct" in c.reason and ctx.current[0].value_text == "65"


def test_stale_disputed_and_preference_drift():
    ctx = MemoryContext(client_id=KENJI, current=[mi("A", value_text="65", valid_from="2025-01-02", event_date="2025-01-02"), mi("D", topic="risk_tolerance", subject="risk_tolerance",
                                                                                                                                 status="disputed", valid_from="2026-06-01", event_date="2026-06-01")],
                        episodes=[mi("P1", memory_type="episodic", topic="investment_trade", subject="style", value_text="conservative", certainty="unconfirmed", event_date="2026-01-10", valid_from=None),
                                  mi("P2", memory_type="episodic", topic="investment_trade", subject="style", value_text="growth", certainty="unconfirmed", event_date="2026-04-10", valid_from=None),
                                  mi("L", memory_type="episodic", status="active", event_date="2026-06-30", valid_from=None, value_text="65")])
    kinds = {c.kind for c in detect(ctx, stale_after_days=180)}
    assert {"stale", "disputed", "changed_over_time"} <= kinds
    drift = next(c for c in detect(ctx) if c.kind == "changed_over_time")
    assert "neither is promoted" in drift.reason and set(drift.memory_ids) == {"P1", "P2"}


# ==================================================================================================================== memory write validator
class FakeConv:
    """A tiny conversation source so adversarial sentences can be tested exactly as a client would say them."""
    def __init__(self, turns):
        self.turns = turns

    def turn(self, iid, idx):
        return self.turns.get((iid, idx))

    def verify_excerpt(self, client_id, iid, idx, excerpt):
        t = self.turns.get((iid, idx))
        return bool(t) and t["client_id"] == client_id and " ".join(excerpt.lower().split()) in " ".join(t["utterance"].lower().split())


class FakeCtx:
    interaction_id, client_id, date = "INT-X", "CLIENT-001", "2026-03-01"


def decision(text, certainty, topic="asset_allocation", statement=None, value=None, role="Client", client="CLIENT-001", turn_text=None, turn_client=None):
    conv = FakeConv({("INT-X", 1): dict(client_id=turn_client or client, speaker=role, utterance=turn_text or text)})
    ctx = FakeCtx()
    ctx.client_id = client
    c = MemoryCandidate(topic=topic, subject=topic, statement=statement or text, value=value, certainty=certainty, speaker_role=role, turn_index=1, excerpt=text)
    d = ing.Decision("conversation_llm", c, "accepted", "valid", c, certainty, certainty, [])
    return MemoryWriteValidator(conv), d, ctx


def test_adversarial_might_consider_is_not_a_verified_preference():
    v, d, ctx = decision("I might consider investing in equities.", "confirmed", statement="Client wants to invest in equities")
    verdict = v.review(d, ctx)
    assert verdict.kind == "hypothesis" and verdict.action == "downgrade" and verdict.certainty_out == "hypothetical"
    v.apply(d, verdict)
    assert d.final.certainty == "hypothetical" and d.decision == "downgraded" and d.candidate.certainty == "confirmed"     # original candidate untouched for the audit
    assert ing.classify_memory(d).semantic_eligible is False


def test_adversarial_prefer_conservative_is_a_reported_preference_not_a_constraint():
    v, d, ctx = decision("I prefer conservative investments.", "confirmed", topic="risk_tolerance", statement="Client prefers conservative investments")
    verdict = v.review(d, ctx)
    assert verdict.action == "downgrade" and verdict.kind == "reported_preference" and verdict.certainty_out == "unconfirmed"
    v.apply(d, verdict)
    cls = ing.classify_memory(d)
    assert d.kept and cls.status == "unconfirmed" and cls.semantic_eligible is False       # stored as a reported preference, never promotable
    # already-unconfirmed input is accepted unchanged
    v2, d2, ctx2 = decision("I prefer conservative investments.", "unconfirmed", topic="risk_tolerance")
    assert v2.review(d2, ctx2).action == "accept"
    # and a preference can never become a semantic version
    ep = dict(memory_id="MEM-EPI-1", certainty="confirmed", status="active", content="I prefer conservative investments.", speaker_role="Client", topic="risk_tolerance",
              subject="risk_tolerance", event_date="2026-03-01")
    ok, why = v.approve_semantic("CLIENT-001", Version("40", "2026-03-01", ep), None)
    assert not ok and "reported preference" in why


def test_adversarial_invented_preference_without_support_is_rejected():
    v, d, ctx = decision("Thanks, see you next week.", "confirmed", topic="risk_tolerance", statement="Client strongly prefers aggressive growth stocks")
    verdict = v.review(d, ctx)
    assert verdict.action == "reject" and "not supported by its excerpt" in verdict.reasons[0]
    v.apply(d, verdict)
    assert d.decision == "rejected" and not d.kept
    v2, d2, ctx2 = decision("I want aggressive growth stocks.", "confirmed", topic="risk_tolerance", turn_text="Let's talk about the mortgage today.")
    assert v2.review(d2, ctx2).reasons == ["excerpt does not exist in the cited turn"]            # quote not in the source turn at all


def test_adversarial_evidence_from_another_client_is_rejected():
    v, d, ctx = decision("We've decided: 63.", "confirmed", topic="retirement_age", value="63", client="CLIENT-002", turn_client="CLIENT-001")
    verdict = v.review(d, ctx)
    assert verdict.action == "reject" and "belongs to CLIENT-001" in verdict.reasons[0]
    v3, d3, ctx3 = decision("We've decided: 63.", "confirmed", topic="retirement_age", value="63", role="Advisor", turn_text="We've decided: 63.")
    v3.conv.turns[("INT-X", 1)]["speaker"] = "Client"
    assert "speaker is Client" in v3.review(d3, ctx3).reasons[0]                                  # speaker identity comes from the source row


def test_semantic_creation_rejects_evidence_that_belongs_to_another_client():
    orch, store, _, _ = make(KENJI_SCRIPT)
    for i in KENJI_MEETINGS:
        orch.ingest_meeting(i)
    ep = next(e for e in store.records(KENJI, "episodic") if e["certainty"] == "confirmed" and e["topic"] == "retirement_age")
    v = MemoryWriteValidator(CONV, store)
    assert v.approve_semantic(KENJI, Version("63", "2026-05-01", ep), store)[0] is True
    ok, why = v.approve_semantic(REBECCA, Version("63", "2026-05-01", ep), store)             # same episode claimed for another client
    assert not ok and "evidence no longer verifies" in why


def _kenji_with_validator():
    orch, store, script, clock = make(KENJI_SCRIPT)
    val = MemoryWriteValidator(CONV, store)
    orch.write_validator = val
    orch.consolidator.validator = val
    for i in KENJI_MEETINGS:
        orch.ingest_meeting(i)
    return orch, store, val


def test_changed_preference_keeps_the_old_version_and_records_the_transition():
    orch, store, val = _kenji_with_validator()
    sem = sorted(store.records(KENJI, "semantic", topics=["retirement_age"]), key=lambda r: r["version_no"])
    assert [(r["value_text"], r["status"]) for r in sem] == [("65", "superseded"), ("63", "active")]
    assert sem[0]["valid_to"] == sem[1]["valid_from"] and sem[1]["supersedes_id"] == sem[0]["memory_id"]
    assert any(e["to_status"] == "superseded" for e in store.lifecycle_events(sem[0]["memory_id"]))
    rels = store.relationships_among([r["memory_id"] for r in sem])
    assert any(r.rel_type == "supersedes" for r in rels)
    ctx = orch.retrieve(plan("retirement age history", [KENJI], ["retirement_age"], needs_history=True))[KENJI]
    assert any(c.kind == "replacement" for c in detect(ctx))
    assert ctx.history and ctx.history[0].value_text == "65"


def test_validator_does_not_change_legitimate_outcomes():
    plain, pstore, _, _ = make(KENJI_SCRIPT)
    for i in KENJI_MEETINGS:
        plain.ingest_meeting(i)
    _, vstore, val = _kenji_with_validator()
    shape = lambda s: sorted((r["topic"], r["value_text"], r["status"], r["version_no"]) for r in s.records(KENJI, "semantic"))     # noqa: E731
    assert shape(pstore) == shape(vstore) and shape(vstore)
    assert any(x["action"] in ("accept", "downgrade") for x in val.log if "action" in x)


def test_classifier_kinds():
    assert classify_statement("I might consider investing in equities.", "Client", "confirmed") == "hypothesis"
    assert classify_statement("I prefer conservative investments.", "Client", "confirmed") == "reported_preference"
    assert classify_statement("We've decided to prefer bonds.", "Client", "confirmed") == "fact"
    assert classify_statement("It sounds like you want less risk.", "Advisor", "confirmed") == "inference"
    assert classify_statement("I recommend 40 percent bonds.", "Advisor", "recommendation") == "recommendation"


def test_sensitive_semantic_change_needs_approval_and_is_not_silently_created():
    orch, store, _, _ = make(KENJI_SCRIPT)
    gate_ = ApprovalGate(ApprovalPolicy(sensitive_memory_topics=("retirement_age",)))
    val = MemoryWriteValidator(CONV, store, approval=gate_, sensitive_topics=("retirement_age",))
    orch.consolidator.validator = val
    for i in KENJI_MEETINGS:
        orch.ingest_meeting(i)
    assert store.records(KENJI, "semantic", topics=["retirement_age"]) == []                  # nothing durable without a human
    pending = [r for r in gate_.store.all() if r.status == "pending"]
    assert pending and all(r.category == "sensitive_memory_write" for r in pending)
    sup = AuthContext.for_test("SUP-1", "supervisor")
    for r in pending:
        gate_.decide(r.approval_id, sup, True, "reviewed with the client")
    orch.consolidator.consolidate_client(KENJI)
    assert store.records(KENJI, "semantic", topics=["retirement_age"])                       # created only after approval


# ==================================================================================================================== approval
def proposal(payload="report body v1", dv="dv1", cat=RiskCategory.SUITABILITY_CONCLUSION, by="ADVISOR-001"):
    return ApprovalProposal(cat, "release_report", by, "suitability-related conclusion", dict(body=payload), dv, (KENJI,))


def agate(**kw):
    return ApprovalGate(ApprovalPolicy(**kw), gate=PermissionGate(PROD, CONV.directory))


def test_high_risk_operation_without_approval_is_blocked():
    g = agate()
    with pytest.raises(ApprovalRequired) as e:
        g.require(proposal())
    assert e.value.record.status == "pending" and not g.is_approved(proposal())        # missing approval is never consent


def test_rejected_approval_stays_blocked():
    g = agate()
    rec = g.submit(proposal())
    g.decide(rec.approval_id, AuthContext.for_test("SUP-1", "supervisor"), False, "not appropriate")
    with pytest.raises(ApprovalRequired):
        g.require(proposal())
    assert rec.status == "rejected" and rec.decided_by == "SUP-1" and rec.decided_at


def test_approval_is_bound_to_the_exact_proposal_and_data_version():
    g = agate()
    rec = g.submit(proposal(), draft="the draft report")
    g.decide(rec.approval_id, AuthContext.for_test("SUP-1", "supervisor"), True, "checked the sources")
    assert g.require(proposal()).status == "approved"
    assert g.release(rec.approval_id, proposal()) == "the draft report"
    for changed in (proposal(payload="report body v2"), proposal(dv="dv2")):
        with pytest.raises(ApprovalRequired):
            g.require(changed)                                                      # a changed proposal needs a FRESH approval
        with pytest.raises(PermissionDenied) as e:
            g.release(rec.approval_id, changed)
        assert e.value.code == "approval_invalidated"


def test_approver_rules():
    g = agate()
    rec = g.submit(proposal(by="SUP-1"))
    with pytest.raises(PermissionDenied) as e:
        g.decide(rec.approval_id, AuthContext.for_test("SUP-1", "supervisor"), True, "me")
    assert e.value.code == "self_approval"
    with pytest.raises(PermissionDenied):
        g.decide(rec.approval_id, AuthContext.for_test(K_ADV, "advisor"), True, "an advisor cannot approve")
    with pytest.raises(PermissionDenied) as e:
        g.decide(rec.approval_id, AuthContext.for_test("SUP-2", "supervisor"), True, "  ")
    assert e.value.code == "reason_required"
    g.decide(rec.approval_id, AuthContext.for_test("SUP-2", "supervisor"), True, "fine")
    with pytest.raises(PermissionDenied) as e:
        g.decide(rec.approval_id, AuthContext.for_test("SUP-3", "supervisor"), False, "changed my mind")
    assert e.value.code == "already_decided"


def test_simulated_synchronous_approver_and_future_write_tool_categories():
    seen = []
    g = ApprovalGate(ApprovalPolicy(), gate=PermissionGate(PROD, CONV.directory), emit=lambda c, n, **p: seen.append(n),
                     decider=lambda rec: (AuthContext.for_test("SUP-1", "supervisor"), True, "simulated approval"))
    assert g.require(proposal(cat=RiskCategory.EXTERNAL_ACTION)).decided_by == "SUP-1"      # a future write tool is gated from day one
    assert seen == ["requested", "approved"]
    refusing = ApprovalGate(ApprovalPolicy(), gate=PermissionGate(PROD, CONV.directory), decider=lambda rec: None)
    with pytest.raises(ApprovalRequired):
        refusing.require(proposal(cat=RiskCategory.IRREVERSIBLE_OPERATION))


def test_policy_classification():
    pol = ApprovalPolicy()
    p = plan("x", [KENJI], task_type="forecast_feasibility")
    assert pol.classify_report(p, "Can Kenji afford to retire at 60?") == [RiskCategory.SUITABILITY_CONCLUSION]
    assert pol.classify_report(plan("x", [KENJI]), "What did Kenji decide about retirement?") == []
    assert ApprovalPolicy(required=False).classify_report(p, "should he sell?") == []
