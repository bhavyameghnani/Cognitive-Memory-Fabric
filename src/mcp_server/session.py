"""Session policy: the client allow-list (CMF_ALLOWED_CLIENTS), enforced by the server on every tool call.

CMF_ALLOWED_CLIENTS is a comma separated list of client ids. Unset or empty means every client in the database is allowed.
The variable is read at call time, so a stdio server started with a restricted environment is restricted for its lifetime.
"""
from __future__ import annotations

import os
import re

CLIENT_ID_RE = re.compile(r"^CLIENT-\d{3}$")


class AccessDenied(ValueError):
    pass


def allowed_clients() -> set[str] | None:
    raw = os.environ.get("CMF_ALLOWED_CLIENTS", "").strip()
    return {c.strip() for c in raw.split(",") if c.strip()} or None if raw else None


def check_client(client_id: str) -> str:
    if not isinstance(client_id, str) or not CLIENT_ID_RE.match(client_id):
        raise AccessDenied(f"invalid client_id {client_id!r}; expected a value like CLIENT-001")
    allowed = allowed_clients()
    if allowed is not None and client_id not in allowed:
        raise AccessDenied(f"client {client_id} is not allowed in this session")
    return client_id
