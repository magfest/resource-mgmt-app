"""
Integration tests for admin features: QuickBooks class fields, date visibility,
and race condition locking.
"""
from datetime import date, datetime

from app import db
from app.models import (
    User,
    UserRole,
    Department,
    Division,
    EventCycle,
    WorkPortfolio,
    WorkItem,
    WorkLine,
    WorkLineReview,
    BudgetLineDetail,
    ExpenseAccount,
    ApprovalGroup,
    ROLE_SUPER_ADMIN,
    REQUEST_KIND_PRIMARY,
    WORK_ITEM_STATUS_SUBMITTED,
    WORK_ITEM_STATUS_FINALIZED,
    WORK_LINE_STATUS_PENDING,
    REVIEW_STAGE_APPROVAL_GROUP,
    REVIEW_STATUS_APPROVED,
    SpendType,
)


def _seed_admin(app):
    """Seed a super admin user and basic org data."""
    with app.app_context():
        admin = User(
            id="test:admin", email="admin@test.local",
            display_name="Test Admin", is_active=True,
        )
        db.session.add(admin)
        db.session.flush()

        db.session.add(UserRole(
            user_id=admin.id, role_code=ROLE_SUPER_ADMIN,
        ))

        cycle = EventCycle(
            code="TST2026", name="Test Event 2026",
            is_active=True, is_default=True, sort_order=1,
            event_start_date=date(2026, 6, 1),
            event_end_date=date(2026, 6, 5),
        )
        db.session.add(cycle)

        div = Division(
            code="TESTDIV", name="Test Division", is_active=True,
        )
        db.session.add(div)

        dept = Department(
            code="TESTDEPT", name="Test Department",
            is_active=True, division_id=None,
        )
        db.session.add(dept)

        db.session.commit()


class TestQuickBooksClassFields:
    """Tests for qb_class field persistence on org models."""

    def test_event_cycle_qb_class_persists(self, app, db_session):
        """qb_class should be saved and loaded on EventCycle."""
        _seed_admin(app)

        cycle = EventCycle.query.filter_by(code="TST2026").one()
        assert cycle.qb_class is None

        cycle.qb_class = "Super_MAGFest"
        db_session.commit()

        reloaded = EventCycle.query.filter_by(code="TST2026").one()
        assert reloaded.qb_class == "Super_MAGFest"

    def test_division_qb_class_persists(self, app, db_session):
        """qb_class should be saved and loaded on Division."""
        _seed_admin(app)

        div = Division.query.filter_by(code="TESTDIV").one()
        div.qb_class = "Gaming"
        db_session.commit()

        reloaded = Division.query.filter_by(code="TESTDIV").one()
        assert reloaded.qb_class == "Gaming"

    def test_department_qb_class_persists(self, app, db_session):
        """qb_class should be saved and loaded on Department."""
        _seed_admin(app)

        dept = Department.query.filter_by(code="TESTDEPT").one()
        dept.qb_class = "Staff Services"
        db_session.commit()

        reloaded = Department.query.filter_by(code="TESTDEPT").one()
        assert reloaded.qb_class == "Staff Services"

    def test_multiple_entities_share_qb_class(self, app, db_session):
        """Multiple entities should be able to share the same qb_class value."""
        _seed_admin(app)

        div2 = Division(
            code="TESTDIV2", name="Test Division 2",
            is_active=True, qb_class="Gaming",
        )
        db_session.add(div2)

        div1 = Division.query.filter_by(code="TESTDIV").one()
        div1.qb_class = "Gaming"
        db_session.commit()

        gaming_divs = Division.query.filter_by(qb_class="Gaming").all()
        assert len(gaming_divs) == 2


class TestDatesArePublic:
    """Tests for the dates_are_public toggle on EventCycle."""

    def test_defaults_to_false(self, app, db_session):
        """dates_are_public should default to False."""
        _seed_admin(app)

        cycle = EventCycle.query.filter_by(code="TST2026").one()
        assert cycle.dates_are_public is False

    def test_toggle_persists(self, app, db_session):
        """dates_are_public should be toggleable and persist."""
        _seed_admin(app)

        cycle = EventCycle.query.filter_by(code="TST2026").one()
        cycle.dates_are_public = True
        db_session.commit()

        reloaded = EventCycle.query.filter_by(code="TST2026").one()
        assert reloaded.dates_are_public is True

    def test_dates_still_stored_when_not_public(self, app, db_session):
        """Event dates should be stored regardless of visibility setting."""
        _seed_admin(app)

        cycle = EventCycle.query.filter_by(code="TST2026").one()
        assert cycle.dates_are_public is False
        assert cycle.event_start_date == date(2026, 6, 1)
        assert cycle.event_end_date == date(2026, 6, 5)


class TestPublicIdLocking:
    """Tests for public ID generation with row locking."""

    def test_sequential_ids_are_unique(self, app, db_session, seed_workflow_data):
        """Two sequential calls should produce different IDs."""
        from app.routes.work.helpers.formatting import generate_public_id_for_portfolio

        portfolio = WorkPortfolio.query.first()
        id1 = generate_public_id_for_portfolio(portfolio)
        id2 = generate_public_id_for_portfolio(portfolio)

        assert id1 != id2
        assert id1 == "TST2026-TESTDEPT-BUD-1"
        assert id2 == "TST2026-TESTDEPT-BUD-2"

    def test_sequence_counter_increments(self, app, db_session, seed_workflow_data):
        """next_public_id_seq should increment after each call."""
        from app.routes.work.helpers.formatting import generate_public_id_for_portfolio

        portfolio = WorkPortfolio.query.first()
        assert portfolio.next_public_id_seq == 1

        generate_public_id_for_portfolio(portfolio)
        assert portfolio.next_public_id_seq == 2

        generate_public_id_for_portfolio(portfolio)
        assert portfolio.next_public_id_seq == 3


class TestCheckoutLocking:
    """Tests for checkout/checkin with row locking."""

    def test_checkout_then_second_checkout_fails(self, app, db_session, seed_workflow_data):
        """Second checkout by different user should fail."""
        from app.routes.work.helpers.checkout import checkout_work_item
        from app.routes import UserContext

        portfolio = WorkPortfolio.query.first()
        work_item = WorkItem(
            portfolio_id=portfolio.id, request_kind=REQUEST_KIND_PRIMARY,
            status=WORK_ITEM_STATUS_SUBMITTED, public_id="TST2026-TESTDEPT-BUD-1",
            created_by_user_id="test:admin",
        )
        db.session.add(work_item)
        db.session.flush()

        admin_ctx = UserContext(
            user_id="test:admin", user=None,
            roles=("SUPER_ADMIN",), is_super_admin=True,
            approval_group_ids=set(),
        )
        reviewer_ctx = UserContext(
            user_id="test:reviewer", user=None,
            roles=(), is_super_admin=False,
            approval_group_ids={1},
        )

        assert checkout_work_item(work_item, admin_ctx) is True
        assert checkout_work_item(work_item, reviewer_ctx) is False

    def test_checkin_then_checkout_succeeds(self, app, db_session, seed_workflow_data):
        """After checkin, a new checkout should succeed."""
        from app.routes.work.helpers.checkout import checkout_work_item, checkin_work_item
        from app.routes import UserContext

        portfolio = WorkPortfolio.query.first()
        work_item = WorkItem(
            portfolio_id=portfolio.id, request_kind=REQUEST_KIND_PRIMARY,
            status=WORK_ITEM_STATUS_SUBMITTED, public_id="TST2026-TESTDEPT-BUD-1",
            created_by_user_id="test:admin",
        )
        db.session.add(work_item)
        db.session.flush()

        admin_ctx = UserContext(
            user_id="test:admin", user=None,
            roles=("SUPER_ADMIN",), is_super_admin=True,
            approval_group_ids=set(),
        )

        assert checkout_work_item(work_item, admin_ctx) is True
        assert checkin_work_item(work_item, admin_ctx) is True
        assert checkout_work_item(work_item, admin_ctx) is True


class TestFinalizationLocking:
    """Tests for finalization idempotency with row locking."""

    def test_finalize_then_second_finalize_fails(self, app, db_session, seed_workflow_data):
        """Second finalization should fail with 'already finalized'."""
        from app.routes.admin_final.helpers import finalize_work_item
        from app.routes import UserContext

        portfolio = WorkPortfolio.query.first()
        ag = ApprovalGroup.query.first()
        ea = ExpenseAccount.query.first()

        work_item = WorkItem(
            portfolio_id=portfolio.id, request_kind=REQUEST_KIND_PRIMARY,
            status=WORK_ITEM_STATUS_SUBMITTED, public_id="TST2026-TESTDEPT-BUD-1",
            created_by_user_id="test:admin",
        )
        db.session.add(work_item)
        db.session.flush()

        line = WorkLine(
            work_item_id=work_item.id, line_number=1,
            status=WORK_LINE_STATUS_PENDING,
            current_review_stage=REVIEW_STAGE_APPROVAL_GROUP,
        )
        db.session.add(line)
        db.session.flush()

        st = SpendType.query.filter_by(code="BANK").one()
        detail = BudgetLineDetail(
            work_line_id=line.id, expense_account_id=ea.id,
            spend_type_id=st.id,
            quantity=1, unit_price_cents=1000,
            routed_approval_group_id=ag.id,
        )
        db.session.add(detail)

        review = WorkLineReview(
            work_line_id=line.id, stage=REVIEW_STAGE_APPROVAL_GROUP,
            approval_group_id=ag.id, status=REVIEW_STATUS_APPROVED,
            approved_amount_cents=1000,
            decided_at=datetime.utcnow(),
            decided_by_user_id="test:admin",
            created_by_user_id="test:admin",
        )
        db.session.add(review)
        db.session.flush()

        admin_ctx = UserContext(
            user_id="test:admin", user=None,
            roles=("SUPER_ADMIN",), is_super_admin=True,
            approval_group_ids=set(),
        )

        success, error = finalize_work_item(work_item, admin_ctx, note="First finalization")
        assert success is True
        db.session.commit()

        success, error = finalize_work_item(work_item, admin_ctx, note="Second attempt")
        assert success is False
        assert "already finalized" in error.lower()

    def test_finalize_blocked_while_checked_out(self, app, db_session, seed_workflow_data):
        """Finalization should fail if a reviewer has an active checkout."""
        from app.routes.admin_final.helpers import can_finalize_work_item
        from app.routes.work.helpers.checkout import checkout_work_item, checkin_work_item
        from app.routes import UserContext

        portfolio = WorkPortfolio.query.first()
        ag = ApprovalGroup.query.first()
        ea = ExpenseAccount.query.first()

        work_item = WorkItem(
            portfolio_id=portfolio.id, request_kind=REQUEST_KIND_PRIMARY,
            status=WORK_ITEM_STATUS_SUBMITTED, public_id="TST2026-TESTDEPT-BUD-1",
            created_by_user_id="test:admin",
        )
        db.session.add(work_item)
        db.session.flush()

        line = WorkLine(
            work_item_id=work_item.id, line_number=1,
            status=WORK_LINE_STATUS_PENDING,
            current_review_stage=REVIEW_STAGE_APPROVAL_GROUP,
        )
        db.session.add(line)
        db.session.flush()

        st = SpendType.query.filter_by(code="BANK").one()
        detail = BudgetLineDetail(
            work_line_id=line.id, expense_account_id=ea.id,
            spend_type_id=st.id,
            quantity=1, unit_price_cents=1000,
            routed_approval_group_id=ag.id,
        )
        db.session.add(detail)

        review = WorkLineReview(
            work_line_id=line.id, stage=REVIEW_STAGE_APPROVAL_GROUP,
            approval_group_id=ag.id, status=REVIEW_STATUS_APPROVED,
            approved_amount_cents=1000,
            decided_at=datetime.utcnow(),
            decided_by_user_id="test:reviewer",
            created_by_user_id="test:reviewer",
        )
        db.session.add(review)
        db.session.flush()

        reviewer_ctx = UserContext(
            user_id="test:reviewer", user=None,
            roles=(), is_super_admin=False,
            approval_group_ids={ag.id},
        )
        admin_ctx = UserContext(
            user_id="test:admin", user=None,
            roles=("SUPER_ADMIN",), is_super_admin=True,
            approval_group_ids=set(),
        )

        # Reviewer checks out the work item
        assert checkout_work_item(work_item, reviewer_ctx) is True

        # Admin tries to finalize — should be blocked
        can_do, reason = can_finalize_work_item(work_item)
        assert can_do is False
        assert "checked out" in reason.lower()

        # Reviewer checks in
        assert checkin_work_item(work_item, reviewer_ctx) is True

        # Now finalization should be allowed
        can_do, reason = can_finalize_work_item(work_item)
        assert can_do is True

    def test_admin_force_checkin_then_finalize(self, app, db_session, seed_workflow_data):
        """Admin can force-release a reviewer's checkout, then finalize."""
        from app.routes.admin_final.helpers import can_finalize_work_item, finalize_work_item
        from app.routes.work.helpers.checkout import checkout_work_item, checkin_work_item
        from app.routes import UserContext

        portfolio = WorkPortfolio.query.first()
        ag = ApprovalGroup.query.first()
        ea = ExpenseAccount.query.first()

        work_item = WorkItem(
            portfolio_id=portfolio.id, request_kind=REQUEST_KIND_PRIMARY,
            status=WORK_ITEM_STATUS_SUBMITTED, public_id="TST2026-TESTDEPT-BUD-1",
            created_by_user_id="test:admin",
        )
        db.session.add(work_item)
        db.session.flush()

        line = WorkLine(
            work_item_id=work_item.id, line_number=1,
            status=WORK_LINE_STATUS_PENDING,
            current_review_stage=REVIEW_STAGE_APPROVAL_GROUP,
        )
        db.session.add(line)
        db.session.flush()

        st = SpendType.query.filter_by(code="BANK").one()
        detail = BudgetLineDetail(
            work_line_id=line.id, expense_account_id=ea.id,
            spend_type_id=st.id,
            quantity=1, unit_price_cents=1000,
            routed_approval_group_id=ag.id,
        )
        db.session.add(detail)

        review = WorkLineReview(
            work_line_id=line.id, stage=REVIEW_STAGE_APPROVAL_GROUP,
            approval_group_id=ag.id, status=REVIEW_STATUS_APPROVED,
            approved_amount_cents=1000,
            decided_at=datetime.utcnow(),
            decided_by_user_id="test:reviewer",
            created_by_user_id="test:reviewer",
        )
        db.session.add(review)
        db.session.flush()

        reviewer_ctx = UserContext(
            user_id="test:reviewer", user=None,
            roles=(), is_super_admin=False,
            approval_group_ids={ag.id},
        )
        admin_ctx = UserContext(
            user_id="test:admin", user=None,
            roles=("SUPER_ADMIN",), is_super_admin=True,
            approval_group_ids=set(),
        )

        # Reviewer checks out the work item
        assert checkout_work_item(work_item, reviewer_ctx) is True

        # Finalization blocked
        can_do, _ = can_finalize_work_item(work_item)
        assert can_do is False

        # Admin force-releases the checkout
        assert checkin_work_item(work_item, admin_ctx, force=True) is True

        # Now finalization succeeds
        success, error = finalize_work_item(work_item, admin_ctx, note="Finalized after force checkin")
        assert success is True
        assert work_item.status == WORK_ITEM_STATUS_FINALIZED
