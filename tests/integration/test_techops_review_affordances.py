"""TechOps review controls match what the server will actually accept.

A per-line review link must survive the request reaching FINALIZED. The
fixture here is shared by the other TechOps review-flow tests.
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
