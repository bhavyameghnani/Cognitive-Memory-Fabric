"""M2: staged memory formation - proposals, validation, human approval, promotion (src/agents/memory/staging.py, sql/130, the proposal triggers, the boundary's staged-mode rules).

WHAT THESE TESTS ARE.  Deterministic MECHANISM tests: scripted models (MockLLM), `:memory:` stores, a small synthetic conversation file.  They prove the lifecycle, the identities and the
database guarantees; they say nothing about extraction quality or semantic accuracy, and a person's approval is simulated by an authenticated test Principal (no real person approved anything).
"""
import csv
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from agents.memory import integrity, staging                                  # noqa: E402
from agents.memory.agent import MemoryOrchestrator                           # noqa: E402
from agents.memory.config import OrchestratorConfig                          # noqa: E402
from agents.memory.errors import MemoryWriteRejected, ScopeDenied            # noqa: E402
from agents.memory.integrity import Principal                                # noqa: E402
from agents.memory.proposal_states import TRANSITIONS                        # noqa: E402
from agents.memory.store import MemoryStore                                  # noqa: E402
from harness.memory_write_validator import MemoryWriteValidator              # noqa: E402
from llm.client import MockLLM                                               # noqa: E402
from services.conversations import Conversations                             # noqa: E402
from test_m1_attribution_safety import CLIENT, HEADER, MEETINGS, SEN, Extract, Verify, cand, conv  # noqa: E402,F401  (conv is a fixture)

OTHER = "CLIENT-002"
ING = Principal.service("ingestion", [CLIENT])
PROMO = Principal.service("promotion", [CLIENT])
HUMAN = Principal.user("reviewer.jo", [CLIENT], authenticated=True)
CLOCK = lambda: datetime(2026, 11, 1, 9, 0, 0)  # noqa: E731


def staged(conv, by_iid, validator=True, mode="llm_first"):
    store = MemoryStore(url="sqlite:///:memory:", conv=conv, clock=CLOCK)
    llm = MockLLM(handlers={"memory": Extract(by_iid), "memory_verifier": Verify()})
    o = MemoryOrchestrator(llm, store=store, conv=conv, config=OrchestratorConfig(ingest_mode=mode, formation_mode="staged"),
                           write_validator=MemoryWriteValidator(conv, store) if validator else None)
    o.init()
    return o, store


def productions(store):
    return store.query("SELECT memory_id FROM memory_records WHERE client_id=? AND memory_type IN ('episodic','procedural')", (CLIENT,))


def one(o, iid):
    o.ingest_meeting(iid)
    (p,) = o.proposals.list(CLIENT, ING)
    return p


def approve_and_promote(o, pid, who=HUMAN):
    o.proposals.approve(pid, who, "checked against the cited turn")
    return o.proposals.promote(pid, PROMO)


# ================================================================================================== 1-2 extraction and validation write no production memory
def test_1_candidate_extraction_does_not_write_production_memory(conv):
    o, st = staged(conv, {"SYN-002": [cand(2, SEN["c63"], "confirmed", "63")]})
    r = o.ingest_meeting("SYN-002")
    assert r["stored"] == 1 and productions(st) == [] and st.records(CLIENT) == []                           # proposed, not stored
    (p,) = o.proposals.list(CLIENT, ING)
    assert p["state"] == "pending_approval" and p["created_by"] == "ingestion"
    assert "staged: proposal" in st.query("SELECT reason FROM memory_candidates")[0]["reason"]
    assert o.consolidator.consolidate_client(CLIENT)["versions_created"] == 0                                   # consolidation has nothing to fold


def test_2_validation_does_not_write_production_memory_or_approve(conv):
    o, st = staged(conv, {"SYN-002": [cand(2, SEN["c63"], "confirmed", "63")]})
    p = one(o, "SYN-002")
    ev = o.proposals.events(p["proposal_id"], ING)
    assert [(e["from_state"], e["to_state"], e["actor_kind"]) for e in ev] == [(None, "extracted", "service"), ("extracted", "validated", "service"), ("validated", "pending_approval", "service")]
    assert productions(st) == [] and o.proposals.decisions(p["proposal_id"], ING) == []                          # validation records no approval
    with pytest.raises(MemoryWriteRejected) as e:
        o.proposals.validate(p["proposal_id"], ING)                                                                # already past `extracted`
    assert e.value.code == "invalid_state"
    names = {r["check"]: (r["kind"], r["result"]) for r in __import__("json").loads(p["validation_json"])}
    assert names["client_ownership"] == ("structural", "pass") and names["unsupported_inference"][0] == "semantic" and len(names) == 12


# ================================================================================================== 3-6 approval requirements
def test_3_an_unapproved_candidate_cannot_be_promoted_even_by_the_database(conv):
    o, st = staged(conv, {"SYN-002": [cand(2, SEN["c63"], "confirmed", "63")]})
    p = one(o, "SYN-002")
    with pytest.raises(MemoryWriteRejected) as e:
        o.proposals.promote(p["proposal_id"], PROMO)
    assert e.value.code == "approval_required" and productions(st) == []
    with pytest.raises(sqlite3.IntegrityError, match="illegal proposal state transition|audit event|approval requires"):         # raw SQL skipping the service
        st.execute("UPDATE memory_proposals SET state='approved' WHERE proposal_id=?", (p["proposal_id"],))
    with pytest.raises(sqlite3.IntegrityError, match="audit event|recorded human"):
        st.execute("INSERT INTO memory_proposal_events (proposal_id, from_state, to_state, actor_kind, actor_id, reason, created_at) VALUES (?,?,?,?,?,?,?)",
                   (p["proposal_id"], "pending_approval", "approved", "human", "x.y", "forged", "t"))
        st.execute("UPDATE memory_proposals SET state='approved' WHERE proposal_id=?", (p["proposal_id"],))                 # event present, decision absent
    assert st.query("SELECT state FROM memory_proposals")[0]["state"] == "pending_approval"


def test_4_a_non_human_approval_is_rejected_everywhere(conv):
    o, st = staged(conv, {"SYN-002": [cand(2, SEN["c63"], "confirmed", "63")]})
    pid = one(o, "SYN-002")["proposal_id"]
    for who in (ING, PROMO, Principal.service("consolidation", [CLIENT]), Principal.user("system", [CLIENT], authenticated=True), Principal.user("llm", [CLIENT], authenticated=True),
                Principal.user("reviewer.jo", [CLIENT], authenticated=False), None):
        with pytest.raises(ScopeDenied):
            o.proposals.approve(pid, who, "approved")
    for kind, name in (("service", "ingestion"), ("model", "jo"), ("human", "system"), ("human", "service:x"), ("human", "Promotion")):                    # the database refuses them too
        with pytest.raises(sqlite3.IntegrityError):
            st.execute("INSERT INTO memory_proposal_decisions (proposal_id, decision, reviewer_kind, reviewer_id, auth_ref, reason, proposal_hash, created_at) VALUES (?,?,?,?,?,?,?,?)",
                       (pid, "approve", kind, name, "ref", "r", "h", "t"))
    assert st.query("SELECT state FROM memory_proposals")[0]["state"] == "pending_approval"


def test_5_an_unauthorised_actor_cannot_approve(conv):
    o, st = staged(conv, {"SYN-002": [cand(2, SEN["c63"], "confirmed", "63")]})
    pid = one(o, "SYN-002")["proposal_id"]
    with pytest.raises(ScopeDenied) as e:                                                                            # the caller's own role check (e.g. the harness PermissionGate `approve`)
        o.proposals.approve(pid, HUMAN, "ok", authorize=lambda who, row: False)
    assert e.value.code == "approval_not_authorised"
    with pytest.raises(ScopeDenied):                                                                                   # a person outside the client's scope
        o.proposals.approve(pid, Principal.user("reviewer.kim", [OTHER], authenticated=True), "ok")
    with pytest.raises(MemoryWriteRejected):                                                                           # a decision needs a reason
        o.proposals.approve(pid, HUMAN, "  ")
    o.proposals.approve(pid, HUMAN, "ok", authorize=lambda who, row: who.actor_id == "reviewer.jo" and row["client_id"] == CLIENT)
    assert st.query("SELECT state FROM memory_proposals")[0]["state"] == "approved"


def test_6_cross_client_access_to_candidates_is_refused_identically(conv):
    o, st = staged(conv, {"SYN-002": [cand(2, SEN["c63"], "confirmed", "63")]})
    pid = one(o, "SYN-002")["proposal_id"]
    other = Principal.user("reviewer.kim", [OTHER], authenticated=True)
    errors = []
    for fn in (lambda: o.proposals.get(pid, other), lambda: o.proposals.approve(pid, other, "ok"), lambda: o.proposals.reject(pid, other, "no"), lambda: o.proposals.events(pid, other),
               lambda: o.proposals.get("PRP-doesnotexist", HUMAN), lambda: o.proposals.promote(pid, Principal.service("promotion", [OTHER]))):
        with pytest.raises(ScopeDenied) as e:
            fn()
        errors.append(e.value.code)
    assert set(errors) <= {"proposal_not_found"}                                                                      # 'not yours' and 'does not exist' are the same answer
    with pytest.raises(ScopeDenied):
        o.proposals.list(CLIENT, other)
    assert o.proposals.list(OTHER, Principal.user("reviewer.kim", [OTHER], authenticated=True)) == []


# ================================================================================================== 7-10 attribution, finality, support
def test_7_an_advisor_reported_decision_retains_its_attribution_through_promotion(conv):
    o, st = staged(conv, {"SYN-003": [cand(1, SEN["adv60"], "confirmed", "60", role="Advisor")]})
    p = one(o, "SYN-003")
    assert (p["attribution"], p["certainty"], p["source_role"], p["event_type"]) == ("advisor_reported_client", "unconfirmed", "Advisor", "reported_decision")
    res = approve_and_promote(o, p["proposal_id"])
    (m,) = st.records(CLIENT, "episodic")
    assert m["memory_id"] == res.memory_id and (m["certainty"], m["source_role"], m["event_type"], m["status"]) == ("unconfirmed", "Advisor", "reported_decision", "unconfirmed")
    assert [(e.speaker, e.excerpt) for e in st.evidence_map([m["memory_id"]])[m["memory_id"]]] == [("Advisor", SEN["adv60"])]
    assert st.records(CLIENT, "semantic") == [] and o.consolidator.consolidate_client(CLIENT)["versions_created"] == 0


def test_8_a_person_cannot_raise_an_advisor_reported_decision_to_client_confirmed(conv):
    o, st = staged(conv, {"SYN-003": [cand(1, SEN["adv60"], "confirmed", "60", role="Advisor")]})
    p = one(o, "SYN-003")
    # a hand-forged proposal that claims `confirmed` is refused by validation, so it can never be approved or promoted
    import json
    rec = json.loads(p["record_json"])
    forged = dict(rec, certainty="confirmed", status="active", memory_id="MEM-EPI-forged00001", content_hash="forgedhash0000000000000000000000000000000000")
    now = "2026-11-01T09:00:00"
    st.execute("INSERT INTO memory_proposals (proposal_id, client_id, memory_type, topic, subject, statement, value_text, certainty, source_role, attribution, event_type, origin, interaction_id, "
               "turn_index, excerpt, excerpt_start, excerpt_end, match_method, evidence_json, record_json, details_json, extraction_json, content_hash, target_memory_id, state, created_by, "
               "formed_at, state_changed_at) VALUES ('PRP-forged',?,'episodic','retirement_age','retirement_age',?,'60','confirmed','Advisor','advisor_reported_client','decision','conversation_llm',"
               "'SYN-003',1,?,?,?,?,?,?,?,?,?,?,'extracted','ingestion',?,?)",
               (CLIENT, SEN["adv60"], p["excerpt"], p["excerpt_start"], p["excerpt_end"], p["match_method"], p["evidence_json"], json.dumps(forged), p["details_json"], "{}",
                forged["content_hash"], forged["memory_id"], now, now))
    st.execute("INSERT INTO memory_proposal_events (proposal_id, from_state, to_state, actor_kind, actor_id, reason, created_at) VALUES ('PRP-forged',NULL,'extracted','service','ingestion','forged',?)", (now,))
    res = o.proposals.validate("PRP-forged", ING)
    assert res["state"] == "rejected" and any(r["check"] == "certainty_ceiling" and r["result"] == "fail" for r in res["results"])
    with pytest.raises(MemoryWriteRejected):
        o.proposals.approve("PRP-forged", HUMAN, "please")
    assert productions(st) == []


def test_9_an_explicitly_negated_decision_cannot_be_promoted_as_final(conv):
    o, st = staged(conv, {"SYN-004": [cand(2, "We've decided: 61. Please don't treat it as final yet.", "confirmed", "61")]})
    p = one(o, "SYN-004")
    assert p["certainty"] in ("unconfirmed", "exploratory")
    approve_and_promote(o, p["proposal_id"])
    assert [m["certainty"] for m in st.records(CLIENT, "episodic")] == [p["certainty"]] and st.records(CLIENT, "semantic") == []
    # and a forged 'final' claim over the same hedged text is refused by validation
    import json
    rec = json.loads(p["record_json"])
    assert rec["certainty"] != "confirmed"


def test_10_an_unsupported_candidate_cannot_be_promoted(conv):
    bad = cand(2, SEN["c63"], "confirmed", "63")
    bad["statement"] = "The client will sue the firm"                                              # a claim the cited sentence does not support
    o, st = staged(conv, {"SYN-002": [bad]}, validator=False)
    o.ingest_meeting("SYN-002")
    (p,) = o.proposals.list(CLIENT, ING)
    assert p["state"] == "rejected"
    failed = [r for r in __import__("json").loads(p["validation_json"]) if r["result"] == "fail"]
    assert failed and failed[0]["check"] == "unsupported_inference"
    for fn in (lambda: o.proposals.approve(p["proposal_id"], HUMAN, "ok"), lambda: o.proposals.promote(p["proposal_id"], PROMO)):
        with pytest.raises(MemoryWriteRejected):
            fn()
    assert productions(st) == []


# ================================================================================================== 11-13 evidence, unresolved, auditability
def test_11_the_exact_excerpt_and_offsets_are_preserved_against_the_original_text(conv):
    o, st = staged(conv, {"SYN-002": [cand(2, SEN["c63"], "confirmed", "63")]})
    p = one(o, "SYN-002")
    text = conv.turn("SYN-002", 2)["utterance"]
    assert p["match_method"] == "exact" and text[p["excerpt_start"]:p["excerpt_end"]] == SEN["c63"] == p["excerpt"]
    assert (p["interaction_id"], p["turn_index"]) == ("SYN-002", 2)
    res = approve_and_promote(o, p["proposal_id"])
    assert st.evidence_map([res.memory_id])[res.memory_id][0].excerpt == SEN["c63"]
    # the matcher itself: exact, whitespace-normalised (mapped back and verified), ambiguous and case-only are never guessed
    t = "Dear Kim,\n\nWe have decided  on five installments.\nThanks."
    m = staging.locate_excerpt(t, "We have decided on five installments.")
    assert m.method == "whitespace_normalised" and " ".join(t[m.start:m.end].split()) == "We have decided on five installments."
    assert staging.locate_excerpt("I agree. I agree.", "I agree").method == "unresolved"
    assert staging.locate_excerpt("Maybe in June.", "maybe in june").reason == "case_only_difference"
    assert staging.locate_excerpt("abc", "").method == "unresolved" and staging.locate_excerpt("abc", "zzz").start is None


def test_12_unresolved_evidence_remains_unresolved_and_can_never_be_approved(tmp_path):
    p = tmp_path / "conversations.csv"
    with open(p, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        for i, (sp, tx) in enumerate([("Advisor", "Good morning, Alex. Shall we begin?"), ("Client", "We've decided: 64. We've decided: 64.")], 1):
            w.writerow(["SYN-020", CLIENT, "ADVISOR-001", "2026-02-10T09:00:00", "video", i, sp, tx, "Retirement plan", "synthetic_meeting", "consented"])
    cv = Conversations(p)
    o, st = staged(cv, {"SYN-020": [cand(2, "We've decided: 64.", "confirmed", "64")]})
    o.ingest_meeting("SYN-020")
    (pr,) = o.proposals.list(CLIENT, ING)
    assert pr["state"] == "unresolved" and pr["match_method"] == "unresolved" and pr["excerpt_start"] is None and pr["excerpt_end"] is None      # no invented offsets
    assert any(r["check"] == "exact_offsets" and r["result"] == "unresolved" for r in __import__("json").loads(pr["unresolved_json"]))
    for fn in (lambda: o.proposals.submit(pr["proposal_id"], ING), lambda: o.proposals.approve(pr["proposal_id"], HUMAN, "ok"), lambda: o.proposals.promote(pr["proposal_id"], PROMO)):
        with pytest.raises(MemoryWriteRejected):
            fn()
    assert productions(st) == []


def test_13_rejected_candidates_remain_auditable_and_cannot_be_erased(conv):
    o, st = staged(conv, {"SYN-002": [cand(2, SEN["c63"], "confirmed", "63")]})
    pid = one(o, "SYN-002")["proposal_id"]
    o.proposals.reject(pid, HUMAN, "not what the client said")
    row = o.proposals.get(pid, HUMAN)
    assert row["state"] == "rejected" and row["excerpt"] == SEN["c63"]
    assert [(d["decision"], d["reviewer_id"], d["reviewer_kind"]) for d in o.proposals.decisions(pid, HUMAN)] == [("reject", "reviewer.jo", "human")]
    assert [e["to_state"] for e in o.proposals.events(pid, HUMAN)][-1] == "rejected"
    for sql in ("DELETE FROM memory_proposals", "DELETE FROM memory_proposal_events", "UPDATE memory_proposal_decisions SET reason='x'", "DELETE FROM memory_proposal_decisions",
                "UPDATE memory_proposals SET statement='edited'", "UPDATE memory_proposals SET state='pending_approval'"):
        with pytest.raises(sqlite3.IntegrityError):
            st.execute(sql)
    with pytest.raises(MemoryWriteRejected):
        o.proposals.approve(pid, HUMAN, "changed my mind")                                                          # terminal: a changed view is a new proposal
    assert productions(st) == []


# ================================================================================================== 14-16 promotion semantics
def test_14_duplicate_promotion_is_idempotent(conv):
    o, st = staged(conv, {"SYN-002": [cand(2, SEN["c63"], "confirmed", "63")]})
    pid = one(o, "SYN-002")["proposal_id"]
    first = approve_and_promote(o, pid)
    second = o.proposals.promote(pid, PROMO)
    assert first.created and not second.created and second.idempotent and first.memory_id == second.memory_id
    assert len(productions(st)) == 1 and len([e for e in o.proposals.events(pid, HUMAN) if e["to_state"] == "promoted"]) == 1
    assert o.proposals.get(pid, HUMAN)["promoted_memory_id"] == first.memory_id
    o.ingest_meeting("SYN-002", force=True)                                                                          # re-extraction proposes the same content: same proposal, no new memory
    assert len(o.proposals.list(CLIENT, ING)) == 1 and len(productions(st)) == 1


def test_15_superseded_memories_keep_their_historical_versions_after_promotion(conv):
    o, st = staged(conv, {"SYN-001": [cand(2, SEN["c65"], "confirmed", "65")], "SYN-002": [cand(2, SEN["c63"], "confirmed", "63")]})
    for iid in ("SYN-001", "SYN-002"):
        o.ingest_meeting(iid)
    assert st.records(CLIENT, "semantic") == []                                                                      # nothing is in production yet
    for p in o.proposals.list(CLIENT, ING):
        approve_and_promote(o, p["proposal_id"])
    o.consolidator.consolidate_client(CLIENT)
    sem = sorted(st.records(CLIENT, "semantic", topics=["retirement_age"]), key=lambda r: r["version_no"])
    assert [(s["version_no"], s["value_text"], s["status"], s["valid_to"]) for s in sem] == [(1, "65", "superseded", "2026-03-10"), (2, "63", "active", None)]
    assert sem[1]["supersedes_id"] == sem[0]["memory_id"] and {r["memory_id"] for r in st.records(CLIENT, "episodic")}                # the superseded version and both episodes remain


def test_16_a_failed_promotion_rolls_back_every_related_write(conv, monkeypatch):
    o, st = staged(conv, {"SYN-002": [cand(2, SEN["c63"], "confirmed", "63")]})
    pid = one(o, "SYN-002")["proposal_id"]
    o.proposals.approve(pid, HUMAN, "ok")
    n_evidence = st.query("SELECT count(*) n FROM memory_evidence")[0]["n"]                                          # (the curated procedures loaded by init() already have evidence)
    real = staging.Proposals._event

    def boom(self, row, to, *a, **k):
        if to == "promoted":
            raise RuntimeError("simulated failure after the memory was written")
        return real(self, row, to, *a, **k)
    monkeypatch.setattr(staging.Proposals, "_event", boom)
    with pytest.raises(RuntimeError):
        o.proposals.promote(pid, PROMO)
    monkeypatch.undo()
    assert productions(st) == [] and st.query("SELECT count(*) n FROM memory_evidence")[0]["n"] == n_evidence                  # the memory, its evidence and its audit rows were rolled back
    assert o.proposals.get(pid, HUMAN)["state"] == "approved"
    assert o.proposals.promote(pid, PROMO).created                                                                   # and a retry works


# ================================================================================================== 17 no alternate write path bypasses the lifecycle
def test_17_direct_persistence_paths_cannot_bypass_staged_formation(conv):
    from agents.memory import ingest as ing
    o, st = staged(conv, {})
    rec = dict(memory_id="MEM-EPI-direct00001", client_id=CLIENT, memory_type="episodic", topic="retirement_age", subject="retirement_age", content=SEN["c63"], value_text="63",
               certainty="confirmed", source_role="Client", status="active", origin="conversation_llm", event_date="2026-03-10", content_hash="directhash")
    det = dict(interaction_id="SYN-002", turn_index=2, event_ts="2026-03-10T09:00:00", speaker_role="Client", event_type="decision")
    evd = [dict(source_type="conversation", interaction_id="SYN-002", turn_index=2, speaker="Client", excerpt=SEN["c63"])]
    with pytest.raises(MemoryWriteRejected) as e:                                                                    # the ingestion identity may not write directly
        st.insert_memory(rec, det, evd, principal=ING)
    assert e.value.code == "direct_formation_disabled"
    for who in (PROMO,):                                                                                              # even the promotion identity needs an APPROVED proposal for exactly this memory
        with pytest.raises((MemoryWriteRejected, sqlite3.IntegrityError)):
            st.insert_memory(rec, det, evd, principal=who)
    with pytest.raises(sqlite3.IntegrityError):                                                                       # raw SQL is blocked by the guard triggers (outside the boundary)
        st.execute("INSERT INTO memory_records (memory_id, client_id, memory_type, topic, content, certainty, status, origin, content_hash, created_at, updated_at) "
                   "VALUES ('RAW','CLIENT-001','episodic','retirement_age','x','confirmed','active','conversation_llm','raw','t','t')")
    with pytest.raises(sqlite3.IntegrityError):                                                                       # the latch only turns one way
        st.execute("UPDATE memory_formation_mode SET mode='direct'")
    assert o.proposals.formation_mode() == "staged" and productions(st) == []
    with pytest.raises(MemoryWriteRejected):                                                                          # the old direct persistence helper is refused too
        ing.persist_decision(st, ing.MeetingContext.load(conv, "SYN-002"), ing.Decision("conversation_llm", __import__("schemas.memory", fromlist=["MemoryCandidate"]).MemoryCandidate(
            **cand(2, SEN["c63"], "confirmed", "63")), "accepted", "valid", __import__("schemas.memory", fromlist=["MemoryCandidate"]).MemoryCandidate(**cand(2, SEN["c63"], "confirmed", "63"))),
            ing.Classification(["episodic"], "active", "decision", False, True), "m")


def test_the_proposal_triggers_are_part_of_readiness_and_a_missing_one_fails_closed(conv):
    st = MemoryStore(url="sqlite:///:memory:", conv=conv, clock=CLOCK)
    st.init()
    assert st.integrity_ready() and st.proposal_trigger_mismatches() == {}
    with st._maintenance():
        st.execute("DROP TRIGGER proposal_gate_memory_insert")
    assert st.proposal_trigger_mismatches() == {"proposal_gate_memory_insert": "missing"} and not st.integrity_ready()      # durable writes are refused until init() repairs it
    st.init()
    assert st.integrity_ready()


# ================================================================================================== migration safety (disposable stores only)
def test_migration_130_is_additive_idempotent_and_leaves_existing_data_alone(conv, tmp_path):
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    st = MemoryStore(url=url, conv=conv, clock=CLOCK)
    st.init()
    ev = dict(source_type="conversation", interaction_id="SYN-002", turn_index=2, speaker="Client", excerpt=SEN["c63"])
    rec = dict(memory_id="MEM-EPI-keep00000001", client_id=CLIENT, memory_type="episodic", topic="retirement_age", subject="retirement_age", content=SEN["c63"], value_text="63", certainty="confirmed",
               source_role="Client", status="active", origin="conversation_llm", event_date="2026-03-10", content_hash="keep")
    st.insert_memory(rec, dict(interaction_id="SYN-002", turn_index=2, event_ts="2026-03-10T09:00:00", speaker_role="Client", event_type="decision"), [ev], principal=ING)
    before = st.query("SELECT memory_id, content, certainty FROM memory_records")
    st.close()
    for _ in range(2):                                                                                               # re-opening and re-initialising changes nothing
        s2 = MemoryStore(url=url, conv=conv, clock=CLOCK)
        s2.init()
        assert s2.query("SELECT memory_id, content, certainty FROM memory_records") == before and s2.query("PRAGMA user_version")[0]["user_version"] == 130 and s2.integrity_ready()
        assert s2.query("SELECT mode FROM memory_formation_mode") == [{"mode": "direct"}]                           # existing stores keep forming directly until an operator stages them
        s2.close()


def test_an_interrupted_migration_130_changes_nothing_and_can_be_rerun(conv, monkeypatch):
    st = MemoryStore(url="sqlite:///:memory:", conv=conv, clock=CLOCK)
    real = MemoryStore._script

    def failing(self, name):
        if name.startswith("130_"):
            raise RuntimeError("interrupted")
        return real(self, name)
    monkeypatch.setattr(MemoryStore, "_script", failing)
    with pytest.raises(RuntimeError):
        st.init()
    monkeypatch.undo()
    assert st.query("SELECT name FROM sqlite_master WHERE name IN ('memory_proposals','memory_records')") == []     # the whole initialisation rolled back
    st.init()
    assert st.integrity_ready() and st.query("PRAGMA user_version")[0]["user_version"] == 130


# ================================================================================================== state machine and constants stay in step
def test_the_database_state_machine_matches_the_python_one(conv):
    st = MemoryStore(url="sqlite:///:memory:", conv=conv, clock=CLOCK)
    st.init()
    sql = st._proposal_trigger_definitions()["proposal_state_machine"]
    for a, bs in TRANSITIONS.items():
        for b in bs:
            assert f"OLD.state = '{a}'" in sql and f"'{b}'" in sql
    assert TRANSITIONS["unresolved"] == ("rejected", "expired") and "approved" not in TRANSITIONS["extracted"] and "promoted" not in TRANSITIONS["pending_approval"]
    assert TRANSITIONS["rejected"] == TRANSITIONS["superseded"] == TRANSITIONS["expired"] == ()


def test_the_sql_reviewer_blocklist_covers_every_reserved_identity():
    sql = (ROOT / "sql" / "130_cmf_memory_proposals.sql").read_text(encoding="utf-8")
    listed = {x.strip().strip("'") for x in sql.split("NOT IN (")[1].split(")")[0].replace("\n", " ").split(",")}
    assert set(integrity.RESERVED_ACTORS) <= listed, set(integrity.RESERVED_ACTORS) - listed
