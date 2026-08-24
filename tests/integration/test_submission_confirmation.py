"""
Tests for notify_submission_confirmation() — the BUDGET-only paper-trail
email queued for the submitting department after a request leaves DRAFT.

These tests exercise the audience selection, BUDGET-only gate, and
template-context wiring at the function level. The function queues outbox
rows and does not render; the body assertions moved to the drainer's tests.
The integration test for the route-level wiring lives separately.
"""
import json
from unittest.mock import patch

import pytest

from app import db
from app.models import (
    User,
    Department,
    Division,
    DepartmentMembership,
    DivisionMembership,
    EmailOutbox,
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
        For a BUDGET submission, every dept member gets one outbox row on
        the submission_confirmation template, and the stored context carries
        the computed line_count + total_requested_dollars.
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

        queued = notify_submission_confirmation(data["work_item"])
        db.session.commit()

        assert queued == 1
        rows = db.session.query(EmailOutbox).all()
        assert len(rows) == 1
        assert rows[0].recipient_email == "member@test.local"
        assert rows[0].template_key == "submission_confirmation"
        # Line math: fixture has 1 line at $50 (5000 cents, qty 1). The row
        # carries the numbers; the body is rendered from them at send time.
        context = json.loads(rows[0].context_json)
        assert context["line_count"] == 1
        assert context["total_requested_dollars"] == 50.0

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

        queued = notify_submission_confirmation(data["work_item"])

        assert queued == 0
        assert db.session.query(EmailOutbox).count() == 0

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

        queued = notify_submission_confirmation(data["work_item"])
        db.session.commit()

        recipients = {r.recipient_email for r in db.session.query(EmailOutbox).all()}
        assert "divhead@test.local" in recipients
        assert queued == len(recipients)


class TestSubmissionConfirmationWiring:
    """Verify the submit route actually invokes the confirmation function."""

    def test_submit_route_calls_notify_submission_confirmation(
        self, app, client, seed_draft_work_item,
    ):
        """
        POSTing the submit action on a BUDGET work item triggers
        notify_submission_confirmation alongside the existing admin
        notification. Patching both notify calls keeps the test focused
        on the route wiring rather than template rendering or SES.
        """
        with client.session_transaction() as sess:
            sess["active_user_id"] = "test:admin"

        with patch(
            "app.services.notifications.notify_work_item_submitted",
            return_value=1,
        ), patch(
            "app.services.notifications.notify_submission_confirmation",
            return_value=2,
        ) as confirm_mock:
            response = client.post(
                "/TST2026/TESTDEPT/budget/item/TST2026-TESTDEPT-BUD-1/submit",
                follow_redirects=False,
            )

        assert response.status_code == 302
        confirm_mock.assert_called_once()
        called_work_item = confirm_mock.call_args.args[0]
        assert called_work_item.public_id == "TST2026-TESTDEPT-BUD-1"