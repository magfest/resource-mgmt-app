"""A work-type admin can open their own work type's review queues.

The team menus link every admin to "Reviewer Groups", and an admin is often
not a member of any group. Seeing the queue does not grant decisions; those
still require membership of the line's routed group.
"""
from app import db
from app.models import (
    ApprovalGroup, ROLE_APPROVER, ROLE_WORKTYPE_ADMIN, User, UserRole, WorkType,
)


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def _seed_techops_admin():
    """Add TECHOPS with two groups and an admin who is in neither."""
    techops = WorkType(code="TECHOPS", name="TechOps", is_active=True)
    db.session.add(techops)
    db.session.flush()
    # GEN is added first so the redirect must honour sort_order, not insert order.
    db.session.add_all([
        ApprovalGroup(work_type_id=techops.id, code="TECHOPS_GEN",
                      name="TechOps Generic", sort_order=20, is_active=True),
        ApprovalGroup(work_type_id=techops.id, code="TECHOPS_NET",
                      name="TechOps Networking", sort_order=10, is_active=True),
    ])
    db.session.add(User(id="test:techopsadmin", email="techopsadmin@test.local",
                        display_name="TechOps Lead", is_active=True))
    db.session.flush()
    db.session.add(UserRole(user_id="test:techopsadmin",
                            role_code=ROLE_WORKTYPE_ADMIN, work_type_id=techops.id))
    db.session.commit()


def test_worktype_admin_without_groups_opens_their_first_group(client, seed_workflow_data):
    _seed_techops_admin()
    _login(client, "test:techopsadmin")

    resp = client.get("/approvals/?work_type=TECHOPS")

    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/approvals/TECHOPS_NET")
    assert client.get("/approvals/TECHOPS_GEN").status_code == 200


def test_worktype_admin_cannot_open_another_work_types_group(client, seed_workflow_data):
    _seed_techops_admin()
    _login(client, "test:techopsadmin")

    # TECH is the BUDGET group seeded by seed_workflow_data.
    assert client.get("/approvals/TECH").status_code == 404


def test_work_type_param_picks_that_teams_group_for_a_mixed_user(client, seed_workflow_data):
    _seed_techops_admin()
    # Also a BUDGET reviewer, whose TECH group would otherwise sort first.
    db.session.add(UserRole(user_id="test:techopsadmin", role_code=ROLE_APPROVER,
                            approval_group_id=seed_workflow_data["approval_group"].id))
    db.session.commit()
    _login(client, "test:techopsadmin")

    resp = client.get("/approvals/?work_type=TECHOPS")

    assert resp.headers["Location"].endswith("/approvals/TECHOPS_NET")


def test_work_type_param_with_no_matching_group_is_refused(client, seed_workflow_data):
    db.session.add(UserRole(user_id="test:reviewer", role_code=ROLE_APPROVER,
                            approval_group_id=seed_workflow_data["approval_group"].id))
    db.session.commit()
    _login(client, "test:reviewer")

    assert client.get("/approvals/?work_type=TECHOPS").status_code == 403


def test_techops_menu_links_to_techops_groups(client, seed_workflow_data):
    _seed_techops_admin()
    _login(client, "test:techopsadmin")

    html = client.get("/").get_data(as_text=True)

    assert "/approvals/?work_type=TECHOPS" in html
