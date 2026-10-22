"""TechOps review screens show decisions to whoever can make them, with no
review-session controls."""
from app import db
from app.models import ROLE_APPROVER, ROLE_WORKTYPE_ADMIN, ApprovalGroup, User, UserRole

from tests.integration.techops_review_helpers import (
    add_routed_line, add_user_with_role, login,
)
from tests.integration.test_techops_review_affordances import (  # noqa: F401
    BASE, DETAIL_URL, ITEM_ID, REVIEW_URL, techops_review,
)

QUICK_URL = f"{BASE}/item/{ITEM_ID}/quick-review"


def test_detail_page_has_no_review_session_controls(client, techops_review):
    login(client, "test:reviewer")

    html = client.get(DETAIL_URL).get_data(as_text=True)

    assert "Start Reviewing" not in html
    assert "Quick Review" in html


def test_admin_outside_the_group_gets_the_decision_form(client, techops_review):
    add_user_with_role("test:techopsadmin", ROLE_WORKTYPE_ADMIN)
    login(client, "test:techopsadmin")

    html = client.get(REVIEW_URL).get_data(as_text=True)

    assert "Make Decision" in html
    assert "checked out" not in html


def test_quick_review_buttons_follow_the_decide_rule(client, techops_review):
    add_routed_line(techops_review["item"], 2, "TECHOPS_NET")
    net = ApprovalGroup.query.filter_by(code="TECHOPS_NET").one()
    db.session.add(User(id="test:net", email="net@test.local",
                        display_name="Net Reviewer", is_active=True))
    db.session.flush()
    db.session.add(UserRole(user_id="test:net", role_code=ROLE_APPROVER,
                            approval_group_id=net.id))
    db.session.commit()
    add_user_with_role("test:techopsadmin", ROLE_WORKTYPE_ADMIN)

    login(client, "test:net")
    net_html = client.get(QUICK_URL).get_data(as_text=True)
    login(client, "test:techopsadmin")
    admin_html = client.get(QUICK_URL).get_data(as_text=True)

    # The NET reviewer sees only the NET line; the admin sees and decides both.
    assert net_html.count('data-action="approve"') == 1
    assert 'data-line-num="1"' not in net_html
    assert admin_html.count('data-action="approve"') == 2
