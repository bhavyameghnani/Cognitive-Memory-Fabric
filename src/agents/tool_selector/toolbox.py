"""Toolbox: a small wrapper over mcp.Client. Default transport is in-process (same protocol, no subprocess); stdio spawns `python -m mcp_server.server`."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

from mcp import Client
from mcp.client.stdio import StdioServerParameters

SRC = Path(__file__).resolve().parents[2]
_ERR_PREFIX = "Error executing tool "


class ToolCallError(RuntimeError):
    """The server answered with a tool error; the message is what the model gets to see."""


class Toolbox:
    def __init__(self, transport: str = "inprocess", env: Optional[dict[str, str]] = None, server: Any = None) -> None:
        if transport not in ("inprocess", "stdio"):
            raise ValueError("transport must be 'inprocess' or 'stdio'")
        self.transport, self.env, self._server = transport, env, server
        self._client: Optional[Client] = None
        self._depth = 0

    def _target(self) -> Any:
        if self.transport == "stdio":
            env = {k: v for k, v in os.environ.items() if k.startswith("CMF_")}
            env.update(self.env or {})
            env["PYTHONPATH"] = str(SRC) + os.pathsep + env.get("PYTHONPATH", "")
            return StdioServerParameters(command=sys.executable, args=["-m", "mcp_server.server"], env=env, cwd=str(SRC.parent))
        if self._server is None:
            from mcp_server.server import build_server
            self._server = build_server()
        return self._server

    async def __aenter__(self) -> "Toolbox":
        if self._depth == 0:
            self._client = Client(self._target())
            await self._client.__aenter__()
        self._depth += 1
        return self

    async def __aexit__(self, *exc) -> None:
        self._depth -= 1
        if self._depth == 0 and self._client is not None:
            client, self._client = self._client, None
            await client.__aexit__(*exc)

    async def list_tools(self) -> list[dict]:
        assert self._client is not None, "use 'async with toolbox'"
        res = await self._client.list_tools()
        return [{"name": t.name, "description": t.description or "", "input_schema": t.input_schema} for t in res.tools]

    async def call(self, name: str, arguments: dict) -> dict:
        assert self._client is not None, "use 'async with toolbox'"
        res = await self._client.call_tool(name, arguments)
        text = "".join(getattr(c, "text", "") for c in res.content)
        if res.is_error:
            if text.startswith(_ERR_PREFIX):  # "Error executing tool <name>: <message>"
                text = text.split(": ", 1)[-1]
            raise ToolCallError(text.strip()[:600])
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise ToolCallError(f"tool {name} returned non-JSON output") from e
