"""M0: the test environment is reproducible and isolated (tests/conftest.py, .gitattributes).

These tests check the isolation machinery and the line-ending policy.  They say nothing about the correctness of any memory or parsing behaviour.  Nothing here reads, copies or writes
a seed, demo or developer database."""
import csv
import hashlib
import os
import sqlite3
import subprocess
from pathlib import Path

import pytest

import conftest as CF

ROOT = Path(__file__).resolve().parents[1]
DB_KEYS = ("CMF_MEMORY_DATABASE_URL", "CMF_DATABASE_URL", "CMF_EVIDENCE_DATABASE_URL", "CMF_APPROVAL_DATABASE_URL")


def path_of(url: str) -> Path:
    assert url.startswith("sqlite:///"), url
    return Path(url[len("sqlite:///"):])


def under(p: Path, parent: Path) -> bool:
    try:
        p.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


# ------------------------------------------------------------------------------------------------------------------ database isolation
@pytest.mark.parametrize("key", DB_KEYS)
def test_every_database_url_points_into_a_disposable_directory(key, isolated_session_dir, tmp_path):
    p = path_of(os.environ[key])
    assert under(p, isolated_session_dir) or under(p, tmp_path.parent)                        # the session directory (or this test's own tmp tree for approvals)
    for protected in (ROOT / "build", ROOT / "data" / "seed", ROOT / "data"):
        assert not under(p, protected), f"{key} points into {protected}"


def test_the_operational_database_is_built_from_the_committed_csvs_not_copied(isolated_session_dir):
    p = path_of(os.environ["CMF_DATABASE_URL"])
    assert p.exists() and under(p, isolated_session_dir)
    c = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True)
    try:
        with open(ROOT / "data" / "operational" / "client_financial_profiles.csv", newline="", encoding="utf-8") as f:
            expected = sum(1 for _ in csv.DictReader(f))
        assert c.execute("SELECT COUNT(*) FROM client_financial_profiles").fetchone()[0] == expected > 0
        assert c.execute("PRAGMA foreign_key_check").fetchall() == []
    finally:
        c.close()
    # A separately built file, not the demo file itself.  Its bytes MAY equal data/seed/cmf_ops_demo.db: the loader is deterministic, so on some platforms (macOS) a fresh build
    # from the same CSVs is byte-identical to the shipped seed.  Content equality is not evidence of copying; the checks above (built here, from the CSVs) are what matter.
    assert p.resolve() != (ROOT / "data" / "seed" / "cmf_ops_demo.db").resolve()


def test_the_application_reads_the_isolated_operational_database():
    from cmf_data.backend import Database
    db = Database()
    assert Path(db.url[len("sqlite:///"):]) == path_of(os.environ["CMF_DATABASE_URL"])
    assert db.query("SELECT COUNT(*) AS n FROM client_financial_profiles")[0]["n"] > 0
    with pytest.raises(sqlite3.DatabaseError):                                               # the handle is read-only
        db._conn.execute("DELETE FROM client_financial_profiles")


def test_a_default_memory_store_never_touches_build(isolated_session_dir):
    from agents.memory.config import database_url
    from agents.memory.store import MemoryStore
    assert database_url() == os.environ["CMF_MEMORY_DATABASE_URL"]
    before = CF._protected_state()
    s = MemoryStore()
    try:
        s.init()
        assert under(Path(s.url[len("sqlite:///"):]), isolated_session_dir)
    finally:
        s.close()
    assert CF._protected_state() == before


def test_a_default_evidence_store_never_touches_build(isolated_session_dir):
    from evidence.store import EvidenceStore
    before = CF._protected_state()
    s = EvidenceStore()
    try:
        assert under(s.path, isolated_session_dir)
    finally:
        s.close()
    assert CF._protected_state() == before


def test_the_evidence_database_fixture_builds_into_the_session_directory(evidence_db, isolated_session_dir):
    assert under(evidence_db, isolated_session_dir) and evidence_db.exists()
    c = sqlite3.connect(f"file:{evidence_db.as_posix()}?mode=ro", uri=True)
    try:
        assert c.execute("SELECT COUNT(*) FROM containers").fetchone()[0] == 212
        assert c.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 2548
    finally:
        c.close()


def test_a_1_a_test_may_delete_an_environment_variable():
    os.environ.pop("CMF_MEMORY_DATABASE_URL", None)                                          # what test_memory_retrieval does in a `finally`


def test_a_2_the_next_test_starts_from_the_isolated_value_again(isolated_session_dir):
    assert under(path_of(os.environ["CMF_MEMORY_DATABASE_URL"]), isolated_session_dir)


def test_the_guard_detects_a_change_in_a_protected_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(CF, "ROOT", tmp_path)
    (tmp_path / "build").mkdir()
    (tmp_path / "data" / "seed").mkdir(parents=True)
    (tmp_path / "data" / "seed" / "demo.db").write_bytes(b"seed")
    (tmp_path / "build" / "a.db").write_bytes(b"a")
    start = CF._protected_state()
    assert CF._protected_state() == start
    (tmp_path / "build" / "new.db").write_bytes(b"x")                                          # a created file
    assert CF._protected_state() != start
    (tmp_path / "build" / "new.db").unlink()
    assert CF._protected_state() == start
    (tmp_path / "data" / "seed" / "demo.db").write_bytes(b"changed")                           # a modified seed
    assert CF._protected_state() != start


def test_the_loader_target_must_be_a_fresh_session_path(tmp_path, monkeypatch):
    """The operational loader deletes an existing file at its target; the conftest builder refuses any target outside the session directory or already present."""
    monkeypatch.setitem(CF.ISOLATED_ENV, "CMF_DATABASE_URL", f"sqlite:///{(ROOT / 'build' / 'cmf_ops_smoke.db').as_posix()}")
    with pytest.raises(AssertionError, match="refusing to build"):
        CF._build_ops_database()
    monkeypatch.setitem(CF.ISOLATED_ENV, "CMF_DATABASE_URL", os.environ["CMF_DATABASE_URL"])
    with pytest.raises(AssertionError, match="refusing to build"):                              # already exists
        CF._build_ops_database()


def _sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


# ------------------------------------------------------------------------------------------------------------------ line endings
def test_gitattributes_pins_the_byte_exact_inputs_and_protects_databases():
    text = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    for needle in ("*.csv    text eol=lf", "*.sql    text eol=lf", "*.db       binary", "evaluation/**/*.md    text eol=lf", "datasets/**/*.sha256  text eol=lf"):
        assert needle in text, needle
    assert "* text" not in text.replace("*.csv", "").replace("*.jsonl", "").replace("*.sql", "")        # no global rewrite of every file


def test_data_files_are_lf_and_email_bodies_carry_no_carriage_return():
    assert b"\r" not in (ROOT / "data" / "conversations.csv").read_bytes()
    assert b"\r" not in (ROOT / "datasets" / "calls" / "conversations.sha256").read_bytes()
    with open(ROOT / "datasets" / "emails" / "emails.csv", newline="", encoding="utf-8") as f:
        bodies = [r["body"] for r in csv.DictReader(f)]
    assert len(bodies) == 81 and not any("\r" in b for b in bodies)                               # a CRLF checkout used to add \r inside every one of them


def test_no_attribute_controlled_file_is_checked_out_with_crlf():
    try:
        out = subprocess.run(["git", "ls-files", "--eol"], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=60)
    except (OSError, subprocess.TimeoutExpired):
        pytest.skip("git not available")
    if out.returncode != 0:
        pytest.skip("not a git checkout")
    bad = [ln.split("\t")[-1] for ln in out.stdout.splitlines() if "attr/text eol=lf" in ln and "w/crlf" in ln]
    assert not bad, bad[:5]
    seeds = [ln for ln in out.stdout.splitlines() if "data/seed/" in ln]
    assert seeds and all("-text" in ln for ln in seeds)                                            # the seed databases are binary, never converted
