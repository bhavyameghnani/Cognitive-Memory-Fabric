"""HarnessToolbox: the CMF-boundary enforcement point for tool execution.

The Tool Selector executes MCP tools through Toolbox (not through Strands tool execution), so the shared tool budget, the per-call timeout and the
permission gate are applied here, around Toolbox.call.  A denial surfaces as ToolCallError, which the Tool Selector already records as a failed
execution and shows to the model; the call never reaches the MCP server.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

from agents.tool_selector.toolbox import ToolCallError, Toolbox
from harness.budget import BudgetExceeded, RequestBudget
from harness.permission_gate import AuthContext, PermissionDenied, PermissionGate


class HarnessToolbox:
    def __init__(self, inner: Toolbox, *, budget: RequestBudget, gate: PermissionGate, auth: AuthContext, emit: Callable[..., None], agent: str = "tool_selector") -> None:
        self.inner, self.budget, self.gate, self.auth, self.emit, self.agent = inner, budget, gate, auth, emit, agent

    async def __aenter__(self) -> "HarnessToolbox":
        await self.inner.__aenter__()
        return self

    async def __aexit__(self, *exc) -> None:
        await self.inner.__aexit__(*exc)

    async def list_tools(self) -> list[dict]:
        return await self.inner.list_tools()

    async def call(self, name: str, arguments: dict) -> dict:
        client = (arguments or {}).get("client_id")
        try:
            self.gate.check_tool(self.auth, name, client)
            self.budget.begin_tool_call(self.agent, name)
        except PermissionDenied as e:
            self.emit("harness.tool", "denied", tool=name, client_id=client, code=e.code, detail=e.detail)
            raise ToolCallError(f"permission denied: {e}") from e
        except BudgetExceeded as e:
            self.emit("harness.tool", "budget_denied", tool=name, client_id=client, reason=e.reason, detail=e.detail)
            raise ToolCallError(str(e)) from e
        t0 = time.perf_counter()
        try:
            res = await asyncio.wait_for(self.inner.call(name, arguments), timeout=self.budget.tool_timeout())
        except asyncio.TimeoutError:
            self.emit("harness.tool", "timeout", tool=name, client_id=client, ms=int((time.perf_counter() - t0) * 1000))
            raise
        # data ownership: a result for a different client than the one authorised is dropped, never forwarded
        owner = res.get("client_id") if isinstance(res, dict) else None
        if owner and not self.gate.owns(self.auth, owner):
            self.emit("harness.tool", "result_denied", tool=name, client_id=owner)
            raise ToolCallError(f"permission denied: result belongs to {owner}")
        self.emit("harness.tool", "call", tool=name, client_id=client, ms=int((time.perf_counter() - t0) * 1000))
        return res


def wrap_factory(factory: Callable[[], Any], **kw) -> Callable[[], HarnessToolbox]:
    return lambda: HarnessToolbox(factory(), **kw)
