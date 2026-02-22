"""
Contract routing strategy - routes via ContractType.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from app.routing import RoutingStrategy

if TYPE_CHECKING:
    from app.models import ApprovalGroup, WorkLine


class ContractTypeRoutingStrategy(RoutingStrategy):
    """Routes contract lines through their contract type's approval group."""

    def get_approval_group(self, line: "WorkLine") -> Optional["ApprovalGroup"]:
        """
        Get approval group from the line's contract type.

        Args:
            line: The work line with contract_detail

        Returns:
            The contract type's approval group, or None
        """
        if line.contract_detail and line.contract_detail.contract_type:
            return line.contract_detail.contract_type.approval_group
        return None
