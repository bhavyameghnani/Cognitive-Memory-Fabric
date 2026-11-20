"""Tests for the four-channel synthetic dataset (read-only; standard library only).

* the full validator runs (24 checks);
* evaluation ground truth (scenarios, scenario links, routine records, access scenarios) can never enter source ingestion;
* the SYNTHETIC source-control metadata behaves: missing permissions, unauthorized access, expired / revoked authorization
  and derived-record (CRM) access restrictions. None of this is a real vendor or regulatory policy.
"""
import copy
import csv
import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
DS = ROOT / "datasets"
EVAL_COLUMNS = {"scenario_id", "storyline_note", "arc_summary", "test_dimensions", "link_basis",
                "gap_category", "expected_decision", "expected_reason_code"}
EVAL_NAMES = ["scenarios.csv", "scenario_links.csv", "routine_records.csv", "access_scenarios.csv"]


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _rows(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def catalog():
    return _load("source_catalog", DS / "source_catalog.py")


@pytest.fixture(scope="module")
def gc():
    return _load("governance_check", DS / "governance_check.py")


@pytest.fixture()
def ctx(gc):
    return copy.deepcopy(gc.load(DS))      # a private copy: tests may mutate it


def test_multichannel_dataset_validation(capsys):
    mod = _load("validate_datasets", DS / "validate_datasets.py")
    failed = mod.main()
    out = capsys.readouterr().out
    assert failed == 0, out


# ------------------------------------------------------------------ evaluation exclusion
def test_discovery_never_returns_evaluation_files(catalog):
    for include_crm in (False, True):
        for include_gov in (False, True):
            found = catalog.discover_source_files(include_crm=include_crm, include_governance=include_gov)
            assert found, "source discovery returned nothing"
            assert not [p for p in found if catalog.is_evaluation_path(p)]
            assert not [p for p in found if Path(p).name in set(EVAL_NAMES)]


def test_governance_files_are_opt_in_and_never_evaluation(catalog):
    default = {Path(p).name for p in catalog.discover_source_files()}
    with_gov = {Path(p).name for p in catalog.discover_source_files(include_governance=True)}
    assert "source_control.csv" not in default and "source_control.csv" in with_gov
    assert not any(catalog.is_evaluation_path(DS / f) for f in catalog.GOVERNANCE_FILES)


@pytest.mark.parametrize("name", EVAL_NAMES)
def test_guard_fails_when_an_evaluation_file_is_added_to_the_ingestion_set(catalog, name):
    """Simulates the accident: the evaluation file is appended to the source-ingestion file list."""
    ingestion_set = catalog.discover_source_files(include_crm=True, include_governance=True) + [DS / "evaluation" / name]
    with pytest.raises(ValueError):
        catalog.assert_no_evaluation_data(ingestion_set)
    with pytest.raises(ValueError):           # also when it is copied out of evaluation/
        catalog.assert_no_evaluation_data([DS / "calls" / name])


def test_no_source_crm_or_governance_file_carries_evaluation_columns(catalog):
    for p in catalog.discover_source_files(include_crm=True, include_governance=True):
        with open(p, encoding="utf-8", newline="") as f:
            header = set(next(csv.reader(f)))
        assert not header & EVAL_COLUMNS, f"{Path(p).name} contains evaluation columns"


def test_evaluation_files_live_only_in_the_evaluation_folder():
    for name in EVAL_NAMES:
        assert (DS / "evaluation" / name).exists()
        assert not (DS / name).exists(), f"stale copy of {name} outside datasets/evaluation/"
        assert [p for p in DS.rglob(name) if "evaluation" not in p.parts] == []


def test_application_code_does_not_read_evaluation_files():
    pat = re.compile(r"scenario_links|datasets[/\\]evaluation|evaluation[/\\]scenarios|routine_records|access_scenarios")
    hits = []
    for base in ("src", "utils", "services"):
        d = ROOT / base
        if not d.exists():
            continue
        for p in d.rglob("*.py"):
            if pat.search(p.read_text(encoding="utf-8", errors="ignore")):
                hits.append(str(p.relative_to(ROOT)))
    assert hits == []


def test_manifest_covers_every_original_interaction_by_modality():
    with open(ROOT / "data" / "conversations.csv", encoding="utf-8", newline="") as f:
        modality = {}
        for r in csv.DictReader(f):
            modality[r["interaction_id"]] = r["channel"]
    man = {r["interaction_id"]: r for r in _rows(DS / "source_manifest.csv")}
    assert set(man) == set(modality) and len(man) == 150
    want = {"telephone": "Calls", "video": "Meetings", "in-person": "Meetings"}
    assert all(man[i]["assigned_channel"] == want[m] for i, m in modality.items())


# ------------------------------------------------------------------ routine communication
def test_routine_classification_lives_only_in_evaluation_metadata():
    routine = {r["record_id"] for r in _rows(DS / "evaluation/routine_records.csv")}
    assert len(routine) >= 12
    for name in ("emails/email_threads.csv", "chats/chat_conversations.csv", "crm/crm_email.csv", "crm/crm_chat.csv"):
        header = set(_rows(DS / name)[0])
        assert not {"routine", "is_routine", "record_class"} & header
    text = " ".join(r["subject"] + " " + r["body"] for r in _rows(DS / "emails/emails.csv") if r["thread_id"] in routine)
    text += " ".join(r["message_text"] for r in _rows(DS / "chats/chats.csv") if r["chat_conversation_id"] in routine)
    assert not re.search(r"\broutine\b|non-memory", text, re.I)
    assert {r["client_id"] for r in _rows(DS / "evaluation/routine_records.csv")} == {f"CLIENT-00{i}" for i in range(1, 7)}


# ------------------------------------------------------------------ source-control metadata: missing permissions
def test_every_record_has_a_control_row_and_policies(ctx):
    crm = {r["crm_record_id"] for k in ("meetings", "email", "calls", "chat") for r in _rows(DS / "crm" / f"crm_{k}.csv")}
    assert crm <= set(ctx["control"])
    for rid, row in ctx["control"].items():
        assert row["access_policy_id"] in ctx["access"] and row["retention_policy_id"] in ctx["retention"], rid
        assert "synthetic" in row["control_basis"]


def test_missing_control_row_is_denied(gc, ctx):
    del ctx["control"]["MTG-001"]
    assert gc.decide(ctx, "ADVISOR-001", "advisor", "", "MTG-001", "2026-06-20") == ("deny", "deny_no_control_row")


def test_policy_without_permissions_is_denied(gc, ctx):
    ctx["access"]["SYN-ACC-MTG"]["permitted_roles"] = ""
    assert gc.decide(ctx, "ADVISOR-001", "advisor", "", "MTG-001", "2026-06-20") == ("deny", "deny_no_permissions_defined")
    assert gc.decide(ctx, "CLIENT-004", "client", "", "MTG-001", "2026-06-20")[0] == "deny"


def test_unknown_principal_and_unknown_record_are_denied(gc, ctx):
    assert gc.decide(ctx, "CLIENT-999", "client", "", "MTG-001", "2026-06-20")[0] == "deny"
    assert gc.decide(ctx, "ADVISOR-001", "advisor", "", "MTG-999", "2026-06-20")[0] == "deny"


# ------------------------------------------------------------------ unauthorized access
def test_documented_scenarios_reproduce(gc, ctx):
    scn = _rows(DS / "evaluation/access_scenarios.csv")
    assert len(scn) >= 20
    for r in scn:
        got = gc.decide(ctx, r["requester_id"], r["requester_type"], r["acting_for_id"], r["target_record_id"], r["request_date"])
        assert got == (r["expected_decision"], r["expected_reason_code"]), r["scenario_id"]
    cats = {r["gap_category"] for r in scn}
    assert {"authorized_external_sharing", "unauthorized_cross_client_disclosure", "advisor_outside_assignment",
            "agent_restricted_retrieval", "derived_crm_stricter_than_source", "revoked_or_expired_authorization"} <= cats


def test_only_the_client_or_assigned_advisor_can_read_any_record_without_a_grant(gc, ctx):
    """Exhaustive: for every record and every client/advisor, allow implies client scope or advisor assignment."""
    people = [(p, v["role"]) for p, v in ctx["principals"].items() if v["role"] in ("client", "advisor")]
    allowed = 0
    for rid, row in ctx["control"].items():
        for pid, role in people:
            d, _ = gc.decide(ctx, pid, role, "", rid, "2026-06-30")
            if d == "allow":
                allowed += 1
                if role == "client":
                    assert pid == row["client_scope"], (pid, rid)
                else:
                    assert row["client_scope"] in ctx["principals"][pid]["client_scope"].split("|"), (pid, rid)
    assert allowed > 0


def test_agent_never_exceeds_its_delegator(gc, ctx):
    for rid, row in ctx["control"].items():
        for adv in [p for p, v in ctx["principals"].items() if v["role"] == "advisor"]:
            agent = gc.decide(ctx, "AGENT-SYN-01", "agent", adv, rid, "2026-06-30")
            human = gc.decide(ctx, adv, "advisor", "", rid, "2026-06-30")
            assert not (agent[0] == "allow" and human[0] == "deny"), (adv, rid)
    restricted = [r for r, v in ctx["control"].items() if v["restriction_policy_id"]]
    assert restricted
    for rid in restricted:
        adv = ctx["control"][rid]["responsible_advisor_id"]
        assert gc.decide(ctx, adv, "advisor", "", rid, "2026-06-30")[0] == "allow"
        assert gc.decide(ctx, "AGENT-SYN-01", "agent", adv, rid, "2026-06-30") == ("deny", "deny_agent_blocked")


def test_a_restriction_can_only_narrow_access(gc, ctx):
    for rid, row in ctx["control"].items():
        if row["restriction_policy_id"]:
            base = set(ctx["access"][row["access_policy_id"]]["permitted_roles"].split("|"))
            assert gc.effective(ctx, row)["roles"] <= base, rid


# ------------------------------------------------------------------ expired / revoked authorization
def test_expired_and_revoked_authorization(gc, ctx):
    g = next(x for x in ctx["grants"] if x["grant_id"] == "GRT-002")
    rec, who = g["record_id"], g["principal_id"]
    day = lambda d: gc.decide(ctx, who, "external", "", rec, d)
    assert day(g["valid_until"])[0] == "allow"
    assert day("2026-07-01") == ("deny", "deny_grant_expired")
    assert day(g["valid_from"])[0] == "allow"
    assert day("2026-06-10") == ("deny", "deny_grant_not_yet_valid")
    rv = next(x for x in ctx["grants"] if x["status"] == "revoked")
    before, after = "2026-06-15", rv["revoked_at"]
    assert gc.decide(ctx, rv["principal_id"], "external", "", rv["record_id"], before)[0] == "allow"
    assert gc.decide(ctx, rv["principal_id"], "external", "", rv["record_id"], after) == ("deny", "deny_grant_revoked")


def test_grant_is_exact_record_and_client_scoped(gc, ctx):
    g = next(x for x in ctx["grants"] if x["grant_id"] == "GRT-001")
    assert gc.decide(ctx, g["principal_id"], "external", "", "THR-014", "2026-06-15")[0] == "deny"          # another client's thread
    assert gc.decide(ctx, g["principal_id"], "external", "", "CHT-021", "2026-06-20")[0] == "deny"          # same client, other record
    del ctx["grants"][:]
    assert gc.decide(ctx, g["principal_id"], "external", "", g["record_id"], "2026-06-15") == ("deny", "deny_no_grant")


# ------------------------------------------------------------------ derived CRM records
def test_crm_records_never_widen_access_and_some_are_stricter(gc, ctx):
    narrower = 0
    for k in ("meetings", "email", "calls", "chat"):
        for r in _rows(DS / "crm" / f"crm_{k}.csv"):
            crm_r = gc.readers(ctx, r["crm_record_id"], "2026-06-30")
            assert ctx["control"][r["crm_record_id"]]["access_inheritance"].startswith("none")
            for s in r["source_refs"].split("|"):
                src_r = gc.readers(ctx, s, "2026-06-30")
                assert crm_r <= src_r, (r["crm_record_id"], s)
                narrower += crm_r < src_r
    assert narrower > 0


def test_source_grant_does_not_open_the_derived_crm_record(gc, ctx):
    g = next(x for x in ctx["grants"] if x["grant_id"] == "GRT-001")
    crm = [r["crm_record_id"] for r in _rows(DS / "crm/crm_email.csv") if g["record_id"] in r["source_refs"].split("|")]
    assert crm
    for rid in crm:
        assert gc.decide(ctx, g["principal_id"], "external", "", rid, "2026-06-12")[0] == "deny"
        assert g["principal_id"] not in gc.readers(ctx, rid, "2026-06-12")


def test_the_widening_check_can_fail(gc, ctx):
    """Mutation: a CRM record under the broader email policy over an advisor-only source is wider than its source."""
    rid, src = "CRM-EML-0011", "THR-010"
    assert gc.readers(ctx, rid, "2026-06-30") <= gc.readers(ctx, src, "2026-06-30")      # true in the real data
    ctx["control"][rid]["access_policy_id"] = "SYN-ACC-EML"       # derived record now readable by the client
    ctx["control"][src]["access_policy_id"] = "SYN-ACC-CAL"       # source is advisor-only
    assert not gc.readers(ctx, rid, "2026-06-30") <= gc.readers(ctx, src, "2026-06-30")


def test_retention_policies_differ_by_channel_and_crm_has_its_own(ctx):
    src_pol = {r["retention_policy_id"] for r in ctx["control"].values() if r["record_layer"] == "original_evidence"}
    crm_pol = {r["retention_policy_id"] for r in ctx["control"].values() if r["record_layer"] == "crm_derived"}
    assert len(src_pol) == 4 and crm_pol and not src_pol & crm_pol
    days = {k: int(v["review_after_days"]) for k, v in ctx["retention"].items()}
    assert len(set(days.values())) == len(days)
    assert all("synthetic" in v["assumption"] for v in ctx["retention"].values())


# ------------------------------------------------------------------ attribution status
def test_registry_distinguishes_established_inferred_mentioned_and_external(ctx):
    reg = ctx["principals"]
    assert all(v["attribution_confidence"] in ("high", "medium", "low") for v in reg.values())
    for pid, v in reg.items():
        if pid.startswith(("CLIENT-", "ADVISOR-")):
            assert v["id_status"] == "established_in_csv" and "inferred" in v["name_status"]
        else:
            assert v["id_status"] == "registry_assigned"
        if v["role_status"] == "role_inferred_from_source":
            assert v["attribution_confidence"] != "high", pid
    assert {v["person_category"] for v in reg.values()} == {"client", "advisor", "external_correspondent", "mentioned_only"}
    assert reg["MENT-007"]["role_status"] == "role_stated_in_source"         # Mei Lin Chu: 'my accountant, Mei Lin Chu'
    assert reg["MENT-001"]["role_status"] == "role_inferred_from_source"     # Mariko: relation never stated outright
