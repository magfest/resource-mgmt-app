"""Integration tests for /approvals/AV_TEAM/ queue rendering (Task 35).

Reuses the 'ta_' fixtures from test_av_team_actions.py to avoid fixture-name
collisions.  All tests import those fixtures explicitly so pytest can collect
this file independently.
"""
from __future__ import annotations

# ---------------------------------------------------------------------------
# Re-use fixtures from test_av_team_actions via import
# ---------------------------------------------------------------------------
# pytest discovers fixtures by import name — importing them here makes them
# available to this module's test classes without re-declaring them.

from tests.integration.test_av_team_actions import (  # noqa: F401
    ta_av_base,
    ta_av_team_client,
    ta_dept_member_client,
    ta_super_admin_client,
    ta_av_submitted_request,
    ta_av_logged_request,
    ta_av_draft_request,
    ta_av_request_with_needs_info,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _queue_url(group_code: str = "AV_TEAM") -> str:
    return f"/approvals/{group_code}"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestAVTeamQueue:

    def test_av_team_member_sees_queue_200(
        self, ta_av_team_client, ta_av_submitted_request,
    ):
        """AV team member loads /approvals/AV_TEAM/ → 200."""
        response = ta_av_team_client.get(_queue_url())
        assert response.status_code == 200

    def test_av_team_queue_shows_public_id(
        self, ta_av_team_client, ta_av_submitted_request,
    ):
        """The pending-requests section shows the submitted request's public_id."""
        response = ta_av_team_client.get(_queue_url())
        assert response.status_code == 200
        assert ta_av_submitted_request.public_id.encode() in response.data

    def test_av_team_queue_shows_dept_name(
        self, ta_av_team_client, ta_av_submitted_request,
    ):
        """The queue table shows the department name so AV team can prioritise."""
        response = ta_av_team_client.get(_queue_url())
        dept_name = ta_av_submitted_request.portfolio.department.name
        assert dept_name.encode() in response.data

    def test_av_team_queue_shows_space_name(
        self, ta_av_team_client, ta_av_submitted_request,
    ):
        """Space name is rendered in the pending-requests table."""
        response = ta_av_team_client.get(_queue_url())
        space_name = ta_av_submitted_request.av_request_detail.space.name
        assert space_name.encode() in response.data

    def test_av_team_queue_shows_priority(
        self, ta_av_team_client, ta_av_submitted_request,
    ):
        """Priority badge is rendered for the pending request (MUST_HAVE → 'MUST HAVE')."""
        response = ta_av_team_client.get(_queue_url())
        # The dashboard renders MUST_HAVE as 'MUST HAVE' in the badge
        assert b"MUST HAVE" in response.data

    def test_av_team_queue_shows_review_link(
        self, ta_av_team_client, ta_av_submitted_request,
    ):
        """A 'Review' button linking to the work item detail page is present."""
        response = ta_av_team_client.get(_queue_url())
        item = ta_av_submitted_request
        # URL pattern: /<event>/<dept>/av/item/<public_id>
        url_fragment = (
            f"/{item.portfolio.event_cycle.code}"
            f"/{item.portfolio.department.code}"
            f"/av/item/{item.public_id}"
        ).lower()
        assert url_fragment.encode() in response.data.lower()

    def test_dept_member_cannot_access_av_team_queue(
        self, ta_dept_member_client, ta_av_base,
    ):
        """Non-AV-team users get 403 when accessing /approvals/AV_TEAM/."""
        response = ta_dept_member_client.get(_queue_url())
        assert response.status_code == 403

    def test_av_team_queue_empty_when_no_pending(
        self, ta_av_team_client, ta_av_base,
    ):
        """Queue renders 200 even when there are no pending requests."""
        # No ta_av_submitted_request fixture — queue should be empty
        response = ta_av_team_client.get(_queue_url())
        assert response.status_code == 200
        assert b"No requests pending review in this queue." in response.data

    def test_av_team_queue_shows_kicked_back_space(
        self, ta_av_team_client, ta_av_request_with_needs_info,
    ):
        """Kicked-back section shows Space name for NEEDS_INFO lines."""
        response = ta_av_team_client.get(_queue_url())
        assert response.status_code == 200
        space_name = ta_av_request_with_needs_info.av_request_detail.space.name
        assert space_name.encode() in response.data

    def test_av_team_queue_no_budget_columns(
        self, ta_av_team_client, ta_av_submitted_request,
    ):
        """AV queue must not render BUDGET-specific column headers (Expense Account)."""
        response = ta_av_team_client.get(_queue_url())
        assert b"Expense Account" not in response.data

    def test_logged_request_appears_in_pending_when_pending_review(
        self, ta_av_team_client, ta_av_submitted_request,
    ):
        """A SUBMITTED request with a PENDING review row is in the pending queue.

        This ensures build_approval_queues finds it via WorkLineReview.status.
        """
        response = ta_av_team_client.get(_queue_url())
        assert ta_av_submitted_request.public_id.encode() in response.data
