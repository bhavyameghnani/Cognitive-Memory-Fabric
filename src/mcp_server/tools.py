"""The single registration table of the 13 MCP tools. Read-only; no write tool and no SQL tool exists.

Argument schemas come from the type hints: Literal[...] enumerations are listed in the schema and rejected by the SDK before execution.
Input errors raise ToolError so the message reaches the client (a plain exception only yields 'Error executing tool').
"""
from __future__ import annotations

import json
from typing import Callable, Literal, Optional

from mcp.server.mcpserver.exceptions import ToolError

from .services import FinancialServices, ServiceError
from .session import AccessDenied

SERVICES = FinancialServices()
DATE_NOTE = "YYYY-MM-DD, must be a snapshot month-end (2026-01-31 ... 2026-06-30)"


def _run(fn: Callable[..., dict], **kw) -> str:
    try:
        return json.dumps(fn(**kw), default=str)
    except (ServiceError, AccessDenied, KeyError) as e:
        raise ToolError(str(e.args[0]) if isinstance(e, KeyError) else str(e)) from e


def get_client_profile(client_id: str) -> str:
    """Client profile as of 2026-06-30: age, income, monthly expenses, dependants, retirement target age, risk category, liquidity target months. Partly synthetic."""
    return _run(SERVICES.get_client_profile, client_id=client_id)


def get_client_portfolio(client_id: str, as_of_date: Optional[str] = None, investable_only: bool = True) -> str:
    """Portfolio positions now, or at a snapshot month-end (as_of_date YYYY-MM-DD), with asset class, sector, issuer, market value and liquidity. Excludes deferred compensation and private interests unless investable_only=false."""
    return _run(SERVICES.get_client_portfolio, client_id=client_id, as_of_date=as_of_date, investable_only=investable_only)


def get_portfolio_history(client_id: str) -> str:
    """Portfolio total, position count and asset-class percentages at each of the six month-end snapshots."""
    return _run(SERVICES.get_portfolio_history, client_id=client_id)


def get_client_goals(client_id: str, as_of_date: Optional[str] = None, include_history: bool = False) -> str:
    """Financial goals (type, target amount and date, confidence, status) valid at a date; include_history=true lists every goal version."""
    return _run(SERVICES.get_client_goals, client_id=client_id, as_of_date=as_of_date, include_history=include_history)


def get_client_liabilities(client_id: str) -> str:
    """Loans and recurring obligations: outstanding balance, rate, monthly payment, maturity."""
    return _run(SERVICES.get_client_liabilities, client_id=client_id)


def calculate_asset_allocation(client_id: str, as_of_date: Optional[str] = None,
                               group_by: Literal["asset_class", "liquidity_category", "sector", "account_type"] = "asset_class") -> str:
    """Percentage allocation of the investable portfolio by asset_class, liquidity_category, sector or account_type; percentages sum to 100."""
    return _run(SERVICES.calculate_asset_allocation, client_id=client_id, as_of_date=as_of_date, group_by=group_by)


def calculate_portfolio_concentration(client_id: str, as_of_date: Optional[str] = None,
                                      dimension: Literal["instrument", "issuer", "sector", "asset_class"] = "issuer", top_n: int = 5) -> str:
    """Concentration along one dimension: top-N share, Herfindahl index and group sizes. Funds are not looked through."""
    return _run(SERVICES.calculate_portfolio_concentration, client_id=client_id, as_of_date=as_of_date, dimension=dimension, top_n=top_n)


def compare_portfolio_snapshots(client_id: str, date_a: str, date_b: str) -> str:
    """Compare two snapshot month-ends (date_a earlier than date_b): added, removed and changed positions, allocation change and transactions between."""
    return _run(SERVICES.compare_portfolio_snapshots, client_id=client_id, date_a=date_a, date_b=date_b)


def calculate_portfolio_returns(client_id: str, start_date: str, end_date: str) -> str:
    """Price-only portfolio change between two snapshot month-ends: simple change, net external flows, flow-adjusted change and Modified Dietz percent. Not time-weighted."""
    return _run(SERVICES.calculate_portfolio_returns, client_id=client_id, start_date=start_date, end_date=end_date)


def calculate_liquidity_ratio(client_id: str, as_of_date: Optional[str] = None) -> str:
    """Months of expenses covered by cash equivalents (and by cash plus daily-liquid assets) against the client's liquidity target, with shortfall."""
    return _run(SERVICES.calculate_liquidity_ratio, client_id=client_id, as_of_date=as_of_date)


def calculate_goal_funding_gap(client_id: str, goal_id: Optional[str] = None, goal_type: Optional[str] = None, as_of_date: Optional[str] = None,
                               resources_basis: Literal["investable_portfolio", "cash_equivalents"] = "investable_portfolio") -> str:
    """Simple funding gap = goal target minus current resources, with coverage percent. Not a projection (no growth, inflation, contributions, drawdown or taxes)."""
    return _run(SERVICES.calculate_goal_funding_gap, client_id=client_id, goal_id=goal_id, goal_type=goal_type, as_of_date=as_of_date, resources_basis=resources_basis)


def get_market_data(as_of_date: Optional[str] = None) -> str:
    """The synthetic shared market path: monthly equity and bond returns up to a date. Fictional, not real market data."""
    return _run(SERVICES.get_market_data, as_of_date=as_of_date)


def get_source_evidence(client_id: str, entity_type: Optional[str] = None, entity_id: Optional[str] = None,
                        interaction_id: Optional[str] = None, turn_index: Optional[int] = None) -> str:
    """Evidence for a data record (entity_type such as goal, account, holding, liability, profile, transaction, with entity_id) or the text of a conversation turn (interaction_id with turn_index)."""
    return _run(SERVICES.get_source_evidence, client_id=client_id, entity_type=entity_type, entity_id=entity_id, interaction_id=interaction_id, turn_index=turn_index)


# the one registration table (names fixed so the Planner's tool list matches)
TOOL_TABLE: list[tuple[str, Callable[..., str]]] = [
    ("get_client_profile", get_client_profile), ("get_client_portfolio", get_client_portfolio), ("get_portfolio_history", get_portfolio_history),
    ("get_client_goals", get_client_goals), ("get_client_liabilities", get_client_liabilities), ("calculate_asset_allocation", calculate_asset_allocation),
    ("calculate_portfolio_concentration", calculate_portfolio_concentration), ("compare_portfolio_snapshots", compare_portfolio_snapshots),
    ("calculate_portfolio_returns", calculate_portfolio_returns), ("calculate_liquidity_ratio", calculate_liquidity_ratio),
    ("calculate_goal_funding_gap", calculate_goal_funding_gap), ("get_market_data", get_market_data), ("get_source_evidence", get_source_evidence)]


def register(server) -> None:
    for name, fn in TOOL_TABLE:
        server.add_tool(fn, name=name, description=fn.__doc__)
