"""P1.4.2 - final memory-integrity remediation (H1 content verification, H2 expiry authority, H3 correction precedence / one active version, H4 valid time, H5 chronology,
H6 existence oracles, H7 conflict visibility and resolution).  Deterministic: real CSV turns, in-memory or temp-file SQLite, no network, no live LLM."""
import sqlite3
import sys
import threading
from datetime import timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

from agents.memory import support  # noqa: E402
from agents.memory.errors import MemoryWriteRejected, ScopeDenied  # noqa: E402
from agents.memory.integrity import Principal  # noqa: E402
from agents.memory.store import MemoryStore  # noqa: E402
from test_memory_support import CONV, K090_T2, K090_T11, KENJI, REBECCA, make, plan  # noqa: E402
from test_p0_security import bearer, first_episode, world  # noqa: E402,F401
from test_p14_memory_integrity import EDET, KEV, KEV2, KI, KP, RI, RP, RDET, REV, RTS, at, erec, put, rig, row  # noqa: E402
from test_p141_memory_remediation import CP, LATER_65, SDET, _corrected_world, active_semantic, counts, srec  # noqa: E402

D11 = dict(EDET, turn_index=11, speaker_role="Advisor")                  # detail row for the INT-090 turn 11 evidence (KEV2)
K001_T13 = "The working plan I'm recording is retirement at 65 on about fifteen thousand a month after tax, with the mortgage cleared by 2032."
K001_EV = dict(source_type="conversation", interaction_id="INT-001", turn_index=13, speaker="Advisor", excerpt=K001_T13)
LATER_EV = dict(source_type="conversation", interaction_id="INT-149", turn_index=3, speaker="Advisor", excerpt=LATER_65)
LATER_DET = dict(interaction_id="INT-149", turn_index=3, event_ts="2026-06-30T08:00:00", speaker_role="Advisor", event_type="decision")
TTL_ALL = Principal.service("ttl_expiry", any_client=True)


def llm(store, mid, content, value, ev=KEV2, det=D11, origin="conversation_llm", **kw):
    """Create an episodic memory with the given STATEMENT and VALUE against the given real turn; returns the stored row."""
    store.insert_memory(erec(mid, h=mid, origin=origin, content=content, value_text=value, source_role="Advisor", **kw), dict(det), [ev], principal=KI)
    return row(store, mid)


# ===================================================================================================== H1: claim-to-source verification
@pytest.mark.parametrize("label,content,value,origin,ev,det", [
    ("excerpt + unsupported terminal-illness claim", K090_T11 + " The client has also been diagnosed with a terminal illness.", "63", "conversation_llm", KEV2, D11),
    ("rule claim containing the source + a legal threat", K090_T2 + " The client will sue the firm.", "63", "conversation_rule", KEV, EDET),
    ("content '63' against a long excerpt", "63", "63", "conversation_llm", KEV2, D11),
    ("content 'decided' with a fabricated value", "decided", "99", "conversation_llm", KEV2, D11),
    ("negated retirement claim", "You have not decided to retire at 63, replacing the earlier plan of 65", "63", "conversation_llm", KEV2, D11),
    ("replaced plan value", "You've decided to retire at 65", "65", "conversation_llm", KEV2, D11),
    ("another person's teaching age", "You've decided to retire at 62", "62", "conversation_llm", KEV2, D11),
    ("percentage against a year-only source", K090_T2, "63 percent", "conversation_llm", KEV, EDET),
    ("ambiguous roles: three numbers, value from the wrong role (rule)", K090_T11, "62", "conversation_rule", KEV2, D11),
    ("ambiguous roles: value is the replaced number (rule)", K090_T11, "65", "conversation_rule", KEV2, D11),
    ("hedged claim of a decided fact", "The client will probably retire at 63 maybe", "63", "conversation_llm", KEV2, D11),
    ("unsupported clause appended inside ONE sentence", K090_T11.rstrip(".") + " and the client also has a serious illness.", "63", "conversation_llm", KEV2, D11),
    ("decided claim for a hedged source", "You have decided to retire at 60", "60", "conversation_llm", None, None),
])
def test_h1_unsupported_claims_are_stored_unverified_and_cannot_override_a_verified_memory(label, content, value, origin, ev, det):
    store, log, _ = rig()
    if ev is None:                                                                                        # the January exploration: "what if I retired at 60 instead of 65?"
        iid, turn, speaker, ex, ts, _v = __import__("test_p14_memory_integrity").TURNS["JAN"]
        ev = dict(source_type="conversation", interaction_id=iid, turn_index=turn, speaker=speaker, excerpt=ex)
        det = dict(interaction_id=iid, turn_index=turn, event_ts=ts, speaker_role=speaker, event_type="decision")
        r = store.insert_memory(erec("BAD", h="bad", content=content, value_text=value, event_date=ts[:10], source_role=speaker), dict(det), [ev], principal=KI)
        bad = row(store, "BAD")
    else:
        bad = llm(store, "BAD", content, value, ev, det, origin)
    assert bad["verification"] == "unverified", (label, bad["verification_basis"])                          # the PERSISTED state, not just a return code
    assert store.query("SELECT verification FROM {S}memory_records WHERE memory_id='BAD'")[0]["verification"] == "unverified"
    # downstream: an unverified memory cannot take the place of a genuinely verified one
    at(store, "JAN", "EARLY", h="early")                                                                    # a verified exploration (January) ...
    assert row(store, "EARLY")["verification"] == "verified"
    with pytest.raises(MemoryWriteRejected) as e:
        store.boundary.supersede("EARLY", "BAD", "newer statement", principal=KP, expected_version=1)
    assert e.value.code == "unverified_cannot_override_verified" and row(store, "EARLY")["status"] == "active" and row(store, "EARLY")["row_version"] == 1


def test_h1_legitimate_claims_remain_verified_including_short_and_equivalent_numeric_forms():
    store, _, _ = rig()
    good = {"direct": llm(store, "G1", "You've decided to retire at 63", "63"),
            "short": llm(store, "G2", "retire at 63", "63"),
            "63.0": llm(store, "G3", "You've decided to retire at 63.0", "63.0"),
            "words": llm(store, "G4", "You've decided to retire at sixty-three", "sixty-three"),
            "rule": llm(store, "G5", K090_T11, "63", origin="conversation_rule"),
            "rule short sentence": llm(store, "G6", K090_T2, "63", ev=KEV, det=EDET, origin="conversation_rule")}
    assert {k: r["verification"] for k, r in good.items()} == {k: "verified" for k in good}, {k: r["verification_basis"] for k, r in good.items() if r["verification"] != "verified"}


def test_h1_each_matching_rule_is_independently_necessary():
    """Isolated cases: the rule under test is the ONLY thing that can reject the claim (the others would let it through)."""
    ok = lambda claim, value, src, **kw: support.claim_supported(claim, value, [src], verbatim=False, **kw)[0]
    assert ok("You retire at 63", "63", "We will retire at 63.")                                                     # control: this wording IS supported
    assert not ok("You retire at 63", "63", "Maybe after the accountant meeting next week we will retire at 63.")    # hedging differs (the hedge word is outside the number's own window)
    assert not ok("You retire at 65", "65", "Previously retire at 65 was the plan.")                                  # a replaced value, no other numbers around to make it ambiguous
    assert not ok("You retire at 65", "65", "Retire at 65 was the plan.")                                              # '... was the plan' after the number
    assert not ok("We did not retire at 63", "63", "We retire at 63.")                                                 # negation differs
    assert not ok("We retire at 63 percent", "63", "We retire at 63.")                                                 # unit differs
    assert not ok("We decided retirement 63", "63", "We've decided: 63.")                                              # half the content words are missing (one word only: the share rule, not the count rule)
    assert not ok("We decided a retirement plan with 63 here", "63", "We've decided: 63.")                              # several unmatched words (the count rule)


def test_h1_substring_overlap_alone_never_verifies():
    for content in ("63", "decided", "We", K090_T11[:20], K090_T11 + " and more", "retire"):
        assert not support.claim_supported(content, "63", [K090_T11], verbatim=False)[0], content
    assert not support.claim_supported(K090_T11 + " and more", "63", [K090_T11], verbatim=True)[0]
    assert support.claim_supported(K090_T11, "63", [K090_T11], verbatim=True, context="retirement age")[0]


def test_h1_an_unsupported_derived_semantic_stays_unverified_even_with_a_verified_episode_of_the_same_turn():
    store, _, _ = rig()
    llm(store, "E63", "You've decided to retire at 63", "63")                                               # verified, INT-090.11: states 63 (and, as the replaced plan, 65; and 62)
    for mid, val, want in (("S63", "63", "verified"), ("S65", "65", "unverified"), ("S62", "62", "unverified"), ("S999", "999", "unverified")):
        store.insert_memory(srec(mid, val, content_hash=mid + "h", version_no=None, subject=mid.lower()), dict(SDET, slot_key="retirement_age|" + mid.lower()), [KEV2], principal=CP)
        assert row(store, mid)["verification"] == want, (mid, row(store, mid)["verification_basis"])


def test_h1_unit_and_role_checks_apply_to_human_corrections_too():
    assert support.value_current_in("63", [K090_T11], "retirement age")
    for bad in ("65", "62", "63 percent", "630", "9999999"):
        assert not support.value_current_in(bad, [K090_T11], "retirement age"), bad
    assert support.value_current_in("sixty-three", [K090_T2], "retirement age")


# ===================================================================================================== H2: expiry is a boundary operation with a trusted clock
def _working(orch, rid, client=KENJI):
    return orch.store_working(rid, plan("t", [client], [], rid=rid), [])


def test_h2_expiry_without_a_principal_or_with_a_made_up_one_is_refused_and_takes_no_clock_from_the_caller():
    orch, store, _, clock = make(None)
    ids = _working(orch, "REQ-1")
    clock.advance(hours=5)
    for bad in (None, "system", "mallory", Principal.user("mallory", [KENJI], authenticated=True), KI, Principal.service("consolidation", [KENJI])):
        with pytest.raises(ScopeDenied):
            store.expire_working(bad)
    with pytest.raises(TypeError):
        store.expire_working(now_iso="2999-01-01T00:00:00")                                                  # the public surface takes no time at all
    with pytest.raises(TypeError):
        store.expire_working(TTL_ALL, "2999-01-01T00:00:00")
    assert row(store, ids[0])["status"] == "active"                                                          # nothing happened, although the rows ARE past expiry
    assert orch.expire_working() == ids and row(store, ids[0])["status"] == "expired"                          # the trusted entry point does the same work


def test_h2_live_working_rows_cannot_be_expired_early_and_ttl_is_limited_to_working_rows_and_to_expired_only():
    orch, store, _, clock = make(None)
    ids = _working(orch, "REQ-1")
    assert orch.expire_working() == [] and row(store, ids[0])["status"] == "active"                          # the store's own clock says "not yet"
    put(store, "EP")
    sem = srec("SEM1", "63", content_hash="sem1h", version_no=None, subject="sx")
    store.insert_memory(sem, dict(SDET, slot_key="retirement_age|sx"), [KEV], principal=CP)
    clock.advance(hours=5)
    for target in ("EP", "SEM1", store.records("GLOBAL", "procedural")[0]["memory_id"]):                       # episodic, semantic, procedural: not the TTL job's business
        with pytest.raises(ScopeDenied) as e:
            store.set_status(target, "expired", "ttl", "x", principal=TTL_ALL)
        assert e.value.code in ("target_not_permitted_for_service", "memory_not_found")
    with pytest.raises(ScopeDenied):                                                                          # ... and an expired-looking working row cannot go anywhere but 'expired'
        store.set_status(ids[0], "disputed", "ttl", "x", principal=TTL_ALL)
    assert [row(store, t)["status"] for t in ("EP", "SEM1")] == ["active", "active"] and row(store, ids[0])["status"] == "active"
    assert orch.expire_working() == ids and row(store, ids[0])["status"] == "expired"
    assert [row(store, t)["status"] for t in ("EP", "SEM1")] == ["active", "active"]


def test_h2_a_client_scoped_ttl_principal_cannot_touch_another_client():
    orch, store, _, clock = make(None)
    k, r = _working(orch, "REQ-K", KENJI), _working(orch, "REQ-R", REBECCA)
    clock.advance(hours=5)
    assert store.expire_working(Principal.service("ttl_expiry", [KENJI])) == k
    assert [row(store, i)["status"] for i in k] == ["expired"] and [row(store, i)["status"] for i in r] == ["active"]
    with pytest.raises(ScopeDenied):
        store.set_status(r[0], "expired", "ttl", "x", principal=Principal.service("ttl_expiry", [KENJI]))
    assert store.expire_working(Principal.service("ttl_expiry", [REBECCA])) == r


def test_h2_retrieval_still_hides_expired_working_memory_and_expiry_works_after_reopening(tmp_path):
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    orch, store, _, clock = make(None, url=url)
    ids = _working(orch, "REQ-1")
    pl = plan("t", [KENJI], [], cats=("working",), rid="REQ-1")
    assert [m.memory_id for m in orch.retrieve(pl)[KENJI].working] == ids
    store.close()
    clock.advance(hours=5)
    again = MemoryStore(url=url, conv=CONV, clock=clock)                                                     # reopened: the same rows, a later clock
    from agents.memory.agent import MemoryOrchestrator
    from llm.client import MockLLM
    orch2 = MemoryOrchestrator(MockLLM(handlers={}), store=again, conv=CONV)
    assert orch2.retrieve(pl)[KENJI].working == []                                                           # the read path expires them (client-scoped TTL principal)
    assert row(again, ids[0])["status"] == "expired" and again.count("memory_lifecycle_events", "memory_id=? AND to_status='expired'", (ids[0],)) == 1
    again.close()


def test_h2_an_unmigrated_database_defers_expiry_but_still_checks_the_principal(tmp_path):
    p = tmp_path / "old.db"
    s = MemoryStore(url=f"sqlite:///{p.as_posix()}", conv=CONV)
    raw = sqlite3.connect(p)
    for stmt in s._script("100_cmf_memory_schema.sql"):
        raw.execute(stmt)
    raw.commit()
    raw.close()
    with pytest.raises(ScopeDenied):
        s.expire_working(None)
    assert s.expire_working(TTL_ALL) == []
    s.close()


# ===================================================================================================== H3: correction precedence and ONE active version at the boundary
def _verified_episode(store, mid="E63"):
    llm(store, mid, "You've decided to retire at 63", "63")


def test_h3_an_automatic_create_cannot_supersede_a_human_correction_or_compete_with_it():
    orch, store, old, cor = _corrected_world()
    n = counts(store)
    for kwargs in (dict(supersedes_id=cor), dict()):                                                          # explicit supersession / a competing version without any reference
        with pytest.raises(MemoryWriteRejected) as e:
            store.insert_memory(srec("SX", "63", content_hash="sxh", version_no=99, event_date="2026-05-01", valid_from="2026-05-01", **kwargs), dict(SDET), [KEV2], principal=CP)
        assert e.value.code == "human_correction_protected"
    assert [r["memory_id"] for r in active_semantic(store)] == [cor] and store.get("SX") is None
    assert {k: v for k, v in counts(store).items() if k != "memory_conflicts"} == {k: v for k, v in n.items() if k != "memory_conflicts"}      # nothing partially written


def test_h3_a_second_active_version_in_a_slot_is_refused_and_a_declared_supersession_is_atomic():
    store, _, _ = rig()
    llm(store, "E1", K090_T2, "63", ev=KEV, det=EDET)
    store.insert_memory(srec("V1", "63", content_hash="v1h", version_no=1), dict(SDET), [KEV], principal=CP)
    n = counts(store)
    with pytest.raises(MemoryWriteRejected) as e:
        store.insert_memory(srec("V2", "63", content_hash="v2h", version_no=2), dict(SDET), [KEV], principal=CP)         # no supersedes_id: a competing active version
    assert e.value.code == "active_version_exists" and counts(store) == n
    assert [r["memory_id"] for r in active_semantic(store)] == ["V1"]
    with pytest.raises(sqlite3.IntegrityError):                                                               # and the database refuses it too (the backstop), even from inside the gate
        with store._gate():
            store.execute("INSERT INTO {S}memory_records (memory_id, client_id, memory_type, topic, subject, content, certainty, status, origin, content_hash, created_at, updated_at) "
                          "VALUES ('RAW','CLIENT-001','semantic','retirement_age','retirement_age','x','confirmed','active','consolidation','rawh','t','t')")


def test_h3_a_declared_supersession_replaces_the_previous_live_version_in_one_transaction():
    store, _, _ = rig()
    jan = TURN_JAN = __import__("test_p14_memory_integrity").TURNS["JAN"]
    at(store, "JAN", "EJ")                                                                                    # verified exploration of 60 (Jan 16)
    store.insert_memory(srec("V1", "60", content_hash="v1h", version_no=1, event_date="2026-01-16", valid_from="2026-01-16"), dict(SDET),
                        [dict(source_type="conversation", interaction_id=jan[0], turn_index=jan[1], speaker=jan[2], excerpt=jan[3])], principal=CP)
    llm(store, "E63", K090_T2, "63", ev=KEV, det=EDET)
    store.insert_memory(srec("V2", "63", content_hash="v2h", version_no=2, supersedes_id="V1"), dict(SDET), [KEV], principal=CP)
    assert [r["memory_id"] for r in active_semantic(store)] == ["V2"]
    v1 = row(store, "V1")
    assert v1["status"] == "superseded" and v1["valid_to"] == "2026-05-01" == row(store, "V2")["valid_from"]
    ev = store.lifecycle_events("V1")[-1]
    assert (ev["to_status"], ev["related_memory_id"]) == ("superseded", "V2")


def test_h3_two_concurrent_semantic_creates_leave_exactly_one_active_version(tmp_path):
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    a, _, _ = rig(url)
    llm(a, "E1", K090_T2, "63", ev=KEV, det=EDET)
    b = MemoryStore(url=url, conv=CONV)
    barrier, out = threading.Barrier(2), []

    def go(st, mid):
        barrier.wait()
        try:
            st.insert_memory(srec(mid, "63", content_hash=mid + "h", version_no=1), dict(SDET), [KEV], principal=CP)
            out.append("ok")
        except MemoryWriteRejected as e:
            out.append(e.code)
    ts = [threading.Thread(target=go, args=(a, "C1")), threading.Thread(target=go, args=(b, "C2"))]
    [t.start() for t in ts]
    [t.join(60) for t in ts]
    assert sorted(out) == ["active_version_exists", "ok"], out                                                  # deterministic invariant: exactly one winner, the other refused
    assert len(active_semantic(a)) == 1
    b.close()


# ===================================================================================================== H4: valid time is derived, never supplied
def test_h4_forged_valid_from_and_valid_to_are_refused_and_a_true_chronology_is_derived():
    store, _, _ = rig()
    llm(store, "E1", K090_T2, "63", ev=KEV, det=EDET)
    n = store.count("memory_records")
    for bad, code in ((dict(valid_from="1999-01-01"), "valid_time_mismatch"), (dict(valid_from="2099-12-31"), "valid_time_mismatch"),
                      (dict(valid_to="2099-12-31"), "valid_time_not_permitted"), (dict(valid_to="2026-05-02"), "valid_time_not_permitted")):
        with pytest.raises(MemoryWriteRejected) as e:
            store.insert_memory(srec("S1", "63", content_hash="s1h", version_no=1, **bad), dict(SDET), [KEV], principal=CP)
        assert e.value.code == code, bad
    assert store.count("memory_records") == n
    store.insert_memory(srec("S1", "63", content_hash="s1h", version_no=1, valid_from=None), dict(SDET), [KEV], principal=CP)         # omitted: derived from the cited turn
    assert row(store, "S1")["valid_from"] == "2026-05-01" and row(store, "S1")["valid_to"] is None and row(store, "S1")["observed_at"] == "2026-05-01T08:15:00"
    assert row(store, "S1")["created_at"] != row(store, "S1")["observed_at"]


def test_h4_a_callers_valid_to_cannot_terminate_or_extend_a_fact_through_a_transition():
    store, _, _ = rig()
    at(store, "JAN", "EJ")
    jan = __import__("test_p14_memory_integrity").TURNS["JAN"]
    store.insert_memory(srec("V1", "60", content_hash="v1h", version_no=1, event_date="2026-01-16", valid_from="2026-01-16"), dict(SDET),
                        [dict(source_type="conversation", interaction_id=jan[0], turn_index=jan[1], speaker=jan[2], excerpt=jan[3])], principal=CP)
    llm(store, "E63", K090_T2, "63", ev=KEV, det=EDET)
    store.insert_memory(srec("V2", "63", content_hash="v2h", version_no=2, supersedes_id=None, status="unconfirmed"), dict(SDET), [KEV], principal=CP)
    store.set_status("V1", "superseded", "newer", "x", related="V2", valid_to="2099-12-31", principal=KP)       # the caller's 2099 is ignored
    assert row(store, "V1")["valid_to"] == "2026-05-01"
    store.set_status("V2", "expired", "gone", "x", valid_to="1999-01-01", principal=KP)
    assert row(store, "V2")["valid_to"] is None


def test_h4_as_of_retrieval_returns_the_version_that_was_valid_then():
    orch, store, old, cor = _corrected_world()
    early = orch.retrieve(plan("x", [KENJI], ["retirement_age"], time_period="as of 2026-02-01", rid="R1"))[KENJI]
    late = orch.retrieve(plan("x", [KENJI], ["retirement_age"], time_period="as of 2026-06-01", rid="R2"))[KENJI]
    assert [m.value_text for m in early.current] == ["65"] and [m.value_text for m in late.current] == ["63"]
    assert row(store, old["memory_id"])["valid_to"] == row(store, cor)["valid_from"] == "2026-05-01"
    assert orch.consolidator.validate_chains(KENJI) == []


# ===================================================================================================== H5: one chronology rule for every replacement path
def test_h5_the_ordering_rule_itself():
    b = rig()[0].boundary
    ok = lambda new, old: b._check_later(new, old, "OP") is None
    assert ok(("2026-09-01T08:00:00", "I2", 1), ("2026-02-01T08:00:00", "I1", 9))                           # September replaces February
    assert ok(("2026-05-01T08:15:00", "I1", 11), ("2026-05-01T08:15:00", "I1", 2))                          # same meeting: the later turn wins
    for new, old, code in ((("2026-02-01T08:00:00", "I1", 1), ("2026-09-01T08:00:00", "I2", 1), "supersession_chronology"),      # February must not replace September
                           (("2026-05-01T08:15:00", "I1", 2), ("2026-05-01T08:15:00", "I1", 2), "supersession_chronology"),       # the same turn
                           (("2026-05-01T08:15:00", "I1", 2), ("2026-05-01T08:15:00", "I1", 11), "supersession_chronology"),      # an earlier turn
                           (("2026-05-01T08:15:00", "I2", 5), ("2026-05-01T08:15:00", "I1", 2), "supersession_chronology"),       # equal times, different meetings: ambiguous
                           (None, ("2026-05-01T08:15:00", "I1", 2), "supersession_time_unknown"), (("2026-05-01T08:15:00", "I1", 2), None, "supersession_time_unknown")):
        with pytest.raises(MemoryWriteRejected) as e:
            b._check_later(new, old, "OP")
        assert e.value.code == code


def _state(store):
    return {**counts(store), **{"log_rejected": store.count("memory_write_log", "outcome='rejected'")}}


def test_h5_transition_correction_and_supersession_all_refuse_an_older_replacement_and_change_nothing():
    store, _, _ = rig()
    at(store, "JAN", "OLD")
    at(store, "MAY", "NEW")
    before = {m: dict(row(store, m)) for m in ("OLD", "NEW")}
    snap = _state(store)
    calls = {"transition": lambda: store.set_status("NEW", "superseded", "older wins?", "x", related="OLD", principal=KP),
             "supersede": lambda: store.boundary.supersede("NEW", "OLD", "older wins?", principal=KP, expected_version=1),
             "correct": lambda: store.boundary.correct("NEW", principal=KP, expected_version=1, new_value="65", evidence=[K001_EV], reason="from the January plan")}
    for name, call in calls.items():
        with pytest.raises(MemoryWriteRejected) as e:
            call()
        assert e.value.code == "supersession_chronology", name
    assert {m: dict(row(store, m)) for m in ("OLD", "NEW")} == before                                           # both rows byte-for-byte unchanged
    after = _state(store)
    assert {k: v for k, v in after.items() if k != "log_rejected"} == {k: v for k, v in snap.items() if k != "log_rejected"} and after["log_rejected"] == snap["log_rejected"] + 3
    store.set_status("OLD", "superseded", "newer", "x", related="NEW", principal=KP)                         # the same relation the right way round is fine
    assert row(store, "OLD")["status"] == "superseded"


def test_h5_a_semantic_create_that_is_older_than_the_live_version_it_names_is_refused():
    store, _, _ = rig()
    llm(store, "E63", K090_T2, "63", ev=KEV, det=EDET)
    store.insert_memory(srec("V2", "63", content_hash="v2h", version_no=2), dict(SDET), [KEV], principal=CP)
    at(store, "JAN", "EJ")
    jan = __import__("test_p14_memory_integrity").TURNS["JAN"]
    with pytest.raises(MemoryWriteRejected) as e:
        store.insert_memory(srec("V1", "60", content_hash="v1h", version_no=1, supersedes_id="V2", event_date="2026-01-16", valid_from="2026-01-16"), dict(SDET),
                            [dict(source_type="conversation", interaction_id=jan[0], turn_index=jan[1], speaker=jan[2], excerpt=jan[3])], principal=CP)
    assert e.value.code == "supersession_chronology" and store.get("V1") is None and row(store, "V2")["status"] == "active"


# ===================================================================================================== H6: existence oracles
def test_h6_foreign_same_client_and_unused_ids_never_reveal_a_foreign_memory_through_the_store():
    store, _ = (lambda s: (s, 0))(rig()[0])
    put(store, "K1")
    store.insert_memory(erec("R1", client=REBECCA, h="r1", content="decline", event_date=RTS[:10]), dict(RDET), [REV], principal=RI)
    outcomes = []
    for taken in ("R1", "K1"):                                                                                  # Rebecca's writer re-uses an id of ANOTHER client / of her own... K1 is Kenji's, R1 hers
        with pytest.raises(MemoryWriteRejected) as e:
            store.insert_memory(erec(taken, client=REBECCA, h="zz" + taken, content="decline", event_date=RTS[:10]), dict(RDET), [REV], principal=RI)
        outcomes.append((type(e.value).__name__, e.value.code, e.value.message))
    assert outcomes[0] == outcomes[1] and "UNIQUE" not in str(outcomes) and "sqlite" not in str(outcomes).lower()


def test_h6_the_api_answers_alike_for_a_foreign_and_a_missing_memory_or_client_and_still_serves_authorized_reads(world):
    build, m, db = world
    c = build("production")
    mid = first_episode(db)
    kenji_advisor, other_advisor, sup = bearer("ADVISOR-001", "advisor"), bearer("ADVISOR-002", "advisor"), bearer("SUP-1", "supervisor")
    assert c.get(f"/api/memories/{mid}", headers=kenji_advisor).status_code == 200                              # authorized retrieval still works
    assert c.get(f"/api/memories/{mid}", headers=sup).status_code == 200
    assert c.get("/api/clients/CLIENT-001/brain", headers=kenji_advisor).status_code == 200
    foreign, missing = c.get(f"/api/memories/{mid}", headers=other_advisor), c.get("/api/memories/MEM-NO-SUCH", headers=other_advisor)
    assert (foreign.status_code, foreign.json()) == (missing.status_code, missing.json()) == (404, {"detail": "unknown memory"})
    f_c, m_c = c.get("/api/clients/CLIENT-001/brain", headers=other_advisor), c.get("/api/clients/CLIENT-999/brain", headers=other_advisor)
    assert (f_c.status_code, f_c.json()) == (m_c.status_code, m_c.json()) == (404, {"detail": "unknown client"})
    for r in (foreign, missing, f_c, m_c):
        assert "sqlite" not in r.text.lower() and "UNIQUE" not in r.text
    for path in (f"/api/memories/{mid}", "/api/memories/MEM-NO-SUCH", "/api/clients/CLIENT-001/brain", "/api/clients/CLIENT-999/brain"):
        assert c.get(path).status_code == 401                                                                    # no identity: no existence information at all
    # GLOBAL (curated) memories stay readable by any authenticated reader
    con = sqlite3.connect(db)
    gid = con.execute("select memory_id from memory_records where client_id='GLOBAL' limit 1").fetchone()[0]
    con.close()
    assert c.get(f"/api/memories/{gid}", headers=other_advisor).status_code == 200


# ===================================================================================================== H7: conflicts are visible, and resolved by a person only
def _conflict_world():
    orch, store, old, cor = _corrected_world()
    store.insert_memory(erec("LATER", h="later", content=LATER_65, value_text="65", event_date="2026-06-30", source_role="Advisor"), dict(LATER_DET), [LATER_EV], principal=KI)
    orch.consolidator.consolidate_client(KENJI)
    cf = [c for c in store.conflicts(KENJI, "open") if c["kind"] == "human_correction_vs_evidence"]
    assert len(cf) == 1
    return orch, store, cor, cf[0]


def _ret(orch, client=KENJI, rid="REQ-C"):
    return orch.retrieve(plan("retirement", [client], ["retirement_age"], rid=rid))[client]


def test_h7_retrieval_exposes_open_conflicts_for_the_right_client_only_and_keeps_both_sides():
    orch, store, cor, cf = _conflict_world()
    ctx = _ret(orch)
    assert [c["conflict_id"] for c in ctx.conflicts] == [cf["conflict_id"]]
    assert any("review required" in t and cf["conflict_id"] in t for t in ctx.contradictions)                    # reaches the Report Agent's uncertainties through `contradictions`
    assert [m.memory_id for m in ctx.current] == [cor] and cor in cf["memory_id"] and row(store, "LATER")["status"] == "active"      # nothing replaced or dropped
    assert _ret(orch, REBECCA, "REQ-R").conflicts == [] and _ret(orch, REBECCA, "REQ-R").contradictions == []      # tenant isolation
    assert store.conflicts(REBECCA, None) == []


def test_h7_services_and_strangers_cannot_resolve_and_a_person_resolves_once_with_an_immutable_history():
    orch, store, cor, cf = _conflict_world()
    cid = cf["conflict_id"]
    for who, code in ((CP, "operation_not_permitted"), (KI, "operation_not_permitted"), (Principal.service("ttl_expiry", any_client=True), "operation_not_permitted"),
                      (Principal.user("advisor-kenji", [KENJI], authenticated=False), "unauthenticated_actor"), (None, "missing_principal"),
                      (Principal.user("system", [KENJI], authenticated=True), "reserved_actor"), (RP, "conflict_not_found")):
        with pytest.raises(MemoryWriteRejected) as e:
            store.boundary.resolve_conflict(cid, "keep", "looked at it", principal=who)
        assert e.value.code == code, who
    with pytest.raises(MemoryWriteRejected) as e2:
        store.boundary.resolve_conflict("CF-NO-SUCH", "keep", "looked at it", principal=RP)
    assert e2.value.code == "conflict_not_found"                                                                 # same answer as a foreign conflict
    assert len(store.conflicts(KENJI, "open")) >= 1 and store.count("memory_conflict_resolutions") == 0
    n_total = len(store.conflicts(KENJI, None))
    res = store.boundary.resolve_conflict(cid, "keep", "the client confirmed 63 on the phone", principal=KP)
    assert res.changed and not [c for c in store.conflicts(KENJI, "open") if c["conflict_id"] == cid]
    done = [c for c in store.conflicts(KENJI, "resolved") if c["conflict_id"] == cid][0]
    assert (done["decision"], done["resolved_by"], done["resolution_reason"], done["status"]) == ("keep", "advisor-kenji", "the client confirmed 63 on the phone", "resolved") and done["resolved_at"]
    assert store.query("SELECT detected_at FROM {S}memory_conflicts WHERE conflict_id=?", (cid,))                    # the original conflict row is still there, untouched
    with pytest.raises(MemoryWriteRejected) as e3:
        store.boundary.resolve_conflict(cid, "dismiss", "again", principal=KP)
    assert e3.value.code == "conflict_already_resolved"
    for sql in ("UPDATE {S}memory_conflict_resolutions SET decision='dismiss'", "DELETE FROM {S}memory_conflict_resolutions", "DELETE FROM {S}memory_conflicts",
                "UPDATE {S}memory_conflicts SET kind='x'"):
        with pytest.raises(sqlite3.IntegrityError):
            store.execute(sql)
        with store._gate(), pytest.raises(sqlite3.IntegrityError):                       # not even the integrity boundary's own gate can edit or delete them
            store.execute(sql)
    # repeated consolidation neither erases nor re-opens it, and the warning is gone from retrieval
    for _ in range(2):
        orch.consolidator.consolidate_client(KENJI)
    assert len(store.conflicts(KENJI, None)) == n_total and not [c for c in store.conflicts(KENJI, "open") if c["conflict_id"] == cid]
    assert _ret(orch, rid="REQ-D").conflicts == [] and not any("review required" in t for t in _ret(orch, rid="REQ-D2").contradictions)
    assert row(store, cor)["status"] == "active" and row(store, cor)["value_text"] == "63"                          # resolving 'keep' changed no memory


def test_h7_resolving_with_a_new_value_goes_through_the_full_correction_validation():
    orch, store, cor, cf = _conflict_world()
    cid, ver = cf["conflict_id"], row(store, cor)["row_version"]
    n = counts(store)
    for kw, code in ((dict(new_value="70", evidence=[LATER_EV]), "correction_value_unsupported"),                  # the source does not state 70
                     (dict(new_value="65", evidence=[]), "provenance_missing"), (dict(new_value="65", evidence=[LATER_EV], expected_version=ver + 5), "stale_version"),
                     (dict(new_value="65", evidence=[K001_EV], expected_version=ver), "supersession_chronology")):     # January evidence cannot replace a May correction
        with pytest.raises(MemoryWriteRejected) as e:
            store.boundary.resolve_conflict(cid, "correct", "settle it", principal=KP, **dict({"expected_version": ver}, **kw))
        assert e.value.code == code, kw
    assert counts(store) == n and row(store, cor)["status"] == "active" and len(store.conflicts(KENJI, "open")) >= 1       # atomic: nothing changed, the conflict is still open
    res = store.boundary.resolve_conflict(cid, "correct", "the client restated 65 in June", principal=KP, new_value="65", evidence=[LATER_EV], expected_version=ver)
    new = row(store, res.memory_id)
    assert new["source_class"] == "human_correction" and new["verification"] == "verified" and new["value_text"] == "65" and row(store, cor)["status"] == "superseded"
    assert [r["memory_id"] for r in active_semantic(store)] == [new["memory_id"]]
    done = [c for c in store.conflicts(KENJI, "resolved") if c["conflict_id"] == cid][0]
    assert done["new_memory_id"] == new["memory_id"] and done["decision"] == "correct"
    for _ in range(2):
        orch.consolidator.consolidate_client(KENJI)
    assert [r["memory_id"] for r in active_semantic(store)] == [new["memory_id"]] and not [c for c in store.conflicts(KENJI, "open") if c["kind"] == "human_correction_vs_evidence"]
    assert orch.consolidator.validate_chains(KENJI) == []


def test_h7_conflicts_and_resolutions_survive_reopening(tmp_path):
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    store, _, _ = rig(url)
    put(store, "A", h="a")
    at(store, "MAY", "B", h="b", value_text="65", content="We've decided: 63. (65)")
    cf = store.conflicts(KENJI, "open")
    assert cf
    first = [c for c in cf if c["kind"] in ("contradiction_same_period", "unverified_vs_verified")][0]
    store.boundary.resolve_conflict(first["conflict_id"], "dismiss", "not material", principal=KP)
    snap = [(c["conflict_id"], c["status"], c["resolved_by"]) for c in store.conflicts(KENJI, None)]
    store.close()
    again = MemoryStore(url=url, conv=CONV)
    assert [(c["conflict_id"], c["status"], c["resolved_by"]) for c in again.conflicts(KENJI, None)] == snap and any(s[1] == "resolved" for s in snap)
    again.close()


def test_h7_the_resolve_endpoint_authorizes_the_person_scopes_the_conflict_and_answers_alike(world, monkeypatch):
    from harness import PermissionGate
    build, m, db = world
    c = build("production")
    st = m.store()
    ids = [r["memory_id"] for r in st.records(KENJI, "episodic")[:2]]
    assert st.boundary.record_conflict(ids[0], ids[1], "contradiction_same_period", "high", "fixture", principal=Principal.service("consolidation", [KENJI]))
    cid = st.conflicts(KENJI, "open")[0]["conflict_id"]
    body = {"decision": "keep", "reason": "looked at it"}
    assert c.post(f"/api/conflicts/{cid}/resolve", json=body).status_code == 401
    assert c.post(f"/api/conflicts/{cid}/resolve", headers=bearer("ADVISOR-001", "advisor"), json=body).status_code == 403             # advisors lack the lifecycle privilege
    with monkeypatch.context() as mp:
        mp.setattr(PermissionGate, "authorized_clients", lambda self, auth: frozenset([REBECCA]))                                   # this supervisor may touch Rebecca only
        foreign, missing = c.post(f"/api/conflicts/{cid}/resolve", headers=bearer("SUP-1", "supervisor"), json=body), c.post("/api/conflicts/CF-NO-SUCH/resolve", headers=bearer("SUP-1", "supervisor"), json=body)
    assert (foreign.status_code, foreign.json()) == (missing.status_code, missing.json()) == (404, {"detail": "unknown conflict"}) and st.conflicts(KENJI, "open")
    ok = c.post(f"/api/conflicts/{cid}/resolve", headers=bearer("SUP-1", "supervisor"), json=body)
    assert ok.status_code == 200 and ok.json()["resolved_by"] == "SUP-1" and not st.conflicts(KENJI, "open")
    assert c.post(f"/api/conflicts/{cid}/resolve", headers=bearer("SUP-1", "supervisor"), json=body).status_code == 409
    assert c.get("/api/clients/CLIENT-001/brain", headers=bearer("SUP-1", "supervisor")).json()["conflicts"] == []


# ===================================================================================================== migration 122
def _downgrade_to_121(path):
    raw = sqlite3.connect(path)
    for (n,) in raw.execute("select name from sqlite_master where type='trigger' and (name like 'guard_memory_conflict_resolutions%' or name like 'resolution_no_%')").fetchall():
        raw.execute(f"drop trigger {n}")
    raw.execute("drop index if exists uq_active_semantic_slot")
    raw.execute("drop index if exists uq_active_semantic_slot_canonical")
    raw.execute("drop table memory_conflict_resolutions")
    raw.execute("pragma user_version=121")
    raw.commit()
    raw.close()


def test_migration_122_fresh_v121_and_repeated_initialisation(tmp_path):
    p = tmp_path / "m.db"
    url = f"sqlite:///{p.as_posix()}"
    s, _, _ = rig(url)
    put(s, "M1")
    assert s.query("PRAGMA user_version")[0]["user_version"] == 123 and s.integrity_ready()
    assert s.query("select 1 as x from sqlite_master where name='uq_active_semantic_slot_canonical'") and s.query("select 1 as x from sqlite_master where name='memory_conflict_resolutions'")
    s.close()
    _downgrade_to_121(p)
    s2 = MemoryStore(url=url, conv=CONV)
    assert not s2.integrity_ready()                                                                              # a v121 database refuses writes until it is migrated ...
    s2.init()
    s2.init()
    assert s2.integrity_ready() and s2.query("PRAGMA user_version")[0]["user_version"] == 123 and row(s2, "M1")["status"] == "active"       # ... and migrates additively, keeping its data
    assert s2.query("select 1 as x from sqlite_master where name='resolution_no_delete'") and s2.query("select 1 as x from sqlite_master where name='guard_memory_conflict_resolutions_insert'")
    s2.close()


def test_migration_122_with_legacy_duplicate_active_versions_is_not_blocked_and_the_boundary_still_refuses_more(tmp_path):
    p = tmp_path / "m.db"
    url = f"sqlite:///{p.as_posix()}"
    s, _, _ = rig(url)
    llm(s, "E1", K090_T2, "63", ev=KEV, det=EDET)
    s.close()
    _downgrade_to_121(p)
    raw = sqlite3.connect(p)                                                                                     # an OFFLINE tool leaves two active versions of one slot (legacy data)
    raw.execute("drop trigger guard_memory_records_insert")                                                      # (written before the guards existed; init() restores them)
    for mid, h in (("LEGA", "ha"), ("LEGB", "hb")):
        raw.execute("INSERT INTO memory_records (memory_id, client_id, memory_type, topic, subject, content, certainty, status, origin, content_hash, created_at, updated_at, event_date, valid_from) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (mid, KENJI, "semantic", "retirement_age", "retirement_age", "legacy", "confirmed", "active", "consolidation", h, "t", "t", "2026-01-01", "2026-01-01"))
    raw.commit()
    raw.close()
    s2 = MemoryStore(url=url, conv=CONV)
    s2.init()
    assert s2.integrity_ready() and not s2.query("select 1 as x from sqlite_master where name='uq_active_semantic_slot_canonical'")      # the index is skipped, not failed
    with pytest.raises(MemoryWriteRejected) as e:
        s2.insert_memory(srec("V9", "63", content_hash="v9h", version_no=9), dict(SDET), [KEV], principal=CP)
    assert e.value.code == "active_version_exists" and row(s2, "LEGA")["status"] == "active"                    # legacy rows are untouched and still readable
    s2.close()


def test_migration_122_failure_rolls_back_and_a_dropped_resolution_trigger_fails_closed(tmp_path):
    p = tmp_path / "m.db"
    url = f"sqlite:///{p.as_posix()}"
    s, _, _ = rig(url)
    s.close()
    _downgrade_to_121(p)
    s2 = MemoryStore(url=url, conv=CONV)
    orig = s2.execute

    def flaky(sql, params=()):
        if sql.startswith("CREATE INDEX IF NOT EXISTS idx_conflict_resolution_client"):
            raise RuntimeError("power cut")
        return orig(sql, params)
    s2.execute = flaky
    with pytest.raises(RuntimeError):
        s2.init()
    s2.execute = orig
    assert s2.query("PRAGMA user_version")[0]["user_version"] == 121 and not s2.query("select 1 as x from sqlite_master where name='memory_conflict_resolutions'")
    s2.init()
    assert s2.integrity_ready()
    raw = sqlite3.connect(p)
    raw.execute("drop trigger resolution_no_update")
    raw.commit()
    raw.close()
    assert not s2.integrity_ready()
    s2.init()
    assert s2.integrity_ready()
    s2.close()
