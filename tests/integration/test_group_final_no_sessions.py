"""TechOps has no review sessions, and a second decision on a line is refused
with a message a reviewer can act on."""
from app.models import WorkItem, WorkLineReview, REVIEW_STATUS_APPROVED

from tests.integration.techops_review_helpers import line_url, login
from tests.integration.test_techops_review_affordances import (  # noqa: F401
    CHECKOUT_URL, _hold_lock, techops_review,
)


def test_checkout_is_refused_on_a_techops_request(client, techops_review):
    login(client, "test:reviewer")

    client.post(CHECKOUT_URL)

    assert WorkItem.query.get(techops_review["item"].id).checked_out_by_user_id is None


def test_second_decision_says_the_line_was_already_decided(client, techops_review):
    login(client, "test:reviewer")
    client.post(line_url(1, "approve"), data={"ajax": "1"})
    login(client, "test:other")

    data = client.post(line_url(1, "reject"),
                       data={"ajax": "1", "note": "too late"}).get_json()

    assert data["success"] is False
    assert data["error"] == "This line was already decided."
    assert data["current_status"] == "APPROVED"


def test_a_lock_left_from_before_deploy_does_not_block(client, techops_review):
    _hold_lock(techops_review["item"], "test:other")
    login(client, "test:reviewer")

    client.post(line_url(1, "approve"))

    review = WorkLineReview.query.filter_by(work_line_id=techops_review["line"].id).one()
    assert review.status == REVIEW_STATUS_APPROVED


def test_a_decision_racing_a_stale_copy_is_refused(app, techops_review):
    """Two simultaneous requests both load the review as PENDING. Simulate
    the slower one: its in-memory copy is stale once the faster one commits."""
    from sqlalchemy import text

    from app import db
    from app.models import REVIEW_ACTION_REJECT, REVIEW_STATUS_REJECTED
    from app.routes import UserContext
    from app.routes.approvals.helpers import apply_review_decision

    line = techops_review["line"]
    stale = WorkLineReview.query.filter_by(work_line_id=line.id).one()
    db.session.execute(
        text("UPDATE work_line_reviews SET status = 'APPROVED' WHERE id = :id"),
        {"id": stale.id},
    )
    reviewer = UserContext(
        user_id="test:other", user=None, roles=("APPROVER",),
        is_super_admin=False, approval_group_ids={techops_review["group"].id},
    )

    ok, error = apply_review_decision(
        review=stale, line=line, work_item=techops_review["item"],
        action=REVIEW_ACTION_REJECT, note="late", amount_cents=None, user_ctx=reviewer,
    )

    assert (ok, error) == (False, "This line was already decided.")
    assert stale.status != REVIEW_STATUS_REJECTED


def test_a_decision_locks_the_request_before_the_review(app, monkeypatch, techops_review):
    """Two reviewers deciding the last two pending lines at once must not both
    see the other's line as pending, or neither finalizes the request.

    Locking the request row first serializes them on PostgreSQL, so the later
    decision sees the earlier one. SQLite cannot run the two transactions at
    once, so this pins the lock order instead of the race itself.
    """
    from app import db
    from app.models import REVIEW_ACTION_APPROVE
    from app.routes import UserContext
    from app.routes.approvals.helpers import apply_review_decision

    locked = []
    real_refresh = db.session.refresh

    def spy(instance, *args, **kwargs):
        if kwargs.get("with_for_update"):
            locked.append(type(instance).__name__)
        return real_refresh(instance, *args, **kwargs)

    monkeypatch.setattr(db.session, "refresh", spy)
    review = WorkLineReview.query.filter_by(work_line_id=techops_review["line"].id).one()
    reviewer = UserContext(
        user_id="test:reviewer", user=None, roles=("APPROVER",),
        is_super_admin=False, approval_group_ids={techops_review["group"].id},
    )

    ok, _ = apply_review_decision(
        review=review, line=techops_review["line"], work_item=techops_review["item"],
        action=REVIEW_ACTION_APPROVE, note=None, amount_cents=None, user_ctx=reviewer,
    )

    assert ok
    assert locked == ["WorkItem", "WorkLineReview"]


def test_a_refusal_reports_the_review_status_not_a_stale_line(client, techops_review):
    """In a race the slower request loaded its line before the faster one
    committed, so the line still reads PENDING while the review is APPROVED."""
    from sqlalchemy import text

    from app import db

    review = WorkLineReview.query.filter_by(work_line_id=techops_review["line"].id).one()
    db.session.execute(
        text("UPDATE work_line_reviews SET status = 'APPROVED' WHERE id = :id"),
        {"id": review.id},
    )
    db.session.commit()
    login(client, "test:other")

    data = client.post(line_url(1, "approve"), data={"ajax": "1"}).get_json()

    assert data["error"] == "This line was already decided."
    assert data["current_status"] == "APPROVED"
