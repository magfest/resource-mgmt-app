"""The TechOps admin home: what needs me, who hasn't asked, and scale."""
import re
from datetime import date, datetime

from app import db
from app.models import (
    ApprovalGroup, Department, EventCycle, ROLE_WORKTYPE_ADMIN,
    REVIEW_STATUS_NEEDS_INFO, WORK_ITEM_STATUS_DRAFT, WORK_ITEM_STATUS_SUBMITTED,
)
from app.routes.work.helpers.review_state import group_progress_for_items
from app.routes.work.techops.home import departments_not_submitted, team_queues

from tests.integration.techops_review_helpers import (
    add_request, add_routed_line, add_space, add_user_with_role, login,
    set_review_status,
)
from tests.integration.test_techops_review_affordances import (  # noqa: F401
    techops_review,
)

HOME = "/admin/techops/"


def _cycle():
    return EventCycle.query.filter_by(code="SMF2027").one()


def _groups(*codes):
    return [ApprovalGroup.query.filter_by(code=c).one() for c in codes]


def test_team_queues_match_the_progress_strip(techops_review):
    item = techops_review["item"]
    item.submitted_at = datetime(2026, 10, 1, 9, 0)
    set_review_status(add_routed_line(item, 2, "TECHOPS_NET"), REVIEW_STATUS_NEEDS_INFO)

    queues = {q.group.code: q for q in team_queues(_cycle(), _groups("TECHOPS_GEN", "TECHOPS_NET"))}
    strip = {p.group.code: p for p in group_progress_for_items([item.id])[item.id]}

    assert (queues["TECHOPS_GEN"].pending, queues["TECHOPS_GEN"].waiting_on_requester) \
        == (strip["TECHOPS_GEN"].pending, 0) == (1, 0)
    assert (queues["TECHOPS_NET"].pending, queues["TECHOPS_NET"].waiting_on_requester) == (0, 1)
    assert queues["TECHOPS_GEN"].oldest_pending_since == datetime(2026, 10, 1, 9, 0)
    assert queues["TECHOPS_NET"].oldest_pending_since is None


def test_departments_with_a_room_and_no_submission_are_listed(techops_review):
    add_space("Hotel Office", holder=add_request("PANELS", WORK_ITEM_STATUS_DRAFT,
                                                  "SMF2027-PANELS-TEC-1").portfolio.department)
    hotels = Department(code="HOTELS", name="Hotels", is_active=True)
    arcade = Department(code="ARCADE", name="Arcade", is_active=True)
    db.session.add_all([hotels, arcade])
    db.session.commit()
    add_space("Hotels Desk", holder=hotels)
    add_space("Main Stage", holder=Department.query.filter_by(code="TESTDEPT").one())

    no_request, draft_only = departments_not_submitted(_cycle())
    wide_no_request, _ = departments_not_submitted(_cycle(), all_departments=True)

    assert [m.department.code for m in no_request] == ["HOTELS"]
    assert [m.department.code for m in draft_only] == ["PANELS"]
    assert draft_only[0].draft.public_id == "SMF2027-PANELS-TEC-1"
    assert [m.department.code for m in wide_no_request] == ["ARCADE", "HOTELS"]


def test_a_department_that_needs_nothing_counts_as_submitted(techops_review):
    item = techops_review["item"]
    add_routed_line(item, 2, "TECHOPS_GEN", service_code="NO_SERVICES")
    add_space("Quiet Room", holder=item.portfolio.department)

    no_request, draft_only = departments_not_submitted(_cycle())

    assert no_request == [] and draft_only == []


def test_a_member_sees_their_queue_and_no_admin_links(client, techops_review):
    login(client, "test:reviewer")

    html = client.get(HOME).get_data(as_text=True)

    assert 'href="/approvals/TECHOPS_GEN"' in html
    assert 'href="/approvals/TECHOPS_NET"' not in html
    assert "/admin/techops/requests/" not in html
    assert 'href="/admin/techops/worklist/"' in html


def test_a_viewer_sees_every_queue_and_all_requests(client, techops_review):
    add_user_with_role("test:viewer", "WORKTYPE_VIEWER")
    login(client, "test:viewer")

    html = client.get(HOME).get_data(as_text=True)

    assert 'href="/approvals/TECHOPS_NET"' in html
    assert "/admin/techops/requests/" in html


def test_scale_card_links_each_service_into_the_worklist(client, techops_review):
    login(client, "test:reviewer")

    html = client.get(HOME).get_data(as_text=True)
    links = re.findall(r'href="(/admin/techops/worklist/\?[^"]+)"', html)

    assert any("service=OTHER" in link and "team=all" in link for link in links)


def test_deadline_shows_on_the_who_hasnt_asked_card(client, techops_review):
    _cycle().submission_deadline = date(2026, 11, 1)
    db.session.commit()
    login(client, "test:reviewer")

    assert "Deadline Nov 01, 2026" in client.get(HOME).get_data(as_text=True)


def test_home_is_refused_to_a_requester_and_linked_from_the_menu(client, techops_review):
    login(client, "test:author")
    assert client.get(HOME).status_code == 403

    login(client, "test:reviewer")
    assert f'href="{HOME}"' in client.get(HOME).get_data(as_text=True)
