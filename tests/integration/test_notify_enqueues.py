"""notify_* writes outbox rows; nothing sends inline any more.

The route-level test is the one that matters. A service-level test still
passes when the route commits the workflow change before notifying, which is
exactly the shape the outbox exists to remove.
"""
from unittest.mock import MagicMock, patch

from sqlalchemy import text

from app import db
from app.models import EmailOutbox, NotificationLog
from app.models.constants import (
    NOTIF_STATUS_SENT,
    OUTBOX_STATUS_QUEUED,
    WORK_ITEM_STATUS_DRAFT,
    WORK_ITEM_STATUS_SUBMITTED,
)
from app.services import email_enqueue, notifications
from app.services.notifications import (
    announce_work_item_event,
    notify_work_item_submitted,
)

SUBMIT_URL = "/TST2026/TESTDEPT/budget/item/TST2026-TESTDEPT-BUD-1/submit"


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def test_submit_enqueues_and_does_not_call_ses(app, seed_draft_work_item):
    work_item = seed_draft_work_item["work_item"]

    with patch("app.services.email.send_via_ses") as ses:
        queued = notify_work_item_submitted(work_item)
        db.session.commit()

    assert ses.call_count == 0
    assert queued > 0

    rows = db.session.query(EmailOutbox).all()
    assert len(rows) == queued
    assert {r.status for r in rows} == {OUTBOX_STATUS_QUEUED}
    assert {r.work_item_id for r in rows} == {work_item.id}


def test_rollback_loses_the_email(app, seed_draft_work_item):
    """The outbox guarantee: an email cannot exist for an action that rolled back."""
    notify_work_item_submitted(seed_draft_work_item["work_item"])
    db.session.rollback()

    assert db.session.query(EmailOutbox).count() == 0


def test_route_level_atomicity(app, client, seed_draft_work_item):
    """A failed enqueue must not leave a submitted item behind.

    The service-level tests above pass even when the route commits first.
    This one does not, which is the point: the guarantee has to hold where
    the workflow transaction actually lives.
    """
    work_item = seed_draft_work_item["work_item"]
    item_id = work_item.id
    _login(client, "test:admin")

    with patch(
        "app.services.notifications._enqueue_emails",
        side_effect=RuntimeError("boom"),
    ):
        try:
            client.post(SUBMIT_URL, follow_redirects=False)
        except RuntimeError:
            pass

    db.session.rollback()
    db.session.expire_all()
    refreshed = db.session.get(type(work_item), item_id)

    # Either both the status change and the rows landed, or neither did.
    rows = db.session.query(EmailOutbox).count()
    assert (refreshed.status == WORK_ITEM_STATUS_DRAFT and rows == 0) or rows > 0


# ============================================================
# Slack is a separate, post-commit call
# ============================================================
#
# Patch target matters. notifications.py does `from .slack import
# send_slack_message` at module load, so it holds its own reference and
# patching "app.services.slack.send_slack_message" does NOT intercept it.
# Verified: with that target, notifications.send_slack_message is still the
# real function and the assertion would be vacuous.

def test_notify_does_not_call_slack(app, seed_draft_work_item):
    """notify_* enqueues only. A webhook call inside the workflow transaction
    would hold the work item's row locks for an HTTP round trip."""
    work_item = seed_draft_work_item["work_item"]

    with patch("app.services.notifications.is_slack_enabled", return_value=True), \
            patch("app.services.notifications.send_slack_message") as slack:
        notify_work_item_submitted(work_item)
        db.session.commit()

    assert slack.call_count == 0
    assert db.session.query(EmailOutbox).count() > 0


def test_announce_calls_slack(app, seed_draft_work_item):
    """The announcement path is what posts to the channel."""
    work_item = seed_draft_work_item["work_item"]

    with patch("app.services.notifications.is_slack_enabled", return_value=True), \
            patch("app.services.notifications.send_slack_message") as slack:
        announce_work_item_event(work_item, "submitted")

    assert slack.call_count == 1
    assert slack.call_args.kwargs["template_key"] == "submitted"
    assert slack.call_args.kwargs["work_item_id"] == work_item.id
    assert db.session.query(EmailOutbox).count() == 0


def test_submit_route_announces_after_commit(app, client, seed_draft_work_item):
    """Guard against a call site queueing the email and forgetting the Slack
    announcement, which the split makes easy to do."""
    _login(client, "test:admin")

    with patch("app.services.notifications.is_slack_enabled", return_value=True), \
            patch("app.services.notifications.send_slack_message") as slack:
        response = client.post(SUBMIT_URL, follow_redirects=False)

    assert response.status_code == 302
    assert slack.call_count == 1
    assert slack.call_args.kwargs["template_key"] == "submitted"


def test_announce_commits_the_notification_log_row(app, seed_draft_work_item):
    """The Slack NotificationLog row must be committed, not just added.

    slack._log_notification only calls session.add; the commit was always the
    caller's job. announce_work_item_event now runs after the last workflow
    commit, so nothing else would commit this row. Losing it also disables
    slack._was_recently_sent, whose one-hour debounce is the only thing
    stopping a repeat event from posting to the channel twice.
    """
    work_item = seed_draft_work_item["work_item"]
    app.config.update(
        SLACK_ENABLED=True,
        SLACK_BOT_TOKEN="xoxb-test",
        SLACK_CHANNEL_ID="C123",
    )

    ok_response = MagicMock()
    ok_response.json.return_value = {"ok": True, "ts": "1700000000.000100"}

    with patch("app.services.slack.requests.post", return_value=ok_response):
        announce_work_item_event(work_item, "submitted")

    # Roll back before asserting. Anything still uncommitted disappears here,
    # which is exactly what happened at request teardown before this fix.
    db.session.rollback()

    rows = db.session.query(NotificationLog).filter_by(
        channel="SLACK", template_key="submitted",
    ).all()
    assert len(rows) == 1
    assert rows[0].status == NOTIF_STATUS_SENT
    assert rows[0].work_item_id == work_item.id


def test_enqueue_db_error_costs_only_that_recipient(app, seed_draft_work_item):
    """A failing INSERT on one recipient costs that recipient only.

    Read this test for what it is. It runs on SQLite, where
    enqueue_savepoint deliberately takes no savepoint, so it proves the
    SQLite half: a failed statement leaves the session usable, the remaining
    recipients enqueue, and the caller's commit succeeds. It does NOT
    exercise the Postgres savepoint path, and no SQLite test can: SQLite has
    no aborted-transaction state to reproduce. The gate itself is covered by
    test_savepoint_taken_on_postgres.
    """
    work_item = seed_draft_work_item["work_item"]
    real_enqueue = email_enqueue.enqueue_email
    calls = []

    def fail_first(*args, **kwargs):
        calls.append(args[1])
        if len(calls) == 1:
            # A real failing INSERT, not a bare RuntimeError: id is NOT NULL.
            db.session.execute(
                text("INSERT INTO email_outbox (id, template_key) VALUES (NULL, 'x')")
            )
        return real_enqueue(*args, **kwargs)

    # A pending workflow change, uncommitted, exactly as a route would have it.
    work_item.status = WORK_ITEM_STATUS_SUBMITTED

    with patch("app.services.notifications.enqueue_email", side_effect=fail_first):
        queued = notifications._enqueue_emails(
            recipients=["first@test.local", "second@test.local"],
            kind="submitted",
            work_item=work_item,
            empty_recipients_msg="none",
        )

    # The caller's commit must still work; this is what breaks without the
    # savepoint once the connection is in an aborted state.
    db.session.commit()

    assert calls == ["first@test.local", "second@test.local"]
    assert queued == 1
    db.session.expire_all()
    rows = db.session.query(EmailOutbox).all()
    assert [r.recipient_email for r in rows] == ["second@test.local"]
    assert db.session.get(type(work_item), work_item.id).status == WORK_ITEM_STATUS_SUBMITTED


def test_savepoint_taken_on_postgres(app):
    """The savepoint branch is engine-gated, so test the gate.

    The suite runs on SQLite, which takes the other branch. This asserts the
    Postgres path reaches begin_nested; without it the gate could invert and
    every SQLite test would still pass.
    """
    fake_db = MagicMock()
    fake_db.session.get_bind.return_value.dialect.name = "postgresql"

    with patch("app.services.notifications.db", fake_db):
        with notifications.enqueue_savepoint():
            pass

    fake_db.session.begin_nested.assert_called_once()

    fake_db = MagicMock()
    fake_db.session.get_bind.return_value.dialect.name = "sqlite"

    with patch("app.services.notifications.db", fake_db):
        with notifications.enqueue_savepoint():
            pass

    fake_db.session.begin_nested.assert_not_called()


def test_announce_swallows_a_raising_formatter(app, seed_draft_work_item):
    """announce_work_item_event must never raise, at any call site.

    Every caller runs it after committing the workflow change it announces.
    A formatter exception escaping here would 500 a request whose real work
    already landed, and would abort the board-release loop partway through
    its pending items. The commit was already guarded; the formatter was not.
    """
    work_item = seed_draft_work_item["work_item"]

    def _boom(_):
        raise RuntimeError("formatter reached a detached relation")

    with patch("app.services.notifications.is_slack_enabled", return_value=True), \
            patch.dict(
                notifications._ANNOUNCEMENT_FORMATTERS,
                {"finalized": _boom},
            ), \
            patch("app.services.notifications.send_slack_message") as slack:
        announce_work_item_event(work_item, "finalized")

    assert slack.call_count == 0
