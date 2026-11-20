"""Structured evidence layer tests, built on the real Dataset v1.  Evidence only: nothing here creates or reads a memory.
Evaluation ground truth is read ONLY by tests (never by the ingestion code) to build expectations."""
import csv
import json
import re
import shutil
import sqlite3
import sys
from collections import Counter
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "datasets"))

from evidence import PARSER_VERSION, semantics  # noqa: E402
from evidence.ingest import ingest, parse_all  # noqa: E402
from evidence.parsers.email import split_quoted  # noqa: E402
from evidence.retrieval import AccessDenied, EvidenceRetriever, NotFound, Requester  # noqa: E402
from evidence.sources import Sources  # noqa: E402
from evidence.store import EvidenceStore  # noqa: E402

DS = ROOT / "datasets"


def rows(rel):
    with open(DS / rel, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def store(tmp_path_factory):
    s = EvidenceStore(f"sqlite:///{(tmp_path_factory.mktemp('ev') / 'evidence.db').as_posix()}")
    s.report = ingest(s)
    yield s
    s.close()


def q(store, sql, *p):
    c = store.reader()
    try:
        return [dict(r) for r in c.execute(sql, p)]
    finally:
        c.close()


def adv(store, a="ADVISOR-001", **kw):
    return EvidenceRetriever(store, Requester.advisor(a), **kw)


# ---------------------------------------------------------------------------------------------------------------- structure
def test_counts_per_channel_match_the_dataset(store):
    assert store.report["totals"] == {"ingested": 378}
    by = {r["channel"]: r["n"] for r in q(store, "SELECT channel, count(*) n FROM containers GROUP BY channel")}
    assert by == {"Meetings": 100, "Calls": 62, "Email": len(rows("emails/email_threads.csv")), "Chat": len(rows("chats/chat_conversations.csv"))}
    turns = {r["channel"]: r["n"] for r in q(store, "SELECT channel, count(*) n FROM turns GROUP BY channel")}
    assert turns["Email"] == len(rows("emails/emails.csv")) == 81
    assert turns["Chat"] == len(rows("chats/chats.csv")) == 94
    assert q(store, "SELECT count(*) n FROM derived_artifacts")[0]["n"] == 166


def test_original_turns_all_present_and_calls_vs_meetings_follow_the_manifest(store):
    orig = list(csv.DictReader(open(ROOT / "data/conversations.csv", encoding="utf-8", newline="")))
    assert len(orig) == 2199
    manifest = {r["interaction_id"]: r["assigned_channel"] for r in rows("source_manifest.csv")}
    assert Counter(manifest.values()) == {"Meetings": 88, "Calls": 62}
    got = {r["container_id"]: r["channel"] for r in q(store, "SELECT container_id, channel FROM containers WHERE container_id LIKE 'INT-%'")}
    assert got == manifest                                    # not the original file's synthetic_meeting label
    kinds = {r["container_id"]: r["container_kind"] for r in q(store, "SELECT container_id, container_kind FROM containers WHERE container_id LIKE 'INT-%'")}
    assert {k for k, v in kinds.items() if v == "call"} == {k for k, v in manifest.items() if v == "Calls"}
    assert q(store, "SELECT count(*) n FROM turns WHERE turn_id LIKE 'INT-%'")[0]["n"] == 2199
    for r in orig:                                            # exact text and order of every original turn
        t = q(store, "SELECT original_text, seq, container_id FROM turns WHERE turn_id=?", f"{r['interaction_id']}#T{r['turn_index']}")[0]
        assert t["original_text"] == r["utterance"] and t["seq"] == int(r["turn_index"])


def test_email_threads_are_rebuilt_message_by_message(store):
    src = {r["email_id"]: r for r in rows("emails/emails.csv")}
    r = adv(store)
    for th in rows("emails/email_threads.csv"):
        cid = th["thread_id"]
        if not r.check(cid)[0]:
            continue
        turns = r.turns_in_container(cid)
        assert [t["seq"] for t in turns] == list(range(1, int(th["message_count"]) + 1))
        for t in turns:
            s = src[t["turn_id"]]
            assert t["original_text"] == s["body"] and t["email"]["message_id"] == s["message_id"]
            assert t["email"]["to_ids"] == s["to_ids"]
            assert t["speaker_id"] == s["sender_id"] and t["ts"] == s["timestamp"]
        # a reply is resolved only through In-Reply-To, inside the thread
        ids = {t["email"]["message_id"]: t["turn_id"] for t in turns}
        for t in turns:
            e = t["email"]
            assert e["in_reply_to_turn_id"] == (ids.get(e["in_reply_to"]) if e["in_reply_to"] else None)


def test_every_email_message_is_individually_retrievable(store):
    r = adv(store, "ADVISOR-001")
    ctx = r.span_context("EV-EML-045")
    assert ctx["turn"]["turn_id"] == "EML-045" and ctx["turn"]["speaker_id"] == "CLIENT-001"
    assert ctx["container"]["container_id"] == "THR-010" and len(ctx["container_turns"]) == 4
    assert any(x["rel_type"] == "replies_to" and x["to_turn_id"] == "EML-044" and x["provenance"] == "observed" for x in ctx["relations"])


def test_chat_messages_keep_their_own_identity_and_source_reply_links(store):
    src = rows("chats/chats.csv")
    for m in src:
        t = q(store, "SELECT t.*, c.reply_to_turn_id FROM turns t JOIN chat_message_details c ON c.turn_id=t.turn_id WHERE t.turn_id=?", m["message_id"])[0]
        assert t["original_text"] == m["message_text"] and t["speaker_id"] == m["sender_id"] and t["ts"] == m["timestamp"]
        assert (t["reply_to_turn_id"] or "") == m["reply_to_message_id"]            # never inferred
    assert q(store, "SELECT count(*) n FROM turn_relations WHERE basis='reply_to_message_id'")[0]["n"] == sum(1 for m in src if m["reply_to_message_id"])


def test_generated_meetings_keep_turn_speaker_and_time(store):
    for t in rows("meetings/meeting_turns.csv")[:200]:
        d = q(store, "SELECT * FROM turns WHERE turn_id=?", t["turn_id"])[0]
        assert (d["speaker_id"], d["ts"], d["original_text"], d["attribution_status"]) == (t["speaker_id"], t["turn_timestamp"], t["utterance"], "source_stated")


# ------------------------------------------------------------------------------------------------------ attribution honesty
def test_original_speakers_are_marked_derived_and_no_time_is_invented(store):
    for t in q(store, "SELECT * FROM turns WHERE turn_id LIKE 'INT-%'"):
        assert t["attribution_status"] == "derived_from_role_label" and t["ts"] is None and t["ts_basis"] == "container_start_only"
    c = q(store, "SELECT metadata FROM containers WHERE container_id='INT-003'")[0]
    meta = json.loads(c["metadata"])
    assert meta["turn_timestamps_available"] is False and meta["audio_or_diarization_available"] is False


def test_attribution_confidence_is_copied_from_the_registry_not_upgraded(store):
    reg = {r["character_id"]: r for r in rows("character_registry.csv")}
    for p in q(store, "SELECT * FROM parties"):
        assert p["attribution_confidence"] == reg[p["party_id"]]["attribution_confidence"]
        assert p["role_status"] == reg[p["party_id"]]["role_status"]
    for t in q(store, "SELECT speaker_id, attribution_confidence FROM turns WHERE speaker_id IS NOT NULL"):
        assert t["attribution_confidence"] == reg[t["speaker_id"]]["attribution_confidence"]


# ------------------------------------------------------------------------------------------------------------ quoted text
def test_quote_splitter_separates_authored_from_quoted():
    body = "Yes, five is fine.\n\nOn Mon, 1 Jun 2026 at 16:10, Hannah Park wrote:\n> Lump sum at 63 pushes the year into a high bracket.\n> Ten installments save a little more.\n"
    a, qt, basis = split_quoted(body)
    assert a == "Yes, five is fine." and "Lump sum at 63" in qt and "Lump sum" not in a and basis == "on_wrote"
    a2, q2, b2 = split_quoted("My answer: no.\n> You asked about the bonus.\nThanks.")
    assert "bonus" not in a2 and "You asked" in q2 and b2 == "angle_bracket_lines"
    a3, q3, b3 = split_quoted("Plain body.\n\nPriya")
    assert (a3, q3, b3) == ("Plain body.\n\nPriya", "", "no_quote_markers")
    a4, q4, _ = split_quoted("See below.\n-----Original Message-----\nFrom: x\nSent: y\n\nold text")
    assert a4 == "See below." and "old text" in q4


def test_dataset_emails_carry_no_quote_markers_and_stay_whole(store):
    """Dataset v1 bodies contain no quoted earlier messages.  The parser reports that fact instead of inventing quotes."""
    d = q(store, "SELECT quote_basis, quoted_text, authored_text, turn_id FROM email_message_details")
    assert {x["quote_basis"] for x in d} == {"no_quote_markers"} and all(x["quoted_text"] == "" for x in d)
    assert q(store, "SELECT count(*) n FROM evidence_spans WHERE span_kind='authored_part'")[0]["n"] == 0


def test_quoted_text_is_never_annotated_as_the_authors_words(tmp_path):
    k = next(c for c in parse_all(Sources())[0] if c.channel == "Email")
    t = next(x for x in k.turns if x.email)
    t.email["authored_text"], t.email["quoted_text"] = "Agreed.", "I decided to sell everything."
    t.original_text = "Agreed.\nOn Mon X wrote:\n> I decided to sell everything."
    semantics.enrich(k, {})
    acts = {a[3] for a in k.annotations if a[1] == t.turn_id and a[2] == "dialogue_act"}
    assert "decision" not in acts


# -------------------------------------------------------------------------------------------------------- segments and context
def test_segments_partition_each_container_and_link_to_exact_turns(store):
    for c in q(store, "SELECT container_id, turn_count FROM containers"):
        segs = q(store, "SELECT * FROM segments WHERE container_id=? ORDER BY seg_index", c["container_id"])
        assert segs and segs[0]["start_seq"] == 1 and segs[-1]["end_seq"] == c["turn_count"]
        for a, b in zip(segs, segs[1:]):
            assert b["start_seq"] == a["end_seq"] + 1                      # contiguous, no gaps or overlap
        linked = q(store, "SELECT st.turn_id, t.seq FROM segment_turns st JOIN turns t ON t.turn_id=st.turn_id JOIN segments s ON s.segment_id=st.segment_id WHERE s.container_id=?", c["container_id"])
        assert len(linked) == c["turn_count"] == len({x["turn_id"] for x in linked})
        for s in segs:
            seqs = [x["seq"] for x in q(store, "SELECT t.seq FROM segment_turns st JOIN turns t ON t.turn_id=st.turn_id WHERE st.segment_id=?", s["segment_id"])]
            assert sorted(seqs) == list(range(s["start_seq"], s["end_seq"] + 1))
            assert s["summary"] is None and s["method"] == "keyword_lexicon_v1" and s["review_status"] == "unreviewed"


def test_segments_are_topic_based_not_fixed_size(store):
    sizes = Counter(r["n"] for r in q(store, "SELECT end_seq - start_seq + 1 AS n FROM segments"))
    assert len(sizes) > 5                                                  # varied lengths, not a fixed window
    assert len({r["topic_label"] for r in q(store, "SELECT DISTINCT topic_label FROM segments")}) >= 8


def test_dialogue_acts_never_turn_exploration_into_decisions():
    assert "decision" not in [a for a, _ in semantics.dialogue_acts("I'm thinking about five installments, but I haven't decided.", "Client")]
    assert "decision" not in [a for a, _ in semantics.dialogue_acts("Maybe we should go ahead and elect five.", "Client")]
    assert "decision" in [a for a, _ in semantics.dialogue_acts("I've decided on five installments starting at 63.", "Client")]
    assert "decision" not in [a for a, _ in semantics.dialogue_acts("My recommendation isn't your decision.", "Advisor")]
    assert "question" in [a for a, _ in semantics.dialogue_acts("Can you confirm it says 65?", "Client")]
    assert "correction" in [a for a, _ in semantics.dialogue_acts("I misspoke earlier, the figure was lower.", "Client")]


def test_generated_annotations_are_labelled_and_source_text_is_untouched(store):
    prov = {r["provenance"] for r in q(store, "SELECT DISTINCT provenance FROM annotations")}
    assert prov <= {"observed", "rule_generated"} and "model_generated" not in prov
    assert all(r["method"] for r in q(store, "SELECT method FROM annotations"))
    for r in q(store, "SELECT confidence FROM annotations WHERE provenance='rule_generated'"):
        assert r["confidence"] is not None and 0 < r["confidence"] <= 1


def test_corrections_are_separate_evidence_and_relations_state_their_basis(store):
    corrs = q(store, "SELECT target_id FROM annotations WHERE kind='dialogue_act' AND value='correction'")
    assert corrs
    for c in corrs:                                                         # a correction turn is its own span, never merged into the corrected one
        assert q(store, "SELECT count(*) n FROM evidence_spans WHERE turn_id=?", c["target_id"])[0]["n"] >= 1
    for r in q(store, "SELECT * FROM turn_relations"):
        assert r["basis"] and r["provenance"] in ("observed", "rule_generated")
        if r["rel_type"] == "corrects":
            assert r["provenance"] == "rule_generated" and "shared_token" in r["basis"] and r["from_turn_id"] != r["to_turn_id"]


def test_reply_relations_follow_email_headers_exactly(store):
    obs = q(store, "SELECT from_turn_id, to_turn_id FROM turn_relations WHERE basis='in_reply_to_header'")
    want = {(m["email_id"], next(x["email_id"] for x in rows("emails/emails.csv") if x["message_id"] == m["in_reply_to"] and x["thread_id"] == m["thread_id"]))
            for m in rows("emails/emails.csv") if m["in_reply_to"]}
    assert {(o["from_turn_id"], o["to_turn_id"]) for o in obs} == want


# ----------------------------------------------------------------------------------------------------------------- provenance
def test_every_span_reproduces_its_exact_source_text(store):
    for sp in q(store, "SELECT e.*, t.original_text FROM evidence_spans e JOIN turns t ON t.turn_id=e.turn_id"):
        assert sp["exact_text"] == sp["original_text"][sp["start_offset"]:sp["end_offset"]]
        assert sp["client_id"] and sp["container_id"] and sp["source_ref"] == sp["turn_id"]
    assert q(store, "SELECT count(*) n FROM turns")[0]["n"] == q(store, "SELECT count(*) n FROM evidence_spans WHERE span_kind='full_turn'")[0]["n"]


def test_no_invented_ids_or_timestamps(store):
    known = {r["email_id"] for r in rows("emails/emails.csv")} | {r["message_id"] for r in rows("chats/chats.csv")} | {r["turn_id"] for r in rows("meetings/meeting_turns.csv")}
    known |= {f"{r['interaction_id']}#T{r['turn_index']}" for r in csv.DictReader(open(ROOT / "data/conversations.csv", encoding="utf-8", newline=""))}
    assert {r["turn_id"] for r in q(store, "SELECT turn_id FROM turns")} == known
    stamps = {r["timestamp"] for r in rows("emails/emails.csv")} | {r["timestamp"] for r in rows("chats/chats.csv")} | {r["turn_timestamp"] for r in rows("meetings/meeting_turns.csv")}
    assert {r["ts"] for r in q(store, "SELECT DISTINCT ts FROM turns WHERE ts IS NOT NULL")} <= stamps


def test_source_references_trace_every_level_to_the_original_file(store):
    r = adv(store, "ADVISOR-001")
    t = r.source_references("INT-001#T3")
    assert t["source_file"] == "data/conversations.csv" and t["turn_source_ref"] == "INT-001#T3" and t["kind"] == "turn"
    assert r.source_references("EV-EML-046")["kind"] == "evidence_span"
    assert r.source_references("THR-010")["source_file"] == "datasets/emails/emails.csv"
    seg = r.segments("CLIENT-001")[0]
    s = r.source_references(seg["segment_id"])
    assert s["kind"] == "segment" and s["constituent_turns"] == seg["turn_ids"]
    with pytest.raises(NotFound):
        r.source_references("INT-999#T1")


# ----------------------------------------------------------------------------------------------------------- derived CRM
def test_crm_is_separate_derived_and_never_in_the_turn_tables(store):
    crm_ids = {x["crm_record_id"] for x in q(store, "SELECT crm_record_id FROM derived_artifacts")}
    assert len(crm_ids) == 166
    for table, col in (("turns", "turn_id"), ("containers", "container_id"), ("segments", "container_id"), ("evidence_spans", "evidence_id")):
        assert not crm_ids & {x[col] for x in q(store, f"SELECT {col} FROM {table}")}
    for d in q(store, "SELECT * FROM derived_artifacts"):
        assert d["derived_status"] == "derived" and d["is_original_source_evidence"] == 0
    assert {r["layer"] for r in q(store, "SELECT layer FROM source_artifacts WHERE artifact_id LIKE 'CRM-%'")} == {"derived"}
    assert q(store, "SELECT count(*) n FROM derived_sources WHERE resolution='unresolved'")[0]["n"] == 0


def test_derived_support_returns_record_and_original_sources_separately(store):
    s = adv(store).derived_support("CRM-EML-0011")
    assert s["derived"]["layer"] == "derived" and s["derived"]["original_text"]
    assert [c["container_id"] for c in s["original_sources"]] == ["THR-010"]
    assert all(t["original_text"] for t in s["original_sources"][0]["turns"]) and s["withheld_source_count"] == 0
    assert {d["crm_record_id"] for d in adv(store).derived_for_container("THR-010")} >= {"CRM-EML-0011"}


# ----------------------------------------------------------------------------------------------------------------- governance
def test_client_isolation_no_cross_client_retrieval(store):
    r = adv(store, "ADVISOR-001")                                           # assigned to CLIENT-001 and CLIENT-004
    assert r.containers_for_client("CLIENT-002") == [] and r.find_spans("CLIENT-002", text="portfolio") == []
    assert r.segments("CLIENT-002") == [] and r.timeline("CLIENT-002", include_derived=True) == []
    with pytest.raises(AccessDenied):
        r.turns_in_container("MTG-012")                                      # CLIENT-002's meeting
    with pytest.raises(AccessDenied):
        r.span_context("EV-MTG-012-T01")
    c = EvidenceRetriever(store, Requester.client("CLIENT-001"))
    assert c.containers_for_client("CLIENT-004") == []
    own = c.containers_for_client("CLIENT-001")
    assert own and {x["channel"] for x in own} == {"Meetings", "Email", "Chat"}      # Calls are advisor-held under the synthetic policy


def test_agents_hold_only_their_delegators_rights_and_restricted_records_block_agents(store):
    agent = EvidenceRetriever(store, Requester("AGENT-SYN-01", "agent", "ADVISOR-002"))
    with pytest.raises(AccessDenied):
        agent.turns_in_container("INT-121")                                  # record-level restriction
    assert agent.turns_in_container("INT-130")
    assert EvidenceRetriever(store, Requester("AGENT-SYN-01", "agent", "")).containers_for_client("CLIENT-002") == []
    wrong = EvidenceRetriever(store, Requester("AGENT-SYN-01", "agent", "ADVISOR-001"))
    with pytest.raises(AccessDenied):
        wrong.turns_in_container("INT-130")


def test_third_party_reads_only_what_an_active_grant_covers(store):
    ext = EvidenceRetriever(store, Requester("EXT-001", "external"), on=date(2026, 6, 12))
    assert ext.turns_in_container("THR-010")
    with pytest.raises(AccessDenied):
        ext.turns_in_container("CHT-021")                                    # chat does not honour grants
    with pytest.raises(AccessDenied):
        ext.derived_support("CRM-EML-0011")                                  # the grant on THR-010 does not open the CRM record about it
    assert ext.timeline("CLIENT-001", include_derived=True) and all(x["layer"] == "original" for x in ext.timeline("CLIENT-001", include_derived=True))
    after = EvidenceRetriever(store, Requester("EXT-002", "external"), on=date(2026, 7, 15))
    with pytest.raises(AccessDenied):
        after.turns_in_container("THR-014")                                  # expired


def test_crm_access_does_not_broaden_access_to_its_sources(store):
    # an agent for ADVISOR-002 may read the CRM Activity logged from MTG-012, but MTG-012 itself carries a record-level restriction that closes it to agents
    ctl = {r["record_id"]: r for r in rows("governance/source_control.csv")}
    target = [d["crm_record_id"] for d in q(store, "SELECT d.crm_record_id FROM derived_artifacts d JOIN derived_sources s ON s.crm_record_id=d.crm_record_id WHERE s.source_ref='MTG-012'")]
    assert target
    agent = EvidenceRetriever(store, Requester("AGENT-SYN-01", "agent", "ADVISOR-002"))
    found = [c for c in target if agent.check(c)[0]]
    assert found, "an agent-readable CRM record about the restricted meeting must exist for this test to bite"
    s = agent.derived_support(found[0])
    assert s["original_sources"] == [] and s["withheld_source_count"] == 1
    assert ctl["MTG-012"]["restriction_policy_id"] == "SYN-ACC-RESTRICTED"


def test_governance_fixtures_are_labelled_synthetic():
    for r in rows("governance/source_control.csv"):
        assert r["synthetic"] == "true" and "synthetic" in r["control_basis"]


# ------------------------------------------------------------------------------------------------ evaluation data exclusion
def test_evaluation_ground_truth_is_unreachable_from_ingestion():
    src = Sources()
    assert not [p for p in src.files if "evaluation" in {x.lower() for x in p.parts}]
    assert not {p.name for p in src.files} & {"scenarios.csv", "scenario_links.csv", "routine_records.csv", "access_scenarios.csv"}
    for py in (ROOT / "src" / "evidence").rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        assert not re.search(r"evaluation/|scenario_links|routine_records|access_scenarios|scenarios\.csv", text.replace("evaluation ground truth", "")), py.name


def test_nothing_in_the_store_names_a_scenario_or_routine_label(store):
    c = sqlite3.connect(store.path)
    dump = "\n".join(l for l in c.iterdump() if not l.startswith("INSERT INTO schema")).lower()
    c.close()
    assert not re.search(r"scn-c\d-\d\d|gov-\d\d|is_routine|\broutine\b", dump)


# --------------------------------------------------------------------------------------------------------------- reliability
def _dump(store):
    c = sqlite3.connect(store.path)
    out = {}
    for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'turns_fts%' AND name NOT IN ('ingest_runs','sqlite_sequence')"):
        cols = [r[1] for r in c.execute(f"PRAGMA table_info({t})") if r[1] not in ("ingested_at", "applied_at", "annotation_id")]
        out[t] = sorted(map(repr, c.execute(f"SELECT {','.join(cols)} FROM {t}")))
    c.close()
    return out


def test_ingestion_is_idempotent_and_adds_no_duplicates(store):
    before = _dump(store)
    rep = ingest(store)
    assert rep["totals"] == {"unchanged": 378} and _dump(store) == before
    assert q(store, "SELECT count(*) n FROM ingest_runs")[0]["n"] >= 2
    assert q(store, "SELECT max(revision) m FROM source_artifacts")[0]["m"] == 1


def test_parsing_is_deterministic(tmp_path, store):
    other = EvidenceStore(f"sqlite:///{(tmp_path / 'other.db').as_posix()}")
    ingest(other)
    assert _dump(other) == _dump(store)
    other.close()


def test_changed_source_or_parser_version_reprocesses_atomically(tmp_path):
    s = EvidenceStore(f"sqlite:///{(tmp_path / 'v.db').as_posix()}")
    s.upsert_parties(Sources().registry())
    k = next(c for c in parse_all(Sources())[0] if c.container_id == "THR-010")
    assert s.ingest_container(k) == "ingested" and s.ingest_container(k) == "unchanged"
    assert s.ingest_container(k, parser_version="evidence-parser-9.9") == "reprocessed"
    c = s.reader()
    assert c.execute("SELECT revision, parser_version FROM source_artifacts WHERE artifact_id='THR-010'").fetchone()[:] == (2, "evidence-parser-9.9")
    assert c.execute("SELECT count(*) FROM turns WHERE container_id='THR-010'").fetchone()[0] == 4
    assert c.execute("SELECT count(*) FROM evidence_spans WHERE container_id='THR-010'").fetchone()[0] == 4
    c.close()
    s.close()


def test_a_failing_container_rolls_back_alone_and_is_reported(tmp_path, monkeypatch):
    s = EvidenceStore(f"sqlite:///{(tmp_path / 'f.db').as_posix()}")
    real = s._insert

    def boom(c, k, *a):
        real(c, k, *a)
        if k.container_id == "THR-010":
            raise RuntimeError("simulated failure after partial write")
    monkeypatch.setattr(s, "_insert", boom)
    rep = ingest(s)
    assert rep["totals"]["failed"] == 1 and rep["totals"]["ingested"] == 377
    c = s.reader()
    assert c.execute("SELECT count(*) FROM turns WHERE container_id='THR-010'").fetchone()[0] == 0          # partial rows rolled back
    assert c.execute("SELECT status, error FROM source_artifacts WHERE artifact_id='THR-010'").fetchone()[0] == "failed"
    assert c.execute("SELECT status FROM ingest_runs ORDER BY run_id DESC LIMIT 1").fetchone()[0] == "completed_with_errors"
    c.close()
    monkeypatch.setattr(s, "_insert", real)
    assert ingest(s)["totals"] == {"reprocessed": 1, "unchanged": 377}
    c = s.reader()
    assert c.execute("SELECT count(*) FROM turns WHERE container_id='THR-010'").fetchone()[0] == 4          # retry recovers
    assert c.execute("SELECT count(*) FROM derived_sources WHERE resolution='unresolved'").fetchone()[0] == 0   # CRM links re-resolve once the source exists
    c.close()
    s.close()


def test_a_structurally_bad_source_row_is_a_reported_failure_not_a_guess(tmp_path):
    base = tmp_path / "ds"
    shutil.copytree(DS, base, ignore=shutil.ignore_patterns("evaluation", "__pycache__", "_generator"))
    shutil.copy(ROOT / "data" / "conversations.csv", base / "conversations.csv")
    p = base / "emails" / "email_threads.csv"
    rowsx = list(csv.DictReader(open(p, encoding="utf-8", newline="")))
    rowsx[0]["message_count"] = "99"
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rowsx[0].keys()))
        w.writeheader()
        w.writerows(rowsx)
    s = EvidenceStore(f"sqlite:///{(tmp_path / 'bad.db').as_posix()}")
    rep = ingest(s, Sources(base))
    assert rep["totals"]["failed"] == 1 and rep["failures"][0][0] == "THR-001" and "message_count" in rep["failures"][0][3]
    c = s.reader()
    assert c.execute("SELECT count(*) FROM containers WHERE channel='Email'").fetchone()[0] == 21
    c.close()
    s.close()


def test_evidence_database_is_separate_from_memory_and_ops_databases(store):
    names = {r["name"] for r in q(store, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert not names & {"memory_records", "memory_evidence", "memory_candidates", "approvals", "portfolio_accounts"}
    assert "build" not in str(store.path) or store.path.name == "cmf_evidence.db"


def test_reader_handle_cannot_write(store):
    c = store.reader()
    with pytest.raises(sqlite3.OperationalError):
        c.execute("DELETE FROM turns")
    c.close()


# --------------------------------------------------------------------------------------------------------------- retrieval quality
_STOP = set("the a an and or but of to in on for with is are was were be been it this that these those i you he she we they my your our his her not no yes so as at by from do does did have has had will would can could should may might just than then there here what when where which who how about into over under more most some any all if me us them".split())


def _query_from(text, n=6):
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z'\-]{3,}", text) if w.lower() not in _STOP]
    seen, out = set(), []
    for w in words[len(words) // 3:]:                         # words from the middle of the turn, not its opening
        if w.lower() not in seen:
            seen.add(w.lower()); out.append(w)
        if len(out) == n:
            break
    return " ".join(out)


def test_retrieval_effectiveness_on_queries_built_from_the_dataset(store):
    sample = q(store, "SELECT * FROM turns WHERE length(original_text) > 120 ORDER BY turn_id")[::9]
    assert len(sample) >= 100
    adv_of = {r["client_id"]: r["advisor_id"] for r in q(store, "SELECT DISTINCT client_id, advisor_id FROM containers")}
    ranks, spk_ok, src_ok, complete, total = [], 0, 0, 0, 0
    K = 5
    p_at_k = r_at_k = 0.0
    for t in sample:
        r = EvidenceRetriever(store, Requester.advisor(adv_of[t["client_id"]]))
        res = r.find_spans(t["client_id"], text=_query_from(t["original_text"]), limit=K)
        ids = [x["turn_id"] for x in res]
        rank = ids.index(t["turn_id"]) + 1 if t["turn_id"] in ids else 0
        ranks.append(rank)
        p_at_k += (1 / K) if rank else 0.0                    # one relevant turn per query: precision@K = hit/K
        r_at_k += 1 if rank else 0
        if rank:
            hit = res[rank - 1]
            total += 1
            spk_ok += hit["speaker_id"] == t["speaker_id"]
            src_ok += hit["container_id"] == t["container_id"] and hit["source_ref"] == t["source_ref"]
            ctx = r.span_context(hit["turn_id"])
            complete += len(ctx["container_turns"]) == q(store, "SELECT turn_count n FROM containers WHERE container_id=?", t["container_id"])[0]["n"]
        r.close()
    n = len(sample)
    mrr = sum(1 / x for x in ranks if x) / n
    recall = r_at_k / n
    print(f"\nretrieval: n={n} recall@{K}={recall:.3f} precision@{K}={p_at_k / n:.3f} mrr={mrr:.3f} speaker={spk_ok / total:.3f} source={src_ok / total:.3f} context={complete / total:.3f}")
    assert recall >= 0.9 and mrr >= 0.8
    assert spk_ok == total and src_ok == total and complete == total


def test_timeline_is_chronological_across_channels_for_every_client(store):
    for cid, aid in {r["client_id"]: r["advisor_id"] for r in q(store, "SELECT DISTINCT client_id, advisor_id FROM containers")}.items():
        tl = adv(store, aid).timeline(cid, include_derived=True)
        assert [x["ts"] for x in tl] == sorted(x["ts"] for x in tl)
        assert {x["channel"] for x in tl} >= {"Meetings", "Email", "Chat", "Calls"}
        assert {x["layer"] for x in tl} == {"original", "derived"}


def test_filters_by_speaker_date_topic_and_channel(store):
    r = adv(store, "ADVISOR-001")
    spans = r.find_spans("CLIENT-001", speaker_id="CLIENT-001", channels=["Email"], start="2026-06-01", end="2026-06-30")
    assert spans and all(s["speaker_id"] == "CLIENT-001" and s["channel"] == "Email" and "2026-06-01" <= s["when_ts"][:10] <= "2026-06-30" for s in spans)
    topic = next(iter(r.topics_for_client("CLIENT-001")))
    assert r.segments("CLIENT-001", topic=topic) and {s["topic_label"] for s in r.segments("CLIENT-001", topic=topic)} == {topic}
    byact = r.find_spans("CLIENT-001", dialogue_act="question", limit=5)
    assert byact and all("?" in s["exact_text"] for s in byact)


def test_scenario_links_resolve_to_ingested_containers_of_the_right_channel_and_client(store):
    """Evaluation files are read here, by the test only, to check the evidence store covers every storyline record."""
    links = rows("evaluation/scenario_links.csv")
    got = {r["container_id"]: r for r in q(store, "SELECT container_id, channel, client_id, started_at FROM containers")}
    for l in links:
        c = got[l["source_id"]]
        assert c["channel"] == l["channel"] and c["client_id"] == l["client_id"]
        if l["record_origin"] == "generated":
            assert c["started_at"] == l["source_timestamp"]
