# Triggerware integration — how to add it back

Removed from the project on 2026-11-04 (it was added the same day on `feature/cmf-agent-implementation`). Everything needed to restore it is in this file:
the full source of the 3 files, the 3 small edits to existing files and the steps. Nothing else in CMF depended on it.

SDK: https://github.com/triggerware/tw2-sdks — Python package `triggerware` (v1.1.1 when written, needs Python >= 3.11; it pulls in `tw2-jsonrpc` and `pyyaml`).
Server: `curl -fsSL https://tw2-releases.s3.us-east-2.amazonaws.com/install.sh | sh` then `triggerware start` (listens on `localhost:5221`).

## What it did (3 SDK calls, exposed as API endpoints; off unless `CMF_TRIGGERWARE_ENABLED=1`)
| # | Feature | SDK call | Endpoint |
|---|---|---|---|
| 1 | Server health check | `system.health()` | `GET /api/triggerware/health` |
| 2 | Trigger poll (drains the trigger's cached deltas) | `triggers.poll(name)` | `POST /api/triggerware/triggers/{name}/poll` |
| 3 | Trigger watch -> memory ingestion | `triggers.watch(name, cb)` / `unwatch` | `POST` / `DELETE /api/triggerware/triggers/{name}/watch`, `GET /api/triggerware/watches` |

Watch contract: the trigger's SQL must return the `interaction_id` in its FIRST column. Each added row goes to `MemoryOrchestrator.ingest_meeting`
(idempotent; one at a time in a worker thread). Deleted rows are ignored; unknown ids are recorded as `rejected`, other failures as `errors`, and the watch keeps running.
Watches live in the API process only (not persisted): POST the watch again after a restart.
Status codes: disabled -> 404, SDK missing / server unreachable -> 503, server-side error -> 502, already watching -> 409, not watching -> 404.

## Restore, step by step
1. `.venv/bin/pip install "triggerware>=1.1"` and add the line `triggerware>=1.1` to `requirements.txt`.
2. Create the three files below, exactly at these paths (also `touch src/integrations/__init__.py`).
3. In `api/main.py` add the two tagged lines:
   - with the other imports (after `from services.conversations import shared`):
     `from api.triggerware_routes import router as triggerware_router  # noqa: E402  (TRIGGERWARE-INTEGRATION)`
   - right after the `app.add_middleware(...)` line:
     `app.include_router(triggerware_router)  # TRIGGERWARE-INTEGRATION`
4. Append to `.env.example` (and set in `.env`):
   ```
   # --- Triggerware (optional; off unless enabled) ---
   CMF_TRIGGERWARE_ENABLED=0
   CMF_TRIGGERWARE_ADDRESS=localhost:5221
   ```
5. Run `.venv/bin/python -m pytest -q`, then restart the API. Set `CMF_TRIGGERWARE_ENABLED=1` and start the Triggerware server to use it.
6. The trigger itself is created on the Triggerware server (`triggerware help`; the SDK repo does not document SQL/schedule syntax), returning `interaction_id` first.

## Known limits
- `client._peer.close()` is a private attribute (no public close at SDK v1.1.1) — recheck on upgrade.
- Tests fake the SDK client; it was never run against a real Triggerware server.

## Source

### `src/integrations/triggerware.py`
```python
"""Optional Triggerware integration (three calls: server health check, trigger poll, trigger watch -> memory ingestion).

Off unless CMF_TRIGGERWARE_ENABLED=1.  The `triggerware` package is imported lazily, so nothing else in CMF depends on it.
Each call opens a short-lived TCP connection to the Triggerware server (default localhost:5221) and closes it again.
Revert instructions: utils/triggerware_integration.md
"""
from __future__ import annotations

import os
import asyncio
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable

DEFAULT_ADDRESS = "localhost:5221"


class TriggerwareDisabled(RuntimeError):
    """CMF_TRIGGERWARE_ENABLED is not set."""


class AlreadyWatching(RuntimeError):
    """A watch on this trigger is already active."""


class NotWatching(RuntimeError):
    """No active watch on this trigger."""


class TriggerwareUnavailable(RuntimeError):
    """SDK not installed or server unreachable."""


def enabled() -> bool:
    return os.environ.get("CMF_TRIGGERWARE_ENABLED", "").strip().lower() in ("1", "true", "yes")


def address() -> str:
    return os.environ.get("CMF_TRIGGERWARE_ADDRESS", DEFAULT_ADDRESS)


async def _open() -> Any:
    if not enabled():
        raise TriggerwareDisabled("set CMF_TRIGGERWARE_ENABLED=1 to use Triggerware")
    try:
        from triggerware import TriggerwareClient
    except ImportError as e:
        raise TriggerwareUnavailable("the 'triggerware' package is not installed (pip install triggerware)") from e
    try:
        return await TriggerwareClient.connect_tcp(address())
    except OSError as e:
        raise TriggerwareUnavailable(f"cannot reach Triggerware at {address()}: {e}") from e


async def _close(client: Any) -> None:
    await client._peer.close()  # the SDK exposes no public close(); Peer.close is the JSON-RPC peer shutdown


@asynccontextmanager
async def connect() -> AsyncIterator[Any]:
    """Short-lived connection used by health and poll."""
    client = await _open()
    try:
        yield client
    finally:
        await _close(client)


async def health() -> dict:
    """Feature 1: client.system.health()."""
    async with connect() as tw:
        return dict(await tw.system.health())


async def poll_trigger(name: str) -> dict:
    """Feature 2: client.triggers.poll(name) - drains the trigger's cached deltas (a destructive read on the Triggerware side)."""
    async with connect() as tw:
        deltas = await tw.triggers.poll(name)
    return dict(trigger=name, deltas=[dict(d) for d in deltas],
                added=sum(len(d["added"]) for d in deltas), deleted=sum(len(d["deleted"]) for d in deltas))


# ---------------------------------------------------------------------------------------------- watch -> ingestion
# Feature 3: client.triggers.watch(name, callback).  The trigger's SQL must return the interaction id in its FIRST column
# (e.g. SELECT interaction_id FROM <table> ...).  Every ADDED row is ingested into memory; deleted rows are ignored.
# Watches hold a long-lived connection and live in this process only: after a restart, call start_watch again.
_watches: dict[str, dict] = {}
_ingest_lock = asyncio.Lock()  # one ingestion at a time: the memory store and the LLM are shared


async def start_watch(name: str, ingest: Callable[[str], dict]) -> dict:
    """Subscribe to trigger `name`; `ingest(interaction_id)` is called (in a worker thread) for each added row."""
    if name in _watches:
        raise AlreadyWatching(f"already watching '{name}'")
    state: dict = dict(trigger=name, ingested=[], skipped=[], rejected=[], errors=[], resume_token=None)

    async def on_delta(added: list[list[Any]], deleted: list[list[Any]]) -> None:
        for row in added:
            iid = str(row[0]) if row else ""
            try:
                async with _ingest_lock:
                    res = await asyncio.to_thread(ingest, iid)
                state["skipped" if res.get("skipped") else "ingested"].append(iid)
            except KeyError:
                state["rejected"].append(iid)  # not a known interaction id
            except Exception as e:  # LLM down etc.: record and keep watching
                state["errors"].append(f"{iid}: {type(e).__name__}: {e}")

    client = await _open()
    try:
        state["resume_token"] = await client.triggers.watch(name, on_delta)
    except Exception:
        await _close(client)
        raise
    state["_client"] = client
    _watches[name] = state
    return watch_status(name)


async def stop_watch(name: str) -> dict:
    state = _watches.pop(name, None)
    if state is None:
        raise NotWatching(f"not watching '{name}'")
    client = state.pop("_client")
    try:
        await client.triggers.unwatch(name=name)
    finally:
        await _close(client)
    return {k: v for k, v in state.items() if k != "_client"}


def watch_status(name: str | None = None) -> dict:
    view = lambda st: {k: v for k, v in st.items() if k != "_client"}  # noqa: E731
    if name is not None:
        return view(_watches[name])
    return {n: view(st) for n, st in _watches.items()}
```

### `api/triggerware_routes.py`
```python
"""HTTP routes for the optional Triggerware integration (mounted from api/main.py). Revert: utils/triggerware_integration.md"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from integrations import triggerware as tw

router = APIRouter(prefix="/api/triggerware", tags=["triggerware"])


async def _guard(coro):
    try:
        return await coro
    except tw.NotWatching as e:
        raise HTTPException(404, str(e))
    except tw.AlreadyWatching as e:
        raise HTTPException(409, str(e))
    except tw.TriggerwareDisabled as e:
        raise HTTPException(404, str(e))
    except tw.TriggerwareUnavailable as e:
        raise HTTPException(503, str(e))
    except Exception as e:  # server-side JSON-RPC error, e.g. unknown trigger
        raise HTTPException(502, f"{type(e).__name__}: {e}")


@router.get("/health")
async def health() -> dict:
    return await _guard(tw.health())


@router.post("/triggers/{name}/poll")  # POST: polling drains the trigger's cached deltas
async def poll(name: str) -> dict:
    return await _guard(tw.poll_trigger(name))


def _ingest(interaction_id: str) -> dict:
    """Same ingestion the CLI/API use; imported lazily so the route module stays light."""
    from agents.memory.agent import MemoryOrchestrator
    from api.main import store
    from llm.client import make_llm
    return MemoryOrchestrator(make_llm(), store=store()).ingest_meeting(interaction_id)


@router.post("/triggers/{name}/watch")  # rows added to the trigger (first column = interaction_id) are ingested into memory
async def watch(name: str) -> dict:
    return await _guard(tw.start_watch(name, _ingest))


@router.delete("/triggers/{name}/watch")
async def unwatch(name: str) -> dict:
    return await _guard(tw.stop_watch(name))


@router.get("/watches")
def watches() -> dict:
    return tw.watch_status()
```

### `tests/test_triggerware.py`
```python
"""Triggerware integration with the SDK client faked (no server needed)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from integrations import triggerware as tw  # noqa: E402


class FakePeer:
    async def close(self):
        self.closed = True


class FakeClient:
    def __init__(self):
        self._peer = FakePeer()
        self.system = type("S", (), {"health": staticmethod(self._health)})()
        self.triggers = type("T", (), {"poll": staticmethod(self._poll), "watch": staticmethod(self._watch), "unwatch": staticmethod(self._unwatch)})()
        self.callback = None
        self.unwatched = None

    async def _watch(self, name, cb):
        self.callback = cb
        return "tok-1"

    async def _unwatch(self, *, name=None, resume_token=None):
        self.unwatched = name

    async def _health(self):
        return {"status": "ok"}

    async def _poll(self, name):
        if name == "bad":
            raise ValueError("unknown trigger")
        return [{"added": [[1], [2]], "deleted": [[3]]}]


@pytest.fixture()
def api(monkeypatch):
    monkeypatch.setenv("CMF_TRIGGERWARE_ENABLED", "1")
    fake = FakeClient()
    import triggerware

    async def fake_connect(address):
        return fake
    monkeypatch.setattr(triggerware.TriggerwareClient, "connect_tcp", staticmethod(fake_connect))
    import api.main as m
    return TestClient(m.app), fake


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("CMF_TRIGGERWARE_ENABLED", raising=False)
    import api.main as m
    assert TestClient(m.app).get("/api/triggerware/health").status_code == 404


def test_health_and_poll(api):
    c, fake = api
    assert c.get("/api/triggerware/health").json() == {"status": "ok"}
    r = c.post("/api/triggerware/triggers/t1/poll").json()
    assert (r["added"], r["deleted"], r["trigger"]) == (2, 1, "t1")
    assert fake._peer.closed


def test_server_error_is_502(api):
    c, _ = api
    assert c.post("/api/triggerware/triggers/bad/poll").status_code == 502


def test_unreachable_is_503(monkeypatch):
    monkeypatch.setenv("CMF_TRIGGERWARE_ENABLED", "1")
    monkeypatch.setenv("CMF_TRIGGERWARE_ADDRESS", "127.0.0.1:1")
    import api.main as m
    assert TestClient(m.app).get("/api/triggerware/health").status_code == 503


def test_watch_ingests_added_rows(api, monkeypatch):
    import asyncio
    import api.triggerware_routes as routes
    calls = []

    def fake_ingest(iid):
        calls.append(iid)
        if iid == "NOPE":
            raise KeyError(iid)
        if iid == "boom":
            raise RuntimeError("llm down")
        return {"interaction_id": iid, "skipped": iid == "DONE"}

    monkeypatch.setattr(routes, "_ingest", fake_ingest)
    monkeypatch.setattr(tw, "_watches", {})
    c, fake = api
    r = c.post("/api/triggerware/triggers/new_meetings/watch").json()
    assert r["resume_token"] == "tok-1" and "_client" not in r
    assert c.post("/api/triggerware/triggers/new_meetings/watch").status_code == 409
    asyncio.run(fake.callback([["A1"], ["DONE"], ["NOPE"], ["boom"]], [["old"]]))
    st = c.get("/api/triggerware/watches").json()["new_meetings"]
    assert (st["ingested"], st["skipped"], st["rejected"]) == (["A1"], ["DONE"], ["NOPE"])
    assert len(st["errors"]) == 1 and "llm down" in st["errors"][0] and calls == ["A1", "DONE", "NOPE", "boom"]
    assert c.delete("/api/triggerware/triggers/new_meetings/watch").status_code == 200
    assert fake.unwatched == "new_meetings" and c.get("/api/triggerware/watches").json() == {}
    assert c.delete("/api/triggerware/triggers/new_meetings/watch").status_code == 404
```
