"""
Pluggable routing strategies for determining approval groups.

Each work type can use a different routing strategy to determine
which approval group should review a given line.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from app.models import ApprovalGroup, WorkLine


class RoutingStrategy(ABC):
    """Base class for all routing strategies."""

    @abstractmethod
    def get_approval_group(self, line: "WorkLine") -> Optional["ApprovalGroup"]:
        """
        Determine the approval group for a work line.

        Args:
            line: The work line to route

        Returns:
            The ApprovalGroup that should review this line, or None if no routing applies
        """
        pass
