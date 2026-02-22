"""
Supply order routing strategy - routes via SupplyCategory.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from app.routing import RoutingStrategy

if TYPE_CHECKING:
    from app.models import ApprovalGroup, WorkLine


class CategoryRoutingStrategy(RoutingStrategy):
    """Routes supply order lines through their item's category approval group."""

    def get_approval_group(self, line: "WorkLine") -> Optional["ApprovalGroup"]:
        """
        Get approval group from the line's supply item category.

        Args:
            line: The work line with supply_detail

        Returns:
            The category's approval group, or None
        """
        if line.supply_detail and line.supply_detail.item:
            return line.supply_detail.item.category.approval_group
        return None
