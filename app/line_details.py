"""
Helper functions for working with type-specific line details.

Each work type has its own line detail model (BudgetLineDetail, ContractLineDetail,
SupplyOrderLineDetail). This module provides generic access to these details.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Optional, Union

if TYPE_CHECKING:
    from app.models import (
        BudgetLineDetail,
        ContractLineDetail,
        SupplyOrderLineDetail,
        WorkLine,
    )

LineDetail = Union["BudgetLineDetail", "ContractLineDetail", "SupplyOrderLineDetail"]


def get_line_detail(line: "WorkLine") -> Optional[LineDetail]:
    """
    Get the type-specific detail for any work line.

    Args:
        line: The work line to get details for

    Returns:
        The line detail (budget, contract, or supply), or None if not found
    """
    return line.budget_detail or line.contract_detail or line.supply_detail


def get_line_amount_cents(line: "WorkLine") -> int:
    """
    Get the requested amount in cents for any line type.

    Args:
        line: The work line to get the amount for

    Returns:
        The amount in cents, or 0 if no detail or amount found
    """
    detail = get_line_detail(line)
    if detail is None:
        return 0

    # Budget lines: unit_price_cents * quantity
    if hasattr(detail, "unit_price_cents") and hasattr(detail, "quantity"):
        return int(Decimal(detail.unit_price_cents) * detail.quantity)

    # Contract lines: contract_amount_cents
    if hasattr(detail, "contract_amount_cents"):
        return detail.contract_amount_cents or 0

    # Supply order lines: unit_cost_cents * quantity_requested
    if hasattr(detail, "quantity_requested"):
        if detail.item and detail.item.unit_cost_cents:
            return detail.item.unit_cost_cents * detail.quantity_requested
        return 0

    return 0


def get_line_description(line: "WorkLine") -> Optional[str]:
    """
    Get the description for any line type.

    Args:
        line: The work line to get the description for

    Returns:
        The description, or None if not found
    """
    detail = get_line_detail(line)
    if detail is None:
        return None

    # Budget and contract lines have description field
    if hasattr(detail, "description"):
        return detail.description

    # Supply order lines use requester_notes
    if hasattr(detail, "requester_notes"):
        return detail.requester_notes

    return None


def get_line_routing_approval_group(line: "WorkLine"):
    """
    Get the snapshot approval group that was captured at submission time.

    Args:
        line: The work line

    Returns:
        The routed approval group, or None
    """
    detail = get_line_detail(line)
    if detail is None:
        return None

    if hasattr(detail, "routed_approval_group"):
        return detail.routed_approval_group

    return None


def get_line_type_name(line: "WorkLine") -> str:
    """
    Get the type name for a work line.

    Args:
        line: The work line

    Returns:
        "budget", "contract", "supply", or "unknown"
    """
    if line.budget_detail:
        return "budget"
    if line.contract_detail:
        return "contract"
    if line.supply_detail:
        return "supply"
    return "unknown"
