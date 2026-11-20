"""Tests for the semantic-annotation evaluation artefacts (preparation phase).  They check the artefacts and the scorer, NOT the accuracy of the
rules: there is no human ground truth yet.  They also check that building the package mutates no source, database or rule file."""
import csv
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "datasets"))
import semantic_eval as SE  # noqa: E402
import inspect_evidence as IE  # noqa: E402

DB = ROOT / "build" / "cmf_evidence.db"
PKG = ROOT / "evaluation" / "semantic_annotation"
GENERATED = ["items.jsonl", "segmentation_items.jsonl", "annotation_template.csv", "segmentation_template.csv", "review_sheet_blind.md", "labels.json",
             "label_definitions.md", "known_defects.json", "known_defects.md", "diagnostics.json", "sample_manifest.json"]
pytestmark = pytest.mark.skipif(not DB.exists(), reason="build/cmf_evidence.db not present")


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def protected():
    files = [DB, ROOT / "data" / "conversations.csv", ROOT / "datasets" / "source_manifest.csv"]
    for sub in ("emails", "chats", "meetings", "calls", "crm", "governance", "evaluation"):
        files += sorted((ROOT / "datasets" / sub).glob("*.csv"))
    files += sorted((ROOT / "src" / "evidence").rglob("*.py")) + [ROOT / "sql" / "200_cmf_evidence_schema.sql"]
    files += [ROOT / "data" / "seed" / "cmf_memory_demo.db", ROOT / "build" / "cmf_memory.db", ROOT / "build" / "cmf_approvals.db"]
    return {str(p.relative_to(ROOT)): sha(p) for p in files if p.exists()}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    before = protected()
    out = tmp_path_factory.mktemp("sem")
    c = IE.connect(DB)
    res = SE.write_all(out, c, IE.Source())
    c.close()
    out2 = tmp_path_factory.mktemp("sem2")
    c = IE.connect(DB)
    SE.write_all(out2, c, IE.Source())
    c.close()
    return dict(out=out, out2=out2, before=before, after=protected(), items=res[0], seg=res[1], kd=res[2], diag=res[3], comp=res[4])


def rows(p):
    return list(csv.DictReader(open(p, encoding="utf-8", newline="")))


# ------------------------------------------------------------------------------------------------------------- safety
def test_building_mutates_no_database_source_rule_or_schema_file(built):
    assert built["before"] == built["after"]
    assert len(built["before"]) >= 35 and any(k.endswith("cmf_evidence.db") for k in built["before"]) and any(k.endswith("conversations.csv") for k in built["before"])


def test_database_handle_is_read_only():
    c = IE.connect(DB)
    import sqlite3
    with pytest.raises(sqlite3.OperationalError):
        c.execute("DELETE FROM turns")
    c.close()


def test_output_is_deterministic(built):
    for name in GENERATED:
        assert (built["out"] / name).read_bytes() == (built["out2"] / name).read_bytes(), name


def test_committed_package_is_not_stale(built):
    for name in GENERATED:
        assert (PKG / name).read_bytes() == (built["out"] / name).read_bytes(), f"{name} differs from a fresh build: run scripts/semantic_eval.py build"


def test_package_is_not_dataset_ground_truth_nor_reachable_by_ingestion():
    import source_catalog
    assert not [p for p in source_catalog.discover_source_files(include_crm=True, include_governance=True) if "semantic_annotation" in str(p)]
    for py in (ROOT / "src" / "evidence").rglob("*.py"):
        assert "semantic_annotation" not in py.read_text(encoding="utf-8") and "semantic_eval" not in py.read_text(encoding="utf-8")
    assert not (ROOT / "datasets" / "evaluation" / "items.jsonl").exists()


# ------------------------------------------------------------------------------------------------------------- sample
def test_sample_covers_every_channel_and_meets_every_stratum_quota(built):
    comp = built["comp"]
    assert set(comp["by_channel"]) == set(SE.CHANNELS) and all(v >= 20 for v in comp["by_channel"].values())
    short = {k: (comp["by_stratum"][k], q) for k, q in comp["stratum_quota"].items() if comp["by_stratum"][k] < q}
    assert not short, short
    assert set(comp["seg_by_channel"]) == set(SE.CHANNELS) and all(v >= 5 for v in comp["seg_by_channel"].values())


def test_every_stratum_requested_by_the_brief_exists(built):
    need = {"decision_label_client", "decision_label_advisor_recap", "correction_label", "corrects_relation_source", "explicit_reference_to_earlier", "question_label",
            "request_unlabelled", "answers_relation_source", "agreement_label", "rejection_label", "preference_statement", "exploratory", "temporal_label",
            "multi_topic_segment", "fragmented_context", "information_only_control"}
    assert need <= set(built["comp"]["stratum_quota"])
    assert all(built["comp"]["by_stratum"][n] > 0 for n in need)


def test_items_expose_source_parsed_provenance_labels_relations_and_context(built):
    for it in built["items"]:
        for k in ("source_text", "parsed_text", "channel", "container_id", "speaker_id", "attribution_status", "attribution_confidence"):
            assert k in it and it[k] is not None or k == "attribution_confidence"
        assert it["source_matches_parsed"] is True and it["source_text"] == it["parsed_text"]
        sy = it["system"]
        assert {"topic_segment", "dialogue_acts", "cues", "relations_out", "relations_in"} <= set(sy)
        assert it["context_before"] is not None and it["context_after"] is not None
        if it["seq"] > 1:
            assert it["context_before"], it["item_id"]
    ids = [i["item_id"] for i in built["items"]]
    assert ids == sorted(ids) and len(set(ids)) == len(ids)


def test_source_text_is_read_from_the_source_files_not_the_database(built):
    conv = {f"{r['interaction_id']}#T{r['turn_index']}": r["utterance"] for r in csv.DictReader(open(ROOT / "data" / "conversations.csv", encoding="utf-8", newline=""))}
    emails = {r["email_id"]: r["body"] for r in csv.DictReader(open(ROOT / "datasets" / "emails" / "emails.csv", encoding="utf-8", newline=""))}
    seen = 0
    for it in built["items"]:
        want = conv.get(it["turn_id"]) or emails.get(it["turn_id"])
        if want is not None:
            assert it["source_text"] == want
            seen += 1
    assert seen > 40


def test_selection_is_stratified_not_dominated_by_one_channel_or_label(built):
    ch = built["comp"]["by_channel"]
    assert max(ch.values()) / sum(ch.values()) < 0.45
    ctrl = [i for i in built["items"] if "information_only_control" in i["strata"]]
    assert ctrl and all([a["act"] for a in i["system"]["dialogue_acts"]] == ["information"] for i in ctrl)   # unlabelled controls exist, so recall can be measured


# ------------------------------------------------------------------------------------------------------------- template / blindness
def test_template_is_blank_separate_and_matches_items(built):
    t = rows(built["out"] / "annotation_template.csv")
    assert list(t[0].keys()) == SE.TEMPLATE_COLS and [r["item_id"] for r in t] == [i["item_id"] for i in built["items"]]
    for r in t:
        assert all(v == "" for k, v in r.items() if k != "item_id")                  # no expected annotation is pre-filled, and none copies a system label
    s = rows(built["out"] / "segmentation_template.csv")
    assert list(s[0].keys()) == SE.SEG_TEMPLATE_COLS and all(v == "" for r in s for k, v in r.items() if k != "container_id")


def test_blind_sheet_hides_system_labels_strata_and_defect_hints(built):
    text = (built["out"] / "review_sheet_blind.md").read_text(encoding="utf-8")
    assert not re.search(r"dialogue_act|rule_generated|rule_v1|keyword_lexicon|SEG-|KD-\d\d|known_defect|information\[|confidence=|strata|stratum", text)
    for name in built["comp"]["stratum_quota"]:
        assert name not in text
    for it in built["items"][:10]:
        assert it["item_id"] in text and it["parsed_text"].splitlines()[0] in text


def test_labels_are_defined_and_cover_every_system_act(built):
    lab = json.loads((built["out"] / "labels.json").read_text(encoding="utf-8"))
    md = (built["out"] / "label_definitions.md").read_text(encoding="utf-8")
    assert set(SE.SYS_ACT_MAP.values()) <= set(lab["dialogue_acts"]) and {"request", "preference", "exploration", "answer", "unresolved"} <= set(lab["dialogue_acts"])
    assert {"reported_by_other", "confirmed", "recommendation_only"} <= set(lab["decision_status"])
    assert {"explicit_reference", "none"} <= set(lab["correction_evidence"])
    for field, vals in lab.items():
        for k in vals:
            assert f"**{k}**" in md
    from evidence import semantics
    assert set(semantics.TOPICS) <= set(lab["topics"])
    assert all(isinstance(v, str) and len(v) > 15 for v in lab["dialogue_acts"].values())


# ------------------------------------------------------------------------------------------------------------- known defects
def test_every_known_defect_is_reproduced_from_the_database_with_text_and_interpretation(built):
    kd = built["kd"]
    assert [k["kd_id"] for k in kd] == [f"KD-{n:02d}" for n in range(1, 14)]
    for k in kd:
        assert k["cases"] and k["reproduced_all"], (k["kd_id"], [c["turn_id"] for c in k["cases"] if not c["reproduced"]])
        for c in k["cases"]:
            assert c["text"] and c["system"]
    md = (built["out"] / "known_defects.md").read_text(encoding="utf-8")
    assert "not ground truth" in md


@pytest.mark.parametrize("kd_id,turn", [("KD-01", "INT-023#T2"), ("KD-02", "EML-022"), ("KD-03", "CHM-0011"), ("KD-04", "INT-001#T1"), ("KD-05", "INT-033#T12"),
                                         ("KD-06", "EML-017"), ("KD-07", "EML-001"), ("KD-08", "EML-051"), ("KD-10", "INT-003#T11")])
def test_each_defect_named_in_the_brief_is_present(built, kd_id, turn):
    k = next(k for k in built["kd"] if k["kd_id"] == kd_id)
    assert turn in {c["turn_id"] for c in k["cases"]}
    assert any(kd_id in i["known_defect_ids"] and i["turn_id"] == turn for i in built["items"])        # and the turn is in the annotation sample


def test_defect_groups_for_will_may_portal_have_cases(built):
    by = {k["kd_id"]: k for k in built["kd"]}
    kinds = " ".join(c["system"] for c in by["KD-08"]["cases"])
    assert "temporal_ref" in kinds and "topic cue scores" in kinds
    assert by["KD-09"]["cases"] and "scheduling_and_admin" in by["KD-09"]["cases"][0]["system"]


def test_date_check_flags_email_corrections_without_treating_it_as_ground_truth(built):
    rows_ = built["diag"]["corrects_date_check"]["rows"]
    wrong = {r["turn_id"] for r in rows_ if r["verdict"] == "system target DISAGREES"}
    assert {"EML-020", "EML-039", "EML-015"} <= wrong
    assert any(r["verdict"] == "system target agrees" for r in rows_)
    assert "not ground truth" in built["diag"]["corrects_date_check"]["note"]


def test_diagnostics_match_direct_database_counts(built):
    c = IE.connect(DB)
    n_corr = c.execute("SELECT count(*) FROM turn_relations WHERE rel_type='corrects'").fetchone()[0]
    n_ans = c.execute("SELECT count(*) FROM turn_relations WHERE rel_type='answers'").fetchone()[0]
    n_seg = c.execute("SELECT count(*) FROM segments").fetchone()[0]
    c.close()
    d = built["diag"]
    assert d["corrects_summary"]["total"] == n_corr and d["answers_cross_segment"]["total"] == n_ans and d["segments"]["total"] == n_seg
    assert d["segments"]["unreviewed"] == n_seg


# ------------------------------------------------------------------------------------------------------------- scorer (exact arithmetic)
def item(i, acts, topic="tax", rel=(), temporal=(), speaker="ADVISOR-001"):
    return dict(item_id=i, speaker_id=speaker, system=dict(
        dialogue_acts=[dict(act=a, confidence=0.5) for a in acts], topic_segment=dict(topic=topic), cues=dict(temporal_ref=list(temporal)) if temporal else {},
        relations_out=[dict(rel_type=r, to=t, basis="x", provenance="rule_generated") for r, t in rel], relations_in=[]))


def gold(i, **kw):
    r = {c: "" for c in SE.TEMPLATE_COLS}
    r.update(item_id=i, **kw)
    return r


def test_scorer_dialogue_act_prf_exact():
    items = {"a": item("a", ["agreement"]), "b": item("b", ["agreement", "question"]), "c": item("c", ["information"])}
    g = [gold("a", dialogue_acts="agreement"), gold("b", dialogue_acts="question"), gold("c", dialogue_acts="fact|request")]
    r = SE.score(items, g)["dialogue_acts"]
    assert r["per_class"]["agreement"]["tp"] == 1 and r["per_class"]["agreement"]["fp"] == 1 and r["per_class"]["agreement"]["precision"] == 0.5
    assert r["per_class"]["question"]["tp"] == 1 and r["per_class"]["question"]["fp"] == 0
    assert r["per_class"]["fact"]["tp"] == 1 and r["per_class"]["request"]["fn"] == 1 and r["per_class"]["request"]["recall"] == 0.0
    assert "request" in r["unsupported_by_system"]
    assert r["micro"]["tp"] == 3 and r["micro"]["fp"] == 1 and r["micro"]["fn"] == 1


def test_scorer_excludes_unresolved_and_blank_rows():
    items = {"a": item("a", ["agreement"]), "b": item("b", ["agreement"])}
    r = SE.score(items, [gold("a", dialogue_acts="unresolved"), gold("b")])["dialogue_acts"]
    assert r["items"] == 0 and r["unresolved_excluded"] == 1 and r["micro"]["tp"] == 0


def test_scorer_decision_attribution_separates_recap_exploration_and_correct():
    items = {"c": item("c", ["decision"], speaker="CLIENT-001"), "r": item("r", ["decision"], speaker="ADVISOR-001"), "e": item("e", ["decision"], speaker="CLIENT-001"),
             "m": item("m", ["information"], speaker="CLIENT-001")}
    g = [gold("c", decision_maker="client", decision_status="confirmed"), gold("r", decision_maker="client", decision_status="reported_by_other"),
         gold("e", decision_maker="client", decision_status="exploratory"), gold("m", decision_maker="client", decision_status="confirmed")]
    d = SE.score(items, g)["decision_attribution"]
    assert d["counts"]["correct_attribution"] == 1 and d["counts"]["recap_read_as_decision"] == 1 and d["counts"]["not_a_confirmed_decision"] == 1
    assert d["attribution_precision"] == pytest.approx(1 / 3)
    assert d["counts"]["gold_first_hand_decisions"] == 2 and d["recall"] == 0.5


def test_scorer_correction_requires_explicit_evidence_and_scores_targets():
    items = {"a": item("a", ["correction"], rel=[("corrects", "T1")]), "b": item("b", ["correction"], rel=[("corrects", "T9")]), "c": item("c", ["correction"], rel=[("corrects", "T5")]),
             "d": item("d", ["information"])}
    g = [gold("a", correction_target_turn="T1", correction_evidence="explicit_reference"), gold("b", correction_target_turn="T2", correction_evidence="explicit_reference"),
         gold("c", correction_target_turn="none"), gold("d", correction_target_turn="T3", correction_evidence="none"),
         gold("d", correction_target_turn="T3", correction_evidence="explicit_reference")]
    r = SE.score(items, g)["corrects_relations"]
    assert r["counts"]["invalid_gold_target_without_evidence"] == 1                    # a target without evidence is refused, not scored
    assert r["relation_precision"] == pytest.approx(2 / 3) and r["target_accuracy"] == 0.5
    assert r["counts"]["gold_links"] == 3 and r["recall"] == pytest.approx(1 / 3)


def test_scorer_answers_temporal_and_topics():
    items = {"a": item("a", ["question"], rel=[("answers", "Q1")], temporal=["May", "Friday"], topic="estate_and_trusts")}
    r = SE.score(items, [gold("a", answers_turn="Q1", temporal_refs="Friday", topics="tax")])
    assert r["answers_relations"]["target_accuracy"] == 1.0
    assert r["temporal"]["exact"]["tp"] == 1 and r["temporal"]["exact"]["fp"] == 1 and r["temporal"]["exact"]["precision"] == 0.5
    assert r["topics"]["micro"]["fp"] == 1 and r["topics"]["micro"]["fn"] == 1 and r["topics"]["per_label"]["tax"]["fn"] == 1
    none = SE.score(items, [gold("a", temporal_refs="none")])["temporal"]
    assert none["exact"]["fp"] == 2 and none["exact"]["tp"] == 0


def test_scorer_segmentation_boundaries_and_related_group_preservation():
    turns = [dict(turn_id=f"T{i}", seq=i, speaker_name="x", text="t") for i in range(1, 7)]
    seg = [dict(container_id="C", system_boundary_turns=["T3", "T5"], turns=turns,
                system_segments=[dict(segment_id="S1", turn_ids=["T1", "T2"]), dict(segment_id="S2", turn_ids=["T3", "T4"]), dict(segment_id="S3", turn_ids=["T5", "T6"])])]
    g = [dict(container_id="C", boundary_turns="T3|T6", segment_topics="", related_groups="T2,T3;T3,T4", notes="")]
    r = SE.score_segmentation(seg, g)
    assert r["boundary_exact"]["tp"] == 1 and r["boundary_exact"]["fp"] == 1 and r["boundary_exact"]["fn"] == 1
    assert r["boundary_within_1_turn"]["tp"] == 2 and r["boundary_within_1_turn"]["fp"] == 0
    assert r["related_groups"] == 2 and r["related_group_preservation"] == 0.5            # T2/T3 split across segments, T3/T4 kept together
    assert r["over_split_ratio"] == pytest.approx(3 / 3)


def test_scorer_runs_on_a_filled_copy_of_the_real_template(built, tmp_path):
    """Smoke: annotate every item with a deliberately simple (non-authoritative) answer and make sure the scorer runs end to end."""
    items = {i["item_id"]: i for i in built["items"]}
    filled = tmp_path / "filled.csv"
    with open(filled, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=SE.TEMPLATE_COLS)
        w.writeheader()
        for iid in items:
            w.writerow(gold(iid, dialogue_acts="fact", topics="none", temporal_refs="none"))
    out = SE.score(items, SE.load_gold(filled))
    assert out["annotated_rows"] == len(items) and out["dialogue_acts"]["items"] == len(items)
    r = subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "semantic_eval.py"), "score", "--items", str(PKG / "items.jsonl"), "--gold", str(filled)],
                       capture_output=True, text=True, cwd=ROOT)
    assert r.returncode == 0 and json.loads(r.stdout)["annotated_rows"] == len(items)
