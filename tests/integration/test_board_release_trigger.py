"""The release request queues its own email. There is no sweeper any more."""
from unittest.mock import patch

from app import db
from app.models import DepartmentMembership, EmailOutbox, User, WORK_ITEM_STATUS_FINALIZED

BOARD_RELEASE_URL = "/admin/final-review/board-release/"


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
