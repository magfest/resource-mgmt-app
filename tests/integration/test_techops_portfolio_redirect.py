"""/<event>/<dept>/techops sends the requester to their one request.

A department holds exactly one TechOps request per event, so the URL has
no list to show. It stays as a redirect because it is the address people
bookmark and type; no page in the app links to it any more.
"""
from __future__ import annotations

import pytest

from app import db
from app.models import (
    Department,
    DepartmentMembership,
    DepartmentMembershipWorkTypeAccess,
    EventCycle,
    User,
    UserRole,
    Venue,
    WorkItem,
    WorkPortfolio,
    WorkType,
    WorkTypeConfig,
    REQUEST_KIND_PRIMARY,
    ROLE_SUPER_ADMIN,
    ROUTING_STRATEGY_CATEGORY,
    WORK_ITEM_STATUS_DRAFT,
    WORK_ITEM_STATUS_SUBMITTED,
)
from app.seeds.bootstrap import seed_approval_groups, seed_work_types

PORTFOLIO_URL = "/SMF2027/TESTDEPT/techops"


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


@pytest.fixture
def techops_portfolio(app):
    """A TechOps portfolio with no work items, an admin, and a view-only member.

    The department is enabled by omission: is_department_enabled_for_event
    treats a missing EventCycleDepartment row as enabled, so no enablement
    row is needed here.
    """
    admin = User(id="test:admin", email="admin@test.local",
                 display_name="Test Admin", is_active=True)
    viewer = User(id="test:viewer", email="viewer@test.local",
                  display_name="Test Viewer", is_active=True)
    db.session.add_all([admin, viewer])
    db.session.flush()
    db.session.add(UserRole(user_id=admin.id, role_code=ROLE_SUPER_ADMIN))

    seed_approval_groups(seed_work_types())
    work_type = WorkType.query.filter_by(code="TECHOPS").one()
    # bootstrap seeds TECHOPS inactive. department_home builds its cards from
    # get_active_work_types(), so without this the page renders no TechOps
    # card at all and these tests would pass or fail for the wrong reason.
    work_type.is_active = True
    db.session.add(WorkTypeConfig(
        work_type_id=work_type.id, url_slug="techops", public_id_prefix="TEC",
        line_detail_type="techops", routing_strategy=ROUTING_STRATEGY_CATEGORY,
        uses_dispatch=False, has_admin_final=False,
    ))

    venue = Venue(code="GLN", name="Gaylord National")
    db.session.add(venue)
    db.session.flush()

    cycle = EventCycle(code="SMF2027", name="Super MAGFest 2027",
                       is_active=True, is_default=True, sort_order=1,
                       venue_id=venue.id)
    dept = Department(code="TESTDEPT", name="Test Department", is_active=True)
    db.session.add_all([cycle, dept])
    db.session.flush()

    membership = DepartmentMembership(
        user_id=viewer.id, department_id=dept.id, event_cycle_id=cycle.id)
    db.session.add(membership)
    db.session.flush()
    db.session.add(DepartmentMembershipWorkTypeAccess(
        department_membership_id=membership.id, work_type_id=work_type.id,
        can_view=True, can_edit=False,
    ))

    db.session.add(WorkPortfolio(
        work_type_id=work_type.id, event_cycle_id=cycle.id,
        department_id=dept.id, created_by_user_id=admin.id,
    ))
    db.session.commit()

    return {"cycle": cycle, "department": dept, "work_type": work_type,
            "admin": admin, "viewer": viewer}


def _make_request(techops_portfolio, status, public_id="SMF2027-TESTDEPT-TEC-1"):
    portfolio = WorkPortfolio.query.filter_by(
        work_type_id=techops_portfolio["work_type"].id,
        event_cycle_id=techops_portfolio["cycle"].id,
        department_id=techops_portfolio["department"].id,
    ).one()
    work_item = WorkItem(
        portfolio_id=portfolio.id, request_kind=REQUEST_KIND_PRIMARY,
        status=status, public_id=public_id,
        created_by_user_id=techops_portfolio["admin"].id,
    )
    db.session.add(work_item)
    db.session.commit()
    return work_item


def test_no_request_sends_an_editor_to_the_new_form(client, techops_portfolio):
    _login(client, "test:admin")

    resp = client.get(PORTFOLIO_URL)

    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/SMF2027/TESTDEPT/techops/new")


def test_a_draft_sends_the_requester_to_its_detail_page(client, techops_portfolio):
    _make_request(techops_portfolio, WORK_ITEM_STATUS_DRAFT)
    _login(client, "test:admin")

    resp = client.get(PORTFOLIO_URL)

    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(
        "/SMF2027/TESTDEPT/techops/item/SMF2027-TESTDEPT-TEC-1")


def test_a_submitted_request_lands_on_the_same_detail_page(client, techops_portfolio):
    """Draft and submitted share one destination. The detail page carries
    Edit Draft and Submit, so it is a complete home for either."""
    _make_request(techops_portfolio, WORK_ITEM_STATUS_SUBMITTED)
    _login(client, "test:admin")

    resp = client.get(PORTFOLIO_URL)

    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(
        "/SMF2027/TESTDEPT/techops/item/SMF2027-TESTDEPT-TEC-1")


def test_a_view_only_member_with_no_request_lands_on_the_department_home(
        client, techops_portfolio):
    """The new-request form aborts 403 for a member who cannot edit, so
    sending them there would replace a redirect with an error page."""
    _login(client, "test:viewer")

    resp = client.get(PORTFOLIO_URL)

    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/SMF2027/TESTDEPT/")


def test_a_view_only_member_still_reaches_an_existing_request(
        client, techops_portfolio):
    _make_request(techops_portfolio, WORK_ITEM_STATUS_SUBMITTED)
    _login(client, "test:viewer")

    resp = client.get(PORTFOLIO_URL)

    assert resp.status_code == 302
    assert resp.headers["Location"].endswith(
        "/SMF2027/TESTDEPT/techops/item/SMF2027-TESTDEPT-TEC-1")


class TestDepartmentHomeTechOpsCard:
    """department_home.html links at the TechOps request directly.

    /techops only redirects, so routing the card through it would cost the
    common path a hop. The card knows the request already: department.py
    loads it as card.primary_item.
    """

    def test_card_links_at_the_new_form_when_nothing_is_started(
            self, client, techops_portfolio):
        _login(client, "test:admin")

        resp = client.get("/SMF2027/TESTDEPT/")

        assert resp.status_code == 200
        assert b"/SMF2027/TESTDEPT/techops/new" in resp.data

    def test_card_links_at_the_request_once_one_exists(
            self, client, techops_portfolio):
        _make_request(techops_portfolio, WORK_ITEM_STATUS_SUBMITTED)
        _login(client, "test:admin")

        resp = client.get("/SMF2027/TESTDEPT/")

        assert resp.status_code == 200
        assert (b"/SMF2027/TESTDEPT/techops/item/SMF2027-TESTDEPT-TEC-1"
                in resp.data)

    def test_a_view_only_member_sees_not_started_rather_than_coming_soon(
            self, client, techops_portfolio):
        """"Coming soon" is the placeholder for a work type with no UI. Using
        it here would claim TechOps is unbuilt when it is merely unstarted."""
        _login(client, "test:viewer")

        resp = client.get("/SMF2027/TESTDEPT/")

        assert resp.status_code == 200
        assert b"Not started yet" in resp.data
        assert b"Coming soon" not in resp.data
