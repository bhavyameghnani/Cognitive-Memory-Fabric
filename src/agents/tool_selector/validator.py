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

    def validate(self, call: PlannedCall, batch_keys: set[str]) -> tuple[str, Optional[str], dict]:
        """Returns (verdict, message, cleaned_args); verdict in ok | rejected | duplicate."""
        args = clean_args(call.arguments)
        t = self.tools.get(call.tool)
        if t is None:
            return "rejected", f"unknown tool '{call.tool}'; choose one of: {', '.join(sorted(self.tools))}", args
        schema = t["input_schema"]
        props = schema.get("properties", {})
        unknown = sorted(set(args) - set(props))
        if unknown:
            return "rejected", f"unknown argument(s) {unknown} for {call.tool}; allowed: {sorted(props)}", args
        try:
            jsonschema.validate(args, schema)
        except jsonschema.ValidationError as e:
            where = ".".join(str(p) for p in e.absolute_path) or "arguments"
            return "rejected", f"invalid argument {where} for {call.tool}: {e.message[:200]}", args
        if "client_id" in props and args.get("client_id") not in self.client_ids:
            return "rejected", f"client_id {args.get('client_id')!r} is not in the plan; allowed: {self.client_ids}", args
        key = call_key(call.tool, args, schema)
        if key in self.done or key in batch_keys:
            return "duplicate", f"{call.tool} was already called with these arguments; use the earlier result", args
        return "ok", None, args
