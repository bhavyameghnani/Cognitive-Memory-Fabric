"""Phase 6.1 - end-to-end tests through the real HTTP entry point, POST /api/ask.

What is REAL here: the FastAPI app (api.main.app), identity (a signed Bearer token), the permission gate, the Harness (budget, trace, telemetry), the four agents
(Planner, Memory Orchestrator, Tool Selector, Report Agent), the in-process MCP tool server over a copy of the operational demo database, the Memory store and its
integrity boundary, the Evidence Verifier and the approval gate/store.

What is INJECTED (the only substitution): the language model.  `llm.client.make_llm` returns a MockLLM whose handlers are scripted per agent; the Report handler is a
function that reads the evidence refs out of the prompt the Report Agent actually sent, so no ref or id is hard-coded.  No network, no provider, no credentials.

Data: a temporary copy of data/seed/cmf_memory_demo.db and of data/seed/cmf_ops_demo.db.  The shipped memory seed predates the integrity schema, so the store's migration
back-fills EVERY seeded memory as `unverified` (by design: nothing is marked verified by a migration).  To have evidence that can legitimately be VERIFIED, the fixture adds
one Kenji memory and one Rebecca memory to the temporary copy THROUGH THE REAL INTEGRITY BOUNDARY (`MemoryStore.insert_memory` with the ingestion service principal, citing
real rows of data/conversations.csv) - the same helpers the P1.4 tests use.  The seed files themselves are never written.

Nothing is written to the repository: databases, approvals and traces live under tmp_path, and the fixture asserts that `logs/` was not created and that the
repository's own databases are byte-identical afterwards."""
import hashlib
import json
import re
import sqlite3
import sys
import shutil
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from test_harness_pipeline import planner_out  # noqa: E402  (a planner payload the real Planner accepts)
from test_memory_support import CONV, KENJI, REBECCA  # noqa: E402
from test_p14_memory_integrity import KI, REB_EX, RDET, REV, RI, erec, put  # noqa: E402
from test_p0_security import b64, claims as token_claims, raw_token  # noqa: E402  (an independent JWT encoder: the verifier under test does not build the attack tokens)

from harness.auth_tokens import issue_token  # noqa: E402
from llm.client import MockLLM  # noqa: E402

SECRET = "e2e-api-test-signing-secret-0123456789abcdef"
ADVISOR = "ADVISOR-001"                       # Kenji's advisor (directory: data/conversations.csv)
QUESTION = "Why did Kenji's retirement age change?"
VERIFIED_ID = "MEM-E2E-KENJI-VERIFIED"
VERIFIED_TEXT = "We've decided: 63."          # a real turn: INT-090 turn 2, spoken by the client
REBECCA_ID = "MEM-E2E-REBECCA"


# ---------------------------------------------------------------------------------------------------------------------------------------------- helpers
def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def ref_of(prompt: str, needle: str) -> str:
    """The evidence ref (E<n>) of the line of the Report Agent's own prompt that contains `needle`.  Fails loudly if the evidence is not there."""
    m = re.search(r"\[(E\d+)[^\]]*\][^\n]*" + re.escape(needle), prompt)
    assert m, f"no evidence line containing {needle!r} in the report prompt:\n{prompt[:3000]}"
    return m.group(1)


def tool_script(client_id=KENJI, tool="get_client_goals"):
    """Tool Selector protocol: one round that calls a permitted read-only tool, then 'finish'."""
    return [{"action": "call_tools", "reasoning": "need the client's goals", "missing_information": [],
             "calls": [{"tool": tool, "arguments": {"client_id": client_id}, "rationale": "goals for the retirement question"}]},
            {"action": "finish", "calls": [], "missing_information": [], "reasoning": "done"}]


def report_json(summary, key_findings=(), **sections):
    out = dict(summary=summary, key_findings=list(key_findings), historical_context=[], financial_analysis=[], uncertainties=[], suggested_followups=[], claims=[])
    out.update(sections)
    return out


USER_SECTIONS = ("summary", "key_findings", "historical_context", "financial_analysis", "uncertainties", "suggested_followups", "assumptions", "limitations")


def user_facing(report: dict) -> str:
    """Everything of the report a reader is shown (not the `validation` audit record, which is diagnostic)."""
    return json.dumps({k: report[k] for k in USER_SECTIONS})


def sha12(text: str) -> str:
    """The fingerprint the report layer records for a withheld sentence (computed independently here)."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def all_strings(obj):
    """Every string in a JSON document - keys AND values - so a leak cannot hide in a field name or a nested record."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from all_strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from all_strings(v)
    elif isinstance(obj, str):
        yield obj


def leaked(doc, phrase: str) -> list:
    """Strings of `doc` that contain `phrase` as whole words, ignoring case, whitespace and punctuation between the words (so 'Zanzibar' does not match 'Zanzibarish')."""
    words = [w for w in re.split(r"\W+", phrase) if w]
    rx = re.compile(r"(?<!\w)" + r"\W+".join(re.escape(w) for w in words) + r"(?!\w)", re.I)
    return [t for t in all_strings(doc) if rx.search(t)]


class World:
    def __init__(self, tmp: Path, client: TestClient, monkeypatch) -> None:
        self.tmp, self.client, self._mp = tmp, client, monkeypatch
        self.mem_db, self.ops_db, self.appr_db, self.trace_dir = tmp / "memory.db", tmp / "ops.db", tmp / "approvals.db", tmp / "traces"
        self.llm: MockLLM = MockLLM()
        self.prompts: dict = {}

    def script(self, **handlers) -> MockLLM:
        """Install the scripted model for this test.  A handler is a list (consumed in order) or a function (system, user, schema)."""
        self.llm = MockLLM(handlers=handlers)
        return self.llm

    def capturing(self, agent: str, fn):
        """Wrap a function handler so the prompt the agent really sent is kept (self.prompts[agent])."""
        def handler(system, user, schema):
            self.prompts[agent] = user
            return fn(user)
        return handler

    def headers(self, sub=ADVISOR, role="advisor") -> dict:
        return {"Authorization": "Bearer " + issue_token(SECRET, sub, role, 600)}

    def ask(self, question=QUESTION, **kw):
        return self.client.post("/api/ask", headers=self.headers(**kw), json={"question": question})

    # read-only views of the temporary databases
    def q(self, db: Path, sql: str, params=()):
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True)
        try:
            return con.execute(sql, params).fetchall()
        finally:
            con.close()

    def trace(self, request_id: str) -> list:
        p = self.trace_dir / f"{request_id}.jsonl"
        assert p.is_file(), f"no trace file for {request_id}: {sorted(x.name for x in self.trace_dir.glob('*'))}"
        return [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.fixture()
def world(tmp_path, monkeypatch):
    """The real app over temporary copies of the seed databases, a temporary approval DB and trace directory, and a scripted model."""
    repo_logs_existed = (ROOT / "logs").exists()
    guarded = [p for p in [*(ROOT / "build").glob("*.db"), *(ROOT / "data" / "seed").glob("*.db")]]
    before = {p: _sha(p) for p in guarded}

    mem_db, ops_db, appr_db, trace_dir = tmp_path / "memory.db", tmp_path / "ops.db", tmp_path / "approvals.db", tmp_path / "traces"
    shutil.copyfile(ROOT / "data" / "seed" / "cmf_memory_demo.db", mem_db)
    shutil.copyfile(ROOT / "data" / "seed" / "cmf_ops_demo.db", ops_db)

    for k in ("HARNESS_PROFILE", "CMF_AUTH_TRUSTED_GATEWAY", "CMF_AUTH_JWT_SECRET", "CMF_API_HOST", "CMF_API_DOCS_ENABLED", "HARNESS_ALLOW_ANONYMOUS_DEV",
              "HARNESS_ALLOW_ASSERTED_IDENTITY", "HARNESS_PERMISSION_GATE", "CMF_ALLOWED_CLIENTS", "HARNESS_MAX_LLM_CALLS", "HARNESS_MAX_TOOL_CALLS"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CMF_MEMORY_DATABASE_URL", f"sqlite:///{mem_db.as_posix()}")        # all environment is set BEFORE the app builds its store / harness
    monkeypatch.setenv("CMF_DATABASE_URL", f"sqlite:///{ops_db.as_posix()}")
    monkeypatch.setenv("CMF_APPROVAL_DATABASE_URL", f"sqlite:///{appr_db.as_posix()}")
    monkeypatch.setenv("CMF_LLM_BACKEND", "mock")
    monkeypatch.setenv("HARNESS_TRACE_ENABLED", "true")                                    # the trace is part of what is asserted; its directory is redirected below
    monkeypatch.setenv("HARNESS_PROFILE", "production")
    monkeypatch.setenv("CMF_AUTH_TRUSTED_GATEWAY", "true")
    monkeypatch.setenv("CMF_AUTH_JWT_SECRET", SECRET)

    import orchestration.tracing as tracing
    import harness.trace_recorder as trace_recorder
    monkeypatch.setattr(tracing, "TRACE_DIR", trace_dir)                                   # Tracer and prune_traces default to <repo>/logs/traces: redirect both
    monkeypatch.setattr(trace_recorder, "TRACE_DIR", trace_dir)

    # Test data written through the REAL integrity boundary (never to the seed files): one verified Kenji memory, one Rebecca memory.
    from agents.memory.store import MemoryStore
    store = MemoryStore(url=f"sqlite:///{mem_db.as_posix()}", conv=CONV)
    store.init()                                                                           # the same migration the application runs
    put(store, VERIFIED_ID, h="e2e-kenji-verified", content=VERIFIED_TEXT, value_text="63")
    store.insert_memory(erec(REBECCA_ID, client=REBECCA, h="e2e-rebecca", content=REB_EX, value_text=None, certainty="exploratory",
                             event_date=RDET["event_ts"][:10], source_role="Advisor"), dict(RDET), [REV], principal=RI)
    store.close()

    import api.main as m
    monkeypatch.setattr(m, "_store", None)
    monkeypatch.setattr(m, "_harness", None)
    holder: dict = {}
    monkeypatch.setattr("llm.client.make_llm", lambda: holder["w"].llm)                    # `ask()` imports make_llm at call time, so this is the injection point

    with TestClient(m.app) as client:                                                      # runs the ASGI lifespan (startup_checks) like a real server
        w = World(tmp_path, client, monkeypatch)
        holder["w"] = w
        yield w
    if m._store is not None:
        m._store.close()

    assert not (ROOT / "logs").exists() or repo_logs_existed, "a test wrote traces into the repository's logs/"
    assert {p: _sha(p) for p in guarded} == before, "a repository database or seed file changed"


def test_fixture_data_is_what_the_tests_assume(world):
    """Guard against a silent change of the seed: Kenji's seeded memories exist and are legacy-unverified; the two added memories are verified/present."""
    kenji = world.q(world.mem_db, "select verification, count(*) from memory_records where client_id=? group by 1", (KENJI,))
    assert dict(kenji).get("verified", 0) >= 1 and dict(kenji).get("unverified", 0) >= 60, kenji
    assert world.q(world.mem_db, "select verification from memory_records where memory_id=?", (VERIFIED_ID,)) == [("verified",)]
    assert world.q(world.mem_db, "select client_id from memory_records where memory_id=?", (REBECCA_ID,)) == [(REBECCA,)]


# ============================================================================================================================ E2E-01
def test_e2e_01_successful_complete_request(world):
    """A model-produced plan -> memory -> a real tool call -> a model-written report -> verification, through POST /api/ask."""
    seen = {}

    def report(user):
        seen["ref"] = ref_of(user, VERIFIED_TEXT)
        return report_json("Kenji decided on a retirement age of 63.", [dict(text="Kenji decided on a retirement age of 63.", refs=[seen["ref"]])])

    llm = world.script(planner=[planner_out(tools=True, required_tools=["get_client_goals"])], tool_selector=tool_script(),
                       report=world.capturing("report", report))
    r = world.ask()
    assert r.status_code == 200, r.text
    body = r.json()

    # the scripted model was used by the four agents, in order - this is NOT the deterministic fallback
    assert [c["agent"] for c in llm.calls] == ["planner", "tool_selector", "tool_selector", "report"]
    rep = body["report"]
    assert rep["validation"]["mode"] == "llm" and rep["validation"]["model_called"] is True
    assert body["client_ids"] == [KENJI] and body["released"] is True

    # the tool really ran (budget + the Tool Selector's own record) and its result is in the evidence
    h = body["harness"]
    assert h["budget"]["llm_calls"] == 4 and h["budget"]["tool_calls"] == 1
    execs = body["agent_run"]["tools"]["executions"]
    assert [(e["tool"], e["status"]) for e in execs] == [("get_client_goals", "success")], execs
    assert any(e["source_class"] == "operational_data" for e in rep["evidence"])

    # evidence-backed finding, verified by the real verifier
    assert [f["text"] for f in rep["key_findings"]] == ["Kenji decided on a retirement age of 63."]
    assert rep["key_findings"][0]["refs"] == [seen["ref"]]
    cited = next(e for e in rep["evidence"] if e["ref"] == seen["ref"])
    assert cited["memory_id"] == VERIFIED_ID and cited["evidence_class"] == "verified"
    he = rep["validation"]["harness_evidence"]
    assert he["mode"] == "structured" and he["counts"] == {"verified": 1, "qualified": 0, "unsupported": 0}
    assert [(f["verdict"], f["reason_codes"]) for f in he["findings"]] == [("verified", [])]

    # outcome: completed, complete, nothing degraded, no approval, no fallback flag
    assert h["outcome"] == "completed" and h["degraded_reasons"] == []
    assert rep["validation"]["harness_runtime"] == {"complete": True, "outcome": "completed", "degraded_reasons": []}
    assert body["agent_run"]["degraded"] == [] and body["agent_run"]["tools"]["fallback_used"] is False
    assert h["approval"] is None and h["released"] is True
    assert world.q(world.appr_db, "select count(*) from approval_requests") == [(0,)]

    # ONE request id across the whole trace, covering all four agents, the verifier and the finish event
    events = world.trace(body["request_id"])
    assert {e["request_id"] for e in events} == {body["request_id"]}
    assert {"planner", "memory", "tool_selector", "report", "harness.tool", "harness.evidence", "pipeline"} <= {e["component"] for e in events}
    finished = [e for e in events if e["component"] == "harness" and e["event"] == "request_finished"]
    assert len(finished) == 1 and finished[0]["payload"]["outcome"] == "completed" and finished[0]["payload"]["degraded_reasons"] == []
    assert [p.name for p in world.trace_dir.glob("*.jsonl")] == [f"{body['request_id']}.jsonl"]

    # database effect: working memory for this client only (the one persistent side effect of an ask)
    assert world.q(world.mem_db, "select count(*) from memory_records where memory_type='working' and client_id=?", (KENJI,))[0][0] >= 1
    assert world.q(world.mem_db, "select count(*) from memory_records where memory_type='working' and client_id<>?", (KENJI,)) == [(0,)]


# ============================================================================================================================ E2E-05
def test_e2e_05_memory_retrieval_and_provenance(world):
    """Retrieved memories belong to the authorized client; evidence keeps its provenance and verification metadata; withheld content is not echoed."""
    seen = {}
    SECRET_PHRASE = "absolutely certain he will never change his mind"

    def report(user):
        seen["verified"] = ref_of(user, VERIFIED_TEXT)
        seen["unverified"] = ref_of(user, "Kenji has decided to retire at 63.")                  # a seeded memory: legacy, so classed unverified
        return report_json("Kenji decided on a retirement age of 63.", [
            dict(text="Kenji decided on a retirement age of 63.", refs=[seen["verified"]]),
            dict(text=f"Kenji is {SECRET_PHRASE} about retiring at 63.", refs=[seen["unverified"]])])

    llm = world.script(planner=[planner_out(tools=False)], report=world.capturing("report", report))
    r = world.ask()
    assert r.status_code == 200, r.text
    body = r.json()
    assert [c["agent"] for c in llm.calls] == ["planner", "report"]                                  # no tool stage was planned

    # 1. relevant memories were retrieved, and all belong to the authorized client (checked against the database, not the response)
    kenji_ids = {row[0] for row in world.q(world.mem_db, "select memory_id from memory_records where client_id=?", (KENJI,))}
    rebecca_ids = {row[0] for row in world.q(world.mem_db, "select memory_id from memory_records where client_id=?", (REBECCA,))}
    assert REBECCA_ID in rebecca_ids and VERIFIED_ID in kenji_ids
    retrieved = set(body["retrieved_memory_ids"])
    assert VERIFIED_ID in retrieved and len(retrieved) >= 5
    assert retrieved <= kenji_ids and not (retrieved & rebecca_ids)
    rep = body["report"]
    mem_evidence = [e for e in rep["evidence"] if e.get("memory_id")]
    assert mem_evidence and {e["memory_id"] for e in mem_evidence} <= kenji_ids
    assert {e["client_id"] for e in mem_evidence} == {KENJI}

    # 2. provenance: each memory evidence item cites the same transcript turn as its database row, and its excerpt is that turn's text
    for e in mem_evidence:
        if e["interaction_id"] is None:
            continue
        rows = world.q(world.mem_db, "select interaction_id, turn_index from memory_evidence where memory_id=? and interaction_id is not null", (e["memory_id"],))
        assert (e["interaction_id"], e["turn_index"]) in rows, (e["memory_id"], rows)
    v = next(e for e in rep["evidence"] if e["memory_id"] == VERIFIED_ID)
    assert (v["interaction_id"], v["turn_index"]) == ("INT-090", 2)
    turn = CONV.turn("INT-090", 2)                                                                   # the transcript row, read independently of the application
    assert turn["client_id"] == KENJI and turn["speaker"] == "Client"
    assert v["excerpt"] == VERIFIED_TEXT and v["excerpt"] in turn["utterance"]                       # the quotation is real text of that turn
    assert VERIFIED_TEXT in v["description"]

    # the API's own performance record agrees: every retrieved item is Kenji's, and the cited flag matches the report's evidence
    items = body["agent_run"]["memory"]["items"]
    assert {i["client_id"] for i in items} == {KENJI} and {i["memory_id"] for i in items} == retrieved
    assert {i["memory_id"] for i in items if i["cited"]} == {e["memory_id"] for e in mem_evidence}

    # 3. verification metadata survives retrieval -> report evidence -> API response
    assert (v["verification"], v["evidence_class"], v["memory_source_class"]) == ("verified", "verified", "llm_extraction")
    u = next(e for e in rep["evidence"] if e["ref"] == seen["unverified"])
    assert (u["verification"], u["evidence_class"]) == ("unverified", "unverified") and "legacy" in (u["verification_basis"] or "").lower()
    assert "UNVERIFIED" in u["description"]                                                          # labelled in the text the model sees and in the response

    # 4. grounding: only the verified finding is kept; the one resting on an unverified memory is withdrawn and its text is not echoed anywhere
    assert [f["text"] for f in rep["key_findings"]] == ["Kenji decided on a retirement age of 63."]
    assert SECRET_PHRASE not in user_facing(rep)                                                     # nowhere a reader looks
    assert leaked(body, SECRET_PHRASE) == []                                                         # ... and nowhere in the WHOLE response, keys or values (P6.2 fix)
    withdrawn = rep["validation"]["findings_demoted"]                                                # the audit record survives, without the sentence
    assert len(withdrawn) == 1 and withdrawn[0]["classes"] == ["unverified"] and withdrawn[0]["refs"] == [seen["unverified"]]
    assert set(withdrawn[0]) == {"finding_sha", "chars", "refs", "classes"} and withdrawn[0]["chars"] == len(f"Kenji is {SECRET_PHRASE} about retiring at 63.")
    assert any("pending confirmation" in u or "unverified" in u.lower() for u in rep["uncertainties"])
    he = rep["validation"]["harness_evidence"]
    assert he["counts"]["verified"] == 1 and he["counts"]["unsupported"] == 0

    # 5. isolation: nothing of Rebecca's - id or text - is anywhere in the response or in the model's prompt
    wire = json.dumps(body)
    assert REBECCA_ID not in wire and REB_EX not in wire and REB_EX not in world.prompts["report"]
    assert "CLIENT-002" not in {e.get("client_id") for e in rep["evidence"]}


# ============================================================================================================================ E2E-08
def test_e2e_08_unsupported_claims_are_not_presented_as_verified(world):
    """One supported claim, one wrong figure, one invented quotation, one phantom evidence reference - each handled by the rule that owns it."""
    seen = {}
    INVENTED = 'Kenji said "I will retire at 55 no matter what the advisor says".'
    WRONG = "Kenji's retirement age is 61, per the Zanzibar lighthouse memo."                  # wrong figure + a distinctive fabricated detail
    PHANTOM = "Kenji's pension was topped up by the Quillfeather endowment."                           # cites evidence that does not exist

    def report(user):
        seen["ref"] = ref_of(user, VERIFIED_TEXT)
        r = seen["ref"]
        return report_json("Kenji's retirement age is 63, not 61.", [
            dict(text="Kenji decided on a retirement age of 63.", refs=[r]),                            # supported
            dict(text=WRONG, refs=[r]),                                                                 # wrong figure, real evidence
            dict(text=INVENTED, refs=[r]),                                                              # invented quotation / figure absent from the evidence
            dict(text=PHANTOM, refs=["E99"])])                                                          # evidence that does not exist

    llm = world.script(planner=[planner_out(tools=False)], report=world.capturing("report", report))
    r = world.ask()
    assert r.status_code == 200, r.text
    body = r.json()
    assert [c["agent"] for c in llm.calls] == ["planner", "report"]
    rep = body["report"]
    val = rep["validation"]
    assert val["mode"] == "llm"

    # the supported claim is kept, with its evidence, and VERIFIED by the verifier
    assert [f["text"] for f in rep["key_findings"]] == ["Kenji decided on a retirement age of 63."]
    assert rep["key_findings"][0]["refs"] == [seen["ref"]]
    he = val["harness_evidence"]
    assert he["counts"] == {"verified": 1, "qualified": 0, "unsupported": 1}
    verdicts = [(f["verdict"], f["outcome"], f["reason_codes"]) for f in he["findings"]]
    assert verdicts[0] == ("verified", "VERIFIED", [])
    # the wrong figure reached the verifier and was refused for its VALUE: that rule names the reason first (docs: "the first failing dimension names the reason"); the clause check may
    # add codes for the fabricated tail (it can only downgrade), so the primary code is pinned rather than the whole list
    assert verdicts[1][:2] == ("unsupported", "CONTRADICTED") and verdicts[1][2][0] == "VALUE_MISMATCH" and set(verdicts[1][2]) <= {"VALUE_MISMATCH", "ACTOR_MISMATCH", "UNSUPPORTED_CLAUSE"}

    # the wrong figure: not in key_findings; listed under uncertainties with the verifier's reason - and WITHOUT the sentence the model wrote
    assert not any("61" in f["text"] for f in rep["key_findings"])
    withheld = [u for u in rep["uncertainties"] if u.startswith("Withheld unsupported statement")]
    assert len(withheld) == 1 and "[text not shown" in withheld[0] and "VALUE_MISMATCH - a figure in the statement does not match the cited evidence" in withheld[0]
    assert not re.search(r"\b61\b", withheld[0])                                                      # P6.2.1: the code is public, the refused figure is not

    # the invented quotation and the phantom reference never reach the verifier: the Report Agent's own filters drop them, each for its own reason.
    # The audit record keeps a fingerprint + length + reason, never the text.
    dropped = {d["finding_sha"]: d for d in val["findings_dropped"]}
    assert all(set(d) == {"finding_sha", "chars", "reason"} for d in dropped.values()) and len(dropped) == 2
    assert dropped[sha12(INVENTED)]["reason"] == "number(s) not in evidence" and dropped[sha12(INVENTED)]["chars"] == len(INVENTED)
    assert dropped[sha12(PHANTOM)]["reason"] == "no valid evidence reference" and val["invalid_refs_removed"] == 1
    assert "E99" not in json.dumps(rep["key_findings"])

    # NO withheld sentence appears anywhere in the API response - report fields, validation records, agent_run copy, keys or values (whole-word, case-insensitive)
    for withheld_text in (WRONG, INVENTED, PHANTOM, "Zanzibar lighthouse memo", "no matter what the advisor says", "Quillfeather endowment", "Zanzibar", "Quillfeather"):
        assert leaked(body, withheld_text) == [], withheld_text
    # ... while the structured, machine-readable record of the withheld claim survives for review: reason codes and the verifier's outcome, with the sentence blanked
    assert all("text" not in w for w in he["withheld"]) and he["withheld"][0]["reason_codes"][0] == "VALUE_MISMATCH" and he["withheld"][0]["outcome"] == "CONTRADICTED"
    claim = he["findings"][1]["claims"][0]
    assert claim["claim"]["text"] is None and claim["claim"]["value"] is None and claim["claim"]["period"]["dates"] == []      # sentence, extracted value, extracted dates: gone
    assert claim["outcome"] == "CONTRADICTED" and claim["reasons"][0] == "VALUE_MISMATCH" and "61" not in claim["detail"] and claim["claim"]["topic"] == "retirement_age"

    # figures of the refused claims are not shown in any reader-facing field other than the verifier's own reason
    readable = json.dumps({k: rep[k] for k in USER_SECTIONS})
    assert not re.search(r"\b(55|61)\b", readable)                                                    # P6.2.1: not even in the uncertainties

    # the summary repeated the unsupported 61 as a fact: the verifier replaced it, and says why
    assert not re.search(r"\b61\b", rep["summary"]) and "63, not 61" not in readable
    assert he["summary_replaced"] == "summary cited figures/dates not in any evidence" and "61" not in he["summary_replaced"]

    # the request is not an error and not an approval case; the verifier ran and left an audit event naming what it withheld
    assert body["released"] is True and body["harness"]["outcome"] == "completed" and body["harness"]["evidence"] == {"verified": 1, "qualified": 0, "unsupported": 1}
    ev = [e for e in world.trace(body["request_id"]) if e["component"] == "harness.evidence" and e["event"] == "verified"]
    assert len(ev) == 1 and ev[0]["payload"]["counts"] == {"verified": 1, "qualified": 0, "unsupported": 1} and len(ev[0]["payload"]["withheld"]) == 1


# ============================================================================================================================ withheld text is not echoed (was a pinned xfail)
def test_e2e_05_withdrawn_unverified_text_appears_nowhere_in_the_api_response(world):
    """Phase 6.1 found that a finding withdrawn for resting on an unverified memory came back verbatim in report.validation.findings_demoted[].text (docs/harness.md and the
    docstring of authority_split say it is NOT echoed).  Fixed in Phase 6.2; this test was a strict xfail until then."""
    phrase = "absolutely certain he will never change his mind"

    def report(user):
        return report_json("Kenji decided on a retirement age of 63.", [dict(text=f"Kenji is {phrase} about retiring at 63.", refs=[ref_of(user, "Kenji has decided to retire at 63.")]),
                                                                          dict(text="Kenji decided on a retirement age of 63.", refs=[ref_of(user, VERIFIED_TEXT)])])
    world.script(planner=[planner_out(tools=False)], report=world.capturing("report", report))
    body = world.ask().json()
    assert [f["text"] for f in body["report"]["key_findings"]] == ["Kenji decided on a retirement age of 63."]          # the supported finding survives
    assert body["report"]["validation"]["harness_evidence"]["counts"]["verified"] == 1
    assert leaked(body, phrase) == [] and leaked(body, "never change his mind") == []                                   # nowhere, any field, key or value
    assert any("Withheld pending confirmation" in u for u in body["report"]["uncertainties"])                           # the reader is still told something was withheld, and why


# ============================================================================================================================ boundary tests (no HTTP): each place a withheld sentence used to be recorded
def test_boundary_withheld_ref_records_a_fingerprint_and_a_length_never_the_text():
    from agents.report.validate import withheld_ref
    t = "Kenji will retire at 61 on the Zanzibar lighthouse trust."
    r = withheld_ref(t)
    assert set(r) == {"finding_sha", "chars"} and r == {"finding_sha": sha12(t), "chars": len(t)}
    assert withheld_ref(t) == r and withheld_ref(t + " ") != r and withheld_ref("") == {"finding_sha": sha12(""), "chars": 0}


def test_boundary_authority_split_and_filter_findings_records_carry_no_text():
    from agents.report.validate import NumberChecker, authority_split, filter_findings
    from schemas.report import EvidenceItem, Finding
    bad_text, ok_text = "Kenji is certain of the Quillfeather plan.", "Kenji decided on 63."
    ev = [EvidenceItem(ref="E1", source_class="memory", description="d", evidence_class="unverified", client_id=KENJI),
          EvidenceItem(ref="E2", source_class="memory", description="d 63", evidence_class="verified", client_id=KENJI, value="63")]
    rec = {"findings_dropped": [], "invalid_refs_removed": 0}
    kept, notes = authority_split([Finding(text=bad_text, refs=["E1"]), Finding(text=ok_text, refs=["E2"])], ev, rec)
    assert [f.text for f in kept] == [ok_text]                                                      # the supported finding survives
    assert rec["findings_demoted"] == [{"finding_sha": sha12(bad_text), "chars": len(bad_text), "refs": ["E1"], "classes": ["unverified"]}]
    assert notes == ["Withheld pending confirmation: a statement rested on E1 (unverified), which is not verified evidence."]   # reason kept, sentence absent
    assert leaked({"rec": rec, "notes": notes}, "Quillfeather") == []

    nc = NumberChecker("Kenji decided on 63.")
    bad2 = "Kenji will retire at 55 near the Zanzibar harbour."
    out = filter_findings([Finding(text=ok_text, refs=["E2"]), Finding(text=bad2, refs=["E2"]), Finding(text="Phantom Quillfeather claim.", refs=["E99"])], ev, nc, rec)
    assert [f.text for f in out] == [ok_text]
    assert [(d["finding_sha"], d["reason"]) for d in rec["findings_dropped"]] == [(sha12(bad2), "number(s) not in evidence"), (sha12("Phantom Quillfeather claim."), "no valid evidence reference")]
    assert all(set(d) == {"finding_sha", "chars", "reason"} for d in rec["findings_dropped"]) and leaked(rec, "Zanzibar") == []


def test_boundary_rejected_structured_claims_keep_their_fields_and_reason_but_not_their_sentence():
    from agents.report.claims import validate_claims
    from schemas.report import Claim
    c = Claim(subject="Kenji", topic="retirement_age", value="61", status="current", evidence_ref="E9", text="Kenji retires at 61 thanks to the Zanzibar trust.")
    ok, rejected = validate_claims([c], [], SimpleNamespace(client_ids=[KENJI]))
    assert ok == [] and len(rejected) == 1 and rejected[0]["reason"]
    assert "text" not in rejected[0]["claim"] and rejected[0]["claim"]["finding_sha"] == sha12(c.text)
    assert "value" not in rejected[0]["claim"]                                                      # P6.2.1: the claimed value is the thing that was refused
    assert (rejected[0]["claim"]["topic"], rejected[0]["claim"]["status"], rejected[0]["claim"]["evidence_ref"]) == ("retirement_age", "current", "E9")
    assert not re.search(r"\b61\b", json.dumps(rejected))
    assert leaked(rejected, "Zanzibar") == []


def test_boundary_the_verifiers_public_records_blank_the_text_of_withheld_findings_only():
    from harness.evidence_verifier import FindingVerdict, VerificationResult
    claim = {"claim": {"claim_id": "C1.1", "text": "Kenji's retirement age is 61, funded by the Zanzibar trust.", "value": "61", "period": {"kind": "HISTORICAL", "dates": ["2026-02-30"]}}, "outcome": "CONTRADICTED", "reasons": ["VALUE_MISMATCH"], "detail": "claim 61 years vs evidence 63 years"}
    kept_claim = {"claim": {"claim_id": "C1.1", "text": "Kenji decided on 63.", "value": "63"}, "outcome": "VERIFIED", "reasons": []}
    bad = FindingVerdict("key_findings", "Kenji's retirement age is 61, funded by the Zanzibar trust.", ["E1"], "unsupported", ["61: VALUE_MISMATCH"], [], "CONTRADICTED", ["VALUE_MISMATCH"], [claim])
    good = FindingVerdict("key_findings", "Kenji decided on 63.", ["E1"], "verified", [], [], "VERIFIED", [], [kept_claim])
    res = VerificationResult(verdicts=[bad, good])
    assert "Zanzibar" in bad.as_dict()["text"]                                                      # the server-side form (trace) still has it ...
    pub = res.withheld_public()
    assert len(pub) == 1 and "text" not in pub[0] and pub[0]["reason_codes"] == ["VALUE_MISMATCH"] and pub[0]["outcome"] == "CONTRADICTED"
    summary = res.findings_summary()
    c0 = summary[0]["claims"][0]
    assert c0["claim"]["text"] is None and c0["claim"]["value"] is None and c0["claim"]["period"]["dates"] == [] and c0["reasons"] == ["VALUE_MISMATCH"]   # P6.2.1: value + dates gone, code kept
    assert "61" not in c0["detail"] and "61" not in json.dumps(pub) and "61" in json.dumps(bad.as_dict())                    # ... but the server-side form (trace) still has the detail
    assert pub[0]["reasons"] == ["VALUE_MISMATCH - a figure in the statement does not match the cited evidence"] and bad.reasons == ["61: VALUE_MISMATCH"]
    assert summary[1]["claims"][0]["claim"]["text"] == "Kenji decided on 63." and summary[1]["claims"][0]["claim"]["value"] == "63"      # a SUPPORTED finding keeps its text, value and claim
    assert claim["claim"]["text"].startswith("Kenji's retirement age")                              # the input was not mutated
    assert leaked(pub + [summary[0]], "Zanzibar") == [] and [v.text for v in res.verdicts][0].endswith("Zanzibar trust.")


def test_boundary_report_layer_reasons_keep_the_category_and_drop_the_figures():
    from agents.report.validate import public_reason
    assert public_reason("number(s) not in evidence: 47, 52") == "number(s) not in evidence"
    assert public_reason("presents non-current value 65 as current") == "presents a non-current value as current"
    assert public_reason("presents the current value 63 as exploratory") == "presents the current value as exploratory"
    assert public_reason("cites INT-777, which is not in the evidence") == "cites INT-777, which is not in the evidence"        # an id of the evidence list, not a claimed value
    assert public_reason("no valid evidence reference") == "no valid evidence reference" and public_reason("") == ""


# ============================================================================================================================ P6.2.1: claimed figures
def test_e2e_08_claimed_figures_of_withheld_claims_are_absent_from_the_public_response(world):
    """A differential check: the same request is run WITHOUT and WITH four fabricated figures, one per channel that used to repeat them (verifier reason / structured claim value,
    report-layer finding reason, summary note, rejected structured claim).  Every string of the second response that contains one of those figures as a whole token must also be in
    the first - i.e. the fabricated figures add nothing to the public response.  The detailed form must still exist in the protected server-side trace."""
    FIG = {"verifier": "60", "finding": "47", "summary": "38", "claim": "29"}
    # The two requests send the SAME report prompt (same evidence), so the application's response cache would answer the second with the first one's output.  Cache off for this test.
    import api.main as m
    world._mp.setenv("HARNESS_CACHE_ENABLED", "false")
    world._mp.setattr(m, "_harness", None)                      # rebuilt on the next request, with the cache disabled

    def report(fabricate):
        def build(user):
            r = ref_of(user, VERIFIED_TEXT)
            findings = [dict(text="Kenji decided on a retirement age of 63.", refs=[r])]
            if not fabricate:
                return report_json("Kenji decided on a retirement age of 63.", findings)
            findings += [dict(text=f"Kenji's retirement age is {FIG['verifier']}.", refs=[r]),                       # 60 occurs in the evidence (other memories), so it passes the report layer and reaches the verifier -> VALUE_MISMATCH
                         dict(text=f"Kenji will retire at {FIG['finding']} whatever happens.", refs=[r])]            # 47 occurs nowhere: the report layer drops it
            return report_json(f"Kenji's retirement age is 63, not {FIG['summary']}.", findings,
                               claims=[dict(subject="Kenji", topic="retirement_age", value=FIG["claim"], status="current", evidence_ref=r, text="Kenji retires at the claimed age.")])
        return build

    def run(fabricate):
        world.script(planner=[planner_out(tools=False)], report=world.capturing("report", report(fabricate)))
        return world.ask().json()

    def strings_with(body, figures):
        return {t for t in all_strings(body) if any(re.search(r"(?<![\w.\-])" + f + r"(?![\w\-]|\.\d)", t) for f in figures)}

    control = run(False)
    body = run(True)
    rep = body["report"]
    # the supported finding is intact in both runs, and the fabricated run really exercised the channels (otherwise the check below would be vacuous)
    assert [f["text"] for f in control["report"]["key_findings"]] == [f["text"] for f in rep["key_findings"]] == ["Kenji decided on a retirement age of 63."]
    he = rep["validation"]["harness_evidence"]
    assert he["counts"] == {"verified": 1, "qualified": 0, "unsupported": 1} and he["withheld"][0]["reason_codes"][0] == "VALUE_MISMATCH"
    assert [d["reason"] for d in rep["validation"]["findings_dropped"]] == ["number(s) not in evidence"]
    assert rep["validation"]["summary_replaced"] == "number(s) not in evidence"                                  # the report layer replaced the summary holding 38 ...
    assert "38" not in rep["summary"] and he["summary_replaced"] is None                                         # ... so the verifier had nothing left to replace
    assert len(rep["validation"]["claims_rejected"]) == 1 and rep["validation"]["claims_rejected"][0]["reason"] == "the claimed value does not match the cited evidence"

    # nothing the fabricated figures added is visible: every string holding one as a whole token was already in the control response (i.e. comes from the evidence)
    added = strings_with(body, FIG.values()) - strings_with(control, FIG.values())
    assert added == set(), added
    # the public reasons keep the CODE and a code-written description
    assert he["withheld"][0]["reasons"][0] == "VALUE_MISMATCH - a figure in the statement does not match the cited evidence"
    assert any("VALUE_MISMATCH - a figure in the statement does not match the cited evidence" in u for u in rep["uncertainties"])
    assert rep["validation"]["claims_rejected"][0]["claim"]["topic"] == "retirement_age" and "value" not in rep["validation"]["claims_rejected"][0]["claim"]

    # ... and the DETAILED form (claimed figure included) is still available, but only on the protected server-side path: the request's trace file
    ev = [e for e in world.trace(body["request_id"]) if e["component"] == "harness.evidence" and e["event"] == "verified"]
    detail = ev[0]["payload"]["withheld"][0]
    assert any(f"claim {FIG['verifier']} years vs evidence 63 years" in r for r in detail["reasons"]) and FIG["verifier"] in detail["text"]


# ============================================================================================================================ E2E-02  (Phase 6.3)
# Authentication at the real entry point.  Contract taken from the code, not guessed: api.main.identity() answers 401 {"detail": <message>} with
# `WWW-Authenticate: Bearer realm="cmf"`; the message of a token failure is "invalid token: <AuthError text>" (harness.auth_tokens.verify_token).  authorize() runs FIRST in ask(),
# before make_llm(), before run_pipeline(), so a rejected request must never reach a model, the memory store, a tool or the approval store.
OTHER_SECRET = "a-different-signing-secret-0123456789abcdef"
WWW_AUTH = 'Bearer realm="cmf"'
FORGED_ADMIN = {"X-CMF-Actor-Id": "mallory", "X-CMF-Actor-Role": "admin"}


def _bearer(token: str) -> dict:
    return {"Authorization": "Bearer " + token}


def _good_claims(role="admin", **extra) -> dict:
    """What an attacker would LIKE the token to say: the most powerful role."""
    return token_claims("ADVISOR-001", role, **extra)


def _tampered() -> str:
    head, _, sig = raw_token(_good_claims("advisor"), secret=SECRET).split(".")        # a genuine signature ...
    return ".".join([head, b64(json.dumps(_good_claims("admin")).encode()), sig])    # ... re-used under a promoted payload


def _bad_header_token() -> str:
    return ".".join([b64(b"this is not json"), b64(b"{}"), b64(b"sig")])


# (id, headers, exact detail).  Every case is an independent request against a fresh application.
ATTACKS = [
    # 1. no bearer token at all
    ("no_authorization_header", lambda: {}, "authentication required"),
    ("basic_scheme_instead_of_bearer", lambda: {"Authorization": "Basic YWRtaW46YWRtaW4="}, "Authorization must be 'Bearer <signed token>'"),
    ("bearer_keyword_without_a_token", lambda: {"Authorization": "Bearer"}, "Authorization must be 'Bearer <signed token>'"),
    # 2. malformed / invalidly signed
    ("garbage_token", lambda: _bearer("garbage"), "invalid token: token must have three non-empty parts"),
    ("two_part_token", lambda: _bearer("a.b"), "invalid token: token must have three non-empty parts"),
    ("empty_signature_part", lambda: _bearer("a.b."), "invalid token: token must have three non-empty parts"),
    ("non_base64_parts", lambda: _bearer("!!!.!!!.!!!"), "invalid token: malformed token header"),      # _b64d's "encoding" error is a ValueError, so verify_token reports it as a header error
    ("header_is_not_json", lambda: _bearer(_bad_header_token()), "invalid token: malformed token header"),
    ("oversized_token", lambda: _bearer("a" * 9000), "invalid token: token is missing or too large"),
    ("signed_with_another_secret", lambda: _bearer(raw_token(_good_claims(), secret=OTHER_SECRET)), "invalid token: invalid token signature"),
    ("genuine_signature_under_a_promoted_role", lambda: _bearer(_tampered()), "invalid token: invalid token signature"),
    ("validly_signed_but_no_exp", lambda: _bearer(raw_token({k: v for k, v in _good_claims().items() if k != "exp"}, secret=SECRET)), "invalid token: required claim 'exp' is missing or not a number"),
    ("validly_signed_but_empty_subject", lambda: _bearer(raw_token({**_good_claims(), "sub": ""}, secret=SECRET)), "invalid token: required claim 'sub' is missing or empty"),
    # 3. time
    ("expired_token", lambda: _bearer(raw_token(_good_claims(ttl=-3600), secret=SECRET)), "invalid token: token has expired"),
    ("not_yet_valid_token", lambda: _bearer(raw_token(_good_claims(nbf=int(time.time()) + 3600), secret=SECRET)), "invalid token: token is not valid yet"),
    # 4. algorithm confusion
    ("alg_none_unsigned", lambda: _bearer(raw_token(_good_claims(), secret=SECRET, alg="none", sign=False)), "invalid token: token must have three non-empty parts"),
    ("alg_none_with_a_signature", lambda: _bearer(raw_token(_good_claims(), secret=SECRET, alg="none")), "invalid token: unsupported token algorithm (only HS256 is accepted)"),
    ("alg_HS512_signed_with_the_real_secret", lambda: _bearer(raw_token(_good_claims(), secret=SECRET, alg="HS512")), "invalid token: unsupported token algorithm (only HS256 is accepted)"),
    ("alg_RS256_key_confusion", lambda: _bearer(raw_token(_good_claims(), secret=SECRET, alg="RS256")), "invalid token: unsupported token algorithm (only HS256 is accepted)"),
    ("alg_lowercase_hs256", lambda: _bearer(raw_token(_good_claims(), secret=SECRET, alg="hs256")), "invalid token: unsupported token algorithm (only HS256 is accepted)"),
    # 5. forged identity headers (never authentication)
    ("forged_admin_headers", lambda: dict(FORGED_ADMIN), "unsigned identity headers are not accepted; present a signed Bearer token"),
    ("forged_supervisor_headers", lambda: {"X-CMF-Actor-Id": "SUP-1", "X-CMF-Actor-Role": "supervisor"}, "unsigned identity headers are not accepted; present a signed Bearer token"),
    ("forged_role_header_only", lambda: {"X-CMF-Actor-Role": "admin"}, "unsigned identity headers are not accepted; present a signed Bearer token"),
    ("forged_actor_header_only", lambda: {"X-CMF-Actor-Id": "ADVISOR-001"}, "unsigned identity headers are not accepted; present a signed Bearer token"),
    ("forged_headers_do_not_rescue_a_bad_token", lambda: {**FORGED_ADMIN, **_bearer(raw_token(_good_claims(), secret=OTHER_SECRET))}, "invalid token: invalid token signature"),
]

# What a protected request would touch.  Each is a PASS-THROUGH counter: the real method still runs, nothing is replaced.
SPY_TARGETS = {
    "make_llm": ("llm.client", None, "make_llm"),
    "run_pipeline": ("orchestration.pipeline", None, "run_pipeline"),
    "planner": ("agents.planner.agent", "Planner", "plan"),
    "memory_retrieve": ("agents.memory.agent", "MemoryOrchestrator", "retrieve"),
    "memory_store_working": ("agents.memory.agent", "MemoryOrchestrator", "store_working"),
    "tool_selector": ("agents.tool_selector.agent", "ToolSelector", "run_sync"),
    "tool_call_guarded": ("harness.tools", "HarnessToolbox", "call"),
    "tool_call_mcp": ("agents.tool_selector.toolbox", "Toolbox", "call"),
    "report": ("agents.report.agent", "ReportAgent", "write"),
    "approval_create": ("harness.approval_store", "SqliteApprovalStore", "create"),
}


class Spies:
    def __init__(self, monkeypatch) -> None:
        import importlib
        self.counts = {k: 0 for k in SPY_TARGETS}
        self.model_calls: list = []                                    # one entry per model call the scripted handlers actually received
        for key, (module, cls, name) in SPY_TARGETS.items():
            owner = importlib.import_module(module)
            if cls:
                owner = getattr(owner, cls)
            original = getattr(owner, name)

            def wrapper(*a, _orig=original, _key=key, **k):
                self.counts[_key] += 1
                return _orig(*a, **k)
            monkeypatch.setattr(owner, name, wrapper)

    def handlers(self, **fns):
        """Scripted handlers that record every model call (agent name) before answering."""
        def counting(agent, fn):
            def handler(system, user, schema):
                self.model_calls.append(agent)
                return fn(user)
            return handler
        return {agent: counting(agent, fn) for agent, fn in fns.items()}

    def total(self) -> int:
        return sum(self.counts.values()) + len(self.model_calls)


@pytest.fixture()
def spies(world, monkeypatch):
    return Spies(monkeypatch)


def db_state(path: Path) -> dict:
    """Content fingerprint of every table of a SQLite file, read-only: {table: (row count, digest of all rows)}."""
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        out = {}
        for (name,) in con.execute("select name from sqlite_master where type='table' and name not like 'sqlite_%' order by name").fetchall():
            rows = con.execute(f'select * from "{name}"').fetchall()
            out[name] = (len(rows), hashlib.sha256("\n".join(sorted(repr(r) for r in rows)).encode("utf-8")).hexdigest()[:16])
        return out
    finally:
        con.close()


def snapshot(world) -> dict:
    return dict(memory=db_state(world.mem_db), approvals=db_state(world.appr_db), ops=_sha(world.ops_db),
                traces=sorted(p.name for p in world.trace_dir.glob("*")) if world.trace_dir.exists() else [])


def _valid_scripts(spies, world):
    """The scripted model for a request that is ALLOWED to proceed (used by the control tests)."""
    def report(user):
        return report_json("Kenji decided on a retirement age of 63.", [dict(text="Kenji decided on a retirement age of 63.", refs=[ref_of(user, VERIFIED_TEXT)])])
    return world.script(**spies.handlers(planner=lambda user: planner_out(tools=False), report=report))


@pytest.mark.parametrize("attack", ATTACKS, ids=[a[0] for a in ATTACKS])
def test_e2e_02_unauthenticated_request_is_rejected_before_any_part_of_the_workflow(world, spies, attack):
    name, make_headers, detail = attack
    _valid_scripts(spies, world)                                                     # a model IS available - it must simply never be asked
    before = snapshot(world)
    headers = make_headers()
    r = world.client.post("/api/ask", headers=headers, json={"question": QUESTION})

    # the HTTP contract
    assert r.status_code == 401, (r.status_code, r.text)
    assert r.json() == {"detail": detail}
    assert r.headers["www-authenticate"] == WWW_AUTH
    # nothing about a client, a memory, a report or the credential itself comes back
    wire = r.text
    for needle in ("Kenji", "CLIENT-0", "MEM-", "key_findings", "retrieved_memory_ids", "report", "request_id", SECRET, OTHER_SECRET, "mallory"):
        assert needle not in wire, needle
    for value in headers.values():
        assert len(value) < 30 or value not in wire                                  # the offered token is not echoed

    # zero model calls: the scripted handlers were never reached, and not even the model factory was called
    assert spies.model_calls == [] and world.llm.calls == []
    assert spies.counts["make_llm"] == 0
    # none of the components of the protected workflow ran (pass-through spies: each would have run the real method)
    assert spies.counts == {k: 0 for k in SPY_TARGETS}, spies.counts
    # no state changed anywhere: every table of the memory DB (retrieval is read-only; a working-memory write would show) and of the approvals DB, the ops DB, the trace directory
    assert snapshot(world) == before
    assert world.q(world.appr_db, "select count(*) from approval_requests") == [(0,)] and world.q(world.appr_db, "select count(*) from approval_events") == [(0,)]
    # a 401 produces no trace at all: authorize() runs before the Tracer exists (a denial INSIDE the pipeline - a 403 - is a different path and is traced; that is E2E-03)
    assert not any(world.trace_dir.glob("*.jsonl")) if world.trace_dir.exists() else True


def test_e2e_02_control_a_valid_token_does_reach_every_detector(world, spies):
    """Without this, 'zero calls' could just mean the spies are broken.  The same request with a correct token must trip them all and change state."""
    llm = _valid_scripts(spies, world)
    before = snapshot(world)
    r = world.ask()                                                                    # a genuine signed ADVISOR-001 token
    assert r.status_code == 200, r.text
    body = r.json()
    assert spies.model_calls == ["planner", "report"] and [c["agent"] for c in llm.calls] == ["planner", "report"]
    for key in ("make_llm", "run_pipeline", "planner", "memory_retrieve", "memory_store_working", "report"):
        assert spies.counts[key] == 1, (key, spies.counts)
    assert spies.counts["tool_selector"] == spies.counts["tool_call_mcp"] == spies.counts["approval_create"] == 0          # not planned / not required for this question
    after = snapshot(world)
    assert after["memory"] != before["memory"]                                          # the working memory written by an allowed ask is visible to the snapshot
    assert after["approvals"] == before["approvals"] and after["ops"] == before["ops"]
    assert after["traces"] == [f"{body['request_id']}.jsonl"]


def test_e2e_02_rejected_requests_leave_no_trace_and_do_not_disturb_the_next_valid_one(world, spies):
    """A burst of attacks, then a legitimate request: only the legitimate one has a request id, a trace, and side effects."""
    _valid_scripts(spies, world)
    before = snapshot(world)
    for _, make_headers, detail in ATTACKS[:8] + ATTACKS[-6:]:
        r = world.client.post("/api/ask", headers=make_headers(), json={"question": QUESTION})
        assert r.status_code == 401 and r.json() == {"detail": detail}
    assert snapshot(world) == before and spies.total() == 0
    ok = world.ask()
    assert ok.status_code == 200 and ok.json()["released"] is True
    assert spies.model_calls == ["planner", "report"]
    assert snapshot(world)["traces"] == [f"{ok.json()['request_id']}.jsonl"]


def test_e2e_02_forged_headers_are_ignored_even_next_to_a_valid_token(world, spies):
    """The headers never grant anything: with a VALID advisor token, 'X-CMF-Actor-Role: admin' changes nothing - the audit trail records the token's identity."""
    _valid_scripts(spies, world)
    r = world.client.post("/api/ask", headers={**world.headers("ADVISOR-001", "advisor"), **FORGED_ADMIN}, json={"question": QUESTION})
    assert r.status_code == 200, r.text
    events = world.trace(r.json()["request_id"])
    started = [e for e in events if e["component"] == "harness" and e["event"] == "request_started"][0]["payload"]
    assert (started["actor"], started["role"]) == ("ADVISOR-001", "advisor")
    assert "mallory" not in json.dumps(events) and "mallory" not in r.text


# ============================================================================================================================ E2E-03  (Phase 7.1)
# Permission enforcement at the real entry point.  From the code (harness/permission_gate.py, api/main.py, orchestration/pipeline.py):
#   * ROLES = advisor, supervisor, admin, operator, service, and OPERATIONS["ask"] allows ALL FIVE: no known role is denied `ask` by role.
#   * A signed token whose role is NOT one of those (identity_from_claims only strips + lower-cases it) passes authentication and is denied by authorize() -> 403 `invalid_role: ...`,
#     before make_llm() / run_pipeline(): nothing is entered and nothing is traced.
#   * A known role whose client scope does not cover the client NAMED in the question (advisors: the clients assigned to them in data/conversations.csv) is denied inside run_pipeline() by
#     the pre-check that runs before the first model call -> 403 `client_not_in_scope: ...`.  The pipeline WAS entered, so a trace with outcome `denied` is written BY DESIGN; it holds no
#     report, evidence or memory content.
# 401 (no / bad credential) is E2E-02; 403 (valid credential, not permitted) is here.
from harness.permission_gate import OPERATIONS, ROLES  # noqa: E402

KNOWN_ROLE_MESSAGE = "invalid_role: role {role!r} is not one of ('advisor', 'supervisor', 'admin', 'operator', 'service')"
KENJI_Q = QUESTION                                                  # names Kenji -> CLIENT-001, assigned to ADVISOR-001
REBECCA_Q = "How is Rebecca's retirement age?"                      # names Rebecca -> CLIENT-002, assigned to ADVISOR-002
MIXED_Q = "Compare Kenji and Rebecca on retirement age."
DENIED_TRACE_SEQUENCE = [("harness", "request_started"), ("harness.permission", "allow"), ("harness.permission", "deny"), ("pipeline", "denied"),
                         ("harness", "request_finished"), ("harness.reliability", "request_denied")]
DOWNSTREAM = [k for k in SPY_TARGETS if k not in ("make_llm", "run_pipeline")]       # everything that is "the workflow"
MEM_PREFIX = "MEM-"


def _scripts_for_any_client(spies, world):
    """Scripted model for a request that IS allowed, whichever client it names (the report cites nothing, so no client-specific text is needed)."""
    return world.script(**spies.handlers(planner=lambda user: planner_out(tools=False), report=lambda user: report_json("No finding is stated.", [])))


def _memory_needles(world) -> list:
    """Text that must never appear in a denied response or trace: every stored memory sentence and evidence excerpt of every client, plus the two fixture quotations."""
    rows = world.q(world.mem_db, "select content from memory_records") + world.q(world.mem_db, "select excerpt from memory_evidence where excerpt is not null")
    return sorted({r[0] for r in rows if r[0] and len(r[0]) > 20} | {VERIFIED_TEXT, REB_EX})


def _assert_denied_without_content(world, response) -> None:
    wire = response.text
    assert set(response.json()) == {"detail"} and response.headers.get("www-authenticate") is None       # 403, not a 401: no challenge, and no report / request_id / harness block
    for needle in _memory_needles(world) + ["key_findings", "retrieved_memory_ids", "agent_run", "harness", MEM_PREFIX]:
        assert needle not in wire, needle


def _denied_trace(world, *, actor: str, role: str, client: str) -> list:
    """The one trace a scope denial leaves behind: a DENIAL record, with no agent activity and no content."""
    files = sorted(world.trace_dir.glob("*.jsonl"))
    assert len(files) == 1, [f.name for f in files]
    events = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines() if line.strip()]
    assert [(e["component"], e["event"]) for e in events] == DENIED_TRACE_SEQUENCE
    assert {e["request_id"] for e in events} == {files[0].stem}
    assert events[0]["payload"]["actor"] == actor and events[0]["payload"]["role"] == role                        # the VERIFIED identity (role normalised)
    perms = [(e["event"], e["payload"]["operation"], e["payload"]["code"]) for e in events if e["component"] == "harness.permission"]
    assert perms == [("allow", "ask", "ok"), ("deny", "read_memory", "client_not_in_scope")]                       # `ask` was permitted; the CLIENT was not
    assert [e["payload"] for e in events if e["event"] == "denied"] == [{"code": "client_not_in_scope", "detail": f"actor {actor} has no access to ['{client}']"}]
    fin = events[4]["payload"]
    assert fin["outcome"] == "denied" and fin["degraded_reasons"] == [] and fin["evidence"] is None                # recorded as DENIED - not completed, not degraded, not abandoned
    assert fin["budget"]["llm_calls"] == 0 and fin["budget"]["tool_calls"] == 0 and fin["budget"]["input_tokens"] == 0
    assert "client_not_in_scope" in str(events[5]["payload"]["error"])
    assert not any(e["component"] in ("planner", "memory", "tool_selector", "report", "harness.llm", "harness.tool", "harness.evidence", "harness.approval") for e in events)
    assert not any((e["component"], e["event"]) == ("pipeline", "finished") for e in events)                      # a successful request would end with pipeline/finished
    blob = json.dumps(events)
    for needle in _memory_needles(world) + ["key_findings", "evidence_built", "report_written"]:
        assert needle not in blob, needle
    return events


def test_e2e_03_the_permission_model_denies_ask_to_no_known_role():
    """The finding behind the next tests: `ask` is open to every known role, so a signed token is denied `ask` by ROLE only when its role is unknown."""
    assert set(OPERATIONS["ask"]) == set(ROLES) == {"advisor", "supervisor", "admin", "operator", "service"}
    assert "advisor" not in OPERATIONS["approve"] and "advisor" not in OPERATIONS["lifecycle"] and "advisor" not in OPERATIONS["ingest"]          # roles ARE restricted elsewhere


# ---- 1. a valid, signed token for a role the permission model does not know
INVALID_ROLES = ["root", "guest", "superuser", "owner", "administrator", "advisor,admin", "supervisor-readonly", "advisor2", "  GUEST  "]


@pytest.mark.parametrize("role", INVALID_ROLES, ids=["root", "guest", "superuser", "owner", "administrator", "two_roles_joined", "supervisor_readonly", "advisor2", "padded_upper_case_guest"])
def test_e2e_03_unknown_role_is_denied_403_before_the_pipeline_is_entered(world, spies, role):
    _valid_scripts(spies, world)
    before = snapshot(world)
    r = world.client.post("/api/ask", headers=world.headers("ADVISOR-001", role), json={"question": KENJI_Q})      # the token itself is genuine: correct signature, fresh exp, sub, role

    assert r.status_code == 403, (r.status_code, r.text)
    assert r.json() == {"detail": KNOWN_ROLE_MESSAGE.format(role=role.strip().lower())}
    _assert_denied_without_content(world, r)
    # nothing was entered: not the model factory, not the pipeline, none of the agents, tools, stores or the approval store; no model call
    assert spies.counts == {k: 0 for k in SPY_TARGETS}, spies.counts
    assert spies.model_calls == [] and world.llm.calls == []
    # no state change anywhere, and (authorize() runs before the Tracer exists) no trace at all
    assert before["traces"] == [] and snapshot(world) == before              # the snapshot includes the trace directory listing: still empty
    assert world.q(world.appr_db, "select count(*) from approval_requests") == [(0,)]


# ---- 2. a known role whose client scope does not cover the client the question names
SCOPE_DENIALS = [
    ("advisor_asks_about_another_advisors_client", "ADVISOR-002", "advisor", KENJI_Q, "CLIENT-001"),
    ("the_reverse_pair", "ADVISOR-001", "advisor", REBECCA_Q, "CLIENT-002"),
    ("advisor_with_no_clients_at_all", "NOBODY-9", "advisor", KENJI_Q, "CLIENT-001"),
    ("role_spelling_does_not_widen_scope", "ADVISOR-002", "Advisor", KENJI_Q, "CLIENT-001"),
    ("a_mixed_request_is_denied_whole", "ADVISOR-001", "advisor", MIXED_Q, "CLIENT-002"),
]


@pytest.mark.parametrize("case", SCOPE_DENIALS, ids=[c[0] for c in SCOPE_DENIALS])
def test_e2e_03_client_outside_the_principals_scope_is_denied_403_before_any_model_call(world, spies, case):
    name, sub, role, question, denied_client = case
    _valid_scripts(spies, world)
    before = snapshot(world)
    r = world.client.post("/api/ask", headers=world.headers(sub, role), json={"question": question})

    assert r.status_code == 403, (r.status_code, r.text)
    assert r.json() == {"detail": "client_not_in_scope: the request refers to a client you are not permitted to use"}        # Phase 6.4: names no client and no actor
    assert denied_client not in r.text and sub not in r.text
    _assert_denied_without_content(world, r)
    if name == "a_mixed_request_is_denied_whole":
        assert "CLIENT-001" not in r.text and "Kenji" not in r.text                                           # the client the caller COULD read is not served either: no partial answer

    # the pipeline was entered (that is where the scope check lives) - and nothing else was: no model call, no agent, no tool, no memory read or write, no approval
    assert spies.counts["run_pipeline"] == 1 and spies.counts["make_llm"] <= 1
    assert {k: spies.counts[k] for k in DOWNSTREAM} == {k: 0 for k in DOWNSTREAM}, spies.counts
    assert spies.model_calls == [] and world.llm.calls == []
    after = snapshot(world)
    assert (after["memory"], after["approvals"], after["ops"]) == (before["memory"], before["approvals"], before["ops"])
    assert world.q(world.appr_db, "select count(*) from approval_requests") == [(0,)] and world.q(world.appr_db, "select count(*) from approval_events") == [(0,)]
    assert world.q(world.mem_db, "select count(*) from memory_records where memory_type='working'") == [(0,)]

    # the denial IS recorded, as a denial: exactly one trace, outcome `denied`, no content
    assert before["traces"] == [] and len(after["traces"]) == 1
    _denied_trace(world, actor=sub, role=role.strip().lower(), client=denied_client)


# ---- 3. positive controls: the same setup, an authorized principal, and the workflow DOES run
CONTROLS = [("advisor_for_their_own_client", "ADVISOR-001", "advisor", KENJI_Q), ("the_reverse_pair_for_their_own_client", "ADVISOR-002", "advisor", REBECCA_Q)]


@pytest.mark.parametrize("control", CONTROLS, ids=[c[0] for c in CONTROLS])
def test_e2e_03_control_an_authorized_principal_with_the_same_setup_proceeds(world, spies, control):
    """If the spies, the scripted model or the fixture were what stopped the denied requests, this would be stopped too."""
    _, sub, role, question = control
    llm = _scripts_for_any_client(spies, world)
    before = snapshot(world)
    r = world.client.post("/api/ask", headers=world.headers(sub, role), json={"question": question})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["client_ids"] and body["released"] is True and "report" in body                           # a real answer, not a denial
    for key in ("make_llm", "run_pipeline", "planner", "memory_retrieve", "memory_store_working", "report"):
        assert spies.counts[key] == 1, (key, spies.counts)
    assert spies.model_calls == ["planner", "report"] and [c["agent"] for c in llm.calls] == ["planner", "report"]
    after = snapshot(world)
    assert after["memory"] != before["memory"] and after["approvals"] == before["approvals"]               # working memory was written - the denied cases changed nothing
    events = world.trace(body["request_id"])
    assert body["harness"]["outcome"] in ("completed", "degraded")
    fin = [e for e in events if e["event"] == "request_finished"][0]["payload"]
    assert fin["outcome"] == body["harness"]["outcome"] and fin["outcome"] != "denied"
    assert not any((e["component"], e["event"]) == ("pipeline", "denied") for e in events) and ("pipeline", "finished") in [(e["component"], e["event"]) for e in events]
    assert {"planner", "memory", "report"} <= {e["component"] for e in events}


KNOWN_ROLE_PRINCIPALS = [("advisor", "ADVISOR-001"), ("supervisor", "SUP-1"), ("admin", "ADM-1"), ("operator", "OPS-1"), ("service", "SVC-1")]


@pytest.mark.parametrize("role,sub", KNOWN_ROLE_PRINCIPALS, ids=[r for r, _ in KNOWN_ROLE_PRINCIPALS])
def test_e2e_03_control_every_known_role_may_ask_and_reaches_the_workflow(world, spies, role, sub):
    _valid_scripts(spies, world)
    r = world.client.post("/api/ask", headers=world.headers(sub, role), json={"question": KENJI_Q})
    assert r.status_code == 200, r.text
    assert spies.counts["planner"] == spies.counts["memory_retrieve"] == spies.counts["report"] == 1
    started = [e for e in world.trace(r.json()["request_id"]) if e["event"] == "request_started"][0]["payload"]
    assert (started["actor"], started["role"]) == (sub, role)


# ---- 4. E2E-03.1 (Phase 6.4): an inaccessible client must not reveal whether it exists
# Root cause (services/conversations.py:resolve_clients): only clients that EXIST in the directory are returned, so `CLIENT-999` resolved to nothing, no scope check ran, and the request went
# on to the planner (200), while an existing client the caller may not use was denied with its id in the message.  Fix: for a caller with a restricted scope an explicit id that does not exist
# goes through the same pre-model scope check (pipeline._run), and a client_not_in_scope 403 never names a client (api.main._forbidden).
GENERIC_SCOPE_DETAIL = "client_not_in_scope: the request refers to a client you are not permitted to use"


def _spy_delta(spies, before_counts, before_calls) -> dict:
    return {**{k: v - before_counts[k] for k, v in spies.counts.items()}, "model_calls": len(spies.model_calls) - before_calls}


def _trace_shape(world, new_files) -> list:
    """Structure of the trace a request left (ids normalised): the sequence of events, the permission decisions, the outcome and the counters.  Used to compare two denials."""
    assert len(new_files) == 1, new_files
    events = [json.loads(line) for line in (world.trace_dir / new_files[0]).read_text(encoding="utf-8").splitlines() if line.strip()]
    norm = lambda o: json.loads(re.sub(r"CLIENT-\d{3}", "CLIENT-X", json.dumps(o)))          # noqa: E731  (the protected trace may name the client; the SHAPE must not depend on it)
    fin = [e for e in events if e["event"] == "request_finished"][0]["payload"]
    return [[(e["component"], e["event"]) for e in events],
            [(e["event"], e["payload"]["operation"], e["payload"]["code"]) for e in events if e["component"] == "harness.permission"],
            norm([e["payload"] for e in events if e["event"] == "denied"]),
            [fin["outcome"], fin["budget"]["llm_calls"], fin["budget"]["tool_calls"], fin["budget"]["input_tokens"], fin["evidence"], fin["degraded_reasons"]]]


def observe(world, spies, sub, role, question) -> dict:
    """Everything an observer (or the database) can see about ONE request, in one comparable structure."""
    before, counts, calls, files = snapshot(world), dict(spies.counts), len(spies.model_calls), set(p.name for p in world.trace_dir.glob("*.jsonl")) if world.trace_dir.exists() else set()
    r = world.client.post("/api/ask", headers=world.headers(sub, role), json={"question": question})
    after = snapshot(world)
    new = sorted(set(p.name for p in world.trace_dir.glob("*.jsonl")) - files) if world.trace_dir.exists() else []
    return dict(status=r.status_code, body=r.json() if r.headers.get("content-type", "").startswith("application/json") else r.text, headers=dict(r.headers), text=r.text,
                spies=_spy_delta(spies, counts, calls), memory_changed=after["memory"] != before["memory"], approvals_changed=after["approvals"] != before["approvals"],
                ops_changed=after["ops"] != before["ops"], new_traces=len(new), trace=_trace_shape(world, new) if new else None)


# (id, caller, role, question about a client that EXISTS but is not the caller's, question about an id that does NOT exist - same sentence shape)
INACCESSIBLE_PAIRS = [
    ("by_id_upper_case", "ADVISOR-002", "advisor", "Tell me about CLIENT-001's retirement age.", "Tell me about CLIENT-999's retirement age."),
    ("by_id_lower_case", "ADVISOR-002", "advisor", "tell me about client-001 retirement age", "tell me about client-999 retirement age"),
    ("the_reverse_caller", "ADVISOR-001", "advisor", "Tell me about CLIENT-002's retirement age.", "Tell me about CLIENT-998's retirement age."),
    ("a_caller_with_no_clients", "NOBODY-9", "advisor", "Tell me about CLIENT-001's retirement age.", "Tell me about CLIENT-777's retirement age."),
    ("own_client_plus_an_inaccessible_one", "ADVISOR-001", "advisor", "Compare CLIENT-001 and CLIENT-002 on retirement age.", "Compare CLIENT-001 and CLIENT-999 on retirement age."),
]


@pytest.mark.parametrize("pair", INACCESSIBLE_PAIRS, ids=[p[0] for p in INACCESSIBLE_PAIRS])
def test_e2e_03_1_an_inaccessible_existing_client_and_a_nonexistent_client_id_are_indistinguishable(world, spies, pair):
    _, sub, role, existing_q, phantom_q = pair
    _valid_scripts(spies, world)
    a = observe(world, spies, sub, role, existing_q)                  # a client that exists and the caller may not use
    b = observe(world, spies, sub, role, phantom_q)                   # a client id that does not exist

    # what the caller sees: identical status, identical COMPLETE body, identical headers
    assert a["status"] == b["status"] == 403
    assert a["body"] == b["body"] == {"detail": GENERIC_SCOPE_DETAIL}
    assert a["text"] == b["text"] and a["headers"] == b["headers"] and "www-authenticate" not in a["headers"]
    # ... and it names nothing: no client id (existing or not), no client name, no actor id, nothing from the data
    for wire in (a["text"], b["text"]):
        assert not re.search(r"CLIENT-\d{3}", wire, re.I) and "Kenji" not in wire and "Rebecca" not in wire and sub not in wire
        for needle in _memory_needles(world):
            assert needle not in wire
    # what the system did: the same, and nothing downstream - no planner / memory / tool / report, no model call, no working-memory write, no approval
    assert a["spies"] == b["spies"]
    assert a["spies"]["model_calls"] == 0 and a["spies"]["run_pipeline"] == 1
    assert {k: a["spies"][k] for k in DOWNSTREAM} == {k: 0 for k in DOWNSTREAM}
    assert (a["memory_changed"], a["approvals_changed"], a["ops_changed"]) == (b["memory_changed"], b["approvals_changed"], b["ops_changed"]) == (False, False, False)
    assert world.q(world.appr_db, "select count(*) from approval_requests") == [(0,)]
    # the protected trace records the same DENIAL for both (same events, same permission decision, same outcome and counters)
    assert a["new_traces"] == b["new_traces"] == 1 and a["trace"] == b["trace"]
    assert a["trace"][0] == DENIED_TRACE_SEQUENCE and a["trace"][3][0] == "denied"
    assert world.llm.calls == []


def test_e2e_03_1_control_an_authorized_client_named_by_id_still_works(world, spies):
    llm = _valid_scripts(spies, world)
    o = observe(world, spies, "ADVISOR-001", "advisor", "Why did CLIENT-001's retirement age change?")
    assert o["status"] == 200 and o["body"]["client_ids"] == ["CLIENT-001"] and o["body"]["released"] is True
    assert o["spies"]["planner"] == o["spies"]["memory_retrieve"] == o["spies"]["report"] == 1 and o["spies"]["model_calls"] == 2
    assert o["memory_changed"] is True and o["trace"][3][0] == "completed"
    assert [c["agent"] for c in llm.calls] == ["planner", "report"]


def test_e2e_03_1_control_a_question_naming_no_client_is_unaffected(world, spies):
    """The legitimate generic question: no explicit client reference, so nothing is denied (the API contract: a 200 answer that says there is no evidence)."""
    _scripts_for_any_client(spies, world)
    o = observe(world, spies, "ADVISOR-002", "advisor", "What is a retirement glide path?")
    assert o["status"] == 200 and o["body"]["client_ids"] == []
    assert o["spies"]["planner"] == 1 and o["spies"]["model_calls"] >= 1
    assert o["trace"][3][0] in ("completed", "degraded") and ("pipeline", "denied") not in o["trace"][0]


@pytest.mark.parametrize("role,sub", [("supervisor", "SUP-1"), ("admin", "ADM-1")], ids=["supervisor", "admin"])
def test_e2e_03_1_control_callers_who_may_read_every_client_see_no_change_for_a_mistyped_id(world, spies, role, sub):
    """Existence is not secret from a caller whose scope is every client (they can list them all), so their behaviour is deliberately unchanged: no denial, the planner runs."""
    _scripts_for_any_client(spies, world)
    o = observe(world, spies, sub, role, "Tell me about CLIENT-999's retirement age.")
    assert o["status"] == 200 and o["body"]["client_ids"] == [] and o["spies"]["planner"] == 1
    assert o["trace"][3][0] in ("completed", "degraded")


def test_e2e_03_1_boundary_explicit_client_ids_and_the_403_text():
    from api.main import _forbidden
    from harness.permission_gate import PermissionDenied
    from services.conversations import shared
    conv = shared()
    assert conv.explicit_client_ids("about client-999, CLIENT-001 and Client-999 again") == ["CLIENT-999", "CLIENT-001"]
    assert conv.explicit_client_ids("Kenji and Rebecca, no ids here") == [] and conv.explicit_client_ids("CLIENT-12") == []
    assert conv.resolve_clients("about CLIENT-999")[0] == []                               # unchanged: resolve_clients still returns only clients that exist
    e = _forbidden(PermissionDenied("client_not_in_scope", "actor ADVISOR-002 has no access to ['CLIENT-001']"))
    assert (e.status_code, e.detail) == (403, GENERIC_SCOPE_DETAIL)
    other = _forbidden(PermissionDenied("invalid_role", "role 'root' is not one of ('advisor',)"))
    assert (other.status_code, other.detail) == (403, "invalid_role: role 'root' is not one of ('advisor',)")           # every other denial keeps its text


# ---- 5. the part that cannot be closed without a contract decision (pinned, not hidden)
@pytest.mark.xfail(strict=True, reason="KNOWN RESIDUAL (Phase 6.4): the client-ID disclosure is closed, but a first NAME is different.  `Kenji` (an existing client the caller may not use) is denied "
                   "(403) while `Zorbax` (not a client) is just a word, so the question is answered (200, one planner call).  Telling the two apart is impossible for the server without either "
                   "(a) denying every question that contains an unknown capitalised word, or (b) answering a question about an inaccessible client with a fake 'no evidence' 200 AFTER spending a "
                   "planner call - both are API-contract changes (and (b) hides legitimate denials from the audit trail).  Production code was NOT changed for this.  strict=True: it XPASSES "
                   "(and fails the run) if the contract is ever changed so the two answers are identical.")
def test_e2e_03_1_residual_a_first_name_still_distinguishes_an_inaccessible_client_from_an_unknown_word(world, spies):
    _scripts_for_any_client(spies, world)
    existing = world.client.post("/api/ask", headers=world.headers("ADVISOR-002", "advisor"), json={"question": KENJI_Q})
    unknown = world.client.post("/api/ask", headers=world.headers("ADVISOR-002", "advisor"), json={"question": "Why did Zorbax's retirement age change?"})
    assert existing.status_code == unknown.status_code


# ============================================================================================================================ E2E-11  (Phase 6.5)
# Cross-client isolation.  Real relationships (data/conversations.csv): ADVISOR-001 -> CLIENT-001 Kenji, CLIENT-004; ADVISOR-002 -> CLIENT-002 Rebecca, CLIENT-005; ADVISOR-003 -> CLIENT-003, CLIENT-006.
# Method: CANARY values (distinct, and containing no client name or id, so a question that quotes one cannot trigger client resolution) are planted in the TEMPORARY copies of the operational
# DB (profile occupation, one goal description) and the memory DB (one memory sentence per client).  A canary that reaches its owner proves the data path is live; a canary that appears
# ANYWHERE else - the HTTP response, a prompt sent to any model-facing agent, working memory, the approval tables, the trace - is a leak.
from services.conversations import shared as _conversations  # noqa: E402

CANARY = {"CLIENT-001": dict(occ="CNRY-K-OCC-4410", goal="CNRY-K-GOAL-7781", mem="CNRY-K-MEM-3302"),
          "CLIENT-002": dict(occ="CNRY-R-OCC-5521", goal="CNRY-R-GOAL-6692", mem="CNRY-R-MEM-9154")}
OWN = {"ADVISOR-001": "CLIENT-001", "ADVISOR-002": "CLIENT-002"}
CLIENT_TEXT = {"CLIENT-001": VERIFIED_TEXT, "CLIENT-002": REB_EX}            # a real stored sentence of each client's memory
MEM_ID = {"CLIENT-001": "MEM-CANARY-K", "CLIENT-002": "MEM-CANARY-R"}
FOREIGN_INTERACTION = {"CLIENT-001": ("INT-061", 11, REB_EX), "CLIENT-002": ("INT-090", 2, VERIFIED_TEXT)}      # (a meeting + turn of the OTHER client, its text)


def _scope(advisor: str) -> set:
    return {c for c, v in _conversations().directory().items() if v["advisor_id"] == advisor}


def _plant_canaries(world) -> None:
    con = sqlite3.connect(world.ops_db.as_posix())
    for cid, c in CANARY.items():
        con.execute("update client_financial_profiles set occupation=? where client_id=?", (c["occ"], cid))
        con.execute("update financial_goals set goal_description=? where client_id=?", (c["goal"], cid))        # every goal row: get_client_goals returns only the current version of each
    con.commit()
    con.close()
    from agents.memory.store import MemoryStore
    st = MemoryStore(url=f"sqlite:///{world.mem_db.as_posix()}", conv=CONV)           # written THROUGH the integrity boundary, like every other test memory
    try:
        put(st, MEM_ID["CLIENT-001"], h="canary-k", content=f"{CANARY['CLIENT-001']['mem']} {VERIFIED_TEXT}", value_text="63")
        st.insert_memory(erec(MEM_ID["CLIENT-002"], client=REBECCA, h="canary-r", content=f"{CANARY['CLIENT-002']['mem']} {REB_EX}", value_text=None, certainty="exploratory",
                              event_date=RDET["event_ts"][:10], source_role="Advisor"), dict(RDET), [REV], principal=RI)
    finally:
        st.close()


@pytest.fixture()
def iso(world):
    _plant_canaries(world)                                                           # before the first request: the application opens its stores lazily
    return world


def run_scripted(world, spies, sub, role, question, *, calls=(), report=None):
    """One real POST /api/ask with a scripted model.  Returns (response, prompts) - `prompts` holds every user prompt each model-facing agent actually received."""
    prompts = {"planner": [], "tool_selector": [], "report": []}
    state = {"n": 0}

    def planner(system, user, schema):
        prompts["planner"].append(user)
        spies.model_calls.append("planner")
        return planner_out(tools=bool(calls), required_tools=sorted({c["tool"] for c in calls}))

    def selector(system, user, schema):
        prompts["tool_selector"].append(user)
        spies.model_calls.append("tool_selector")
        state["n"] += 1
        if state["n"] == 1 and calls:
            return {"action": "call_tools", "reasoning": "r", "missing_information": [], "calls": list(calls)}
        return {"action": "finish", "calls": [], "missing_information": [], "reasoning": "done"}

    def rep(system, user, schema):
        prompts["report"].append(user)
        spies.model_calls.append("report")
        return (report or (lambda u: report_json("No finding is stated.", [])))(user)

    world.script(planner=planner, tool_selector=selector, report=rep)
    return world.client.post("/api/ask", headers=world.headers(sub, role), json={"question": question}), prompts


def call(tool, client_id, **extra):
    return {"tool": tool, "arguments": {"client_id": client_id, **extra}, "rationale": "scripted"}


def surfaces(world, response_text: str, prompts: dict) -> dict:
    """Every place client data could end up, as text."""
    work = world.q(world.mem_db, "select client_id, coalesce(title,''), coalesce(content,''), coalesce(value_text,'') from memory_records where memory_type='working'")
    appr = world.q(world.appr_db, "select * from approval_requests") + world.q(world.appr_db, "select * from approval_events")
    trace = "\n".join(p.read_text(encoding="utf-8") for p in sorted(world.trace_dir.glob("*.jsonl"))) if world.trace_dir.exists() else ""
    return dict(response=response_text, prompts="\n".join("\n".join(v) for v in prompts.values()), working_memory="\n".join(map(repr, work)), approvals="\n".join(map(repr, appr)), trace=trace)


def _foreign(advisor: str, *, ids_and_names=True) -> dict:
    """What must not reach `advisor`: the canaries and stored sentences of clients outside their scope, plus those clients' names and ids."""
    d, mine = _conversations().directory(), _scope(advisor)
    other = [c for c in d if c not in mine]
    literal = [v for c in other if c in CANARY for v in CANARY[c].values()] + [CLIENT_TEXT[c] for c in other if c in CLIENT_TEXT]
    words = ([d[c]["first_name"] for c in other] + other) if ids_and_names else []
    return dict(literal=literal, words=words)


def leaks(surf: dict, needles: dict, skip=()) -> dict:
    found = {}
    for name, text in surf.items():
        if name in skip:
            continue
        hit = [n for n in needles["literal"] if n in text] + [w for w in needles["words"] if re.search(r"(?<![\w-])" + re.escape(w) + r"(?![\w-]|\d)", text, re.I)]
        if hit:
            found[name] = sorted(set(hit))
    return found


def _working_clients(world) -> set:
    return {r[0] for r in world.q(world.mem_db, "select client_id from memory_records where memory_type='working'")}


# ---- 0. the canaries are where the tests assume
def test_e2e_11_fixture_canaries_are_distinct_planted_and_name_free(iso):
    flat = [v for c in CANARY.values() for v in c.values()]
    assert len(set(flat)) == len(flat) == 6
    names = [v["first_name"].lower() for v in _conversations().directory().values()]
    assert not any(n in v.lower() for v in flat for n in names) and not any(re.search(r"client-\d", v, re.I) for v in flat)         # quoting one cannot trigger client resolution
    for cid, c in CANARY.items():
        assert iso.q(iso.ops_db, "select occupation from client_financial_profiles where client_id=?", (cid,)) == [(c["occ"],)]
        assert iso.q(iso.ops_db, "select count(*) from financial_goals where client_id=? and goal_description<>?", (cid, c["goal"])) == [(0,)]
        assert iso.q(iso.ops_db, "select count(*) from financial_goals where client_id=?", (cid,))[0][0] >= 1
        assert iso.q(iso.mem_db, "select client_id from memory_records where memory_id=? and content like ?", (MEM_ID[cid], f"{c['mem']}%")) == [(cid,)]
    assert _scope("ADVISOR-001") == {"CLIENT-001", "CLIENT-004"} and _scope("ADVISOR-002") == {"CLIENT-002", "CLIENT-005"} and _scope("ADVISOR-003") == {"CLIENT-003", "CLIENT-006"}


# ---- 1. positive controls: each advisor reaches exactly their own data, a global role keeps its scope
OWN_CASES = [("advisor_a_own_client", "ADVISOR-001", "CLIENT-001", "Why did Kenji's retirement age change?"),
             ("advisor_b_own_client", "ADVISOR-002", "CLIENT-002", "How is Rebecca's retirement age?")]


@pytest.mark.parametrize("case", OWN_CASES, ids=[c[0] for c in OWN_CASES])
def test_e2e_11_control_each_advisor_reaches_exactly_their_own_data(iso, spies, case):
    _, sub, own, question = case
    r, prompts = run_scripted(iso, spies, sub, "advisor", question, calls=[call("get_client_profile", own), call("get_client_goals", own)])
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["client_ids"] == [own] and [e["status"] for e in body["agent_run"]["tools"]["executions"]] == ["success", "success"]
    surf = surfaces(iso, r.text, prompts)
    # the owner's data DID flow: through the tool server into the report evidence and the report prompt, and the memory canary through retrieval
    for key in ("occ", "goal"):
        assert CANARY[own][key] in surf["response"] and CANARY[own][key] in "\n".join(prompts["report"]), key
    assert CANARY[own]["mem"] in "\n".join(prompts["report"]) and MEM_ID[own] in body["retrieved_memory_ids"]
    # ... and nothing of anyone else's, anywhere: response, every prompt, working memory, approvals, trace
    assert leaks(surf, _foreign(sub)) == {}, leaks(surf, _foreign(sub))
    assert _working_clients(iso) == {own} and body["harness"]["outcome"] == "completed"
    assert {e["client_id"] for e in body["report"]["evidence"] if e["client_id"]} == {own}


def test_e2e_11_control_a_global_role_keeps_its_scope_and_sees_both_clients_only_when_asked_for_both(iso, spies):
    r, prompts = run_scripted(iso, spies, "SUP-1", "supervisor", "Why did Kenji's retirement age change?", calls=[call("get_client_profile", "CLIENT-001")])
    assert r.status_code == 200 and r.json()["client_ids"] == ["CLIENT-001"]
    surf = surfaces(iso, r.text, prompts)
    assert CANARY["CLIENT-001"]["occ"] in surf["response"]
    assert leaks({k: v for k, v in surf.items() if k != "prompts"}, dict(literal=[v for v in CANARY["CLIENT-002"].values()] + [REB_EX], words=[])) == {}       # scoped to what was ASKED
    both, p2 = run_scripted(iso, spies, "SUP-1", "supervisor", "Compare Kenji and Rebecca on retirement age.", calls=[call("get_client_profile", "CLIENT-001"), call("get_client_profile", "CLIENT-002")])
    assert both.status_code == 200 and sorted(both.json()["client_ids"]) == ["CLIENT-001", "CLIENT-002"]                       # a supervisor MAY ask about both
    assert CANARY["CLIENT-001"]["occ"] in both.text and CANARY["CLIENT-002"]["occ"] in both.text
    assert [e["status"] for e in both.json()["agent_run"]["tools"]["executions"]] == ["success", "success"]


# ---- 2. attacks that must be refused before anything runs
ATTACKS_11 = [
    ("a_asks_about_bs_client_by_name", "ADVISOR-001", "What did Rebecca decide about the employee-ownership route?"),
    ("b_asks_about_as_client_by_name", "ADVISOR-002", "Why did Kenji's retirement age change?"),
    ("a_attempts_to_pull_bs_memory", "ADVISOR-001", "What do the stored memories say about Rebecca's sale offer?"),
    ("general_question_containing_a_foreign_id", "ADVISOR-001", "What is a sensible retirement glide path? (my colleague mentioned CLIENT-002)"),
    ("mixed_authorized_and_unauthorized", "ADVISOR-001", "Compare Kenji and Rebecca on retirement age."),
    ("mixed_request_that_would_create_an_approval", "ADVISOR-001", "Should Kenji and Rebecca retire at 60?"),
    ("a_foreign_client_of_a_third_advisor", "ADVISOR-001", "How is the portfolio of CLIENT-003?"),
]


@pytest.mark.parametrize("attack", ATTACKS_11, ids=[a[0] for a in ATTACKS_11])
def test_e2e_11_cross_client_requests_are_refused_before_any_data_or_model_is_touched(iso, spies, attack):
    _, sub, question = attack
    before = snapshot(iso)
    r, prompts = run_scripted(iso, spies, sub, "advisor", question, calls=[call("get_client_profile", "CLIENT-002"), call("get_client_goals", "CLIENT-001")])
    assert r.status_code == 403 and r.json() == {"detail": GENERIC_SCOPE_DETAIL}, (r.status_code, r.text)
    assert set(r.json()) == {"detail"}
    # nothing ran: no model call, no agent, no tool, no memory read or write, no approval
    assert spies.counts["run_pipeline"] == 1 and {k: spies.counts[k] for k in DOWNSTREAM} == {k: 0 for k in DOWNSTREAM}
    assert spies.model_calls == [] and iso.llm.calls == [] and all(v == [] for v in prompts.values())
    after = snapshot(iso)
    assert (after["memory"], after["approvals"], after["ops"]) == (before["memory"], before["approvals"], before["ops"])
    assert _working_clients(iso) == set() and iso.q(iso.appr_db, "select count(*) from approval_requests") == [(0,)]
    # no client's data - the caller's own included - in the response or the protected trace (the trace may name the DENIED client id: that is the denial decision itself)
    surf = surfaces(iso, r.text, prompts)
    every_client = dict(literal=[v for c in CANARY.values() for v in c.values()] + [VERIFIED_TEXT, REB_EX], words=[])
    assert leaks(surf, every_client) == {}
    assert leaks({"response": surf["response"]}, dict(literal=[], words=["Kenji", "Rebecca", "CLIENT-001", "CLIENT-002", "CLIENT-003"])) == {}
    assert len(after["traces"]) == 1 and "denied" in surf["trace"]


# ---- 3. indirect retrieval WITHOUT naming a client: the request is legitimate, and it retrieves nothing
INDIRECT = [("by_memory_id", "ADVISOR-001", "Show me the stored record {mem_b} in full."),
            ("by_topic_words_of_bs_memories", "ADVISOR-001", "Which stored memories mention the employee-ownership alternative and the earn-out?"),
            ("asking_for_every_clients_data", "ADVISOR-001", "List every client's retirement age and occupation.")]


@pytest.mark.parametrize("probe", INDIRECT, ids=[p[0] for p in INDIRECT])
def test_e2e_11_indirect_retrieval_without_a_client_name_returns_no_client_data(iso, spies, probe):
    _, sub, question = probe
    r, prompts = run_scripted(iso, spies, sub, "advisor", question.format(mem_b=MEM_ID["CLIENT-002"]), calls=[])
    assert r.status_code == 200, r.text                                              # not a client reference, so not refused (the documented contract for a generic question)
    body = r.json()
    assert body["client_ids"] == [] and body["retrieved_memory_ids"] == [] and body["report"]["evidence"] == []
    surf = surfaces(iso, r.text, prompts)
    assert leaks(surf, _foreign(sub, ids_and_names=False)) == {}, leaks(surf, _foreign(sub, ids_and_names=False))
    assert leaks({k: v for k, v in surf.items() if k != "prompts"}, dict(literal=[v for v in CANARY["CLIENT-001"].values()] + [VERIFIED_TEXT], words=[])) == {}      # not even the caller's own
    assert _working_clients(iso) == set() and iso.q(iso.appr_db, "select count(*) from approval_requests") == [(0,)]


# ---- 4. a malicious Tool Selector: the indirect path to another client's operational data
TOOL_ATTACKS = [
    # (id, caller, own client, calls, expected statuses, MCP tool-server calls reaching the data layer)
    ("foreign_client_id", "ADVISOR-001", "CLIENT-001", [call("get_client_profile", "CLIENT-002")], ["rejected"], 0),
    ("foreign_client_id_lower_case", "ADVISOR-001", "CLIENT-001", [call("get_client_profile", "client-002")], ["rejected"], 0),
    ("foreign_client_id_padded", "ADVISOR-001", "CLIENT-001", [call("get_client_goals", " CLIENT-002 ")], ["rejected"], 0),
    ("foreign_client_id_reverse_direction", "ADVISOR-002", "CLIENT-002", [call("get_client_profile", "CLIENT-001")], ["rejected"], 0),
    ("own_id_with_the_others_meeting_turn", "ADVISOR-001", "CLIENT-001", [call("get_source_evidence", "CLIENT-001", interaction_id="INT-061", turn_index=11)], ["error"], 1),
    ("own_id_with_the_others_meeting_turn_reverse", "ADVISOR-002", "CLIENT-002", [call("get_source_evidence", "CLIENT-002", interaction_id="INT-090", turn_index=2)], ["error"], 1),
    ("own_id_with_the_others_goal_id", "ADVISOR-001", "CLIENT-001", [call("get_source_evidence", "CLIENT-001", entity_type="goal", entity_id="@FOREIGN_GOAL@")], ["success"], 1),
]


@pytest.mark.parametrize("attack", TOOL_ATTACKS, ids=[a[0] for a in TOOL_ATTACKS])
def test_e2e_11_a_tool_selector_cannot_pull_another_clients_operational_data(iso, spies, attack):
    name, sub, own, calls, statuses, mcp = attack
    other = "CLIENT-002" if own == "CLIENT-001" else "CLIENT-001"
    foreign_goal = iso.q(iso.ops_db, "select goal_id from financial_goals where client_id=? order by goal_id limit 1", (other,))[0][0]
    calls = [{**c, "arguments": {k: (foreign_goal if v == "@FOREIGN_GOAL@" else v) for k, v in c["arguments"].items()}} for c in calls]
    question = "Why did Kenji's retirement age change?" if own == "CLIENT-001" else "How is Rebecca's retirement age?"
    r, prompts = run_scripted(iso, spies, sub, "advisor", question, calls=calls)
    assert r.status_code == 200, r.text
    body = r.json()
    execs = body["agent_run"]["tools"]["executions"]
    assert body["client_ids"] == [own] and [e["status"] for e in execs] == statuses, execs
    assert spies.counts["tool_call_mcp"] == mcp, spies.counts                      # how far the call got: stopped by the selector's plan check, or by the tool service itself
    if statuses == ["rejected"]:
        assert "is not in the plan" in execs[0]["error"] and spies.counts["tool_call_guarded"] == 0
    elif statuses == ["error"]:
        assert f"for {own}" in execs[0]["error"]                                    # "no turn 11 of INT-061 for CLIENT-001"
    surf = surfaces(iso, r.text, prompts)
    foreign = _foreign(sub, ids_and_names=False)
    assert leaks(surf, foreign) == {}, leaks(surf, foreign)                          # no canary, no stored sentence of the other client anywhere
    assert FOREIGN_INTERACTION[own][2] not in "\n".join(surf.values())
    assert _working_clients(iso) == {own}                                            # working memory holds only the caller's client
    if name == "own_id_with_the_others_goal_id":                                      # the tool answered `success` - with nothing: the evidence rows are filtered by client
        item = [e for e in body["report"]["evidence"] if e["source_class"] == "operational_data"]
        assert len(item) == 1 and '"evidence": []' in item[0]["description"]


def test_e2e_11_the_own_client_canary_does_flow_through_the_same_tool_path(iso, spies):
    """Control for the tool attacks: the same tool with the caller's OWN client returns data (so 'nothing came back' above is the boundary, not a broken tool)."""
    r, _ = run_scripted(iso, spies, "ADVISOR-001", "advisor", "Why did Kenji's retirement age change?", calls=[call("get_source_evidence", "CLIENT-001", interaction_id="INT-090", turn_index=2)])
    execs = r.json()["agent_run"]["tools"]["executions"]
    assert [e["status"] for e in execs] == ["success"] and VERIFIED_TEXT in r.text and spies.counts["tool_call_mcp"] == 1


# ---- 5. the report: a model that talks about another client cannot get it into the answer
def test_e2e_11_a_report_naming_another_client_is_withheld_not_echoed(iso, spies):
    def rep(user):
        r = ref_of(user, ": " + VERIFIED_TEXT)                                       # the VERIFIED memory's line (the canary memory's sentence also ends in the same words, but unverified and not preceded by ': ')
        return report_json("Kenji decided on a retirement age of 63.", [
            dict(text="Kenji decided on a retirement age of 63.", refs=[r]),
            dict(text=f"Rebecca decided on a retirement age of 63 and {CANARY['CLIENT-002']['occ']}.", refs=[r]),          # a foreign client, quoting a foreign canary, on the caller's evidence
            dict(text=f"Rebecca said {CLIENT_TEXT['CLIENT-002'][:60]}", refs=["E99"])])                                    # ... and on evidence that does not exist
    r, prompts = run_scripted(iso, spies, "ADVISOR-001", "advisor", "Why did Kenji's retirement age change?", report=rep)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [f["text"] for f in body["report"]["key_findings"]] == ["Kenji decided on a retirement age of 63."]                 # the legitimate finding survives
    he = body["report"]["validation"]["harness_evidence"]
    assert he["counts"]["verified"] == 1 and he["counts"]["unsupported"] == 1 and "CLIENT_MISMATCH" in he["withheld"][0]["reason_codes"]
    assert any("text not shown" in u and "outside the authorised scope" in u for u in body["report"]["uncertainties"])
    # the withheld claim keeps its reason code but not what it was ABOUT: no sentence, no value, and (Phase 6.5) no `subject_client` - that field used to carry "CLIENT-002" for a statement
    # naming "Rebecca", which resolved a name to a client id for an advisor with no access to her
    claims = he["findings"][1]["claims"]
    assert len(claims) >= 2 and claims[0]["reasons"][0] == "CLIENT_MISMATCH"                  # one sentence -> several claims (63 and the canary number); only one carries the scope code ...
    assert all(c["claim"]["text"] is None and c["claim"]["value"] is None and c["claim"]["subject_client"] is None for c in claims)      # ... and NONE of them may carry the client
    # PUBLIC surfaces only: the finding's text was authored by the scripted MODEL (the system never supplied Rebecca's data to this request), and the protected trace records what the verifier
    # withheld, text included, by design
    surf = surfaces(iso, r.text, prompts)
    public = {k: v for k, v in surf.items() if k in ("response", "working_memory", "approvals")}
    assert leaks(public, _foreign("ADVISOR-001")) == {}, leaks(public, _foreign("ADVISOR-001"))                                   # neither the name, nor the id, nor the canary, nor the quotation
    assert leaks({"prompts": surf["prompts"]}, _foreign("ADVISOR-001")) == {}                                                       # ... and nothing of hers was ever IN a prompt (Phase 6.5 planner fix)
    assert {e["client_id"] for e in body["report"]["evidence"] if e["client_id"]} == {"CLIENT-001"}


# ---- 6. the model is told only about the clients the caller may use (defect found by this phase; see tests/test_e2e_api.py::test_e2e_11_boundary_planner_*)
def _known_line(prompt: str) -> str:
    return re.search(r"KNOWN CLIENTS: (.*)", prompt).group(1)


PLANNER_VIEW = [("advisor_a", "ADVISOR-001", "advisor", "Why did Kenji's retirement age change?", {"CLIENT-001", "CLIENT-004"}),
                ("advisor_b", "ADVISOR-002", "advisor", "How is Rebecca's retirement age?", {"CLIENT-002", "CLIENT-005"}),
                ("advisor_c", "ADVISOR-003", "advisor", "Why did CLIENT-003's retirement age change?", {"CLIENT-003", "CLIENT-006"})]


@pytest.mark.parametrize("case", PLANNER_VIEW, ids=[c[0] for c in PLANNER_VIEW])
def test_e2e_11_the_planner_prompt_names_only_the_callers_clients(iso, spies, case):
    _, sub, role, question, mine = case
    r, prompts = run_scripted(iso, spies, sub, role, question)
    assert r.status_code == 200, r.text
    d = _conversations().directory()
    listed = _known_line(prompts["planner"][0])
    assert set(re.findall(r"CLIENT-\d{3}", listed)) == mine                         # exactly the caller's clients ...
    for cid in set(d) - mine:                                                         # ... and no other client's id or first name, in ANY prompt a model received
        for text in ("\n".join(v) for v in prompts.values()):
            assert cid not in text and not re.search(rf"\b{re.escape(d[cid]['first_name'])}\b", text, re.I), (cid, d[cid]["first_name"])
    assert all(d[c]["first_name"] in listed for c in mine)


def test_e2e_11_control_a_global_role_is_told_about_every_client(iso, spies):
    r, prompts = run_scripted(iso, spies, "SUP-1", "supervisor", "Why did Kenji's retirement age change?")
    assert r.status_code == 200 and set(re.findall(r"CLIENT-\d{3}", _known_line(prompts["planner"][0]))) == set(_conversations().directory())


def test_e2e_11_boundary_planner_visible_clients_and_replan():
    from agents.planner.agent import Planner
    from schemas.tools import TOOL_NAMES
    seen: list = []

    def handler(system, user, schema):
        seen.append(user)
        return planner_out(tools=False)
    llm = MockLLM(handlers={"planner": handler})
    pl = Planner(llm, CONV)
    plan = pl.plan("Why did Kenji's retirement age change?", list(TOOL_NAMES), request_id="REQ-V1", visible_clients={"CLIENT-001"})
    assert _known_line(seen[-1]) == "CLIENT-001 = Kenji" and plan.client_ids == ["CLIENT-001"]                     # the plan itself is unchanged: clients come from the request text
    pl.plan("Why did Kenji's retirement age change?", list(TOOL_NAMES), request_id="REQ-V2")
    assert len(re.findall(r"CLIENT-\d{3} =", _known_line(seen[-1]))) == len(CONV.directory())                       # no caller known -> unrestricted, as before
    pl.plan("What is a glide path?", list(TOOL_NAMES), request_id="REQ-V3", visible_clients=set())
    assert _known_line(seen[-1]) == "none"
    pl.replan(plan, ["get_client_goals"], "tool server error", list(TOOL_NAMES), visible_clients={"CLIENT-001"})
    assert _known_line(seen[-1]) == "CLIENT-001 = Kenji"                                                                # a re-plan keeps the restriction


# ---- 7. approvals: created from one advisor's request, invisible to another advisor
def test_e2e_11_an_approval_holds_only_its_own_client_and_is_invisible_to_another_advisor(iso, spies):
    r, prompts = run_scripted(iso, spies, "ADVISOR-001", "advisor", "Should Kenji retire at 60?", calls=[call("get_client_profile", "CLIENT-001")])
    assert r.status_code == 200 and r.json()["released"] is False
    aid = r.json()["approval"]["approval_id"]
    rows = iso.q(iso.appr_db, "select client_id, requester_subject from approval_requests")
    assert rows == [("CLIENT-001", "ADVISOR-001")]
    surf = surfaces(iso, r.text, prompts)
    assert leaks(surf, _foreign("ADVISOR-001")) == {}, leaks(surf, _foreign("ADVISOR-001"))                              # the stored draft (approvals) holds nothing of CLIENT-002
    # another advisor: the approval answers exactly like an id that does not exist
    unknown = "APR-" + "0" * 32
    for method, path in (("get", f"/api/approvals/{aid}"), ("post", f"/api/approvals/{aid}/release")):
        a = getattr(iso.client, method)(path, headers=iso.headers("ADVISOR-002", "advisor"))
        b = getattr(iso.client, method)(path.replace(aid, unknown), headers=iso.headers("ADVISOR-002", "advisor"))
        assert (a.status_code, a.json()) == (b.status_code, b.json()) == (404, {"detail": "unknown approval"})
    # a supervisor (a reviewer) sees metadata, never the report content
    listing = iso.client.get("/api/approvals", headers=iso.headers("SUP-1", "supervisor"))
    assert listing.status_code == 200 and [x["client_ids"] for x in listing.json()] == [["CLIENT-001"]]
    assert leaks({"listing": listing.text}, dict(literal=[v for v in CANARY["CLIENT-001"].values()] + [VERIFIED_TEXT], words=[])) == {}


# ---- 8. the read routes of the same API
def test_e2e_11_read_routes_show_an_advisor_only_their_own_clients_and_answer_like_missing_for_the_rest(iso):
    h = iso.headers("ADVISOR-001", "advisor")
    listed = iso.client.get("/api/clients", headers=h)
    assert listed.status_code == 200 and {c["client_id"] for c in listed.json()} == {"CLIENT-001", "CLIENT-004"}
    assert leaks({"list": listed.text}, dict(literal=[], words=["Rebecca", "CLIENT-002", "CLIENT-005", "CLIENT-003", "CLIENT-006"])) == {}
    # brain and memory of a client / memory the caller may not use == a client / memory that does not exist, byte for byte
    a, b = iso.client.get("/api/clients/CLIENT-002/brain", headers=h), iso.client.get("/api/clients/CLIENT-999/brain", headers=h)
    assert (a.status_code, a.text) == (b.status_code, b.text) == (404, '{"detail":"unknown client"}')
    a, b = iso.client.get(f"/api/memories/{MEM_ID['CLIENT-002']}", headers=h), iso.client.get("/api/memories/MEM-NOPE", headers=h)
    assert (a.status_code, a.text) == (b.status_code, b.text) == (404, '{"detail":"unknown memory"}')
    # the caller's own brain: only their client's memories (plus the global curated procedures), no canary of anyone else
    own = iso.client.get("/api/clients/CLIENT-001/brain", headers=h)
    assert own.status_code == 200
    ids = {m["memory_id"] for m in own.json()["memories"]}
    owners = {r[0] for r in iso.q(iso.mem_db, f"select client_id from memory_records where memory_id in ({','.join('?' * len(ids))})", tuple(ids))}
    assert owners <= {"CLIENT-001", "GLOBAL"} and MEM_ID["CLIENT-001"] in ids and MEM_ID["CLIENT-002"] not in ids
    assert leaks({"brain": own.text}, dict(literal=[v for v in CANARY["CLIENT-002"].values()] + [REB_EX], words=[])) == {}


def test_e2e_11_boundary_a_withheld_finding_about_an_out_of_scope_client_carries_no_client_id():
    """`subject_client` is blanked for the WHOLE finding when any of its claims is refused for scope; a finding refused for another reason keeps it (it is the caller's own client)."""
    from harness.evidence_verifier import _redact_claims
    mk = lambda reasons, subject="CLIENT-002": {"claim": {"claim_id": "C", "text": "t", "value": "9", "subject_client": subject, "period": {"kind": "UNSPECIFIED", "dates": []}},          # noqa: E731
                                                "outcome": "UNVERIFIED", "reasons": reasons, "detail": "x"}
    scope = _redact_claims([mk(["VALUE_MISMATCH"]), mk(["CLIENT_MISMATCH"]), mk(["UNIT_MISMATCH"])])               # the scope code is on the middle claim only
    assert [c["claim"]["subject_client"] for c in scope] == [None, None, None] and [c["reasons"] for c in scope] == [["VALUE_MISMATCH"], ["CLIENT_MISMATCH"], ["UNIT_MISMATCH"]]
    inaccessible = _redact_claims([mk(["EVIDENCE_INACCESSIBLE"])])
    assert inaccessible[0]["claim"]["subject_client"] is None
    own = _redact_claims([mk(["VALUE_MISMATCH"], "CLIENT-001"), mk(["UNIT_MISMATCH"], "CLIENT-001")])
    assert [c["claim"]["subject_client"] for c in own] == ["CLIENT-001", "CLIENT-001"]                           # not a scope refusal: unchanged
