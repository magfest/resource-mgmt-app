"""techops_return_options decides who may return a TechOps order to draft."""
from datetime import datetime, timedelta

import pytest

from app import db
from app.models import (
    WorkLine,
    REVIEW_STATUS_APPROVED,
    WORK_ITEM_STATUS_DRAFT,
    WORK_ITEM_STATUS_FINALIZED,
    WORK_ITEM_STATUS_SUBMITTED,
    WORK_LINE_STATUS_PENDING,
)
from app.routes.work.helpers.context import WorkItemPerms
from app.routes.work.techops.send_back import ReturnOptions, techops_return_options
from tests.integration.techops_review_helpers import set_review_status
from tests.integration.test_techops_review_affordances import techops_review  # noqa: F401


def _perms(is_admin):
    return WorkItemPerms(
        can_view=True, can_edit=False, can_submit=False, can_recall=False,
        can_add_lines=False, can_delete=False, can_checkout=False,
        can_checkin=False, is_worktype_admin=is_admin, is_draft=False,
        is_checked_out=False, is_checked_out_by_current_user=False,
    )


def _lock(item, user_id):
    item.checked_out_by_user_id = user_id
    item.checked_out_at = datetime.utcnow()
    item.checked_out_expires_at = datetime.utcnow() + timedelta(minutes=30)
    db.session.commit()


def _editor(item):
    return techops_return_options(item, _perms(False), True, "test:author")


def _admin(item, user_id="test:admin"):
    # build_portfolio_perms gives a work-type admin can_edit, so pass True.
    return techops_return_options(item, _perms(True), True, user_id)


def test_editor_can_pull_back_unreviewed(techops_review):
    assert _editor(techops_review["item"]) == ReturnOptions(True, False)


def test_editor_cannot_pull_back_after_a_decision(techops_review):
    set_review_status(techops_review["line"], REVIEW_STATUS_APPROVED)
    assert _editor(techops_review["item"]) == ReturnOptions(False, False)


def test_line_without_review_counts_as_unreviewed(techops_review):
    item = techops_review["item"]
    db.session.add(WorkLine(work_item_id=item.id, line_number=2,
                            status=WORK_LINE_STATUS_PENDING))
    db.session.commit()
    assert _editor(item) == ReturnOptions(True, False)


def test_editor_cannot_pull_back_while_checked_out(techops_review):
    _lock(techops_review["item"], "test:other")
    assert _editor(techops_review["item"]) == ReturnOptions(False, False)


@pytest.mark.parametrize("status", [WORK_ITEM_STATUS_SUBMITTED, WORK_ITEM_STATUS_FINALIZED])
def test_admin_can_send_back_submitted_and_finalized(techops_review, status):
    item = techops_review["item"]
    item.status = status
    db.session.commit()
    assert _admin(item) == ReturnOptions(False, True)


def test_admin_blocked_only_by_another_users_lock(techops_review):
    item = techops_review["item"]
    _lock(item, "test:admin")
    assert _admin(item) == ReturnOptions(False, True)
    _lock(item, "test:other")
    assert _admin(item) == ReturnOptions(False, False)


def test_neither_option_on_draft(techops_review):
    item = techops_review["item"]
    item.status = WORK_ITEM_STATUS_DRAFT
    db.session.commit()
    assert _editor(item) == ReturnOptions(False, False)
    assert _admin(item) == ReturnOptions(False, False)


def test_approver_gets_neither(techops_review):
    options = techops_return_options(
        techops_review["item"], _perms(False), False, "test:reviewer")
    assert options == ReturnOptions(False, False)
