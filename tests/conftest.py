"""Test-session defaults.  The harness is production-strict by default (anonymous requests are refused); the existing API tests call the API without any
identity, so the test session runs in the development profile.  Harness tests that need production behaviour build their own HarnessConfig explicitly.

Every test gets its OWN approval database (CMF_APPROVAL_DATABASE_URL): approvals are durable now, so tests must never share or pollute build/cmf_approvals.db."""
import os

import pytest

os.environ.setdefault("HARNESS_PROFILE", "development")


@pytest.fixture(autouse=True)
def _isolated_approval_database(tmp_path, monkeypatch):
    monkeypatch.setenv("CMF_APPROVAL_DATABASE_URL", f"sqlite:///{(tmp_path / 'approvals.db').as_posix()}")
    yield
