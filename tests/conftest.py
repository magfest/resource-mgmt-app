"""
Shared pytest fixtures for the MAGFest Budget application.
"""
import pytest
from app import create_app, db
from app.models import (
    User,
    UserRole,
    Department,
    Division,
    EventCycle,
    WorkType,
    WorkTypeConfig,
    WorkPortfolio,
    WorkItem,
    WorkLine,
    BudgetLineDetail,
    ExpenseAccount,
    ApprovalGroup,
    SpendType,
    ROLE_SUPER_ADMIN,
    ROUTING_STRATEGY_DIRECT,
    REQUEST_KIND_PRIMARY,
    WORK_ITEM_STATUS_DRAFT,
    WORK_LINE_STATUS_PENDING,
    REVIEW_STAGE_APPROVAL_GROUP,
)


@pytest.fixture(scope="function")
def app():
    """Create a Flask application configured for testing."""
    test_app = create_app()

    # Override configuration for testing
    test_app.config.update({
        "TESTING": True,
        "SQLALCHEMY_DATABASE_URI": "sqlite:///:memory:",
        "WTF_CSRF_ENABLED": False,
        "DEV_LOGIN_ENABLED": False,  # Disable to avoid demo seeding issues
        "SECRET_KEY": "test-secret-key",
    })

    with test_app.app_context():
        db.create_all()
        yield test_app
        db.session.remove()
        db.drop_all()


@pytest.fixture(scope="function")
def client(app):
    """Create a Flask test client for HTTP requests."""
    return app.test_client()


@pytest.fixture(scope="function")
def db_session(app):
    """Provide a database session with automatic rollback."""
    with app.app_context():
        yield db.session
        db.session.rollback()


@pytest.fixture(scope="function")
def authenticated_client(app, client):
    """Create a client with session configured for a test user."""
    with client.session_transaction() as sess:
        sess["active_user_id"] = "dev:admin"
    return client


# ============================================================
# Shared seed data fixtures
# ============================================================

@pytest.fixture(scope="function")
def seed_workflow_data(app):
    """
    Seed the standard org structure, work type config, and users needed
    for workflow tests.

    Creates: admin user (SUPER_ADMIN), reviewer user, event cycle, division,
    department, BUDGET work type + config, approval group, expense account,
    spend type, and an empty portfolio.

    Note: Does not use a nested app.app_context() — the `app` fixture
    already provides one. This keeps ORM objects attached to the session.
    """
    admin = User(
        id="test:admin", email="admin@test.local",
        display_name="Test Admin", is_active=True,
    )
    reviewer = User(
        id="test:reviewer", email="reviewer@test.local",
        display_name="Test Reviewer", is_active=True,
    )
    db.session.add_all([admin, reviewer])
    db.session.flush()

    db.session.add(UserRole(user_id=admin.id, role_code=ROLE_SUPER_ADMIN))

    cycle = EventCycle(
        code="TST2026", name="Test Event 2026",
        is_active=True, is_default=True, sort_order=1,
    )
    db.session.add(cycle)

    div = Division(
        code="TESTDIV", name="Test Division", is_active=True,
    )
    db.session.add(div)

    dept = Department(
        code="TESTDEPT", name="Test Department", is_active=True,
    )
    db.session.add(dept)

    wt = WorkType(code="BUDGET", name="Budget", is_active=True)
    db.session.add(wt)
    db.session.flush()

    wtc = WorkTypeConfig(
        work_type_id=wt.id, url_slug="budget",
        public_id_prefix="BUD", line_detail_type="budget",
        routing_strategy=ROUTING_STRATEGY_DIRECT,
    )
    db.session.add(wtc)

    ag = ApprovalGroup(
        code="TECH", name="Tech Team", is_active=True,
    )
    db.session.add(ag)

    ea = ExpenseAccount(
        code="TEST_ACC", name="Test Account", is_active=True,
    )
    db.session.add(ea)

    st = SpendType(
        code="BANK", name="Bank", is_active=True,
    )
    db.session.add(st)

    portfolio = WorkPortfolio(
        work_type_id=wt.id, event_cycle_id=cycle.id,
        department_id=dept.id, created_by_user_id=admin.id,
    )
    db.session.add(portfolio)
    db.session.commit()

    return {
        "admin": admin,
        "reviewer": reviewer,
        "cycle": cycle,
        "division": div,
        "department": dept,
        "work_type": wt,
        "work_type_config": wtc,
        "approval_group": ag,
        "expense_account": ea,
        "spend_type": st,
        "portfolio": portfolio,
    }


@pytest.fixture(scope="function")
def seed_draft_work_item(app, seed_workflow_data):
    """
    Seed a DRAFT work item with one valid budget line, ready for submission.

    Depends on seed_workflow_data for the org structure.
    """
    data = seed_workflow_data

    work_item = WorkItem(
        portfolio_id=data["portfolio"].id,
        request_kind=REQUEST_KIND_PRIMARY,
        status=WORK_ITEM_STATUS_DRAFT,
        public_id="TST2026-TESTDEPT-BUD-1",
        created_by_user_id=data["admin"].id,
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

    detail = BudgetLineDetail(
        work_line_id=line.id,
        expense_account_id=data["expense_account"].id,
        spend_type_id=data["spend_type"].id,
        quantity=1, unit_price_cents=5000,
        routed_approval_group_id=data["approval_group"].id,
    )
    db.session.add(detail)
    db.session.commit()

    return {
        **data,
        "work_item": work_item,
        "line": line,
        "detail": detail,
    }
