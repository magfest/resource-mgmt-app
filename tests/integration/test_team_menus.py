"""Top nav: one menu per team, with role-gated sections inside.

Reviewers find their groups inside the team menu that owns them, so the
separate Review menu is gone and no menu is named "Admin" except the system
menu super admins use.
"""
import re

from app import db
from app.models import (
    ApprovalGroup, ROLE_APPROVER, ROLE_WORKTYPE_ADMIN, User, UserRole, WorkType,
)


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def _menus(html):
    return [label.strip() for label in re.findall(r'top-nav-toggle">([^<]+?)\s*<span', html)]


def _seed_teams(seed_workflow_data):
    """Activate TECHOPS and SUPPLY and give SUPPLY one reviewer group."""
    techops = WorkType(code="TECHOPS", name="TechOps", is_active=True)
    supply = WorkType(code="SUPPLY", name="Supply Orders", is_active=True)
    db.session.add_all([techops, supply])
    db.session.flush()
    supply_group = ApprovalGroup(work_type_id=supply.id, code="SUPPLYOPS",
                                 name="SupplyOps Review", is_active=True)
    db.session.add(supply_group)
    db.session.add(User(id="test:supplyadmin", email="supplyadmin@test.local",
                        display_name="Supply Lead", is_active=True))
    db.session.flush()
    db.session.add(UserRole(user_id="test:supplyadmin", role_code=ROLE_WORKTYPE_ADMIN,
                            work_type_id=supply.id))
    db.session.commit()
    return supply_group


def test_super_admin_sees_five_team_menus(client, seed_workflow_data):
    _seed_teams(seed_workflow_data)
    _login(client, "test:admin")
    html = client.get("/").get_data(as_text=True)
    assert _menus(html) == ["Budget", "TechOps", "FestOps", "Event Ops", "Admin", "Test Admin"]


def test_budget_reviewer_sees_only_their_review_section(client, seed_workflow_data):
    db.session.add(UserRole(user_id="test:reviewer", role_code=ROLE_APPROVER,
                            approval_group_id=seed_workflow_data["approval_group"].id))
    db.session.commit()
    _login(client, "test:reviewer")
    html = client.get("/").get_data(as_text=True)
    assert _menus(html) == ["Budget", "Test Reviewer"]
    assert "Tech Team" in html
    assert "Assign Reviewers" not in html


def test_supply_reviewer_and_supply_admin_both_land_in_festops(client, seed_workflow_data):
    supply_group = _seed_teams(seed_workflow_data)
    db.session.add(UserRole(user_id="test:reviewer", role_code=ROLE_APPROVER,
                            approval_group_id=supply_group.id))
    db.session.commit()

    _login(client, "test:reviewer")
    reviewer_html = client.get("/").get_data(as_text=True)
    assert _menus(reviewer_html) == ["FestOps", "Test Reviewer"]
    assert "SupplyOps Review" in reviewer_html and "Supply Queue" not in reviewer_html

    _login(client, "test:supplyadmin")
    admin_html = client.get("/").get_data(as_text=True)
    assert _menus(admin_html) == ["FestOps", "Supply Lead"]
    assert "Supply Queue" in admin_html


def test_reports_index_lists_reports_for_budget_admins_only(client, seed_workflow_data):
    _login(client, "test:admin")
    page = client.get("/admin/budget/reports/").get_data(as_text=True)
    assert "Master Ledger" in page and "Missing Budgets" in page

    _login(client, "test:reviewer")
    assert client.get("/admin/budget/reports/").status_code == 403
