"""TechOps review controls match what the server will actually accept.

Two gaps BUDGET fixed and TechOps did not: a "Start Reviewing" button that
renders while another reviewer holds the lock, and a per-line review link that
disappears once the request is FINALIZED.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta

import pytest

from app import db
from app.models import (
    ApprovalGroup,
    Department,
    EventCycle,
    TechOpsLineDetail,
    TechOpsServiceType,
    User,
    UserRole,
    WorkItem,
    WorkLine,
    WorkLineReview,
    WorkPortfolio,
    WorkType,
    WorkTypeConfig,
    REQUEST_KIND_PRIMARY,
    REVIEW_STAGE_APPROVAL_GROUP,
    REVIEW_STATUS_PENDING,
    ROLE_APPROVER,
    ROUTING_STRATEGY_CATEGORY,
    WORK_ITEM_STATUS_FINALIZED,
    WORK_ITEM_STATUS_SUBMITTED,
    WORK_LINE_STATUS_PENDING,
)
from app.seeds.bootstrap import (
    seed_approval_groups,
    seed_techops_service_types,
    seed_work_types,
)

ITEM_ID = "SMF2027-TESTDEPT-TEC-1"
BASE = "/SMF2027/TESTDEPT/techops"
REVIEW_URL = f"{BASE}/item/{ITEM_ID}/line/1/review"
DETAIL_URL = f"{BASE}/item/{ITEM_ID}"
CHECKOUT_URL = f"{BASE}/item/{ITEM_ID}/checkout"


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


@pytest.fixture
def techops_review(app):
    """A SUBMITTED TechOps request with one routed line, a reviewer, and a
    second reviewer who can take the lock."""
    seed_techops_service_types(seed_approval_groups(seed_work_types()))
    work_type = WorkType.query.filter_by(code="TECHOPS").one()
    work_type.is_active = True
    db.session.add(WorkTypeConfig(
        work_type_id=work_type.id, url_slug="techops", public_id_prefix="TEC",
        line_detail_type="techops", routing_strategy=ROUTING_STRATEGY_CATEGORY,
        uses_dispatch=False, has_admin_final=False,
    ))
    group = ApprovalGroup.query.filter_by(
        work_type_id=work_type.id, code="TECHOPS_GEN").one()

    author = User(id="test:author", email="author@test.local",
                  display_name="Test Author", is_active=True)
    reviewer = User(id="test:reviewer", email="reviewer@test.local",
                    display_name="Test Reviewer", is_active=True)
    other = User(id="test:other", email="other@test.local",
                 display_name="Other Reviewer", is_active=True)
    db.session.add_all([author, reviewer, other])
    db.session.flush()
    for u in (reviewer, other):
        db.session.add(UserRole(user_id=u.id, role_code=ROLE_APPROVER,
                                approval_group_id=group.id))

    cycle = EventCycle(code="SMF2027", name="Super MAGFest 2027",
                       is_active=True, is_default=True, sort_order=1)
    dept = Department(code="TESTDEPT", name="Test Department", is_active=True)
    db.session.add_all([cycle, dept])
    db.session.flush()

    portfolio = WorkPortfolio(
        work_type_id=work_type.id, event_cycle_id=cycle.id,
        department_id=dept.id, created_by_user_id=author.id,
    )
    db.session.add(portfolio)
    db.session.flush()

    item = WorkItem(
        portfolio_id=portfolio.id, request_kind=REQUEST_KIND_PRIMARY,
        status=WORK_ITEM_STATUS_SUBMITTED, public_id=ITEM_ID,
        created_by_user_id=author.id,
    )
    db.session.add(item)
    db.session.flush()

    line = WorkLine(work_item_id=item.id, line_number=1,
                    status=WORK_LINE_STATUS_PENDING,
                    current_review_stage=REVIEW_STAGE_APPROVAL_GROUP)
    db.session.add(line)
    db.session.flush()

    service = TechOpsServiceType.query.filter_by(code="OTHER").one()
    db.session.add(TechOpsLineDetail(
        work_line_id=line.id, service_type_id=service.id,
        description="A department-wide request with no space.",
        routed_approval_group_id=group.id,
    ))
    db.session.add(WorkLineReview(
        work_line_id=line.id, stage=REVIEW_STAGE_APPROVAL_GROUP,
        approval_group_id=group.id, status=REVIEW_STATUS_PENDING,
        created_by_user_id=author.id,
    ))
    db.session.commit()

    return {"item": item, "line": line, "group": group,
            "reviewer": reviewer, "other": other, "author": author}


def _hold_lock(item, user_id):
    item.checked_out_by_user_id = user_id
    item.checked_out_at = datetime.utcnow()
    item.checked_out_expires_at = datetime.utcnow() + timedelta(minutes=30)
    db.session.commit()


def _line_action_label(body: str) -> str:
    """The text of the per-line review anchor. The label sits on its own line
    inside the tag, so a bare `>View<` never matches."""
    match = re.search(
        r'href="[^"]*/line/1/review"[^>]*>\s*([A-Za-z]+)\s*</a>', body)
    assert match, "no per-line review anchor found on the page"
    return match.group(1)


class TestCheckoutButton:
    def test_no_checkout_button_while_another_reviewer_holds_the_lock(
            self, client, techops_review):
        """The old gate keyed off "do I hold it", which is false both when the
        item is free and when someone else has it, so the button rendered and
        the POST always refused."""
        _hold_lock(techops_review["item"], "test:other")
        _login(client, "test:reviewer")

        resp = client.get(REVIEW_URL)

        assert resp.status_code == 200
        assert CHECKOUT_URL not in resp.get_data(as_text=True)

    def test_a_held_request_still_says_someone_else_has_it(self, client, techops_review):
        """The page must not go silent about the lock. The message comes from
        the "Under Active Review" banner at the top, which predates this fix;
        the checkout callout stays hidden rather than repeating it."""
        _hold_lock(techops_review["item"], "test:other")
        _login(client, "test:reviewer")

        body = client.get(REVIEW_URL).get_data(as_text=True)

        assert "This item is checked out by another reviewer." in body
        assert "Checkout Required" not in body

    def test_the_checkout_form_targets_the_techops_route(
            self, client, techops_review):
        """The shared macro hardcoded the BUDGET checkout URL, so reusing it
        without passing the slug would post a TechOps item to /budget/."""
        _login(client, "test:reviewer")

        body = client.get(REVIEW_URL).get_data(as_text=True)

        assert CHECKOUT_URL in body
        assert f"/SMF2027/TESTDEPT/budget/item/{ITEM_ID}/checkout" not in body


class TestFinalizedLineLink:
    def test_a_finalized_request_still_links_each_line(self, client, techops_review):
        """A finished TechOps request is exactly what a department goes back to
        read. The link vanished at FINALIZED."""
        techops_review["item"].status = WORK_ITEM_STATUS_FINALIZED
        db.session.commit()
        _login(client, "test:reviewer")

        resp = client.get(DETAIL_URL)

        assert resp.status_code == 200
        assert f"{BASE}/item/{ITEM_ID}/line/1/review" in resp.get_data(as_text=True)

    def test_the_link_reads_view_once_finalized(self, client, techops_review):
        techops_review["item"].status = WORK_ITEM_STATUS_FINALIZED
        db.session.commit()
        _login(client, "test:reviewer")

        body = client.get(DETAIL_URL).get_data(as_text=True)

        assert _line_action_label(body) == "View"

    def test_a_submitted_request_still_reads_review(self, client, techops_review):
        """Guards the label swap: only FINALIZED changes wording."""
        _login(client, "test:reviewer")

        body = client.get(DETAIL_URL).get_data(as_text=True)

        assert _line_action_label(body) == "Review"


class TestCheckoutReturnsToTheLine:
    """Checkout must return the reviewer to the line they were reading.

    safe_redirect_url (admin/helpers.py:218) accepts relative paths only, so an
    absolute return_to is silently dropped and the route falls back to the item
    detail page. A reviewer part-way down an eleven-line request lands back at
    the top.
    """

    def test_the_form_sends_a_relative_return_to(self, client, techops_review):
        _login(client, "test:reviewer")

        body = client.get(REVIEW_URL).get_data(as_text=True)

        match = re.search(r'name="return_to" value="([^"]*)"', body)
        assert match, "no return_to field in the checkout form"
        assert match.group(1).startswith("/"), (
            f"return_to must be relative or safe_redirect_url drops it; "
            f"got {match.group(1)!r}"
        )

    def test_checkout_redirects_back_to_the_line(self, client, techops_review):
        _login(client, "test:reviewer")
        body = client.get(REVIEW_URL).get_data(as_text=True)
        return_to = re.search(r'name="return_to" value="([^"]*)"', body).group(1)
        token = re.search(r'name="csrf_token" value="([^"]*)"', body).group(1)

        resp = client.post(CHECKOUT_URL,
                           data={"csrf_token": token, "return_to": return_to})

        assert resp.status_code == 302
        assert "/line/1/review" in resp.headers["Location"]
