"""The release request queues its own email. There is no sweeper any more."""
from datetime import datetime
from unittest.mock import patch

from app import db
from app.models import (
    DepartmentMembership, EmailOutbox, User, WorkLineReview,
    OUTBOX_STATUS_CANCELLED, OUTBOX_STATUS_QUEUED,
    REVIEW_STAGE_APPROVAL_GROUP, REVIEW_STATUS_APPROVED,
    WORK_ITEM_STATUS_FINALIZED, WORK_ITEM_STATUS_SUBMITTED,
    WORK_LINE_STATUS_APPROVED,
)

BOARD_RELEASE_URL = "/admin/final-review/board-release/"
FINALIZE_URL = "/admin/final-review/finalize/{}"


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def _hold_a_finalized_budget(data):
    """Put the fixture item in the state get_held_budgets selects."""
    item = data["work_item"]
    item.status = WORK_ITEM_STATUS_FINALIZED
    db.session.commit()
    return item


def _add_department_member(data):
    """Add one DepartmentMembership recipient.

    seed_draft_work_item seeds no memberships, so notify_work_item_finalized
    has nothing to queue without this; _get_department_member_emails matches
    only DepartmentMembership and DivisionMembership rows.
    """
    user = User(
        id="test:dept-member", email="deptmember@test.local",
        display_name="Dept Member", is_active=True,
    )
    db.session.add(user)
    db.session.flush()
    db.session.add(DepartmentMembership(
        user_id=user.id,
        department_id=data["department"].id,
        event_cycle_id=data["cycle"].id,
    ))
    db.session.commit()
    return user


def _release(client, data, note="board approved"):
    _login(client, "test:admin")
    return client.post(BOARD_RELEASE_URL,
                       data={"event": data["cycle"].code, "note": note},
                       follow_redirects=True)


def test_the_release_queues_the_department_email(app, client, seed_draft_work_item):
    """The sweeper used to queue this. The release request does it now."""
    data = seed_draft_work_item
    _add_department_member(data)
    item = _hold_a_finalized_budget(data)
    assert db.session.query(EmailOutbox).count() == 0

    resp = _release(client, data)

    assert resp.status_code == 200
    db.session.refresh(item)
    assert item.board_released_at is not None
    assert db.session.query(EmailOutbox).count() > 0


def test_a_second_release_queues_nothing_more(app, client, seed_draft_work_item):
    """release_event_budgets is idempotent: nothing held means nothing queued."""
    data = seed_draft_work_item
    _add_department_member(data)
    _hold_a_finalized_budget(data)
    _release(client, data)
    after_first = db.session.query(EmailOutbox).count()
    assert after_first > 0

    _release(client, data, note="approved again")

    assert db.session.query(EmailOutbox).count() == after_first


def test_the_release_posts_one_channel_summary(app, client, seed_draft_work_item):
    """One post for the release, not one per released budget.

    Patch target is the notifications module, not the dashboard namespace: the
    view imports the name inside its own body, so patching dashboard would
    intercept nothing and this test would pass while asserting nothing.
    """
    data = seed_draft_work_item
    _hold_a_finalized_budget(data)

    with patch("app.services.notifications.announce_board_release") as announce:
        _release(client, data)

    assert announce.call_count == 1
    assert announce.call_args.args[1] == 1


def test_one_bad_department_does_not_cost_the_release(app, client, seed_draft_work_item):
    """A department whose enqueue raises must not roll back the release."""
    data = seed_draft_work_item
    item = _hold_a_finalized_budget(data)

    with patch("app.services.notifications.notify_work_item_finalized",
               side_effect=RuntimeError("recipient lookup failed")):
        resp = _release(client, data)

    assert resp.status_code == 200
    db.session.refresh(item)
    assert item.board_released_at is not None


def _ready_to_finalize(data):
    """Put the fixture item in a state can_finalize_work_item accepts.

    Copied from test_board_approval_hold.py, which is the pattern for
    finalize tests in this repo.
    """
    item, line = data["work_item"], data["line"]
    item.status = WORK_ITEM_STATUS_SUBMITTED
    line.status = WORK_LINE_STATUS_APPROVED
    line.current_review_stage = REVIEW_STAGE_APPROVAL_GROUP
    db.session.add(WorkLineReview(
        work_line_id=line.id, stage=REVIEW_STAGE_APPROVAL_GROUP,
        approval_group_id=data["approval_group"].id,
        status=REVIEW_STATUS_APPROVED, approved_amount_cents=5000,
        created_by_user_id=data["admin"].id))
    db.session.commit()
    return item


def _approve_the_board_topline(data):
    """Set the event latch so a later finalize releases immediately."""
    data["cycle"].board_approved_at = datetime.utcnow()
    db.session.commit()


def _finalize(client, data, note="ok"):
    item = _ready_to_finalize(data)
    _login(client, "test:admin")
    resp = client.post(FINALIZE_URL.format(item.id), data={"note": note},
                       follow_redirects=True)
    return item, resp


def test_finalize_after_board_approval_queues_immediately(app, client, seed_draft_work_item):
    """The board already approved, so this budget releases on finalize.

    Before this task nothing queued here; the sweeper caught it later.
    """
    data = seed_draft_work_item
    _add_department_member(data)
    _approve_the_board_topline(data)

    item, resp = _finalize(client, data)

    assert resp.status_code == 200
    db.session.refresh(item)
    assert item.board_released_at is not None
    assert db.session.query(EmailOutbox).count() > 0


def test_finalize_before_board_approval_queues_nothing(app, client, seed_draft_work_item):
    """Held budgets tell nobody. The department waits for the board."""
    data = seed_draft_work_item
    _add_department_member(data)

    item, resp = _finalize(client, data)

    assert resp.status_code == 200
    db.session.refresh(item)
    assert item.board_released_at is None
    assert db.session.query(EmailOutbox).count() == 0


def test_a_held_finalize_announces_nothing(app, client, seed_draft_work_item):
    """Only a released budget is channel news.

    Patch target is the notifications module, not the dashboard namespace: the
    view imports the name inside its own body.
    """
    data = seed_draft_work_item

    with patch("app.services.notifications.announce_work_item_event") as announce:
        _finalize(client, data)

    assert announce.call_count == 0


def test_finalize_survives_a_failing_enqueue(app, client, seed_draft_work_item):
    """A finalize must not be lost because its email could not be queued.

    test_notification_resilience.py dropped this when finalize stopped
    notifying inline and deferred to the sweeper's coverage. Finalize notifies
    inline again and the sweeper is gone, so the test comes back here.
    """
    data = seed_draft_work_item
    _add_department_member(data)
    _approve_the_board_topline(data)

    with patch("app.services.notifications.notify_work_item_finalized",
               side_effect=RuntimeError("recipient lookup failed")):
        item, resp = _finalize(client, data)

    assert resp.status_code == 200
    db.session.refresh(item)
    assert item.status == WORK_ITEM_STATUS_FINALIZED
    assert item.board_released_at is not None


def test_refinalize_after_unfinalize_queues_again(app, client, seed_draft_work_item):
    """Unfinalize clears board_released_at, so the dedup key changes.

    The dropped finalized_notified_at column is not what made this work.
    _event_stamp('finalized', item) returns board_released_at
    (notifications.py:301-302), so a re-release is a different key and enqueues
    a second row rather than being swallowed as a duplicate.
    """
    data = seed_draft_work_item
    _add_department_member(data)
    _approve_the_board_topline(data)
    item = _ready_to_finalize(data)
    _login(client, "test:admin")

    client.post(FINALIZE_URL.format(item.id), data={"note": "first pass"},
                follow_redirects=True)
    first = db.session.query(EmailOutbox).count()
    assert first > 0

    client.post(f"/admin/final-review/unfinalize/{item.id}",
                data={"reason": "numbers changed"}, follow_redirects=True)
    db.session.refresh(item)
    assert item.board_released_at is None

    # Unfinalize already puts status back to SUBMITTED and leaves the line
    # review intact (reset_lines was not "yes"), so a second
    # _ready_to_finalize call would insert a duplicate WorkLineReview and
    # violate its (work_line_id, stage, approval_group_id) unique constraint.
    client.post(FINALIZE_URL.format(item.id), data={"note": "second pass"},
                follow_redirects=True)

    assert db.session.query(EmailOutbox).count() == first * 2


def _unfinalize(client, item, reason="numbers changed"):
    _login(client, "test:admin")
    return client.post(f"/admin/final-review/unfinalize/{item.id}",
                       data={"reason": reason}, follow_redirects=True)


def _release_rows():
    return db.session.query(EmailOutbox).filter_by(template_key="finalized").all()


def test_a_dark_finalized_template_stops_the_release(app, client, seed_draft_work_item):
    """The drainer cancels a row whose template is off, and nothing re-sends it.

    Releasing anyway would stamp the budgets, lose every email for good, and
    drop the budgets out of get_held_budgets. The release refuses instead.
    """
    data = seed_draft_work_item
    _add_department_member(data)
    item = _hold_a_finalized_budget(data)
    data["finalized_template"].is_active = False
    db.session.commit()

    resp = _release(client, data)

    assert resp.status_code == 200
    assert "missing or inactive" in resp.get_data(as_text=True)
    db.session.refresh(item)
    db.session.refresh(data["cycle"])
    assert item.board_released_at is None
    assert data["cycle"].board_approved_at is None
    assert db.session.query(EmailOutbox).count() == 0

    # Mutation check: the only thing holding the release back is the checkbox.
    data["finalized_template"].is_active = True
    db.session.commit()

    _release(client, data)

    db.session.refresh(item)
    assert item.board_released_at is not None
    assert db.session.query(EmailOutbox).count() > 0


def test_unfinalize_cancels_the_queued_release_email(app, client, seed_draft_work_item):
    """A queued release email claims a final budget. Unfinalize makes that false."""
    data = seed_draft_work_item
    _add_department_member(data)
    item = _hold_a_finalized_budget(data)
    _release(client, data)
    assert [r.status for r in _release_rows()] == [OUTBOX_STATUS_QUEUED]

    # A different kind for the same item. The cancel is scoped to one template
    # key, so this row must survive untouched.
    decoy = EmailOutbox(
        template_key="needs_attention", recipient_email="deptmember@test.local",
        work_item_id=item.id, status=OUTBOX_STATUS_QUEUED,
    )
    db.session.add(decoy)
    db.session.commit()

    resp = _unfinalize(client, item)

    assert resp.status_code == 200
    rows = _release_rows()
    assert [r.status for r in rows] == [OUTBOX_STATUS_CANCELLED]
    assert "Unfinalize" in rows[0].last_error
    db.session.refresh(decoy)
    assert decoy.status == OUTBOX_STATUS_QUEUED


def test_refinalize_after_a_cancel_queues_a_fresh_row(app, client, seed_draft_work_item):
    """Cancelling the old row must not poison the dedup key.

    The cancelled row keeps the key built from the old board_released_at. A
    re-finalize writes a new stamp, so the new key differs and a row is created.
    """
    data = seed_draft_work_item
    _add_department_member(data)
    item = _hold_a_finalized_budget(data)
    _release(client, data)
    _unfinalize(client, item)
    assert [r.status for r in _release_rows()] == [OUTBOX_STATUS_CANCELLED]

    _finalize(client, data)

    db.session.refresh(item)
    assert item.board_released_at is not None
    statuses = sorted(r.status for r in _release_rows())
    assert statuses == [OUTBOX_STATUS_CANCELLED, OUTBOX_STATUS_QUEUED]
