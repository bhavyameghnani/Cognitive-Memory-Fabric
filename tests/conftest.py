"""Test-session defaults.  The harness is production-strict by default (anonymous requests are refused); the existing API tests call the API without any
identity, so the test session runs in the development profile.  Harness tests that need production behaviour build their own HarnessConfig explicitly.

Every test gets its OWN approval database (CMF_APPROVAL_DATABASE_URL): approvals are durable now, so tests must never share or pollute build/cmf_approvals.db."""
import os

import pytest

os.environ.setdefault("HARNESS_PROFILE", "development")
# Hermetic memory defaults: a developer's .env may switch on LLM-first ingestion and the extra source channels for the UI (docs/llm_first_ingestion.md),
# but the suite is written against the frozen data/conversations.csv and the rules pipeline.  Set before src/llm loads .env (which never overrides a set
# variable); tests that need channels or llm_first construct MultiChannelConversations / OrchestratorConfig(ingest_mode=...) explicitly.
os.environ["CMF_MEMORY_CHANNELS"] = ""
os.environ["CMF_INGEST_MODE"] = "rules"


@pytest.fixture(autouse=True)
def _isolated_approval_database(tmp_path, monkeypatch):
    monkeypatch.setenv("CMF_APPROVAL_DATABASE_URL", f"sqlite:///{(tmp_path / 'approvals.db').as_posix()}")
    yield
