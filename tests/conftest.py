"""Test-session defaults and database isolation (Milestone M0).

PROFILE.  The harness is production-strict by default (anonymous requests are refused); the existing API tests call the API without any identity, so the test session runs in
the development profile.  Harness tests that need production behaviour build their own HarnessConfig explicitly.

DATABASE ISOLATION.  The test suite never depends on, creates or modifies a developer's local databases:
  * Every database URL the application reads from the environment is pointed at a DISPOSABLE session directory (tempfile.mkdtemp) BEFORE any application module is imported:
    CMF_MEMORY_DATABASE_URL, CMF_DATABASE_URL (operational), CMF_EVIDENCE_DATABASE_URL, CMF_APPROVAL_DATABASE_URL.  The assignment is unconditional: a developer's environment or
    .env can never point the tests at build/, data/seed/ or a real database.
  * The operational database is built from the committed CSVs (data/operational) with the repository's own loader into that directory; it is never copied from a demo or seed file.
  * The structured evidence database (needed by tests/test_semantic_eval.py) is built on demand by the `evidence_db` fixture with the repository's own ingestion, into the same directory.
  * Every test starts from these values again (an existing test deletes one of them in a `finally`), and a test that needs its own database sets it with monkeypatch as before.
  * At the end of the session the guard below compares build/ and data/seed/ with their state at the start and FAILS the run if a file was created, changed or removed there.
  * The session directory is deleted at exit (best effort: a Windows process may still hold a file open).
Remaining boundary: a test that passes an explicit path to a database constructor can still open it; the guard detects the effect, it cannot prevent the call.
"""
import atexit
import hashlib
import os
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# ---------------------------------------------------------------------------------------------------------------------- disposable session directory and environment
SESSION_DIR = Path(tempfile.mkdtemp(prefix="cmf-pytest-"))
atexit.register(shutil.rmtree, SESSION_DIR, True)


def _url(name: str) -> str:
    return f"sqlite:///{(SESSION_DIR / name).as_posix()}"


ISOLATED_ENV = {
    "CMF_MEMORY_DATABASE_URL": _url("memory.db"),
    "CMF_DATABASE_URL": _url("ops.db"),
    "CMF_EVIDENCE_DATABASE_URL": _url("evidence.db"),
    "CMF_APPROVAL_DATABASE_URL": _url("approvals.db"),
}
os.environ.update(ISOLATED_ENV)                                    # unconditional, before src/llm loads .env (which never overrides a set variable)
os.environ.setdefault("HARNESS_PROFILE", "development")
# Hermetic memory defaults: a developer's .env may switch on LLM-first ingestion and the extra source channels for the UI (docs/llm_first_ingestion.md),
# but the suite is written against the frozen data/conversations.csv and the rules pipeline.  Tests that need channels or llm_first construct
# MultiChannelConversations / OrchestratorConfig(ingest_mode=...) explicitly.
os.environ["CMF_MEMORY_CHANNELS"] = ""
os.environ["CMF_INGEST_MODE"] = "rules"


def _path_of(url: str) -> Path:
    return Path(url[len("sqlite:///"):])


def _inside_session_dir(p: Path) -> bool:
    try:
        p.resolve().relative_to(SESSION_DIR.resolve())
        return True
    except ValueError:
        return False


def _build_ops_database() -> Path:
    """The operational smoke database from the committed CSVs.  load_sqlite() deletes an existing file at its target, so the target is asserted to be a fresh path in the session directory."""
    target = _path_of(ISOLATED_ENV["CMF_DATABASE_URL"])
    assert _inside_session_dir(target) and not target.exists(), f"refusing to build the operational database at {target}"
    from opdata.config import OUT_DIR
    from opdata.load_db import load_sqlite
    load_sqlite(str(target), OUT_DIR)
    return target


_build_ops_database()


# ---------------------------------------------------------------------------------------------------------------------- guard over build/ and data/seed/
def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _protected_state() -> dict:
    out = {}
    for sub in ("build", "data/seed"):
        d = ROOT / sub
        if d.exists():
            for p in sorted(d.rglob("*")):
                if p.is_file() and "__pycache__" not in p.parts:
                    out[p.relative_to(ROOT).as_posix()] = _sha(p)
    return out


_STATE_AT_START: dict = {}


def pytest_sessionstart(session):
    _STATE_AT_START.clear()
    _STATE_AT_START.update(_protected_state())


def pytest_sessionfinish(session, exitstatus):
    after = _protected_state()
    changed = sorted(k for k in set(_STATE_AT_START) | set(after) if _STATE_AT_START.get(k) != after.get(k))
    if changed:
        sys.stderr.write("\nISOLATION VIOLATION: the test session created, changed or removed files in build/ or data/seed/:\n  " + "\n  ".join(changed) + "\n")
        session.exitstatus = pytest.ExitCode.TESTS_FAILED
    shutil.rmtree(SESSION_DIR, ignore_errors=True)


# ---------------------------------------------------------------------------------------------------------------------- fixtures
@pytest.fixture(autouse=True)
def _isolated_databases(tmp_path, monkeypatch):
    """Each test starts from the isolated environment (a previous test may have deleted a variable) and gets its OWN approval database: approvals are durable, so tests must never
    share or pollute one."""
    for k, v in ISOLATED_ENV.items():
        if os.environ.get(k) != v:
            monkeypatch.setenv(k, v)
    monkeypatch.setenv("CMF_APPROVAL_DATABASE_URL", f"sqlite:///{(tmp_path / 'approvals.db').as_posix()}")
    yield


@pytest.fixture(scope="session")
def isolated_session_dir() -> Path:
    return SESSION_DIR


@pytest.fixture(scope="session")
def evidence_db() -> Path:
    """The structured evidence database for tests that evaluate it, built once per session by the repository's own ingestion into the disposable directory."""
    target = _path_of(ISOLATED_ENV["CMF_EVIDENCE_DATABASE_URL"])
    assert _inside_session_dir(target), target
    from evidence.ingest import ingest
    from evidence.store import EvidenceStore
    store = EvidenceStore(ISOLATED_ENV["CMF_EVIDENCE_DATABASE_URL"])       # idempotent: an earlier test may already have created the (empty) file
    try:
        if store.reader().execute("SELECT COUNT(*) FROM containers").fetchone()[0] == 0:
            rep = ingest(store)
            assert not rep["totals"].get("failed"), rep["totals"]
    finally:
        store.close()
    return target
