"""
Integration tests for the admin Work Types page.

Work types themselves are seed-only — only ``is_active`` can be
toggled. This admin page is what staging uses to flip TECHOPS back on
after the deactivate-unbuilt-worktypes migration runs in production.
"""
from __future__ import annotations

import pytest

from app import db
from app.models import (
    User,
    UserRole,
    WorkType,
    WorkTypeConfig,
    ROLE_SUPER_ADMIN,
    ROUTING_STRATEGY_DIRECT,
)


@pytest.fixture
def admin_setup(app):
    """Seed a SUPER_ADMIN plus BUDGET (active) and TECHOPS (inactive) work types."""
    admin = User(
        id="test:admin", email="admin@test.local",
        auth_subject="test:admin", display_name="Test Admin", is_active=True,
    )
    db.session.add(admin)
    db.session.flush()
    db.session.add(UserRole(user_id=admin.id, role_code=ROLE_SUPER_ADMIN))

    budget = WorkType(code="BUDGET", name="Budget", is_active=True, sort_order=10)
    techops = WorkType(code="TECHOPS", name="TechOps", is_active=False, sort_order=40)
    db.session.add_all([budget, techops])
    db.session.flush()

    db.session.add(WorkTypeConfig(
        work_type_id=budget.id, url_slug="budget",
        public_id_prefix="BUD", line_detail_type="budget",
        routing_strategy=ROUTING_STRATEGY_DIRECT,
    ))
    db.session.add(WorkTypeConfig(
        work_type_id=techops.id, url_slug="techops",
        public_id_prefix="TEC", line_detail_type="techops",
        routing_strategy=ROUTING_STRATEGY_DIRECT,
    ))
    db.session.commit()

    return {"admin": admin, "budget": budget, "techops": techops}


@pytest.fixture
def admin_client(client, admin_setup):
    with client.session_transaction() as sess:
        sess["active_user_id"] = admin_setup["admin"].id
    return client


class TestWorkTypesAdmin:
    def test_list_page_shows_active_and_inactive(self, admin_client):
        resp = admin_client.get("/admin/config/work-types/")
        assert resp.status_code == 200
        body = resp.get_data(as_text=True)
        assert "BUDGET" in body
        assert "TECHOPS" in body

    def test_activate_inactive_worktype(self, admin_client, admin_setup):
        techops_id = admin_setup["techops"].id

        resp = admin_client.post(
            f"/admin/config/work-types/{techops_id}/restore",
            follow_redirects=False,
        )
        assert resp.status_code == 302

        # Re-read from DB; the original ORM ref may be stale across the request boundary.
        wt = db.session.get(WorkType, techops_id)
        assert wt.is_active is True

    def test_deactivate_active_worktype(self, admin_client, admin_setup):
        budget_id = admin_setup["budget"].id

        resp = admin_client.post(
            f"/admin/config/work-types/{budget_id}/archive",
            follow_redirects=False,
        )
        assert resp.status_code == 302

        wt = db.session.get(WorkType, budget_id)
        assert wt.is_active is False

    def test_non_admin_blocked(self, client, admin_setup):
        # Create a non-admin user
        regular = User(
            id="test:regular", email="regular@test.local",
            auth_subject="test:regular", display_name="Regular", is_active=True,
        )
        db.session.add(regular)
        db.session.commit()

        with client.session_transaction() as sess:
            sess["active_user_id"] = regular.id

        resp = client.post(
            f"/admin/config/work-types/{admin_setup['techops'].id}/restore",
            follow_redirects=False,
        )
        assert resp.status_code == 403

        # Confirm no state change
        wt = db.session.get(WorkType, admin_setup["techops"].id)
        assert wt.is_active is False
