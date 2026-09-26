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
        # budget_ prefixed since em3315c9a74b; the bare key no longer
        # exists in any real database.
        template_key='budget_submission_confirmation',
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
        assert rows[0].template_key == "budget_submission_confirmation"
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

class TestReceiptForEveryWorkType:
    """PR 1 removed the bare-key fallback target, so a work type either has a
    row of its own or gets nothing. Budget-worded mail to a TechOps department
    is worse than silence, so this refuses instead of falling back."""

    def _grant_membership(self, seed_workflow_data, user=None):
        """conftest seeds no DepartmentMembership, and the receipt audience is
        exactly that table (notifications.py:633). Without this the receipt
        queues nothing for any work type, including BUDGET."""
        from app import db
        from app.models import DepartmentMembership

        data = seed_workflow_data
        member = user or data["admin"]
        db.session.add(DepartmentMembership(
            user_id=member.id, department_id=data["department"].id,
            event_cycle_id=data["cycle"].id,
        ))
        db.session.commit()
        return member

    def _techops_item(self, seed_workflow_data):
        """A SUBMITTED TechOps work item in the seeded department."""
        from app import db
        from app.models import (
            WorkItem, WorkPortfolio, WorkType, WorkTypeConfig,
            REQUEST_KIND_PRIMARY, ROUTING_STRATEGY_CATEGORY,
            WORK_ITEM_STATUS_SUBMITTED,
        )
        data = seed_workflow_data
        wt = WorkType(code="TECHOPS", name="TechOps", is_active=True)
        db.session.add(wt)
        db.session.flush()
        db.session.add(WorkTypeConfig(
            work_type_id=wt.id, url_slug="techops", public_id_prefix="TEC",
            line_detail_type="techops",
            routing_strategy=ROUTING_STRATEGY_CATEGORY,
            uses_dispatch=False, has_admin_final=False,
        ))
        portfolio = WorkPortfolio(
            work_type_id=wt.id, event_cycle_id=data["cycle"].id,
            department_id=data["department"].id,
            created_by_user_id=data["admin"].id,
        )
        db.session.add(portfolio)
        db.session.flush()
        item = WorkItem(
            portfolio_id=portfolio.id, request_kind=REQUEST_KIND_PRIMARY,
            status=WORK_ITEM_STATUS_SUBMITTED,
            public_id="TST2026-TESTDEPT-TEC-1",
            created_by_user_id=data["admin"].id,
        )
        db.session.add(item)
        db.session.commit()
        return item

    def test_techops_receipt_queues_against_its_own_template(
        self, app, seed_workflow_data
    ):
        from app import db
        from app.models import EmailOutbox, EmailTemplate
        from app.services.notifications import notify_submission_confirmation

        self._grant_membership(seed_workflow_data)
        item = self._techops_item(seed_workflow_data)
        db.session.add(EmailTemplate(
            template_key="techops_submission_confirmation",
            name="TechOps Submission Confirmation",
            subject="[MAGFest TechOps] Request received",
            body_text="Your TechOps request was received.", is_active=True,
        ))
        db.session.commit()

        queued = notify_submission_confirmation(item)
        db.session.commit()

        assert queued > 0
        assert EmailOutbox.query.filter_by(
            template_key="techops_submission_confirmation").count() == queued

    def test_a_work_type_with_no_row_queues_nothing(
        self, app, seed_workflow_data, caplog
    ):
        """No techops_submission_confirmation row. Falling back would send
        "Your budget request was submitted and is waiting for a budget admin
        to dispatch it", which is wrong on every clause."""
        import logging
        from app import db
        from app.models import EmailOutbox
        from app.services.notifications import notify_submission_confirmation

        self._grant_membership(seed_workflow_data)
        item = self._techops_item(seed_workflow_data)

        with caplog.at_level(logging.WARNING):
            queued = notify_submission_confirmation(item)
        db.session.commit()

        assert queued == 0
        assert EmailOutbox.query.count() == 0
        assert "techops_submission_confirmation" in caplog.text

    def test_budget_receipt_is_unchanged(self, app, seed_draft_work_item):
        """PR 1 seeded budget_submission_confirmation. Budget must keep
        sending, and keep its money figure."""
        from app import db
        from app.models import EmailOutbox, EmailTemplate
        from app.services.notifications import notify_submission_confirmation

        self._grant_membership(seed_draft_work_item)
        db.session.add(EmailTemplate(
            template_key="budget_submission_confirmation",
            name="Budget Submission Confirmation",
            subject="[MAGFest Budget] Submission received",
            body_text="Totalling ${{ '%.2f'|format(total_requested_dollars) }}.",
            is_active=True,
        ))
        db.session.commit()

        queued = notify_submission_confirmation(seed_draft_work_item["work_item"])
        db.session.commit()

        assert queued > 0
        assert EmailOutbox.query.filter_by(
            template_key="budget_submission_confirmation").count() == queued

    def _with_contact(self, item, email):
        from app import db
        from app.models import TechOpsRequestDetail

        db.session.add(TechOpsRequestDetail(
            work_item_id=item.id, primary_contact_name="Named Contact",
            primary_contact_email=email,
        ))
        db.session.commit()
        return item

    def _seed_techops_template(self):
        from app import db
        from app.models import EmailTemplate

        db.session.add(EmailTemplate(
            template_key="techops_submission_confirmation",
            name="TechOps Submission Confirmation",
            subject="[MAGFest TechOps] Request received",
            body_text="Your TechOps request was received.", is_active=True,
        ))
        db.session.commit()

    def test_the_named_contact_is_included(self, app, seed_workflow_data):
        """The form asks for this on purpose; primary_contact_email is
        nullable=False. Ignoring it would make the field decorative."""
        from app import db
        from app.models import EmailOutbox
        from app.services.notifications import notify_submission_confirmation

        self._grant_membership(seed_workflow_data)
        item = self._with_contact(
            self._techops_item(seed_workflow_data), "contact@outside.local")
        self._seed_techops_template()

        notify_submission_confirmation(item)
        db.session.commit()

        recipients = {r.recipient_email for r in EmailOutbox.query.all()}
        assert "contact@outside.local" in recipients

    def test_a_contact_who_is_already_a_member_gets_one_email(
        self, app, seed_workflow_data
    ):
        """The union must not double-send to the common case."""
        from app import db
        from app.models import EmailOutbox
        from app.services.notifications import notify_submission_confirmation

        member = self._grant_membership(seed_workflow_data)
        item = self._with_contact(
            self._techops_item(seed_workflow_data), member.email)
        self._seed_techops_template()

        notify_submission_confirmation(item)
        db.session.commit()

        matching = EmailOutbox.query.filter_by(
            recipient_email=member.email).count()
        assert matching == 1


    def test_a_contact_differing_only_in_case_gets_one_email(
        self, app, seed_workflow_data
    ):
        """Requesters type the contact by hand. form_utils.py:431 strips it but
        never lower-cases, and User.email is returned verbatim, so a
        case-sensitive check queues two rows and SES sends both."""
        from app import db
        from app.models import EmailOutbox
        from app.services.notifications import notify_submission_confirmation

        member = self._grant_membership(seed_workflow_data)
        item = self._with_contact(
            self._techops_item(seed_workflow_data), member.email.upper())
        self._seed_techops_template()

        notify_submission_confirmation(item)
        db.session.commit()

        assert EmailOutbox.query.count() == 1, [
            r.recipient_email for r in EmailOutbox.query.all()
        ]

    def test_a_techops_item_with_no_detail_row_does_not_raise(
        self, app, seed_workflow_data
    ):
        """techops_detail is optional on the work item even though
        primary_contact_email is NOT NULL on the detail itself."""
        from app import db
        from app.services.notifications import notify_submission_confirmation

        self._grant_membership(seed_workflow_data)
        item = self._techops_item(seed_workflow_data)
        self._seed_techops_template()

        queued = notify_submission_confirmation(item)
        db.session.commit()

        assert queued > 0


class TestTheSubmitPathsActuallySendIt:
    """The receipt has to be called, not merely callable.

    notify_submission_confirmation had one call site, the BUDGET submit route
    (work_items/actions.py:101), which rejects any line without a budget_detail
    and so can never serve TechOps or Supply. Turning the function on for every
    work type did nothing until their own submit paths called it.
    """

    def _prepare(self, seed_workflow_data, code, slug, prefix):
        from app import db
        from app.models import (
            DepartmentMembership, EmailTemplate, WorkItem, WorkPortfolio,
            WorkType, WorkTypeConfig, REQUEST_KIND_PRIMARY,
            ROUTING_STRATEGY_CATEGORY, WORK_ITEM_STATUS_DRAFT,
        )
        data = seed_workflow_data
        db.session.add(DepartmentMembership(
            user_id=data["admin"].id, department_id=data["department"].id,
            event_cycle_id=data["cycle"].id,
        ))
        wt = WorkType(code=code, name=code.title(), is_active=True)
        db.session.add(wt)
        db.session.flush()
        db.session.add(WorkTypeConfig(
            work_type_id=wt.id, url_slug=slug, public_id_prefix=prefix,
            line_detail_type=slug, routing_strategy=ROUTING_STRATEGY_CATEGORY,
            uses_dispatch=False, has_admin_final=False,
        ))
        db.session.add(EmailTemplate(
            template_key=f"{code.lower()}_submission_confirmation",
            name=f"{code.title()} Submission Confirmation",
            subject=f"[MAGFest {code.title()}] Received",
            body_text="Received.", is_active=True,
        ))
        db.session.add(EmailTemplate(
            template_key=f"{code.lower()}_submitted",
            name=f"{code.title()} Submitted", subject="[x] New",
            body_text="New.", is_active=True,
        ))
        portfolio = WorkPortfolio(
            work_type_id=wt.id, event_cycle_id=data["cycle"].id,
            department_id=data["department"].id,
            created_by_user_id=data["admin"].id,
        )
        db.session.add(portfolio)
        db.session.flush()
        item = WorkItem(
            portfolio_id=portfolio.id, request_kind=REQUEST_KIND_PRIMARY,
            status=WORK_ITEM_STATUS_DRAFT,
            public_id=f"TST2026-TESTDEPT-{prefix}-1",
            created_by_user_id=data["admin"].id,
        )
        db.session.add(item)
        db.session.commit()
        return item

    def test_techops_submit_queues_the_receipt(
        self, app, seed_workflow_data, super_admin_ctx
    ):
        from app.models import EmailOutbox
        from app.routes.work.techops.create import _do_submit

        item = self._prepare(seed_workflow_data, "TECHOPS", "techops", "TEC")

        _do_submit(item, super_admin_ctx)

        assert EmailOutbox.query.filter_by(
            template_key="techops_submission_confirmation").count() > 0
