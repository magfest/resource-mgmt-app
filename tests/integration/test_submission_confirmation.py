"""
Tests for notify_submission_confirmation() — the BUDGET-only paper-trail
email sent to the submitting department after a request leaves DRAFT.

These tests exercise the audience selection, BUDGET-only gate, and
template-context wiring at the function level. The integration test for
the route-level wiring lives separately.
"""
from unittest.mock import patch

import pytest

from app import db
from app.models import (
    User,
    Department,
    Division,
    DepartmentMembership,
    DivisionMembership,
    EmailTemplate,
    WorkType,
    WorkTypeConfig,
    WorkPortfolio,
    WorkItem,
    WorkLine,
    BudgetLineDetail,
    ROUTING_STRATEGY_CATEGORY,
    REQUEST_KIND_PRIMARY,
    WORK_ITEM_STATUS_DRAFT,
    WORK_LINE_STATUS_PENDING,
    REVIEW_STAGE_APPROVAL_GROUP,
)
from app.services.notifications import notify_submission_confirmation


@pytest.fixture
def seed_submission_confirmation_template(app):
    """
    Seed the submission_confirmation EmailTemplate row. The test
    conftest uses db.create_all() which builds tables from the ORM
    but does NOT run Alembic data-seeding migrations, so any test
    that exercises render_email_template must seed the row itself.
    """
    db.session.add(EmailTemplate(
        template_key='submission_confirmation',
        name='Budget Submission Confirmation',
        description='test seed',
        subject='[MAGFest Budget] Submission received - {{ work_item.public_id }}',
        body_text=(
            "Your budget request was submitted.\n\n"
            "Submitted: {{ line_count }} line"
            "{{ 's' if line_count != 1 else '' }} totaling "
            "${{ '%.2f'|format(total_requested_dollars) }} requested.\n"
        ),
        is_active=True,
        version=1,
    ))
    db.session.commit()


class TestSubmissionConfirmation:
    """Verify the BUDGET-only submission confirmation email behavior."""

    def test_fires_for_budget_and_includes_line_totals(
        self, app, seed_draft_work_item, seed_submission_confirmation_template,
    ):
        """
        For a BUDGET submission, every dept member gets one email with
        the submission_confirmation template, and the rendered context
        carries the computed line_count + total_requested_dollars.
        """
        data = seed_draft_work_item
        # Add a second dept member so we can confirm multi-recipient send.
        member = User(
            id="test:dept-member", email="member@test.local",
            display_name="Dept Member", is_active=True,
        )
        db.session.add(member)
        db.session.add(DepartmentMembership(
            user_id=member.id,
            department_id=data["department"].id,
            event_cycle_id=data["cycle"].id,
        ))
        db.session.commit()

        # Patch only the SES-bound send_email so we can assert call args
        # without hitting the rate limiter / SUPPRESSED log path.
        with patch("app.services.notifications.send_email", return_value=True) as send:
            sent = notify_submission_confirmation(data["work_item"])

        assert sent == 1
        assert send.call_count == 1
        call = send.call_args
        assert call.kwargs["to"] == "member@test.local"
        assert call.kwargs["template_key"] == "submission_confirmation"
        # Line math: fixture has 1 line at $50 (5000 cents, qty 1).
        assert "1 line totaling $50.00 requested" in call.kwargs["body_text"]

    def test_skipped_for_non_budget_worktype(self, app, seed_draft_work_item):
        """
        Non-BUDGET worktypes (e.g. TECHOPS) get a silent zero — the
        submit route stays worktype-neutral and the function gates
        itself.
        """
        data = seed_draft_work_item
        # Re-point the portfolio's work_type to a new non-BUDGET type.
        techops_wt = WorkType(code="TECHOPS", name="TechOps", is_active=True)
        db.session.add(techops_wt)
        db.session.flush()
        db.session.add(WorkTypeConfig(
            work_type_id=techops_wt.id, url_slug="techops",
            public_id_prefix="TOPS", line_detail_type="techops",
            routing_strategy=ROUTING_STRATEGY_CATEGORY,
            uses_dispatch=False, has_admin_final=False,
        ))
        data["portfolio"].work_type_id = techops_wt.id
        db.session.commit()

        with patch("app.services.notifications.send_email") as send:
            sent = notify_submission_confirmation(data["work_item"])

        assert sent == 0
        send.assert_not_called()

    def test_recipients_include_division_members(
        self, app, seed_draft_work_item, seed_submission_confirmation_template,
    ):
        """
        Division-membership users count as dept members for this
        notification — same audience semantics as needs_attention /
        finalized.
        """
        data = seed_draft_work_item
        # Wire the dept into a division and add a division-only member.
        data["department"].division_id = data["division"].id
        div_user = User(
            id="test:div-head", email="divhead@test.local",
            display_name="Division Head", is_active=True,
        )
        db.session.add(div_user)
        db.session.add(DivisionMembership(
            user_id=div_user.id,
            division_id=data["division"].id,
            event_cycle_id=data["cycle"].id,
        ))
        db.session.commit()

        with patch("app.services.notifications.send_email", return_value=True) as send:
            sent = notify_submission_confirmation(data["work_item"])

        recipients = {c.kwargs["to"] for c in send.call_args_list}
        assert "divhead@test.local" in recipients
        assert sent == len(recipients)