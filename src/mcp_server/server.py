"""MCP server: protocol endpoint for the deterministic, read-only financial tools. It lists tools, validates arguments, runs Python and makes no decisions.

Run over stdio:  python -m mcp_server.server   (with src on PYTHONPATH)
Configuration:   CMF_DATABASE_URL (default sqlite:///build/cmf_ops_smoke.db), CMF_ALLOWED_CLIENTS (comma separated session allow-list).

Deliberately NOT built (no data source exists; they would force invented results):
get_pending_actions, calculate_risk_metrics, calculate_rebalancing_scenarios, search_market_research, search_economic_indicators, search_client_documents.
No write tool and no SQL tool exists; all queries are parameterised and live inside cmf_data (read-only connection).
Only synthetic data is served; PostgreSQL parity is unvalidated (SQLite smoke database only).
"""
from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):  # allow `python src/mcp_server/server.py`
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp.server.mcpserver import MCPServer  # noqa: E402

from mcp_server.tools import register  # noqa: E402


def build_server() -> MCPServer:
    server = MCPServer("cmf-financial-tools", instructions="Read-only deterministic financial tools over synthetic client data. Every result carries provenance and limitations.")
    register(server)
    return server


mcp = build_server()

if __name__ == "__main__":
    mcp.run("stdio")
