"""
Strategy registry - lookup and instantiate routing strategies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from app.models import (
    ROUTING_STRATEGY_EXPENSE_ACCOUNT,
    ROUTING_STRATEGY_CONTRACT_TYPE,
    ROUTING_STRATEGY_CATEGORY,
    ROUTING_STRATEGY_DIRECT,
)
from app.routing import RoutingStrategy
from app.routing.budget import ExpenseAccountRoutingStrategy
from app.routing.contracts import ContractTypeRoutingStrategy
from app.routing.supply_orders import CategoryRoutingStrategy

if TYPE_CHECKING:
    from app.models import ApprovalGroup, WorkLine, WorkTypeConfig


class DirectRoutingStrategy(RoutingStrategy):
    """Routes directly to a configured default approval group."""

    def __init__(self, default_approval_group: Optional["ApprovalGroup"] = None):
        self.default_approval_group = default_approval_group

    def get_approval_group(self, line: "WorkLine") -> Optional["ApprovalGroup"]:
        """Return the configured default approval group."""
        return self.default_approval_group


# Strategy instances (singletons for stateless strategies)
_STRATEGIES = {
    ROUTING_STRATEGY_EXPENSE_ACCOUNT: ExpenseAccountRoutingStrategy(),
    ROUTING_STRATEGY_CONTRACT_TYPE: ContractTypeRoutingStrategy(),
    ROUTING_STRATEGY_CATEGORY: CategoryRoutingStrategy(),
}


def get_routing_strategy(strategy_name: str, config: Optional["WorkTypeConfig"] = None) -> RoutingStrategy:
    """
    Get a routing strategy by name.

    Args:
        strategy_name: The strategy identifier (e.g., "expense_account")
        config: Optional work type config for strategies that need configuration

    Returns:
        The appropriate routing strategy instance
    """
    if strategy_name == ROUTING_STRATEGY_DIRECT:
        default_group = config.default_approval_group if config else None
        return DirectRoutingStrategy(default_group)

    strategy = _STRATEGIES.get(strategy_name)
    if strategy is None:
        raise ValueError(f"Unknown routing strategy: {strategy_name}")

    return strategy


def get_approval_group_for_line(line: "WorkLine") -> Optional["ApprovalGroup"]:
    """
    Get the approval group for a work line using the work type's routing strategy.

    This is the main entry point for routing. It looks up the work type's
    configured routing strategy and uses it to determine the approval group.

    Args:
        line: The work line to route

    Returns:
        The ApprovalGroup that should review this line, or None
    """
    # Get the work type config
    work_item = line.work_item
    if not work_item:
        return None

    portfolio = work_item.portfolio
    if not portfolio:
        return None

    work_type = portfolio.work_type
    if not work_type or not work_type.config:
        return None

    config = work_type.config
    strategy = get_routing_strategy(config.routing_strategy, config)
    return strategy.get_approval_group(line)
