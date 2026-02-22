"""
Budget routing strategy - routes via ExpenseAccount.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from app.routing import RoutingStrategy

if TYPE_CHECKING:
    from app.models import ApprovalGroup, WorkLine


class ExpenseAccountRoutingStrategy(RoutingStrategy):
    """Routes budget lines through their expense account's approval group."""

    def get_approval_group(self, line: "WorkLine") -> Optional["ApprovalGroup"]:
        """
        Get approval group from the line's budget detail expense account.

        Args:
            line: The work line with budget_detail

        Returns:
            The expense account's approval group, or None
        """
        if line.budget_detail and line.budget_detail.expense_account:
            return line.budget_detail.expense_account.approval_group
        return None
