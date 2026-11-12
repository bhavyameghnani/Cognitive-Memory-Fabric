"""Permission gate: authorization before retrieval, tools, reports and memory operations.  Deny by default.

Authentication and authorization are separate.  CMF does not authenticate people; it VERIFIES a signed token minted by whatever does (harness/auth_tokens.py):
  * identity_from_claims() turns the claims of a token whose signature verified into an AuthContext (authenticated=True, source "signed-token").  This is the
    only identity source the HTTP API accepts; request headers such as X-CMF-Actor-Id are never authentication.
  * The CLI runs as AuthContext.local_operator (whoever can run it already holds the data files).
  * resolve_identity() is a library helper kept for backward compatibility: it builds an UNAUTHENTICATED identity (or the labelled dev-anonymous one) unless the
    caller itself vouches for authentication via `authenticated_by`.  The API does not call it with request data.
  * An identity with authenticated=False is refused unless the profile is development/test.
  * A client_id typed by the user is never proof of access.  An advisor's clients come from the data (advisor_id in data/conversations.csv), not
    from the request.
No decision here depends on an LLM or on a prompt instruction.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from harness.config import HarnessConfig

ROLES = ("advisor", "supervisor", "admin", "operator", "service")
READ_ONLY_TOOLS = frozenset({
    "get_client_profile", "get_client_portfolio", "get_portfolio_history", "get_client_goals", "get_client_liabilities", "calculate_asset_allocation",
    "calculate_portfolio_concentration", "compare_portfolio_snapshots", "calculate_portfolio_returns", "calculate_liquidity_ratio",
    "calculate_goal_funding_gap", "get_market_data", "get_source_evidence"})
# operation -> roles allowed.  Unknown operations are denied.
OPERATIONS = {
    "ask": ("advisor", "supervisor", "admin", "operator", "service"),
    "read_memory": ("advisor", "supervisor", "admin", "operator", "service"),
    "run_tool": ("advisor", "supervisor", "admin", "operator", "service"),
    "generate_report": ("advisor", "supervisor", "admin", "operator", "service"),
    "ingest": ("admin", "operator", "service"),
    "lifecycle": ("supervisor", "admin", "operator"),
    "approve": ("supervisor", "admin"),
}
# roles whose scope is every client in the directory (still never "no scope"): they are not tied to one advisor's book
GLOBAL_SCOPE_ROLES = ("supervisor", "admin", "operator", "service")


class PermissionDenied(PermissionError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail = code, detail


@dataclass(frozen=True)
class AuthContext:
    actor_id: str
    role: str
    authenticated: bool = False
    client_scope: Optional[frozenset] = None     # None = everything the role allows; otherwise an explicit extra restriction
    allowed_tools: Optional[frozenset] = None    # None = the role default (all read-only tools)
    source: str = "unknown"                      # "gateway" | "header-asserted" | "local-cli" | "dev-anonymous" | "test"

    @classmethod
    def local_operator(cls, actor_id: str = "local-operator") -> "AuthContext":
        """The CLI runs as a local OS user with direct access to the data files; this is a statement of fact, not of authentication."""
        return cls(actor_id=actor_id, role="operator", authenticated=True, source="local-cli")

    @classmethod
    def for_test(cls, actor_id: str, role: str = "advisor", scope=None, tools=None) -> "AuthContext":
        return cls(actor_id, role, True, frozenset(scope) if scope is not None else None, frozenset(tools) if tools is not None else None, "test")


def identity_from_claims(claims: dict) -> AuthContext:
    """AuthContext for a token whose signature, expiry and required claims were ALREADY verified by auth_tokens.verify_token."""
    return AuthContext(actor_id=str(claims["sub"]).strip(), role=str(claims["role"]).strip().lower(), authenticated=True, source="signed-token")


def resolve_identity(cfg: HarnessConfig, actor_id: Optional[str], role: Optional[str], authenticated_by: Optional[str] = None) -> AuthContext:
    """Build the AuthContext at the API boundary.  Raises PermissionDenied when no usable identity exists (unless dev anonymous is allowed)."""
    actor, r = (actor_id or "").strip(), (role or "").strip().lower()
    if not actor and not r:
        if cfg.allow_anonymous_dev:
            return AuthContext("dev-anonymous", "admin", False, None, None, "dev-anonymous")
        raise PermissionDenied("missing_identity", "no actor identity was provided")
    if not actor or not r:
        raise PermissionDenied("missing_identity", "both actor id and role are required")
    return AuthContext(actor, r, bool(authenticated_by), None, None, authenticated_by or "header-asserted")


@dataclass
class Decision:
    allowed: bool
    operation: str
    code: str
    detail: str = ""
    actor: str = ""
    clients: tuple = ()


@dataclass
class PermissionGate:
    config: HarnessConfig
    directory: Callable[[], dict]                # client_id -> {"advisor_id": ...}
    emit: Callable[..., None] = field(default=lambda *a, **k: None)   # tracer.event
    decisions: list = field(default_factory=list)

    # ------------------------------------------------------------------ internals
    def _record(self, d: Decision) -> Decision:
        self.decisions.append(d)
        self.emit("harness.permission", "allow" if d.allowed else "deny", operation=d.operation, code=d.code, detail=d.detail, actor=d.actor, clients=list(d.clients))
        return d

    def _identity_problem(self, auth: Optional[AuthContext]) -> Optional[tuple]:
        if auth is None or not auth.actor_id:
            return "missing_identity", "no actor identity"
        if auth.role not in ROLES:
            return "invalid_role", f"role {auth.role!r} is not one of {ROLES}"
        if not auth.authenticated and not self.config.allow_asserted_identity:
            return "unauthenticated", "identity is asserted, not authenticated, and this deployment does not accept that"
        return None

    def authorized_clients(self, auth: Optional[AuthContext]) -> frozenset:
        """Clients this actor may touch.  Derived from the data (advisor assignment), never from the request."""
        if self._identity_problem(auth):
            return frozenset()
        directory = self.directory()
        if auth.role in GLOBAL_SCOPE_ROLES:
            base = set(directory)
        else:   # advisor: only the clients assigned to this advisor id
            base = {c for c, v in directory.items() if v.get("advisor_id") == auth.actor_id}
        if auth.client_scope is not None:
            base &= set(auth.client_scope)
        return frozenset(base)

    # ------------------------------------------------------------------ public checks (each raises PermissionDenied and traces the decision)
    def check(self, auth: Optional[AuthContext], operation: str, clients: Iterable[str] = ()) -> Decision:
        clients = tuple(dict.fromkeys(clients))
        actor = getattr(auth, "actor_id", "") or ""
        if not self.config.permission_gate:   # only possible outside the production profile
            return self._record(Decision(True, operation, "gate_disabled", "permission gate is disabled by configuration", actor, clients))
        problem = self._identity_problem(auth)
        if problem:
            self._record(Decision(False, operation, problem[0], problem[1], actor, clients))
            raise PermissionDenied(*problem)
        if operation not in OPERATIONS:
            self._record(Decision(False, operation, "unknown_operation", operation, actor, clients))
            raise PermissionDenied("unknown_operation", operation)
        if auth.role not in OPERATIONS[operation]:
            self._record(Decision(False, operation, "role_not_permitted", f"role {auth.role} may not {operation}", actor, clients))
            raise PermissionDenied("role_not_permitted", f"role {auth.role} may not {operation}")
        if clients:
            allowed = self.authorized_clients(auth)
            denied = [c for c in clients if c not in allowed]
            if denied:
                self._record(Decision(False, operation, "client_not_in_scope", f"{denied}", actor, clients))
                raise PermissionDenied("client_not_in_scope", f"actor {actor} has no access to {denied}")
        return self._record(Decision(True, operation, "ok", "", actor, clients))

    def check_tool(self, auth: Optional[AuthContext], tool: str, client_id: Optional[str] = None) -> Decision:
        actor = getattr(auth, "actor_id", "") or ""
        clients = (client_id,) if client_id else ()
        if not self.config.permission_gate:
            return self._record(Decision(True, "run_tool", "gate_disabled", tool, actor, clients))
        self.check(auth, "run_tool", clients)
        if tool not in READ_ONLY_TOOLS:   # write-capable or unknown tools need an explicit grant; none exist today
            self._record(Decision(False, "run_tool", "tool_not_permitted", tool, actor, clients))
            raise PermissionDenied("tool_not_permitted", f"{tool} is not a permitted read-only tool")
        if auth.allowed_tools is not None and tool not in auth.allowed_tools:
            self._record(Decision(False, "run_tool", "tool_not_permitted", f"{tool} not granted to {actor}", actor, clients))
            raise PermissionDenied("tool_not_permitted", f"{tool} is not granted to {actor}")
        return self._record(Decision(True, "run_tool", "ok", tool, actor, clients))

    def owns(self, auth: Optional[AuthContext], client_id: Optional[str]) -> bool:
        """Data-ownership test for one record (memory, evidence, tool envelope).  Records with no client are not owned by anyone."""
        if not self.config.permission_gate:
            return True
        return bool(client_id) and client_id in self.authorized_clients(auth)
