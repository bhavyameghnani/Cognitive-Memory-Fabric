"""B5: complete backend integration through the REAL HTTP entry point, POST /api/ask.

REAL here: the FastAPI app, signed-token identity, the permission gate, the Harness (request and context budgets, trace, telemetry, evidence verification, temporal-conflict handling, approval
gate), the four agents (Planner, Memory Orchestrator, Tool Selector, Report Agent including B1 chronological retrieval, B2 evidence selection, B3 chronology, B4 narrative and the verifier's
independent narrative check), the in-process MCP tool server and the real conversation record.  INJECTED (the only substitution): the language model, scripted per agent.  The live-model
counterpart (real qwen2.5:3b through the same endpoint) is run separately and reported separately; nothing here calls a model.

Databases are temporary copies (see tests/test_e2e_api.py `world`); the repository's own databases are asserted byte-identical afterwards."""
import json
import os
import re
import sys
from pathlib import Path

import pytest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
pytest.importorskip("fastapi")

from test_e2e_api import KENJI, report_json, tool_script, world  # noqa: E402,F401  (`world` is the fixture)
from test_harness_pipeline import planner_out  # noqa: E402

CHRONOLOGY = ("Give me the chronology of Kenji's retirement age with the source conversation turns, including any corrections. Distinguish what Kenji explicitly stated from what the "
              "advisor reported or inferred.")


def history_plan():
    return planner_out(task="history", tools=True, required_tools=["get_client_goals"])


def narrative_json(*sentences):
    return {"paragraphs": [{"sentences": [{"text": t, "claims": c} for t, c in sentences]}]}


def ask_history(world, narrative=None, report_summary="The record shows how the retirement age was discussed.", question=CHRONOLOGY, **ask_kw):
    """Run the chronology question: scripted Planner, Tool Selector, main Report call and narrative call; returns (response json, llm).  The prompts the agents really sent are kept in world.prompts."""
    prompts: dict = {}

    def report(system, user, schema):
        if "Write the story now" in user:                                        # the readable-story call (B6)
            prompts["story"] = user
            return honest_story(user, 3)
        if "Write the paragraphs now" in user:                                   # the dedicated narrative call
            prompts["narrative"] = user
            n = narrative(user) if callable(narrative) else narrative
            if isinstance(n, Exception):
                raise n
            return n
        prompts["report"] = user
        return report_json(report_summary, [])

    llm = world.script(planner=[history_plan()], tool_selector=tool_script(), report=report)
    r = world.ask(question, **ask_kw)
    assert r.status_code == 200, r.text
    world.prompts.update(prompts)
    return r.json(), llm


LINE = re.compile(r"^\[(C\d+)\] (\d{4}-\d{2}-\d{2}), (INT-\d+) turn (\d+), the client's own words, [^:]*?: \"(.*)\"$")
RISKY = re.compile(r"decid|agree|confirm|final|current|decision|approv|accept|sign|consent|instruct|committ|authori", re.I)


def honest_narrative(user: str, n: int = 3):
    """What a well-behaved model would return for the TIMELINE in `user`: one quote-led sentence per plain client line (no relation to another claim, no decision vocabulary), in the lines' order."""
    out = []
    for raw in user.splitlines():
        m = LINE.match(raw.strip())
        if not m or "(corrects" in raw or "(corrected" in raw or "conflicts with" in raw:
            continue
        cid, date, _, _, wording = m.groups()
        first = wording.split(". ")[0].rstrip(".").strip()
        if len(first) < 12 or RISKY.search(first) or '"' in first:
            continue
        out.append((f'On {date} the client said "{first}".', [cid]))
        if len(out) == n:
            break
    return narrative_json(*out)


def honest_story(user: str, n: int = 3):
    """What a well-behaved model would return for the story call: one plain sentence per plain client line of the TIMELINE in `user`."""
    lines = []
    for raw in user.splitlines():
        m = LINE.match(raw.strip())
        if not m or "(corrects" in raw or "(corrected" in raw or "conflicts with" in raw:
            continue
        cid, date, _, _, wording = m.groups()
        first = wording.split(". ")[0].rstrip(".").strip()
        if len(first) < 12 or RISKY.search(first) or '"' in first or len(first) > 150:
            continue
        lines.append({"claims": [cid], "sentence": f'On {date} the client said "{first}".'})
        if len(lines) == n:
            break
    return {"lines": lines}


def narrative_findings(rep):
    return [f for f in rep["historical_context"] if f.get("origin") == "narrative_checked"]


STORY_START = ("In meeting", "Later, in meeting", "Continuing in meeting", "On the same day, in meeting")


def chronology_findings(rep):
    """The code-written (B3) chronology paragraphs: rendered findings that open with a meeting lead-in (the source-record findings that follow them are rendered too)."""
    return [f for f in rep["historical_context"] if f.get("origin") == "rendered" and f["text"].startswith(STORY_START)]


# ============================================================================================================================ four agents, one orchestrator
def test_b5_01_the_four_agents_hand_over_through_the_real_orchestrator(world):
    body, llm = ask_history(world, narrative=honest_narrative)
    rep = body["report"]
    agents = [c["agent"] for c in llm.calls]
    # Phase 1 (C): a single-client chronology request is planned deterministically by the Planner agent (no model call); Memory uses no model; then Tool Selector -> Report (main + narrative + story)
    assert set(agents) == {"report"} and len(agents) >= 2, agents                  # no model planner (deterministic plan), no model selector loop; the Report agent: main call + one call per narrative window (the story is off by default)
    assert body["client_ids"] == [KENJI] and body["released"] is True
    assert body["agent_run"] is not None
    # the Memory Orchestrator's result reached the Report Agent: memory evidence is in the report, and its prompt carried evidence lines
    assert any(e["source_class"] == "memory" for e in rep["evidence"])
    assert world.prompts["report"].count("[E") >= 10
    # the Tool Selector's retrieved source turns reached the Report Agent: real conversation turns with their speaker and meeting
    turns = [e for e in rep["evidence"] if e.get("speaker") and e.get("interaction_id")]
    assert len(turns) >= 20 and {e["client_id"] for e in turns} == {KENJI}
    # the Report Agent's timeline is built from them, and the narrative call was shown that timeline (not the raw evidence)
    assert rep["timeline"] and "TIMELINE (oldest first)" in world.prompts["narrative"] and "EVIDENCE:" not in world.prompts["narrative"]
    assert [c["claim_id"] for c in rep["timeline"]][:3] == ["C1", "C2", "C3"]


def test_b5_02_the_harness_is_active_for_every_stage_of_the_real_request(world):
    body, llm = ask_history(world, narrative=honest_narrative)
    h = body["harness"]
    rep = body["report"]
    assert h["outcome"] == "completed" and h["degraded_reasons"] == [] and h["released"] is True
    assert h["profile"] == "production" and h["actor"]
    # request budget and telemetry: every model call of every agent is counted, the narrative call included
    by_agent = h["budget"]["llm_calls_by_agent"]
    assert by_agent.get("planner", 0) == 0 and by_agent.get("report", 0) >= 2 and h["budget"]["llm_calls"] == sum(by_agent.values()) == len(llm.calls)
    assert h["budget"]["tool_calls"] >= 1 and h["telemetry"]
    # permission decisions were taken and recorded
    ops = {d["operation"] for d in h["permission_decisions"]}
    assert {"ask", "read_memory", "generate_report"} <= ops and all(d["allowed"] for d in h["permission_decisions"])
    # tracing: every stage is in one trace; the context budget controller saw the report prompts
    events = world.trace(body["request_id"])
    components = {e["component"] for e in events}
    assert {"planner", "memory", "tool_selector", "report", "pipeline", "harness", "harness.evidence", "harness.context"} <= components, sorted(components)
    assert len({e["request_id"] for e in events}) == 1
    # evidence verification ran on the narrative as well as on everything else
    he = rep["validation"]["harness_evidence"]
    assert he["mode"] == "structured" and he["counts"]["unsupported"] == 0 and he["counts"]["verified"] >= 1
    # memory selection and temporal-conflict handling ran (their records are part of the request summary)
    assert "memory_selection" in h and "conflicts" in h
    # a chronology is not a suitability conclusion: the approval gate was consulted and held nothing
    assert h["approval"] is None
    assert world.q(world.appr_db, "select count(*) from approval_requests") == [(0,)]


# ============================================================================================================================ B1-B4 through the API
def test_b5_03_a_verified_narrative_and_the_b3_chronology_reach_the_client_together(world):
    body, _ = ask_history(world, narrative=honest_narrative)
    rep = body["report"]
    nar, chron = narrative_findings(rep), chronology_findings(rep)
    assert nar and chron, (len(nar), len(chron))
    rec = rep["validation"]["narrative"]
    assert rec["paragraphs_accepted"] == len(nar) >= 1 and rec["sentences_returned"] >= 2 and rec["sentences_rejected"] == []
    for f in nar:                                                    # the paragraph is the narrative; the citations support it (handles + meeting/turn/speaker written by code)
        assert re.search(r"INT-\d+ turn \d+, recorded speaker Client", f["text"]) and re.search(r"\[E\d+\]", f["text"]) and f["refs"]
        assert f["narrative"] and all(s["sources"] for s in f["narrative"])
    he = rep["validation"]["harness_evidence"]
    assert he["counts"]["unsupported"] == 0                          # the independent verifier accepted every narrative paragraph
    # chronology is still in date order and carries the corrections the memory layer recorded
    dates = re.findall(r"\((\d{4}-\d{2}-\d{2})\)", " ".join(f["text"] for f in chron))
    assert dates == sorted(dates) and len(set(dates)) >= 5
    assert any("correct" in f["text"].lower() for f in chron)
    assert all(f.get("origin") != "rendered" for f in nar)           # nothing the model wrote carries code provenance


def test_b5_04_the_narrative_call_failing_still_returns_the_complete_b3_report(world):
    for bad in (RuntimeError("model crashed"), "not json at all", json.dumps({"paragraphs": []})):
        body, _ = ask_history(world, narrative=bad)
        rep = body["report"]
        assert body["released"] is True and rep["validation"]["mode"] == "llm"
        assert rep["validation"]["narrative"].get("error") and not narrative_findings(rep)
        assert len(chronology_findings(rep)) >= 3 and rep["timeline"]
        assert any("No model-written narrative passed verification" in u for u in rep["uncertainties"])


def test_b5_05_unsupported_interpretation_never_reaches_the_response(world):
    def bad(user):
        good = honest_narrative(user, 2)["paragraphs"][0]["sentences"]
        first = good[0]["claims"][0]
        extra = [{"text": "The retirement age was clarified to 63, superseding all previous exploratory and corrected plans.", "claims": [first]},
                 {"text": "Rebecca confirmed this because the market had fallen.", "claims": [first]},
                 {"text": 'On 2026-04-04 the client decided "I was thinking about it" after reflecting.', "claims": [first]}]
        return {"paragraphs": [{"sentences": good + extra}]}

    body, _ = ask_history(world, narrative=bad)
    rep = body["report"]
    rec = rep["validation"]["narrative"]
    assert len(rec["sentences_rejected"]) >= 3 and len(rec["sentences_rejected"]) % 3 == 0 and rec["paragraphs_accepted"] >= 1      # the same three bad sentences come back in every window; none of them is ever accepted
    everything = json.dumps({k: rep[k] for k in ("summary", "key_findings", "historical_context", "uncertainties", "limitations")})
    for phrase in ("superseding all previous", "clarified to 63", "market had fallen", "after reflecting", "Rebecca confirmed"):
        assert phrase not in everything, phrase


def test_b5_06_a_request_budget_that_runs_out_degrades_to_b3_and_says_so(world):
    import api.main as m
    world._mp.setenv("HARNESS_MAX_LLM_CALLS", "1")                  # the main report call; the narrative call is the second (the planner and the selector make no model call for this request)
    world._mp.setattr(m, "_harness", None)
    body, llm = ask_history(world, narrative=honest_narrative)
    h, rep = body["harness"], body["report"]
    assert [c["agent"] for c in llm.calls].count("report") == 1     # the narrative call never reached the model
    assert h["budget"]["llm_calls"] == 1 and any(e["reason"] == "max_llm_calls" for e in h["budget"]["exhausted"])
    assert h["outcome"] == "degraded" and "max_llm_calls" in h["degraded_reasons"] and h["released"] is True
    assert any("Resource budget exhausted" in x for x in rep["limitations"])
    assert not narrative_findings(rep) and len(chronology_findings(rep)) >= 3


# ============================================================================================================================ isolation, approval
def test_b5_07_nothing_about_another_client_reaches_a_kenji_report(world):
    body, _ = ask_history(world, narrative=honest_narrative)
    text = json.dumps(body)
    assert "CLIENT-002" not in text and "Rebecca" not in text and "Lakeshore" not in text
    assert {e["client_id"] for e in body["report"]["evidence"] if e.get("client_id")} == {KENJI}


def test_b5_08_a_high_risk_question_is_held_for_approval_and_leaks_no_narrative(world):
    world.script(planner=[history_plan()], tool_selector=tool_script(),
                 report=lambda s, u, sc: (honest_narrative(u) if "Write the paragraphs now" in u else report_json("The record shows the discussion.", [])))
    r = world.ask("Should Kenji retire at 60? Please recommend whether that is suitable and give me the chronology of his retirement age.")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["released"] is False
    assert world.q(world.appr_db, "select count(*), min(status) from approval_requests") == [(1, "pending")]
    held = body["report"]                                            # the content-free withheld view: no findings, evidence, timeline, narrative or ids
    assert not held.get("historical_context") and not held.get("key_findings") and not held.get("evidence") and not held.get("timeline")
    assert "recorded speaker" not in r.text and "INT-0" not in r.text and "CLIENT-001" not in json.dumps(held)


def test_b5_09_the_other_advisor_is_refused_before_any_model_call(world):
    llm = world.script(planner=[history_plan()], tool_selector=tool_script(), report=lambda s, u, sc: report_json("x", []))
    r = world.ask(CHRONOLOGY, sub="ADVISOR-002")
    assert r.status_code == 403 and llm.calls == []


# ============================================================================================================================ ask as a background job (long requests)
def _poll(world, job_id, sub="ADVISOR-001", timeout=60):
    import time as _t
    end = _t.time() + timeout
    while _t.time() < end:
        r = world.client.get(f"/api/ask/jobs/{job_id}", headers=world.headers(sub=sub))
        assert r.status_code == 200, r.text
        if r.json()["status"] != "running":
            return r.json()
        _t.sleep(0.2)
    raise AssertionError("job did not finish")


def _script_history(world, delay=0.0, narrative=honest_narrative):
    import time as _t

    def report(system, user, schema):
        if delay:
            _t.sleep(delay)
        if "Write the paragraphs now" in user:
            return narrative(user) if callable(narrative) else narrative
        return report_json("The record shows how the retirement age was discussed.", [])
    return world.script(planner=[history_plan()], tool_selector=tool_script(), report=report)


def test_b5_10_a_job_returns_at_once_and_delivers_the_same_verified_report(world):
    import time as _t
    llm = _script_history(world, delay=1.0)
    t0 = _t.time()
    r = world.client.post("/api/ask/jobs", headers=world.headers(), json={"question": CHRONOLOGY})
    assert r.status_code == 200 and _t.time() - t0 < 1.0, "starting a job must not wait for the model"
    job = r.json()
    assert job["status"] == "running" and job["job_id"].startswith("JOB-")
    first = world.client.get(f"/api/ask/jobs/{job['job_id']}", headers=world.headers()).json()
    assert first["status"] == "running" and first["elapsed_s"] >= 0 and "result" not in first
    done = _poll(world, job["job_id"])
    assert done["status"] == "done" and done["elapsed_s"] >= 1.0
    res = done["result"]
    rep = res["report"]
    assert res["client_ids"] == [KENJI] and res["released"] is True
    assert narrative_findings(rep) and chronology_findings(rep)                 # B3 chronology and the verified B4 narrative, exactly as /api/ask returns them
    assert rep["validation"]["harness_evidence"]["counts"]["unsupported"] == 0
    assert set(c["agent"] for c in llm.calls) == {"report"}
    # still the real harness: one trace, budget counted, same request id in the result and the trace
    assert world.trace(res["request_id"]) and res["harness"]["budget"]["llm_calls"] == len(llm.calls)


def test_b5_11_job_routes_need_a_verified_identity_and_a_job_belongs_to_its_starter(world):
    _script_history(world)
    assert world.client.post("/api/ask/jobs", json={"question": CHRONOLOGY}).status_code == 401
    assert world.client.get("/api/ask/jobs/JOB-000000000000").status_code == 401
    assert world.client.post("/api/ask/jobs", headers={"Authorization": "Bearer not-a-token"}, json={"question": CHRONOLOGY}).status_code == 401
    job = world.client.post("/api/ask/jobs", headers=world.headers(), json={"question": CHRONOLOGY}).json()["job_id"]
    other = world.client.get(f"/api/ask/jobs/{job}", headers=world.headers(sub="ADVISOR-002"))
    unknown = world.client.get("/api/ask/jobs/JOB-ffffffffffff", headers=world.headers(sub="ADVISOR-002"))
    assert other.status_code == 404 and unknown.status_code == 404 and other.json() == unknown.json()      # another caller cannot tell a real job from a missing one
    assert world.client.get(f"/api/ask/jobs/{job}").status_code == 401
    assert _poll(world, job)["status"] == "done"


def test_b5_12_a_job_for_a_client_outside_the_callers_scope_fails_with_403_before_any_model_call(world):
    llm = _script_history(world)
    job = world.client.post("/api/ask/jobs", headers=world.headers(sub="ADVISOR-002"), json={"question": CHRONOLOGY}).json()["job_id"]
    done = _poll(world, job, sub="ADVISOR-002")
    assert done["status"] == "error" and done["http_status"] == 403 and "result" not in done
    assert llm.calls == []


def test_b5_13_a_failing_model_ends_the_job_with_an_error_and_never_leaves_it_running(world):
    world.script(planner=[RuntimeError("model crashed")], report=lambda s, u, sc: report_json("x", []))
    with mock.patch.dict(os.environ, {"CMF_DETERMINISTIC_PLANNING": "0"}):                      # the crash is in the model planner, so the model planner must be the one used
        job = world.client.post("/api/ask/jobs", headers=world.headers(), json={"question": CHRONOLOGY}).json()["job_id"]
        done = _poll(world, job)
    assert done["status"] == "error" and done["http_status"] == 503 and "report" not in json.dumps(done)


def test_b5_14_a_high_risk_job_is_held_for_approval_exactly_like_a_direct_ask(world):
    _script_history(world)
    job = world.client.post("/api/ask/jobs", headers=world.headers(), json={"question": "Should Kenji retire at 60? Please recommend whether that is suitable and give me the chronology."}).json()["job_id"]
    done = _poll(world, job)
    assert done["status"] == "done" and done["result"]["released"] is False
    assert world.q(world.appr_db, "select count(*), min(status) from approval_requests") == [(1, "pending")]
    assert "recorded speaker" not in json.dumps(done)


def test_b5_15_the_direct_ask_route_is_unchanged(world):
    _script_history(world)
    r = world.ask(CHRONOLOGY)
    assert r.status_code == 200 and r.json()["released"] is True and narrative_findings(r.json()["report"])


def test_b5_16_a_large_job_result_is_compressed_for_clients_that_accept_gzip(world):
    _script_history(world)
    job = world.client.post("/api/ask/jobs", headers=world.headers(), json={"question": CHRONOLOGY}).json()["job_id"]
    assert _poll(world, job)["status"] == "done"
    gz = world.client.get(f"/api/ask/jobs/{job}", headers={**world.headers(), "Accept-Encoding": "gzip"})
    plain = world.client.get(f"/api/ask/jobs/{job}", headers={**world.headers(), "Accept-Encoding": "identity"})
    assert gz.headers.get("content-encoding") == "gzip" and plain.headers.get("content-encoding") in (None, "identity")
    assert gz.json() == plain.json()                       # compression changes the bytes on the wire, never the content
    assert gz.num_bytes_downloaded < plain.num_bytes_downloaded / 3                 # bytes on the wire


# ============================================================================================================================ the readable story (B6)
def test_b5_17_the_story_is_a_separate_unverified_field_and_never_enters_the_verified_findings(world):
    world._mp.setenv("CMF_REPORT_STORY", "1")                      # the story is an optional extra since Phase 2
    body, llm = ask_history(world, narrative=honest_narrative)
    rep = body["report"]
    assert rep["story"] and all(f.get("origin") is None and f.get("narrative") is None for f in rep["story"])      # model prose: no code provenance, no verifier label
    assert all(f["text"] not in [h["text"] for h in rep["historical_context"]] for f in rep["story"])                # not mixed into the findings the verifier checks
    assert "Write the story now" in world.prompts["story"] and "EVIDENCE:" not in world.prompts["story"]
    assert re.search(r"\[E\d+\]", rep["story"][0]["text"])                                                          # every sentence ends with its evidence handles
    st = rep["validation"]["story"]
    assert st["attempted"] and st["paragraphs_accepted"] == len(rep["story"]) >= 1
    assert body["harness"]["budget"]["llm_calls_by_agent"]["report"] == 1 + rep["coverage"]["model_calls"] + 1                  # main call + one call per narrative window attempt + the story call
    # the verifier did not touch it: the finding counts are those of the sections it checks
    assert rep["validation"]["harness_evidence"]["counts"]["unsupported"] == 0


def test_b5_18_a_failing_story_call_changes_nothing_else(world):
    world._mp.setenv("CMF_REPORT_STORY", "1")
    def story_down(user):
        raise RuntimeError("story model down")
    prompts = {}

    def report(system, user, schema):
        if "Write the story now" in user:
            raise RuntimeError("story model down")
        if "Write the paragraphs now" in user:
            return honest_narrative(user)
        return report_json("The record shows how the retirement age was discussed.", [])
    world.script(planner=[history_plan()], tool_selector=tool_script(), report=report)
    r = world.ask(CHRONOLOGY)
    assert r.status_code == 200
    rep = r.json()["report"]
    assert rep["story"] == [] and rep["validation"]["story"].get("error")
    assert narrative_findings(rep) and chronology_findings(rep) and r.json()["released"] is True


def test_b5_19_a_high_risk_answer_held_for_approval_carries_no_story(world):
    world._mp.setenv("CMF_REPORT_STORY", "1")
    world.script(planner=[history_plan()], tool_selector=tool_script(),
                 report=lambda s, u, sc: (honest_story(u) if "Write the story now" in u else honest_narrative(u) if "Write the paragraphs now" in u else report_json("The record shows the discussion.", [])))
    r = world.ask("Should Kenji retire at 60? Please recommend whether that is suitable and give me the chronology of his retirement age.")
    assert r.status_code == 200 and r.json()["released"] is False
    assert r.json()["report"]["story"] == [] and "recorded speaker" not in r.text and "INT-0" not in r.text          # the held view carries no story text
