"""The worklist page: who opens it, what it shows, and how filters travel."""
from app.models import ROLE_WORKTYPE_ADMIN, REVIEW_STATUS_REJECTED

from tests.integration.techops_review_helpers import (
    add_routed_line, add_user_with_role, login, set_review_status,
)
from tests.integration.test_techops_review_affordances import (  # noqa: F401
    BASE, ITEM_ID, REVIEW_URL, techops_review,
)

WORKLIST = "/admin/techops/worklist/"
NET_LINE_URL = f"{BASE}/item/{ITEM_ID}/line/2/review"


def test_group_member_admin_and_viewer_can_open_it(client, techops_review):
    add_user_with_role("test:techopsadmin", ROLE_WORKTYPE_ADMIN)
    add_user_with_role("test:viewer", "WORKTYPE_VIEWER")

    for user in ("test:reviewer", "test:techopsadmin", "test:viewer"):
        login(client, user)
        assert client.get(WORKLIST).status_code == 200, user


def test_requester_and_another_work_types_admin_are_refused(client, techops_review):
    add_user_with_role("test:budgetadmin", ROLE_WORKTYPE_ADMIN, work_type_code="BUDGET")

    for user in ("test:author", "test:budgetadmin"):
        login(client, user)
        assert client.get(WORKLIST).status_code == 403, user


def test_page_shows_group_counts_and_links_each_line(client, techops_review):
    login(client, "test:reviewer")

    html = client.get(WORKLIST).get_data(as_text=True)

    assert "Department-wide · 0 approved · 1 pending" in html
    assert f'href="{REVIEW_URL}"' in html
    assert 'class="responsive-cards"' in html


def test_a_member_starts_on_their_team_and_can_widen_to_any_team(client, techops_review):
    add_routed_line(techops_review["item"], 2, "TECHOPS_NET")
    login(client, "test:reviewer")

    default_html = client.get(WORKLIST).get_data(as_text=True)
    net_html = client.get(WORKLIST + "?team=TECHOPS_NET&group=service").get_data(as_text=True)

    assert NET_LINE_URL not in default_html
    assert NET_LINE_URL in net_html
    assert REVIEW_URL not in net_html
    assert 'value="TECHOPS_NET" selected' in net_html
    assert 'value="service" selected' in net_html


def test_unknown_event_is_404_and_empty_results_say_so(client, techops_review):
    login(client, "test:reviewer")
    assert client.get(WORKLIST + "?event=NOPE").status_code == 404

    set_review_status(techops_review["line"], REVIEW_STATUS_REJECTED)
    html = client.get(WORKLIST).get_data(as_text=True)

    assert "No submitted TechOps lines for SMF2027 match these filters." in html


def test_techops_menu_links_to_the_worklist(client, techops_review):
    login(client, "test:reviewer")

    html = client.get(WORKLIST).get_data(as_text=True)

    assert f'href="{WORKLIST}"' in html


def _make_wired(techops_review, config, location=None):
    from app import db
    from app.models import TechOpsLineDetail, TechOpsServiceType
    detail = db.session.get(TechOpsLineDetail, techops_review["line"].id)
    detail.service_type_id = TechOpsServiceType.query.filter_by(code="ETHERNET").one().id
    detail.config = config
    detail.location = location
    db.session.commit()


def test_a_wired_line_shows_its_kinds_and_rough_count(client, techops_review):
    _make_wired(techops_review, {"kinds": ["PRINTERS", "SCANNERS"], "rough_count": "6-10"})
    login(client, "test:reviewer")

    html = client.get(WORKLIST).get_data(as_text=True)

    assert "Printers, Badge scanners" in html
    assert "Roughly how many: 6-10" in html


def test_a_wired_line_with_unanswered_kinds_says_so(client, techops_review):
    _make_wired(techops_review, {"open_questions": ["kinds"]})
    login(client, "test:reviewer")

    html = client.get(WORKLIST).get_data(as_text=True)

    assert "What needs a cable: not given" in html
    assert "1 open question" in html


def test_a_wired_line_saved_per_drop_still_shows_its_location(client, techops_review):
    _make_wired(techops_review, None, location="Booth 4 by the stage")
    login(client, "test:reviewer")

    html = client.get(WORKLIST).get_data(as_text=True)

    assert "Booth 4 by the stage" in html
