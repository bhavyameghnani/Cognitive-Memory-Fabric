"""Tool Selector / MCP contracts (docs/agent_prompts/tool_selector_agent.md, sections B, D, F)."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

# the 13 tools of the catalogue (names fixed so the Planner's tool list matches)
TOOL_NAMES = ["get_client_profile", "get_client_portfolio", "get_portfolio_history", "get_client_goals", "get_client_liabilities",
              "calculate_asset_allocation", "calculate_portfolio_concentration", "compare_portfolio_snapshots", "calculate_portfolio_returns",
              "calculate_liquidity_ratio", "calculate_goal_funding_gap", "get_market_data", "get_source_evidence"]


class Provenance(BaseModel):
    source: str
    note: str = "All operational values are synthetic."
    synthetic: bool = True
    row_provenance: list[str] = Field(default_factory=list)


class Envelope(BaseModel):
    """Every MCP tool result."""
    tool: str
    client_id: Optional[str] = None
    as_of: Optional[str] = None
    data: dict[str, Any] = Field(default_factory=dict)
    provenance: Provenance
    limitations: list[str] = Field(default_factory=list)


class PlannedCall(BaseModel):
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    rationale: str = Field("", max_length=240)


class SelectorDecision(BaseModel):
    """What Qwen returns each iteration."""
    action: Literal["call_tools", "finish"]
    calls: list[PlannedCall] = Field(default_factory=list, max_length=5)
    missing_information: list[str] = Field(default_factory=list)
    reasoning: str = Field("", max_length=300)


ExecStatus = Literal["success", "error", "rejected", "duplicate", "timeout"]


class ToolExecution(BaseModel):
    id: str  # EX-<request_id>-NN
    iteration: int
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    status: ExecStatus
    result: Optional[Envelope] = None
    error: Optional[str] = None
    duration_ms: int = 0
    rationale: str = ""


class ToolSelectorState(BaseModel):
    request_id: str
    executions: list[ToolExecution] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)
    completion_status: Literal["complete", "partial", "failed", "not_needed"] = "complete"
    stop_reason: str = ""
    fallback_used: bool = False
    iterations: int = 0
    tools_discovered: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)

    def successes(self) -> list[ToolExecution]:
        return [e for e in self.executions if e.status == "success"]
