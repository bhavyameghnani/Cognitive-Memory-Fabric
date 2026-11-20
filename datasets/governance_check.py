"""Reference access evaluator and consistency checks for the SYNTHETIC source-control metadata.

This is a dataset-validation aid, not production access control and not governed memory storage. It exists so that the
expected outcomes in evaluation/access_scenarios.csv can be reproduced from the control files by an independent rule
implementation. Deny by default. Nothing here reflects a vendor or regulatory rule. Standard library only; read-only.
"""
import csv
from datetime import date
from pathlib import Path

DS = Path(__file__).resolve().parent


def _read(p):
    with open(p, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def load(ds=DS):
    ds = Path(ds)
    reg = _read(ds / "character_registry.csv")
    return dict(
        control={r["record_id"]: r for r in _read(ds / "governance/source_control.csv")},
        access={r["access_policy_id"]: r for r in _read(ds / "governance/access_policies.csv")},
        retention={r["retention_policy_id"]: r for r in _read(ds / "governance/retention_policies.csv")},
        grants=_read(ds / "governance/access_grants.csv"),
        principals={r["character_id"]: r for r in reg},
    )


def _d(s):
    return date.fromisoformat(s) if s else None


def grant_state(g, on):
    """active | not_yet_valid | expired | revoked, for a grant on a given date."""
    on = _d(on) if isinstance(on, str) else on
    if g["status"] == "revoked" and _d(g["revoked_at"]) and _d(g["revoked_at"]) <= on:
        return "revoked"
    if _d(g["valid_from"]) and on < _d(g["valid_from"]):
        return "not_yet_valid"
    if _d(g["valid_until"]) and on > _d(g["valid_until"]):
        return "expired"
    return "active"


def effective(ctx, row):
    """Channel access policy narrowed by an optional record-level restriction (a restriction can only narrow)."""
    pol = ctx["access"].get(row["access_policy_id"])
    if not pol or not pol["permitted_roles"].strip():
        return None
    roles = set(pol["permitted_roles"].split("|"))
    agent, grants = pol["agent_access"], pol["third_party_grants"]
    res = ctx["access"].get(row.get("restriction_policy_id", "")) if row.get("restriction_policy_id") else None
    if res:
        roles &= set(res["permitted_roles"].split("|"))
        agent = "none" if "none" in (agent, res["agent_access"]) else agent
        grants = "not_honoured" if "not_honoured" in (grants, res["third_party_grants"]) else grants
    return dict(roles=roles, agent_access=agent, third_party_grants=grants)


def decide(ctx, requester_id, requester_type, acting_for, record_id, on):
    """Return (decision, reason_code). Deny by default; derived records never inherit from their sources."""
    row = ctx["control"].get(record_id)
    if not row:
        return "deny", "deny_no_control_row"
    pol = effective(ctx, row)
    if pol is None or not pol["roles"]:
        return "deny", "deny_no_permissions_defined"
    roles = pol["roles"]
    eff = requester_id
    agent = requester_type == "agent"
    if agent:
        if not acting_for:
            return "deny", "deny_missing_delegation"
        if pol["agent_access"] != "delegated":
            return "deny", "deny_agent_blocked"
        eff = acting_for                      # an agent holds no rights beyond its delegator's
    who = ctx["principals"].get(eff)
    if not who:
        return "deny", "deny_unknown_principal"
    client = row["client_scope"]
    kind = who["person_category"] if "person_category" in who else who["role"]
    if kind == "client":
        if eff != client:
            return "deny", "deny_other_client"
        return ("allow", "allow_delegated_agent" if agent else "allow_client_self") if "client_self" in roles \
            else ("deny", "deny_role_not_permitted")
    if kind == "advisor":
        if client not in who["client_scope"].split("|"):
            return "deny", "deny_not_assigned_advisor"
        return ("allow", "allow_delegated_agent" if agent else "allow_assigned_advisor") if "assigned_advisor" in roles \
            else ("deny", "deny_role_not_permitted")
    # anyone else (third-party correspondents): only an explicit, dated grant on this exact record
    if pol["third_party_grants"] != "honoured":
        return "deny", "deny_grants_not_honoured"
    mine = [g for g in ctx["grants"] if g["record_id"] == record_id and g["principal_id"] == eff and g["client_scope"] == client]
    if not mine:
        return "deny", "deny_no_grant"
    states = [grant_state(g, on) for g in mine]
    if "active" in states:
        return "allow", "allow_active_grant"
    for s in ("revoked", "expired", "not_yet_valid"):
        if s in states:
            return "deny", f"deny_grant_{s}"
    return "deny", "deny_no_grant"


def readers(ctx, record_id, on):
    """Set of (principal) ids that may read a record on a date: client, assigned advisor, active third-party grantees."""
    row = ctx["control"].get(record_id)
    pol = effective(ctx, row) if row else None
    if pol is None:
        return set()                      # no control row or no permissions defined: nobody may read (deny by default)
    roles = pol["roles"]
    out = set()
    if "client_self" in roles:
        out.add(row["client_scope"])
    if "assigned_advisor" in roles:
        out |= {pid for pid, p in ctx["principals"].items()
                if p.get("role") == "advisor" and row["client_scope"] in p["client_scope"].split("|")}
    if pol["third_party_grants"] == "honoured":
        out |= {g["principal_id"] for g in ctx["grants"] if g["record_id"] == record_id and grant_state(g, on) == "active"}
    return out
