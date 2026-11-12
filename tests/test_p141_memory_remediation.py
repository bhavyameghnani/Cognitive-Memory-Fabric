"""P1.4.1 - memory integrity remediation (findings H1-H4, M1-M6, L2/L3/L4/L7 of the independent P1.4 audit).
Deterministic: real transcript turns from data/conversations.csv, in-memory or temp-file SQLite, no network, no live LLM.  Every destructive test uses a temporary database."""
import dataclasses
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from agents.memory.errors import EvidenceRejected, MemoryResetRefused, MemoryWriteRejected, ScopeDenied  # noqa: E402
from agents.memory.integrity import PROCEDURES_FILE, Principal, _h, classify_temporal  # noqa: E402
from agents.memory.store import MemoryStore  # noqa: E402
from test_memory_support import CONV, K090_T2, KENJI, KENJI_MEETINGS, KENJI_SCRIPT, REBECCA, make  # noqa: E402
from test_p0_security import bearer, first_episode, world  # noqa: E402,F401  (the real-app fixture)
from test_p14_memory_integrity import (EDET, KEV, KEV2, KI, KP, RDET, REV, RI, RP, TURNS, at, erec, put, rig, row)  # noqa: E402

CP = Principal.service("consolidation", [KENJI], request_id="REQ-141")
SDET = dict(slot_key="retirement_age|retirement_age", basis="test", supporting_episodes=1)
NEWER = ("After the integration role, you considered 63 and in May, with Mariko, decided on 63.")        # INT-149 turn 3, 2026-06-30: a real, LATER Kenji turn
NEWER_EV = dict(source_type="conversation", interaction_id="INT-149", turn_index=3, speaker="Advisor", excerpt=NEWER)


def srec(mid, value="63", **kw):
    base = dict(memory_id=mid, client_id=KENJI, memory_type="semantic", topic="retirement_age", subject="retirement_age", title=f"retirement_age: {value}",
                content=f"retirement age = {value} (valid from 2026-05-01)", value_text=value, certainty="confirmed", source_role="System", status="active", confidence=0.9,
                event_date="2026-05-01", valid_from="2026-05-01", version_no=1, supersedes_id=None, origin="consolidation", content_hash=mid)
    base.update(kw)
    return base


def active_semantic(store, client=KENJI):
    return [r for r in store.records(client, "semantic", topics=["retirement_age"]) if r["status"] == "active"]


def counts(store):
    return {t: store.count(t) for t in ("memory_records", "memory_evidence", "memory_relationships", "memory_lifecycle_events", "memory_conflicts")}


# ===================================================================================================== H1: human corrections survive consolidation
def _kenji_world():
    orch, store, _, _ = make(KENJI_SCRIPT)
    for i in KENJI_MEETINGS:
        orch.ingest_meeting(i)
    return orch, store


LATER_65 = "In January you told me you'd retire at 65."          # INT-149 turn 3 (2026-06-30): a real, LATER Kenji turn that states 65


def _corrected_world():
    """The original audit scenario (P9): ingest + consolidate, a person corrects the active semantic value, consolidation runs again.  The audit corrected to 70; no turn of this
    transcript states 70, so the correction here is 65 -> 63 (INT-090 turn 11, 2026-05-01: 'You've decided to retire at 63'), made BEFORE that meeting is ingested."""
    orch, store, _, _ = make(KENJI_SCRIPT)
    for i in KENJI_MEETINGS[:4]:
        orch.ingest_meeting(i)
    cur = active_semantic(store)
    assert [(r["value_text"], r["valid_from"]) for r in cur] == [("65", "2026-01-02")]
    res = store.boundary.correct(cur[0]["memory_id"], principal=KP, expected_version=cur[0]["row_version"], new_value="63", evidence=[KEV2], reason="client restated the date")
    orch.ingest_meeting("INT-090")                              # the meeting that states 63 arrives and is consolidated AFTER the correction
    return orch, store, cur[0], res.memory_id


def test_h1_a_human_correction_survives_the_next_consolidation_and_the_chain_stays_whole():
    orch, store, old, cor_id = _corrected_world()
    cor = row(store, cor_id)
    assert cor["source_class"] == "human_correction" and cor["status"] == "active" and cor["value_text"] == "63" and row(store, old["memory_id"])["status"] == "superseded"
    snap, n_sem = dict(cor), len(store.records(KENJI, "semantic"))
    for _ in range(3):
        orch.consolidator.consolidate_client(KENJI)                                  # the run that used to supersede the correction, repeated
    again = row(store, cor_id)
    assert again["status"] == "active" and again["value_text"] == "63" and again["row_version"] == snap["row_version"] and again["valid_to"] == snap["valid_to"]
    assert [r["memory_id"] for r in active_semantic(store)] == [cor_id]                                           # exactly one active version: never empty, never two
    assert len(store.records(KENJI, "semantic")) == n_sem                                                          # no duplicate chain was produced
    assert row(store, old["memory_id"])["status"] == "superseded"                                                  # the previous version stays superseded
    assert [e["to_status"] for e in store.lifecycle_events(cor_id)] == ["active"]                                  # consolidation never touched it
    assert [c for c in store.conflicts(KENJI) if c["kind"] == "human_correction_vs_evidence"] == []                # evidence that agrees raises nothing
    assert orch.consolidator.validate_chains(KENJI) == []
    assert store.integrity_ready()


def test_h1_the_audits_value_70_is_refused_because_no_source_states_it():
    orch, store = _kenji_world()
    cur = active_semantic(store)[0]
    with pytest.raises(MemoryWriteRejected) as e:
        store.boundary.correct(cur["memory_id"], principal=KP, expected_version=cur["row_version"], new_value="70", evidence=[KEV2], reason="audit scenario")
    assert e.value.code == "correction_value_unsupported" and row(store, cur["memory_id"])["status"] == "active" and store.count("memory_records", "memory_id LIKE 'MEM-COR-%'") == 0


def test_h1_newer_conflicting_evidence_is_recorded_as_a_review_requirement_and_never_overwrites_the_correction():
    orch, store, old, cor = _corrected_world()
    ev = dict(source_type="conversation", interaction_id="INT-149", turn_index=3, speaker="Advisor", excerpt=LATER_65)
    mid, created = store.insert_memory(erec("LATER", h="later", content=LATER_65, value_text="65", event_date="2026-06-30", source_role="Advisor"),
                                       dict(interaction_id="INT-149", turn_index=3, event_ts="2026-06-30T08:00:00", speaker_role="Advisor", event_type="decision"), [ev], principal=KI)
    assert created and row(store, "LATER")["verification"] == "verified"
    out = orch.consolidator.consolidate_client(KENJI)
    assert out.get("conflicts_recorded") == 1
    assert [r["memory_id"] for r in active_semantic(store)] == [cor] and row(store, cor)["value_text"] == "63"      # the correction still stands
    cf = [c for c in store.conflicts(KENJI, "open") if c["kind"] == "human_correction_vs_evidence"]
    assert [(c["memory_id"], c["other_memory_id"], c["status"]) for c in cf] == [(cor, "LATER", "open")]
    n_sem = len(store.records(KENJI, "semantic"))
    for _ in range(3):
        orch.consolidator.consolidate_client(KENJI)                                                              # re-running adds no second conflict, no new version
    assert len([c for c in store.conflicts(KENJI) if c["kind"] == "human_correction_vs_evidence"]) == 1 and len(store.records(KENJI, "semantic")) == n_sem
    assert row(store, "LATER")["status"] == "active"                                                              # nothing auto-resolved


def test_h1_without_a_correction_consolidation_still_rebuilds_the_chain_as_before():
    orch, store = _kenji_world()
    chain = [(r["version_no"], r["value_text"], r["status"]) for r in store.records(KENJI, "semantic", topics=["retirement_age"])]
    assert len(chain) >= 2 and sum(1 for c in chain if c[2] == "active") == 1
    orch.consolidator.consolidate_client(KENJI)
    assert chain == [(r["version_no"], r["value_text"], r["status"]) for r in store.records(KENJI, "semantic", topics=["retirement_age"])]
    assert orch.consolidator.validate_chains(KENJI) == []


def test_h1_consolidation_cannot_transition_a_human_corrected_row_even_directly():
    orch, store, old, cor = _corrected_world()
    for call in (lambda: store.set_status(cor, "superseded", "rebuilt", "consolidation", principal=CP), lambda: store.set_semantic_support(cor, 5, principal=CP)):
        with pytest.raises(ScopeDenied) as e:
            call()
        assert e.value.code == "human_correction_protected"
    assert row(store, cor)["status"] == "active"
    assert orch.consolidator.validate_chains(KENJI) == []


# ===================================================================================================== H2: no implicit identity
MUTATIONS = {
    "insert_memory": lambda s, **k: s.insert_memory(erec("X1", h="x1"), dict(EDET), [KEV], **k),
    "set_status": lambda s, **k: s.set_status("K1", "disputed", "r", "someone", **k),
    "set_approval": lambda s, **k: s.set_approval("K1", "approved", "someone", **k),
    "set_semantic_support": lambda s, **k: s.set_semantic_support("K1", 2, **k),
    "add_relationship": lambda s, **k: s.add_relationship("K1", "K2", "related_to", **k),
    "add_evidence": lambda s, **k: s.add_evidence("K1", KENJI, dict(KEV), **k),
    "correct": lambda s, **k: s.boundary.correct("K1", expected_version=1, new_value="65", evidence=[KEV2], reason="r", **k),
    "supersede": lambda s, **k: s.boundary.supersede("K1", "K2", "r", expected_version=1, **k),
    "record_conflict": lambda s, **k: s.boundary.record_conflict("K1", "K2", "contradiction", **k),
}


def _two_rows():
    store, log, _ = rig()
    put(store, "K1")
    at(store, "MAR", "K2")
    return store, log


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_h2_every_mutation_path_refuses_a_missing_principal_before_looking_at_the_target(name):
    store, _ = _two_rows()
    before = counts(store)
    kwargs = {} if name in ("correct", "supersede", "record_conflict") else {"principal": None}
    if name in ("correct", "supersede"):
        kwargs = {"principal": None}
    with pytest.raises(ScopeDenied) as e:
        MUTATIONS[name](store, **kwargs)
    assert e.value.code == "missing_principal"
    assert counts(store) == before and row(store, "K1")["status"] == "active"


@pytest.mark.parametrize("actor", ["mallory", "system", "admin", "Kenji Tanaka", "consolidation", "ttl_expiry"])
def test_h2_an_actor_string_is_not_an_identity(actor):
    store, _ = _two_rows()
    victim = store.insert_memory(erec("R9", client=REBECCA, h="r9", content="decline", event_date=RDET["event_ts"][:10]), dict(RDET), [REV], principal=RI)[0]
    before = counts(store)
    with pytest.raises(ScopeDenied) as e:
        store.set_status(victim, "expired", "gone", actor)                                       # the pre-P1.4.1 legacy call shape
    assert e.value.code == "missing_principal"
    with pytest.raises(ScopeDenied):
        store.insert_memory(erec("R10", client=REBECCA, h="r10"), dict(RDET), [REV], actor=actor)
    with pytest.raises(ScopeDenied):
        store.add_relationship(victim, victim, "related_to", principal=None)
    assert counts(store) == before and row(store, victim)["status"] == "active"
    # ... and a PERSON whose name is a reserved / service name is no better (any other name is a real identity - but only inside the scope it was issued for)
    if actor in ("system", "admin", "consolidation", "ttl_expiry"):
        with pytest.raises(ScopeDenied) as e2:
            store.set_status(victim, "expired", "gone", actor, principal=Principal.user(actor, [REBECCA], authenticated=True))
        assert e2.value.code == "reserved_actor"
    else:
        with pytest.raises(MemoryWriteRejected) as e3:
            store.set_status(victim, "expired", "gone", actor, principal=Principal.user(actor, [KENJI], authenticated=True))
        assert e3.value.code == "memory_not_found" and row(store, victim)["status"] == "active"


def test_h2_unauthenticated_malformed_and_edited_principals_are_refused():
    store, _ = _two_rows()
    for who, code in ((Principal.user("advisor-kenji", [KENJI], authenticated=False), "unauthenticated_actor"),
                      (Principal("advisor-kenji", "user", frozenset([KENJI]), authenticated=True), "malformed_principal"),                       # hand-built: no seal
                      (dataclasses.replace(KP, scopes=frozenset([KENJI, REBECCA])), "malformed_principal"),                                       # edited after issue
                      (dataclasses.replace(KI, scopes=frozenset([REBECCA])), "malformed_principal"),
                      ("advisor-kenji", "malformed_principal"), (object(), "malformed_principal")):
        with pytest.raises(ScopeDenied) as e:
            store.set_status("K1", "disputed", "r", "x", principal=who)
        assert e.value.code == code
    assert row(store, "K1")["status"] == "active"
    with pytest.raises(TypeError):
        Principal.user("advisor-kenji", [KENJI])                                                    # `authenticated` has no default any more


def test_h2_a_valid_person_cannot_mutate_a_foreign_client_and_people_cannot_create_rows():
    store, _ = _two_rows()
    for call in (lambda: store.set_status("K1", "disputed", "r", "x", principal=RP), lambda: store.boundary.correct("K1", principal=RP, expected_version=1, new_value="65", evidence=[KEV2], reason="r"),
                 lambda: store.boundary.supersede("K1", "K2", "r", principal=RP, expected_version=1)):
        with pytest.raises(MemoryWriteRejected) as e:
            call()
        assert e.value.code == "memory_not_found"
    with pytest.raises(ScopeDenied) as e2:
        store.insert_memory(erec("U1", h="u1"), dict(EDET), [KEV], principal=KP)
    assert e2.value.code == "operation_not_permitted" and store.get("U1") is None
    assert row(store, "K1")["status"] == "active"


def test_h2_services_are_limited_by_operation_origin_type_scope_and_target():
    store, _ = _two_rows()
    ttl = Principal.service("ttl_expiry", any_client=True)
    cases = [   # (callable, expected code)
        (lambda: store.set_status("K1", "disputed", "r", "x", principal=KI), "operation_not_permitted"),                              # ingestion cannot transition
        (lambda: store.set_status("K1", "expired", "r", "x", principal=ttl), "target_not_permitted_for_service"),                      # the TTL job: an episode is not its business
        (lambda: store.set_status("K1", "superseded", "r", "x", principal=CP), None),                                                  # consolidation MAY supersede an automatic row (below)
        (lambda: store.insert_memory(srec("S1"), dict(SDET), [KEV], principal=KI), "origin_not_permitted"),                            # ingestion cannot write consolidation output
        (lambda: store.insert_memory(erec("E9", h="e9", origin="consolidation"), dict(EDET), [KEV], principal=CP), "origin_not_permitted"),   # consolidation cannot write episodes
        (lambda: store.insert_memory(erec("E8", h="e8", origin="conversation_rule"), dict(EDET), [KEV], principal=Principal.service("orchestrator", [KENJI])), "origin_not_permitted"),
        (lambda: store.insert_memory(erec("E7", h="e7", origin="curated_file"), dict(EDET), [KEV], principal=KI), "origin_not_permitted"),
    ]
    for call, code in cases:
        if code is None:
            assert call() is True
            continue
        with pytest.raises(MemoryWriteRejected) as e:
            call()
        assert e.value.code == code
    shape = [(lambda: store.set_status("K2", "expired", "r", "x", principal=Principal.service("ttl_expiry", [KENJI], any_client=True))),
             (lambda: store.insert_memory(erec("Z1", h="z1"), dict(EDET), [KEV], principal=Principal.service("curated_loader", [KENJI]))),
             (lambda: store.insert_memory(erec("Z2", h="z2"), dict(EDET), [KEV], principal=Principal.service("ingestion", ["GLOBAL"]))),
             (lambda: store.set_status("K2", "superseded", "r", "x", principal=Principal.service("consolidation", [KENJI], any_client=True)))]
    for call in shape:
        with pytest.raises(ScopeDenied) as e2:
            call()
        assert e2.value.code in ("malformed_principal", "target_not_permitted_for_service")
    other = Principal.service("consolidation", [REBECCA])                                          # scoped to ANOTHER client: indistinguishable from "no such memory"
    with pytest.raises(MemoryWriteRejected) as e3:
        store.set_status("K2", "superseded", "r", "x", principal=other)
    assert e3.value.code == "memory_not_found"


def test_h2_the_ttl_service_expires_only_active_working_rows_that_are_past_their_expiry():
    orch, store, _, clock = make(KENJI_SCRIPT)
    from test_memory_support import plan
    ids = orch.store_working("REQ-T", plan("t", [KENJI], []), [])
    assert ids and orch.expire_working() == []                                                    # not yet due, by the store's own clock
    clock.advance(hours=5)
    assert orch.expire_working() == ids and row(store, ids[0])["status"] == "expired"
    ids2 = orch.store_working("REQ-T2", plan("t", [KENJI], [], rid="REQ-T2"), [])
    with pytest.raises(TypeError):                                                                # no caller can pick the instant: the TTL Principal has no clock at all
        Principal.service("ttl_expiry", any_client=True, as_of="2999-01-01")
    with pytest.raises(TypeError):
        store.expire_working(now_iso="2999-01-01T00:00:00")
    assert row(store, ids2[0])["status"] == "active"


def test_h2_the_shipped_workflows_still_work_with_explicit_principals():
    orch, store = _kenji_world()                                                                   # ingestion + consolidation (create/transition/link/support) through services
    assert store.count("memory_records", "client_id=?", (KENJI,)) > 5 and store.records("GLOBAL", "procedural")
    assert all(r["created_by"] in ("ingestion", "consolidation", "curated_loader") for r in store.records(KENJI))
    pro = store.records("GLOBAL", "procedural")
    assert all(r["source_class"] == "curated" and r["verification"] == "verified" for r in pro)
    mid = store.records(KENJI, "episodic")[0]["memory_id"]
    r = orch.update_memory_lifecycle(mid, "disputed", "checking", "reviewer-1", principal=Principal.user("reviewer-1", [KENJI], authenticated=True))
    assert r["to_status"] == "disputed"
    with pytest.raises(ScopeDenied):
        orch.update_memory_lifecycle(mid, "expired", "no principal", "reviewer-1")


# ===================================================================================================== H3: verification binds content to evidence
def _created(store, mid, origin, content, value, principal=KI, ev=(KEV,), h=None):
    return store.insert_memory(erec(mid, h=h or mid, origin=origin, content=content, value_text=value), dict(EDET), list(ev), principal=principal)


def test_h3_rule_derived_content_must_be_the_cited_sentence():
    store, _, _ = rig()
    _created(store, "OK", "conversation_rule", K090_T2, "63")
    _created(store, "BAD", "conversation_rule", "The client was diagnosed with a serious illness", "63")                  # a real excerpt, unrelated content
    assert (row(store, "OK")["verification"], row(store, "OK")["source_class"]) == ("verified", "observed_transcript")
    assert row(store, "BAD")["verification"] == "unverified" and "not one of the cited sentences" in row(store, "BAD")["verification_basis"]


def test_h3_llm_content_without_a_supported_value_is_never_verified():
    store, _, _ = rig()
    _created(store, "NOVAL", "conversation_llm", "The client was diagnosed with a serious illness", None)
    _created(store, "FAKE", "conversation_llm", "We've decided: 63.", "99")
    _created(store, "UNREL", "conversation_llm", "The client was diagnosed with a serious illness", "63")              # the value IS in the excerpt, the statement is not
    _created(store, "FAB", "conversation_llm", "We've decided: 63 and 4000", "63")                                    # a number the source does not state
    _created(store, "GOOD", "conversation_llm", "We have decided 63.", "63")
    got = {m: row(store, m)["verification"] for m in ("NOVAL", "FAKE", "UNREL", "FAB", "GOOD")}
    assert got == {"NOVAL": "unverified", "FAKE": "unverified", "UNREL": "unverified", "FAB": "unverified", "GOOD": "verified"}, got
    assert "no extracted value" in row(store, "NOVAL")["verification_basis"]


def test_h3_equivalent_numeric_spellings_verify_and_unit_words_do_not_change_the_value():
    store, _, _ = rig()
    for mid, val in (("N1", "63"), ("N2", "63.0"), ("N3", "sixty-three"), ("N4", "63 years")):
        _created(store, mid, "conversation_llm", K090_T2, val)
    assert {m: row(store, m)["verification"] for m in ("N1", "N2", "N3", "N4")} == {m: "verified" for m in ("N1", "N2", "N3", "N4")}
    _created(store, "N5", "conversation_llm", K090_T2, "64")
    assert row(store, "N5")["verification"] == "unverified"


def test_h3_a_derived_semantic_is_verified_only_when_a_verified_episode_of_that_turn_states_the_same_value():
    store, _, _ = rig()
    put(store, "E1")                                                                                    # verified episode: INT-090.2 states 63
    for mid, val, want in (("S63", "63", "verified"), ("S999", "999", "unverified"), ("S65", "65", "unverified"), ("SWORDS", "sixty-three", "verified")):
        store.insert_memory(srec(mid, val, content_hash=mid + "h", version_no=None, subject=mid.lower()), dict(SDET, slot_key="retirement_age|" + mid.lower()), [KEV], principal=CP)
        assert row(store, mid)["verification"] == want, (mid, row(store, mid)["verification_basis"])
    store.insert_memory(erec("E2", h="e2", value_text="99", content="We've decided: 63."), dict(EDET), [KEV], principal=KI)          # an UNVERIFIED episode of the same turn ...
    store.insert_memory(srec("S99", "99", content_hash="s99h", version_no=None, subject="s99"), dict(SDET, slot_key="retirement_age|s99"), [KEV], principal=CP)
    assert row(store, "S99")["verification"] == "unverified"                                            # ... lends nothing


def test_h3_a_human_correction_must_be_supported_by_its_source():
    store, _, _ = rig()
    at(store, "JAN", "OLD")
    for bad, ev in (("9999999", KEV2), ("70", KEV2), ("sixty", KEV2), ("65", KEV2), ("62", KEV2)):               # KEV2 states 63; 65 is the REPLACED plan and 62 is Mariko's teaching age
        with pytest.raises(MemoryWriteRejected) as e:
            store.boundary.correct("OLD", principal=KP, expected_version=1, new_value=bad, evidence=[ev], reason="typo")
        assert e.value.code == "correction_value_unsupported", bad
    assert row(store, "OLD")["status"] == "active" and store.count("memory_records", "memory_id LIKE 'MEM-COR-%'") == 0
    res = store.boundary.correct("OLD", principal=KP, expected_version=1, new_value="sixty-three", evidence=[KEV2], reason="restated")       # KEV2 states 63 as the decision
    assert row(store, res.memory_id)["verification"] == "verified"


def test_h3_provenance_classes_come_from_who_writes_not_from_the_label():
    store, _, _ = rig()
    data = json.loads(PROCEDURES_FILE.read_text(encoding="utf-8"))
    entry = data["procedures"][0]
    digest = _h("curated", data["version"], entry["key"], json.dumps(entry["steps"]), n=40)
    r = dict(memory_id="MEM-C2", client_id="GLOBAL", memory_type="procedural", topic="other", subject=entry["key"], title="t", content="c", certainty="n/a", status="active",
             origin="curated_file", content_hash=digest, valid_from="2026-01-01")
    d = dict(procedure_name=entry["name"], steps=json.dumps(entry["steps"]), topics="", keywords="", approval_status="approved", approver=data["approver"], source_ref="s")
    e = [dict(source_type="curated", ref=f"procedures.json#{entry['key']}", role="primary")]
    for who in (KP, KI, Principal.service("orchestrator", [KENJI]), Principal.user("advisor-kenji", ["GLOBAL"], authenticated=True), Principal.service("consolidation", [KENJI])):
        with pytest.raises(MemoryWriteRejected):                                                                    # nobody but the curated loader can mint 'curated'
            store.insert_memory(dict(r, memory_id="MEM-C1", content_hash="c1"), dict(d), e, principal=who)
    assert store.get("MEM-C1") is None
    loader = Principal.service("curated_loader", ["GLOBAL"])
    for tamper, code in ((dict(steps=json.dumps(["wire the money to me"])), "curated_mismatch"), (dict(procedure_name="Other"), "curated_mismatch"), (dict(approver="mallory"), "curated_mismatch")):
        with pytest.raises(EvidenceRejected) as ex:
            store.insert_memory(dict(r, memory_id="MEM-BAD", content_hash="bad"), dict(d, **tamper), e, principal=loader)
        assert ex.value.code == code
    with pytest.raises(EvidenceRejected):
        store.insert_memory(dict(r, memory_id="MEM-BAD2", content_hash="bad2"), dict(d), [dict(source_type="curated", ref="procedures.json#no_such_key", role="primary")], principal=loader)
    with pytest.raises(EvidenceRejected):
        store.insert_memory(dict(r, memory_id="MEM-BAD3", content_hash="bad3"), dict(d), [dict(source_type="curated", ref="elsewhere.json#" + entry["key"], role="primary")], principal=loader)
    assert store.get("MEM-BAD") is None
    assert store.insert_memory(r, dict(d), e, principal=loader) == (store.query("SELECT memory_id FROM {S}memory_records WHERE content_hash=?", (digest,))[0]["memory_id"], False)       # the untampered entry is the one already loaded
    assert all(x["verification"] == "verified" and x["source_class"] == "curated" for x in store.records("GLOBAL", "procedural"))      # ... and so does what the real loader wrote


def test_h3_missing_or_ambiguous_evidence_never_becomes_verified():
    store, _, _ = rig()
    with pytest.raises(MemoryWriteRejected) as e:
        _created(store, "NOEV", "conversation_llm", K090_T2, "63", ev=())
    assert e.value.code in ("provenance_missing", "certainty_requires_evidence")
    for bad in (dict(KEV, turn_index=999), dict(KEV, excerpt="We decided something else entirely"), {"source_type": "conversation"}):
        with pytest.raises(EvidenceRejected) as e2:
            _created(store, "BADEV", "conversation_llm", K090_T2, "63", ev=(bad,))
        assert e2.value.code == "evidence_unresolved"
    wrec = dict(memory_id="W1", client_id=KENJI, memory_type="working", topic="other", content="tool said 63", certainty="n/a", status="active", origin="tool_summary", content_hash="w1")
    store.insert_memory(wrec, dict(session_id="S", plan_id="P", derived_from_id=None, expires_at="2099-01-01T00:00:00"), [dict(source_type="tool", ref="EXEC-?", role="primary")],
                        principal=Principal.service("orchestrator", [KENJI]))
    assert row(store, "W1")["verification"] == "unverified" and row(store, "W1")["observed_at"] is None


# ===================================================================================================== H4: references and identity oracles
def _world2():
    store, log, _ = rig()
    put(store, "K1")
    store.insert_memory(erec("R1", client=REBECCA, h="r1", content="decline", event_date=RDET["event_ts"][:10]), dict(RDET), [REV], principal=RI)
    return store, log


def test_h4_a_cross_client_or_nonexistent_supersedes_id_is_refused_identically_and_nothing_is_stored():
    store, _ = _world2()
    n = store.count("memory_records")
    errs = []
    for ref in ("K1", "NO-SUCH-MEMORY"):                                                              # K1 is Kenji's; the writer is Rebecca's ingestion service
        with pytest.raises(MemoryWriteRejected) as e:
            store.insert_memory(erec("R2", client=REBECCA, h="r2", content="decline", event_date=RDET["event_ts"][:10], supersedes_id=ref), dict(RDET), [REV], principal=RI)
        errs.append((type(e.value).__name__, e.value.code, e.value.message))
    assert errs[0] == errs[1] and errs[0][1] == "reference_not_found"
    assert "FOREIGN" not in str(errs) and "UNIQUE" not in str(errs) and store.count("memory_records") == n and store.get("R2") is None


def test_h4_a_same_client_reference_still_works():
    store, _ = _world2()
    at(store, "MAR", "K2", supersedes_id="K1")
    assert row(store, "K2")["supersedes_id"] == "K1"


def test_h4_an_episodic_detail_row_cannot_point_at_another_clients_interaction():
    store, _ = _world2()
    n = store.count("episodic_details")
    for det in (dict(EDET, interaction_id="INT-061", turn_index=11), dict(EDET, turn_index=3)):                 # Rebecca's meeting / a turn this write never cited
        with pytest.raises(MemoryWriteRejected) as e:
            store.insert_memory(erec("D1", h="d1"), det, [KEV], principal=KI)
        assert e.value.code == "detail_reference_invalid"
    with pytest.raises(MemoryWriteRejected):
        store.insert_memory(erec("D2", h="d2"), dict(EDET, surprise="x"), [KEV], principal=KI)                  # unknown columns never reach SQL
    assert store.count("episodic_details") == n


def test_h4_ids_that_exist_elsewhere_and_ids_that_do_not_exist_look_the_same_on_every_path():
    store, _ = _world2()
    outcomes = []
    for target in ("R1", "NO-SUCH-MEMORY"):
        row_ = []
        for call in (lambda t=target: store.set_status(t, "disputed", "r", "x", principal=KP), lambda t=target: store.add_relationship("K1", t, "related_to", principal=CP),
                     lambda t=target: store.add_relationship(t, "K1", "related_to", principal=CP), lambda t=target: store.boundary.supersede("K1", t, "r", principal=KP, expected_version=1),
                     lambda t=target: store.boundary.record_conflict("K1", t, "contradiction", principal=CP), lambda t=target: store.add_evidence(t, KENJI, dict(KEV), principal=CP),
                     lambda t=target: store.boundary.correct(t, principal=KP, expected_version=1, new_value="65", evidence=[KEV2], reason="r")):
            with pytest.raises(Exception) as e:
                call()
            row_.append((type(e.value).__name__, getattr(e.value, "code", None), getattr(e.value, "message", str(e.value))))
        outcomes.append(row_)
    assert outcomes[0] == outcomes[1], outcomes
    assert all(c == "memory_not_found" for _, c, _ in outcomes[0])
    orch, _, _, _ = make()
    for target in ("R1", "NO-SUCH-MEMORY"):                                                                     # the agent-level lifecycle call: same KeyError for both
        orch.store.init()
    with pytest.raises(KeyError):
        orch.update_memory_lifecycle("NO-SUCH-MEMORY", "disputed", "r", "x", principal=KP)


def test_h4_duplicate_ids_and_dangling_references_never_leak_database_messages():
    store, _ = _world2()
    for kw in (dict(memory_id="K1", content_hash="different"), dict(memory_id="R1", content_hash="another")):         # a memory id that is taken (own client / another client)
        with pytest.raises(MemoryWriteRejected) as e:
            store.insert_memory(erec(**{"mid": kw["memory_id"], "h": kw["content_hash"]}), dict(EDET), [KEV], principal=KI)
        assert e.value.code == "memory_id_unavailable" and "UNIQUE" not in str(e.value) and "memory_records" not in str(e.value)
    wrec = dict(memory_id="W9", client_id=KENJI, memory_type="working", topic="other", content="x", certainty="n/a", status="active", origin="orchestrator", content_hash="w9")
    with pytest.raises(MemoryWriteRejected) as e2:
        store.insert_memory(wrec, dict(session_id="S", plan_id="P", derived_from_id="R1", expires_at="2099-01-01T00:00:00"), [], principal=Principal.service("orchestrator", [KENJI]))
    assert e2.value.code == "reference_not_found"
    # whatever a trigger or constraint still catches is translated, not surfaced
    store.boundary.store._p_insert = lambda *a, **k: (_ for _ in ()).throw(sqlite3.IntegrityError("UNIQUE constraint failed: memory_records.memory_id"))
    with pytest.raises(MemoryWriteRejected) as e3:
        put(store, "Z1", h="z1")
    assert e3.value.code == "write_conflict" and "UNIQUE" not in str(e3.value)


def test_h4_the_api_scopes_the_write_from_the_request_context_and_answers_alike(world, monkeypatch):
    from harness import PermissionGate
    build, m, db = world
    c = build("production")
    con = sqlite3.connect(db)
    mid_k = first_episode(db)
    mid_r = con.execute("select memory_id from memory_records where client_id='GLOBAL' and memory_type='procedural' and status='active' limit 1").fetchone()[0]       # the seed holds Kenji + GLOBAL
    con.close()
    body = {"new_status": "disputed", "reason": "checking the record"}
    monkeypatch.setattr(PermissionGate, "authorized_clients", lambda self, auth: frozenset([REBECCA]))   # this identity may touch ONE other client only
    sup = bearer("SUP-1", "supervisor")
    foreign = c.post(f"/api/memories/{mid_k}/lifecycle", headers=sup, json=body)
    missing = c.post("/api/memories/MEM-NO-SUCH/lifecycle", headers=sup, json=body)
    assert (foreign.status_code, foreign.json()) == (missing.status_code, missing.json()) == (404, {"detail": "unknown memory"})
    assert c.post(f"/api/memories/{mid_r}/lifecycle", headers=sup, json=dict(body, new_status="expired")).status_code == 200      # GLOBAL stays reachable for a lifecycle-privileged identity
    con = sqlite3.connect(db)
    assert con.execute("select status from memory_records where memory_id=?", (mid_k,)).fetchone()[0] == "active"
    con.close()
    dev = build("development")                                                                                  # an unverified dev identity cannot mutate memory
    monkeypatch.undo()
    assert dev.post(f"/api/memories/{mid_k}/lifecycle", json=body).status_code == 403


# ===================================================================================================== M1 / M2: time
def test_m1_observation_time_comes_from_the_cited_turn_and_forged_dates_are_refused():
    store, _, _ = rig()
    n = store.count("memory_records")
    for bad_date in ("1999-01-01", "2099-12-31"):
        with pytest.raises(MemoryWriteRejected) as e:
            store.insert_memory(erec("F1", h="f1", event_date=bad_date), dict(EDET), [KEV], principal=KI)
        assert e.value.code == "event_date_mismatch"
        with pytest.raises(MemoryWriteRejected) as e2:
            store.insert_memory(erec("F2", h="f2"), dict(EDET, event_ts=bad_date + "T00:00:00"), [KEV], principal=KI)
        assert e2.value.code == "event_time_mismatch"
    with pytest.raises(MemoryWriteRejected):                                                                    # a turn of another day than the claimed one
        store.insert_memory(erec("F3", h="f3", event_date="2026-01-16"), dict(EDET), [KEV], principal=KI)
    assert store.count("memory_records") == n
    put(store, "OK")
    r = row(store, "OK")
    assert r["observed_at"] == "2026-05-01T08:15:00" and r["created_at"] != r["observed_at"]
    assert store.query("SELECT event_ts FROM {S}episodic_details WHERE memory_id='OK'")[0]["event_ts"] == r["observed_at"]
    wrec = dict(memory_id="W1", client_id=KENJI, memory_type="working", topic="other", content="x", certainty="n/a", status="active", origin="orchestrator", content_hash="w1")
    store.insert_memory(wrec, dict(session_id="S", plan_id="P", derived_from_id=None, expires_at="2099-01-01T00:00:00"), [], principal=Principal.service("orchestrator", [KENJI]))
    assert row(store, "W1")["observed_at"] is None                                                              # no transcript source = unknown observation time, not a made-up one
    with pytest.raises(MemoryWriteRejected) as e3:                                                              # a semantic memory cannot carry a date other than its source turn's either
        store.insert_memory(srec("S1", content_hash="s1h", event_date="1999-01-01", valid_from="1999-01-01", version_no=None), dict(SDET), [KEV], principal=CP)
    assert e3.value.code == "event_date_mismatch"


def _pair(store, old, new, **kw):
    at(store, old, "OLD", h="old")
    at(store, new, "NEW", h="new", **kw)
    return store.boundary.supersede("OLD", "NEW", "newer statement", principal=KP, expected_version=1)


def test_m2_supersession_requires_the_replacement_to_be_observed_strictly_later():
    store, _, _ = rig()
    res = _pair(store, "JAN", "MAY")                                                                            # older -> newer: allowed
    assert res.outcome == "superseded" and row(store, "OLD")["status"] == "superseded" and row(store, "OLD")["valid_to"] >= (row(store, "OLD")["valid_from"] or "")
    store2, _, _ = rig()
    with pytest.raises(MemoryWriteRejected) as e:                                                               # newer -> older: refused, the verified newer fact stays active
        _pair(store2, "MAY", "JAN")
    assert e.value.code == "supersession_chronology" and row(store2, "OLD")["status"] == "active" and row(store2, "OLD")["row_version"] == 1
    store3, _, _ = rig()
    at(store3, "MAY", "OLD", h="old")
    put(store3, "NEW", h="new", content="We've decided: 63")                                                    # the SAME turn: equal timestamps
    with pytest.raises(MemoryWriteRejected) as e2:
        store3.boundary.supersede("OLD", "NEW", "same moment", principal=KP, expected_version=1)
    assert e2.value.code == "supersession_chronology" and row(store3, "OLD")["status"] == "active"


def test_m2_unknown_observation_times_are_refused():
    store, _, _ = rig()
    w = lambda mid: dict(memory_id=mid, client_id=KENJI, memory_type="working", topic="other", content="x" + mid, certainty="n/a", status="active", origin="orchestrator", content_hash=mid)
    wd = dict(session_id="S", plan_id="P", derived_from_id=None, expires_at="2099-01-01T00:00:00")
    for mid in ("W1", "W2"):
        store.insert_memory(w(mid), dict(wd), [], principal=Principal.service("orchestrator", [KENJI]))
    with pytest.raises(MemoryWriteRejected) as e:
        store.boundary.supersede("W1", "W2", "r", principal=KP, expected_version=1)
    assert e.value.code == "supersession_time_unknown" and row(store, "W1")["status"] == "active"


def test_m2_a_correction_never_produces_a_window_that_ends_before_it_began():
    store, _, _ = rig()
    at(store, "JAN", "EPI")                                                                                     # January exploration (verified as an exploration)
    jan = dict(source_type="conversation", interaction_id="INT-012", turn_index=4, speaker="Client", excerpt=TURNS["JAN"][3])
    store.insert_memory(srec("SEM", value="60", version_no=1, content_hash="semh", event_date="2026-01-16", valid_from="2026-01-16", certainty="exploratory"),
                        dict(SDET), [jan], principal=CP)
    res = store.boundary.correct("SEM", principal=KP, expected_version=1, new_value="63", evidence=[KEV2], reason="the client decided in May")
    old, new = row(store, "SEM"), row(store, res.memory_id)
    assert old["valid_from"] == "2026-01-16" and old["valid_to"] == "2026-05-01" and new["valid_from"] == "2026-05-01" and old["valid_to"] >= old["valid_from"]


# ===================================================================================================== M3: destructive surfaces
@pytest.mark.parametrize("sql", ["DROP TRIGGER guard_memory_records_update", "DROP TRIGGER immutable_memory_content", "DROP TABLE memory_write_log", "ALTER TABLE memory_records ADD COLUMN x TEXT",
                                 "CREATE TRIGGER evil BEFORE INSERT ON memory_records BEGIN SELECT 1; END", "PRAGMA writable_schema = 1", "PRAGMA user_version = 0",
                                 "PRAGMA foreign_keys = OFF", "ATTACH DATABASE ':memory:' AS other", "DROP INDEX uq_write_log_key"])
def test_m3_ordinary_callers_cannot_change_the_schema_through_execute_or_query(sql):
    store, _, _ = rig()
    put(store, "M1")
    triggers = {r["name"] for r in store.query("SELECT name FROM sqlite_master WHERE type='trigger'")}
    for call in (store.execute, store.query):
        with pytest.raises(sqlite3.DatabaseError):
            call(sql)
    assert triggers == {r["name"] for r in store.query("SELECT name FROM sqlite_master WHERE type='trigger'")} and store.integrity_ready()
    with pytest.raises(sqlite3.IntegrityError):
        store.execute("UPDATE {S}memory_records SET status='expired' WHERE memory_id='M1'")                     # and the guard is still live
    assert store.query("PRAGMA user_version")[0]["user_version"] == 123                                          # reading a pragma stays possible


def test_m3_a_statement_prepared_during_maintenance_cannot_be_replayed_by_an_application_call():
    store, _, _ = rig()
    with store._maintenance():
        store.execute("CREATE TABLE probe_table (x)")
        store.execute("DROP TABLE probe_table")
    with pytest.raises(sqlite3.DatabaseError):                                                                   # the very same text, now from an application call
        store.execute("CREATE TABLE probe_table (x)")
    assert not store.query("SELECT 1 AS x FROM sqlite_master WHERE name='probe_table'")


def test_m3_a_populated_store_cannot_be_reset_by_an_application_call_but_an_operator_can(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    store, _, _ = rig(url)
    put(store, "M1")
    n, logs = store.count("memory_records"), store.count("memory_write_log")
    monkeypatch.delenv("CMF_MEMORY_MAINTENANCE", raising=False)
    with pytest.raises(MemoryResetRefused):
        store.init(reset=True)
    assert (store.count("memory_records"), store.count("memory_write_log")) == (n, logs) and row(store, "M1") is not None
    monkeypatch.setenv("CMF_MEMORY_MAINTENANCE", "1")                                                            # the operator's explicit offline decision
    store.init(reset=True)
    assert store.count("memory_records") == 0 and store.integrity_ready()
    store.close()
    empty = MemoryStore(url="sqlite:///:memory:", conv=CONV)
    monkeypatch.delenv("CMF_MEMORY_MAINTENANCE", raising=False)
    empty.init()
    empty.init(reset=True)                                                                                       # an EMPTY store (first run) resets freely
    assert empty.integrity_ready()


# ===================================================================================================== M4 / M5 / M6
def test_m4_an_observer_that_fails_after_the_commit_does_not_turn_the_write_into_an_error():
    store, _, _ = rig()
    seen = []

    def exploding(component, name, **payload):
        seen.append(name)
        raise OSError("trace sink is down")
    store.boundary.emit_fn = exploding
    mid, created = put(store, "M1")                                                                              # does not raise
    assert (mid, created) == ("M1", True) and row(store, "M1")["status"] == "active"
    assert "memory_write_committed" in seen and {n for n, _ in store.observer_failures} >= {"memory_write_committed"}
    assert all("OSError" in msg for _, msg in store.observer_failures)
    assert put(store, "M1")[1] is False                                                                          # the committed row is simply a duplicate on retry
    with pytest.raises(MemoryWriteRejected):                                                                     # a refusal is still reported as a refusal while observers fail
        store.insert_memory(erec("M2", h="m2", status="bogus"), dict(EDET), [KEV], principal=KI)


def test_m5_conflicts_are_durable_linked_and_survive_reopening(tmp_path):
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    store, _, _ = rig(url)
    put(store, "A", h="a", value_text="63")
    at(store, "MAY", "B", h="b", value_text="65", content="We've decided: 63. (65)")
    cf = store.conflicts(KENJI)
    assert any(c["kind"] == "contradiction_same_period" and {c["memory_id"], c["other_memory_id"]} == {"A", "B"} for c in cf)
    assert row(store, "A")["status"] == row(store, "B")["status"] == "active"                                    # visible, not resolved
    store.close()
    again = MemoryStore(url=url, conv=CONV)
    assert [c["kind"] for c in again.conflicts(KENJI)] == [c["kind"] for c in cf] and again.conflicts(REBECCA) == []
    with pytest.raises(sqlite3.IntegrityError):
        again.execute("DELETE FROM {S}memory_conflicts")
    with pytest.raises(sqlite3.IntegrityError):
        again.execute("UPDATE {S}memory_conflicts SET status='resolved'")
    again.close()


def test_m6_a_store_that_opened_before_the_migration_recovers_by_itself(tmp_path):
    p = tmp_path / "m.db"
    url = f"sqlite:///{p.as_posix()}"
    first = MemoryStore(url=url, conv=CONV)
    sql = [s for s in first._script("100_cmf_memory_schema.sql")]
    raw = sqlite3.connect(p)
    for s in sql:
        raw.execute(s)
    raw.commit()
    raw.close()
    assert first.integrity_ready() is False
    with pytest.raises(Exception) as e:
        put(first, "M1")
    assert type(e.value).__name__ == "MemoryIntegrityUnavailable"                                                # fails closed
    second = MemoryStore(url=url, conv=CONV)
    second.init()                                                                                                # another process completes the migration
    assert first.integrity_ready() is True                                                                       # no restart needed
    assert put(first, "M1")[1] is True and row(second, "M1") is not None
    first.close()
    second.close()


# ===================================================================================================== low findings that were cheap and safe
def test_l2_a_state_dependent_rejection_does_not_reserve_the_idempotency_key():
    store, _, _ = rig()
    at(store, "JAN", "OLD")
    store.set_status("OLD", "disputed", "checking", "reviewer-1", principal=KP)                                    # version 1 -> 2
    with pytest.raises(MemoryWriteRejected) as e:
        store.boundary.correct("OLD", principal=KP, expected_version=1, new_value="63", evidence=[KEV2], reason="r", idempotency_key="K-L2")
    assert e.value.code == "stale_version"
    res = store.boundary.correct("OLD", principal=KP, expected_version=2, new_value="63", evidence=[KEV2], reason="r", idempotency_key="K-L2")      # the same key, re-read version
    assert res.outcome == "corrected"


def test_l3_a_replay_by_another_actor_of_the_same_client_is_audited():
    store, _, _ = rig()
    at(store, "JAN", "OLD")
    store.boundary.correct("OLD", principal=KP, expected_version=1, new_value="63", evidence=[KEV2], reason="r", idempotency_key="K-L3")
    colleague = Principal.user("advisor-kenji-2", [KENJI], authenticated=True)
    rep = store.boundary.correct("OLD", principal=colleague, expected_version=1, new_value="63", evidence=[KEV2], reason="r", idempotency_key="K-L3")
    assert rep.replayed
    assert store.count("memory_write_log", "actor_id='advisor-kenji-2' AND outcome='duplicate' AND reason_codes LIKE '%replay_by_other_actor%'") == 1


def test_l4_negation_and_planned_certainty_are_not_read_as_completed():
    assert classify_temporal(dict(certainty="confirmed", event_date="2026"), "The client has not retired yet") != "historical"
    assert classify_temporal(dict(certainty="confirmed", event_date="2026"), "He never sold the house") != "historical"
    assert classify_temporal(dict(certainty="exploratory", event_date="2026"), "The client retired at 60") == "planned"
    assert classify_temporal(dict(certainty="confirmed", event_date="2026"), "The client retired at 60") == "historical"
    store, _, _ = rig()
    put(store, "A", h="a", value_text="63")
    store.insert_memory(erec("B", h="b", value_text="63.0", content="We've decided: 63."), dict(EDET), [KEV], principal=KI)         # same number spelled differently
    store.insert_memory(erec("C", h="c", value_text="sixty-three", content="We've decided: 63. again"), dict(EDET), [KEV], principal=KI)
    assert not [c for c in store.conflicts(KENJI) if c["kind"].startswith("contradiction")]


def test_l7_expiry_on_an_unmigrated_database_defers_instead_of_failing_the_read_path(tmp_path):
    p = tmp_path / "old.db"
    s = MemoryStore(url=f"sqlite:///{p.as_posix()}", conv=CONV)
    raw = sqlite3.connect(p)
    for stmt in s._script("100_cmf_memory_schema.sql"):
        raw.execute(stmt)
    raw.commit()
    raw.close()
    assert s.expire_working(Principal.service("ttl_expiry", any_client=True)) == []
    s.close()
