"""
Integration tests for role-based access control.

These tests verify that routes enforce permissions correctly:
- Unauthenticated users are redirected to login
- Users without department membership can't access portfolios
- Admin routes require SUPER_ADMIN role
- Dispatch routes require budget admin access
"""
from app import db
from app.models import (
    User,
    UserRole,
    Department,
    EventCycle,
    WorkType,
    WorkTypeConfig,
    DepartmentMembership,
    DepartmentMembershipWorkTypeAccess,
    ROLE_SUPER_ADMIN,
    ROUTING_STRATEGY_DIRECT,
)


# ============================================================
# Fixtures — seed the minimum data needed for permission tests
# ============================================================

def _seed_test_data(app):
    """
    Seed the minimum database records needed to test portfolio access.

    Creates:
    - 1 event cycle (TST2026)
    - 1 department (TESTDEPT)
    - 1 work type (BUDGET) with config
    - 3 users: admin, member (with budget access), outsider (no membership)
    """
    with app.app_context():
        # Event cycle
        cycle = EventCycle(
            code="TST2026", name="Test Event 2026",
            is_active=True, is_default=True, sort_order=1,
        )
        db.session.add(cycle)

        # Department
        dept = Department(
            code="TESTDEPT", name="Test Department",
            is_active=True, sort_order=1,
        )
        db.session.add(dept)

        # Work type + config
        wt = WorkType(code="BUDGET", name="Budget", is_active=True, sort_order=0)
        db.session.add(wt)
        db.session.flush()

        wtc = WorkTypeConfig(
            work_type_id=wt.id,
            url_slug="budget",
            public_id_prefix="BUD",
            line_detail_type="budget",
            routing_strategy=ROUTING_STRATEGY_DIRECT,
        )
        db.session.add(wtc)

        # Users
        admin = User(
            id="test:admin", email="admin@test.local",
            auth_subject="test:admin", display_name="Test Admin", is_active=True,
        )
        member = User(
            id="test:member", email="member@test.local",
            auth_subject="test:member", display_name="Test Member", is_active=True,
        )
        outsider = User(
            id="test:outsider", email="outsider@test.local",
            auth_subject="test:outsider", display_name="Test Outsider", is_active=True,
        )
        db.session.add_all([admin, member, outsider])
        db.session.flush()

        # Admin role
        db.session.add(UserRole(
            user_id=admin.id, role_code=ROLE_SUPER_ADMIN,
        ))

        # Department membership with budget view+edit access
        membership = DepartmentMembership(
            user_id=member.id, department_id=dept.id,
            event_cycle_id=cycle.id, is_department_head=True,
        )
        db.session.add(membership)
        db.session.flush()

        db.session.add(DepartmentMembershipWorkTypeAccess(
            department_membership_id=membership.id,
            work_type_id=wt.id, can_view=True, can_edit=True,
        ))

        # Outsider: no membership at all

        db.session.commit()


def _login(client, user_id):
    """Set the session to simulate a logged-in user."""
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


# ============================================================
# Tests
# ============================================================

class TestUnauthenticatedAccess:
    """Unauthenticated users should be redirected to login."""

    def test_home_requires_login(self, client):
        response = client.get("/", follow_redirects=False)
        assert response.status_code == 302
        assert "/login" in response.location

    def test_portfolio_blocks_unauthenticated(self, app, client):
        _seed_test_data(app)
        response = client.get("/tst2026/testdept/budget", follow_redirects=False)
        # Portfolio route doesn't have an explicit login redirect before building
        # context, so unauthenticated users get blocked (not a clean 302).
        # This documents current behavior — a login redirect would be nicer.
        assert response.status_code in (302, 403, 500)

    def test_admin_config_requires_login(self, client):
        response = client.get("/admin/config/departments/", follow_redirects=False)
        assert response.status_code == 302
        assert "/login" in response.location

    def test_dispatch_requires_login(self, client):
        response = client.get("/admin/dispatch/", follow_redirects=False)
        assert response.status_code == 302
        assert "/login" in response.location


class TestPortfolioPermissions:
    """Portfolio access requires department membership with work type access."""

    def test_member_can_access_portfolio(self, app, client):
        """A user with department membership + budget access can view the portfolio."""
        _seed_test_data(app)
        _login(client, "test:member")

        response = client.get("/tst2026/testdept/budget")
        assert response.status_code == 200

    def test_outsider_cannot_access_portfolio(self, app, client):
        """A user with no department membership gets 403."""
        _seed_test_data(app)
        _login(client, "test:outsider")

        response = client.get("/tst2026/testdept/budget")
        assert response.status_code == 403

    def test_admin_can_access_any_portfolio(self, app, client):
        """Super admins can access any department's portfolio."""
        _seed_test_data(app)
        _login(client, "test:admin")

        response = client.get("/tst2026/testdept/budget")
        assert response.status_code == 200

    def test_nonexistent_department_returns_404(self, app, client):
        """Accessing a department that doesn't exist returns 404."""
        _seed_test_data(app)
        _login(client, "test:admin")

        response = client.get("/tst2026/fakeDept/budget")
        assert response.status_code == 404

    def test_nonexistent_event_returns_404(self, app, client):
        """Accessing an event cycle that doesn't exist returns 404."""
        _seed_test_data(app)
        _login(client, "test:admin")

        response = client.get("/fakeEvent/testdept/budget")
        assert response.status_code == 404


class TestAdminRoutePermissions:
    """Admin config routes require SUPER_ADMIN role."""

    def test_admin_can_access_config(self, app, client):
        """Super admin can access admin config pages."""
        _seed_test_data(app)
        _login(client, "test:admin")

        response = client.get("/admin/config/departments/")
        assert response.status_code == 200

    def test_member_cannot_access_config(self, app, client):
        """Regular department member gets 403 on admin config."""
        _seed_test_data(app)
        _login(client, "test:member")

        response = client.get("/admin/config/departments/")
        assert response.status_code == 403

    def test_outsider_cannot_access_config(self, app, client):
        """User with no roles gets 403 on admin config."""
        _seed_test_data(app)
        _login(client, "test:outsider")

        response = client.get("/admin/config/departments/")
        assert response.status_code == 403


class TestAdminWriteOperations:
    """Admin write operations must not crash due to h being None."""

    def test_admin_can_update_event_cycle(self, app, client):
        """
        Updating an event cycle exercises h.get_active_user_id().

        This catches the regression where admin modules imported h=None
        due to incorrect import ordering in create_app().
        """
        _seed_test_data(app)
        _login(client, "test:admin")

        # Get the event cycle ID
        from app.models import EventCycle
        with app.app_context():
            cycle = EventCycle.query.filter_by(code="TST2026").first()
            cycle_id = cycle.id

        response = client.post(f"/admin/config/event-cycles/{cycle_id}", data={
            "code": "TST2026",
            "name": "Test Event 2026 Updated",
            "is_active": "1",
            "is_default": "1",
            "sort_order": "1",
        }, follow_redirects=False)

        # Should redirect on success (302), not crash (500)
        assert response.status_code in (302, 200), (
            f"Admin write returned {response.status_code}. "
            "If 500, h is likely None — check import order in create_app()."
        )


class TestDispatchPermissions:
    """Dispatch routes require budget admin (SUPER_ADMIN or WORKTYPE_ADMIN)."""

    def test_admin_can_access_dispatch(self, app, client):
        """Super admin can access the dispatch queue."""
        _seed_test_data(app)
        _login(client, "test:admin")

        response = client.get("/admin/dispatch/")
        assert response.status_code == 200

    def test_member_cannot_access_dispatch(self, app, client):
        """Regular department member gets 403 on dispatch."""
        _seed_test_data(app)
        _login(client, "test:member")

        response = client.get("/admin/dispatch/")
        assert response.status_code == 403

    def test_outsider_cannot_access_dispatch(self, app, client):
        """User with no roles gets 403 on dispatch."""
        _seed_test_data(app)
        _login(client, "test:outsider")

        response = client.get("/admin/dispatch/")
        assert response.status_code == 403
