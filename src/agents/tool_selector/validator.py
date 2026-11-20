"""Deterministic validation of model-proposed calls: tool exists, schema, no unknown arguments, client in plan, no repeat of a successful call."""
from __future__ import annotations

import json
from typing import Any, Optional

import jsonschema

from schemas.tools import PlannedCall


def clean_args(args: dict) -> dict:
    """Drop null/empty-string values: 'not given' for optional arguments (small models often send them)."""
    return {k: v for k, v in args.items() if v is not None and v != ""}


def call_key(tool: str, args: dict, schema: Optional[dict] = None) -> str:
    full = dict(clean_args(args))
    for k, p in ((schema or {}).get("properties") or {}).items():
        if k not in full and p.get("default") is not None:
            full[k] = p["default"]
    return tool + ":" + json.dumps(full, sort_keys=True, default=str)


class CallValidator:
    def __init__(self, catalog: list[dict], client_ids: list[str]) -> None:
        self.tools = {t["name"]: t for t in catalog}
        self.client_ids = list(client_ids)
        self.done: set[str] = set()  # keys of successful calls
        self.failed: set[str] = set()  # keys of calls that were EXECUTED and ended in error/timeout (never rejected or schema-invalid ones)
        self.invalid: set[str] = set()  # keys of calls rejected as invalid; the same invalid call again is reported as a repeat

    def record_failure(self, tool: str, args: dict) -> None:
        """Remember an executed call that returned error/timeout so the identical call is not sent to the tool again."""
        t = self.tools.get(tool)
        if t is not None:
            self.failed.add(call_key(tool, args, t["input_schema"]))

    def validate(self, call: PlannedCall, batch_keys: set[str]) -> tuple[str, Optional[str], dict]:
        """Returns (verdict, message, cleaned_args); verdict in ok | rejected | duplicate."""
        args = clean_args(call.arguments)
        t = self.tools.get(call.tool)
        if t is None:
            return "rejected", f"unknown tool '{call.tool}'; choose one of: {', '.join(sorted(self.tools))}", args
        schema = t["input_schema"]
        props = schema.get("properties", {})
        note = None
        if "client_id" in props and "client_id" in schema.get("required", []) and "client_id" not in args and len(self.client_ids) == 1:
            args["client_id"] = self.client_ids[0]                                   # the plan names exactly one client, so a missing client_id is unambiguous
            note = "client_id filled in from the plan (the call omitted it)"
        ikey = call_key(call.tool, args, schema)
        again = "repeated invalid call - " if ikey in self.invalid else ""
        bad = self._invalid(call, args, schema, props)
        if bad:
            self.invalid.add(ikey)
            return "rejected", again + bad, args
        key = ikey
        if key in self.done or key in batch_keys:
            return "duplicate", f"{call.tool} was already called with these arguments; use the earlier result", args
        if key in self.failed:
            return "rejected", (f"{call.tool} was already called with these identical arguments and failed; do not repeat it unchanged - "
                                f"correct the arguments or choose another tool"), args
        return "ok", note, args

    def _invalid(self, call: PlannedCall, args: dict, schema: dict, props: dict) -> Optional[str]:
        """Why the call cannot be sent (unknown / missing / ill-typed argument, client not in the plan), or None."""
        unknown = sorted(set(args) - set(props))
        if unknown:
            return f"unknown argument(s) {unknown} for {call.tool}; allowed: {sorted(props)}"
        try:
            jsonschema.validate(args, schema)
        except jsonschema.ValidationError as e:
            where = ".".join(str(p) for p in e.absolute_path) or "arguments"
            return f"invalid argument {where} for {call.tool}: {e.message[:200]}"
        if "client_id" in props and args.get("client_id") not in self.client_ids:
            return f"client_id {args.get('client_id')!r} is not in the plan; allowed: {self.client_ids}"
        return None
