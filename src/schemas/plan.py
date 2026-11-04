"""Planner contract."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

TaskType = Literal["retrieval", "summarization", "comparison", "calculation", "forecast_feasibility", "history", "followup_review"]
Agent = Literal["memory_orchestrator", "tool_selector", "report_agent"]


class Subtask(BaseModel):
    id: str = Field(..., pattern=r"^T\d{1,2}$")
    description: str = Field(..., min_length=5, max_length=240)
    agent: Agent
    depends_on: list[str] = Field(default_factory=list)


class PlannerLLMOutput(BaseModel):
    """What Qwen is asked to fill in. Client IDs and tool names are validated/overridden by deterministic code."""
    user_intent: str = Field(..., min_length=5, max_length=300)
    task_type: TaskType
    time_period: Optional[str] = Field(None, max_length=80, description="e.g. 'last six months', 'January to June 2026', or null")
    needs_history: bool
    needs_calculation: bool
    required_memory_categories: list[Literal["episodic", "semantic", "procedural", "working"]]
    memory_topics: list[str] = Field(default_factory=list, description="from the allowed topic list")
    required_data: list[str] = Field(default_factory=list, description="short phrases, e.g. 'current portfolio'")
    required_tools: list[str] = Field(default_factory=list, description="tool names from the catalogue")
    subtasks: list[Subtask] = Field(..., min_length=1, max_length=8)
    missing_information: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(..., min_length=1, max_length=6)


class ExecutionPlan(BaseModel):
    request_id: str
    plan_id: str
    request_text: str
    client_ids: list[str]
    user_intent: str
    task_type: TaskType
    time_period: Optional[str] = None
    needs_history: bool
    needs_calculation: bool
    subtasks: list[Subtask]
    required_memory_categories: list[str]
    memory_topics: list[str]
    required_data: list[str]
    required_tools: list[str]
    dependencies: dict[str, list[str]]
    execution_order: list[str]
    parallelizable_tasks: list[list[str]]
    missing_information: list[str]
    success_criteria: list[str]
    revision: int = 0
    planner_notes: list[str] = Field(default_factory=list)
