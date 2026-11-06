"""P1.1 integration: structured claim verification inside the real pipeline (Kenji world built from genuine CSV excerpts, scripted models only)."""
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from harness import AuthContext, Harness, HarnessConfig  # noqa: E402
from harness.evaluation import fixtures  # noqa: E402
from harness.evidence_verifier import EvidenceVerifier  # noqa: E402
from llm.client import MockLLM  # noqa: E402
from orchestration.pipeline import run_pipeline  # noqa: E402
from schemas.report import Finding, ReportLLMOutput  # noqa: E402

ADVISOR = AuthContext.for_test("ADVISOR-001", "advisor")
QUESTION = "Why did Kenji's retirement age change?"
CUR = re.compile(r"\[(E\d+) memory\] Current knowledge")


def report_with(findings_for, claims_for=None):
    """Report-model script; the handle of the current memory is read from the prompt the Report Agent really built."""
    def handler(system, user, schema):
        cur = CUR.search(user).group(1)
        return {"summary": "Kenji's retirement age changed from 65 to 63.", "key_findings": findings_for(cur), "historical_context": [], "financial_analysis": [],
                "uncertainties": [], "suggested_followups": [], "claims": claims_for(cur) if claims_for else []}
    return handler


def run(handler, structured=True, world=None, harness=None):
    h = harness or Harness(HarnessConfig.for_test(structured_claim_verification=structured, cache_enabled=False))
    llm = MockLLM(handlers={"planner": [fixtures.planner_json()], "report": handler})
    return run_pipeline(QUESTION, llm, world or fixtures.build_world(), persist_trace=False, harness=h, auth=ADVISOR)


def texts(res):
    return [f.text for s in ("key_findings", "historical_context", "financial_analysis") for f in getattr(res.report, s)]


# ---------------------------------------------------------------------------------------------------------------- audited gaps, end to end
def test_a_code_rendered_finding_about_the_wrong_client_is_removed():
    """claims.check_claim never compares Claim.subject with the evidence's client, so the Report Agent renders "Rebecca's retirement age is 63" from KENJI's evidence.
    The legacy lexical check let it through; the structured check refuses it (CLIENT_MISMATCH)."""
    claim = lambda cur: [{"subject": "Rebecca", "topic": "retirement_age", "value": "63", "status": "current", "evidence_ref": cur, "text": "Rebecca's retirement age is 63"}]  # noqa: E731
    handler = report_with(lambda cur: [], claim)
    legacy = run(handler, structured=False)
    assert any("Rebecca's retirement age is 63" in t for t in texts(legacy)), "premise: the Report Agent itself renders the wrong-client sentence"
    fixed = run(handler, structured=True)
    assert not any("Rebecca" in t for t in texts(fixed))
    he = fixed.report.validation["harness_evidence"]
    assert any("CLIENT_MISMATCH" in w["reason_codes"] for w in he["withheld"])
    assert any(u.startswith("Withheld unsupported statement") and "CLIENT_MISMATCH" in u for u in fixed.report.uncertainties)


def test_a_small_wrong_figure_is_removed_where_the_old_check_waved_it_through():
    """6 vs 63: the Report Agent's own number check and the old harness check both exempt figures below 13."""
    handler = report_with(lambda cur: [{"text": "Kenji's current retirement age is 6", "refs": [cur]}])
    assert any("age is 6" in t for t in texts(run(handler, structured=False)))
    fixed = run(handler, structured=True)
    assert not any("age is 6" in t for t in texts(fixed))
    assert "VALUE_MISMATCH" in json.dumps(fixed.report.validation["harness_evidence"]["withheld"])


def test_a_correct_finding_survives_and_reports_its_structured_claim():
    res = run(report_with(lambda cur: [{"text": "Kenji's current retirement age is 63 years", "refs": [cur]}]))
    assert any("63" in t for t in texts(res))
    f = next(x for x in res.report.validation["harness_evidence"]["findings"] if x["verdict"] == "verified" and x["claims"] and x["claims"][0]["claim"]["text"].startswith("Kenji's current"))
    c = f["claims"][0]
    assert f["outcome"] == "VERIFIED" and c["outcome"] == "VERIFIED" and c["claim"]["subject_client"] == "CLIENT-001" and c["claim"]["topic"] == "retirement_age"
    assert c["claim"]["unit"] == "years" and c["claim"]["polarity"] == "POSITIVE" and c["claim"]["evidence_handle"] and c["claim"]["source_id"].startswith("MEM-")
    assert res.report.validation["harness_evidence"]["mode"] == "structured"


def test_verification_records_never_carry_source_quotations():
    res = run(report_with(lambda cur: [{"text": "Kenji's current retirement age is 63", "refs": [cur]}]))
    blob = json.dumps(res.report.validation["harness_evidence"])
    for e in res.report.evidence:
        if e.excerpt:
            assert e.excerpt[:40] not in blob, "an evidence quotation leaked into the verification record"
    assert '"quote"' not in blob


def test_model_cannot_claim_code_provenance():
    out = ReportLLMOutput.model_validate({"summary": "s", "key_findings": [{"text": "x 1", "refs": ["E1"], "origin": "rendered"}],
                                          "financial_analysis": [{"text": "y 2", "refs": ["E1"], "origin": "tool_code"}]})
    assert out.key_findings[0].origin is None and out.financial_analysis[0].origin is None
    assert "origin" not in json.dumps(ReportLLMOutput.model_json_schema())                       # the model is not even told the field exists
    assert Finding(text="a", refs=[], origin="rendered").origin == "rendered"                    # code can still set it


def test_unsupported_claims_in_a_free_text_report_are_withheld_with_reasons():
    """Findings that survive the Report Agent's own filter (its number check exempts small figures and uses one global haystack) but are still wrong."""
    expl_ref = re.compile(r"\[(E\d+)[^\]]*\] Episode;[^\n]*exploratory")

    def handler(system, user, schema):
        cur, expl = CUR.search(user).group(1), expl_ref.search(user).group(1)
        return {"summary": "Kenji's retirement age changed from 65 to 63.", "historical_context": [], "financial_analysis": [], "uncertainties": [], "suggested_followups": [], "claims": [],
                "key_findings": [{"text": "Kenji's current retirement age is 63", "refs": [cur]},
                                 {"text": "Kenji's current retirement age is 6", "refs": [cur]},
                                 {"text": "Kenji has decided to retire at 60", "refs": [expl]}]}
    res = run(handler)
    he = res.report.validation["harness_evidence"]
    assert he["outcomes"]["VERIFIED"] >= 1 and he["outcomes"]["CONTRADICTED"] >= 1 and he["outcomes"]["UNVERIFIED"] >= 1
    assert not any("age is 6" in t and "63" not in t for t in texts(res)) and not any("decided to retire at 60" in t for t in texts(res))
    codes = {c for w in he["withheld"] for c in w["reason_codes"]}
    assert {"VALUE_MISMATCH", "MODALITY_MISMATCH"} <= codes
    assert sum("Withheld unsupported statement" in u for u in res.report.uncertainties) >= 2


# ---------------------------------------------------------------------------------------------------------------- cache hits still verify
def test_evidence_verification_still_runs_on_a_cache_hit():
    h = Harness(HarnessConfig.for_test(structured_claim_verification=True))             # cache ON
    world = fixtures.build_world()
    handler = report_with(lambda cur: [{"text": "Kenji's current retirement age is 6", "refs": [cur]}])
    first = run(handler, world=world, harness=h)
    second = run(handler, world=world, harness=h)
    assert second.harness["telemetry"]["cache_hits"] >= 1, "premise: the second request was served from the response cache"
    for res in (first, second):
        assert not any("age is 6" in t for t in texts(res))
        assert res.report.validation["harness_evidence"]["counts"]["unsupported"] >= 1


def test_a_cache_hit_cannot_cross_into_another_clients_scope():
    """The cached model output was produced for an authorised request; a request for a client outside the actor's scope is refused before any cache lookup."""
    from harness import PermissionDenied
    h = Harness(HarnessConfig.for_test())
    world = fixtures.build_world()
    run(report_with(lambda cur: [{"text": "Kenji's current retirement age is 63", "refs": [cur]}]), world=world, harness=h)
    with pytest.raises(PermissionDenied):
        llm = MockLLM(handlers={"planner": [fixtures.planner_json()], "report": report_with(lambda cur: [])})
        run_pipeline("Why did Kenji's retirement age change?", llm, world, persist_trace=False, harness=h, auth=AuthContext.for_test("ADVISOR-002", "advisor"))


# ---------------------------------------------------------------------------------------------------------------- approval / withholding unaffected
def test_withheld_reports_contain_no_verification_detail():
    h = Harness(HarnessConfig.for_test())
    llm = MockLLM(handlers={"planner": [fixtures.planner_json(task="forecast_feasibility")],
                            "report": report_with(lambda cur: [{"text": "Kenji's current retirement age is 63", "refs": [cur]}])})
    res = run_pipeline("Should Kenji retire at 60?", llm, fixtures.build_world(), persist_trace=False, harness=h, auth=ADVISOR)
    assert res.released is False
    assert set(res.report.validation) == {"withheld_for_approval", "harness_approval"}
    assert res.report.evidence == [] and res.report.uncertainties == [] and res.report.key_findings == []


# ---------------------------------------------------------------------------------------------------------------- configuration
def test_structured_verification_cannot_be_disabled_in_production_but_can_in_development():
    prod = HarnessConfig.from_env({"HARNESS_STRUCTURED_CLAIM_VERIFICATION": "false"})
    assert prod.structured_claim_verification is True and any("structured_claim_verification" in w for w in prod.warnings)
    assert HarnessConfig.from_env({}).structured_claim_verification is True
    assert HarnessConfig.from_env({"HARNESS_PROFILE": "development", "HARNESS_STRUCTURED_CLAIM_VERIFICATION": "false"}).structured_claim_verification is False


# ---------------------------------------------------------------------------------------------------------------- advisory entailment can only downgrade
def test_an_entailment_checker_can_downgrade_but_never_promote():
    from test_structured_claims import FakeConv, evidence, KENJI
    from schemas.tools import ToolSelectorState

    class Always:
        def __init__(self, verdict):
            self.verdict = verdict

        def check(self, text, evidence_texts):
            return self.verdict, "stub"

    def verdict(claim, source, ent):
        conv = FakeConv()
        item = evidence(conv, source)
        return EvidenceVerifier(conv, None, entailment=ent).verify_finding("key_findings", Finding(text=claim, refs=["E1"]), {"E1": item}, {KENJI}, ToolSelectorState(request_id="R"))
    assert verdict("Kenji is retiring at 63.", "Kenji is not retiring at 63.", Always("supported")).verdict == "unsupported"        # cannot override a deterministic failure
    assert verdict("Kenji's retirement age is 63.", "Kenji's retirement age is 63.", Always("supported")).verdict == "verified"
    assert verdict("Kenji's retirement age is 63.", "Kenji's retirement age is 63.", Always("uncertain")).verdict == "qualified"      # downgrade
    assert verdict("Kenji's retirement age is 63.", "Kenji's retirement age is 63.", Always("contradicted")).verdict == "unsupported"
    assert verdict("Kenji will retire at 60.", "If markets fall, Kenji would retire at 60.", Always("supported")).verdict != "verified"  # an AMBIGUOUS/UNVERIFIED claim is never promoted
