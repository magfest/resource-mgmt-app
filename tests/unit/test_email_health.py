"""Queue health and message lookup queries."""
from datetime import datetime, timedelta

from app import db
from app.models import EmailMessageBody, EmailOutbox, NotificationLog, WorkItem
from app.models.constants import (
    OUTBOX_STATUS_FAILED,
    OUTBOX_STATUS_QUEUED,
    OUTBOX_STATUS_RENDER_BLOCKED,
    OUTBOX_STATUS_SENT,
    OUTBOX_STATUS_SUPPRESSED,
    REQUEST_KIND_PRIMARY,
    WORK_ITEM_STATUS_DRAFT,
)
from app.services.email_health import (
    get_queue_health,
    lookup_messages,
    pending_messages,
)


def _outbox(status, dispatch_at, template_key="submitted", **kwargs):
    return EmailOutbox(
        template_key=template_key,
        recipient_email="queue@test.local",
        dispatch_at=dispatch_at,
        status=status,
        **kwargs,
    )


def test_future_rows_do_not_count_as_backlog(app):
    """A row parked 30 days out is scheduled work, not backlog."""
    with app.app_context():
        now = datetime.utcnow()
        db.session.add(_outbox(OUTBOX_STATUS_QUEUED, now + timedelta(days=30)))
        db.session.commit()

        health = get_queue_health(now=now)
        assert health["oldest_due_minutes"] == 0
        assert health["scheduled_count"] == 1


def test_oldest_due_minutes_reports_the_overdue_row(app):
    """A row parked in the future must not displace the overdue row."""
    with app.app_context():
        now = datetime.utcnow()
        db.session.add_all([
            _outbox(OUTBOX_STATUS_QUEUED, now - timedelta(minutes=90)),
            _outbox(OUTBOX_STATUS_QUEUED, now + timedelta(days=30)),
        ])
        db.session.commit()

        health = get_queue_health(now=now)
        assert health["oldest_due_minutes"] >= 89


def test_render_blocked_templates_are_named(app):
    with app.app_context():
        now = datetime.utcnow()
        db.session.add(_outbox(
            OUTBOX_STATUS_RENDER_BLOCKED,
            now - timedelta(minutes=5),
            template_key="supply_submitted",
            blocked_since=now - timedelta(minutes=5),
        ))
        db.session.commit()

        health = get_queue_health(now=now)
        assert health["render_blocked_count"] == 1
        assert "supply_submitted" in health["render_blocked_templates"]


def test_live_and_finished_rows_are_counted_separately(app):
    """One combined count reads as backlog. A finished row lingers up to 90
    days before the prune, so SENT and SUPPRESSED sitting beside QUEUED under
    one heading says the queue is deep when it is empty. Statuses with no rows
    are ABSENT from either dict, not zero."""
    with app.app_context():
        now = datetime.utcnow()
        db.session.add_all([
            _outbox(OUTBOX_STATUS_QUEUED, now - timedelta(minutes=1)),
            _outbox(OUTBOX_STATUS_QUEUED, now - timedelta(minutes=2)),
            _outbox(OUTBOX_STATUS_SENT, now - timedelta(minutes=3)),
            _outbox(OUTBOX_STATUS_RENDER_BLOCKED, now - timedelta(minutes=4)),
        ])
        db.session.commit()

        health = get_queue_health(now=now)
        live, done = health["live_by_status"], health["outcome_by_status"]

        assert live[OUTBOX_STATUS_QUEUED] == 2
        assert live[OUTBOX_STATUS_RENDER_BLOCKED] == 1
        assert OUTBOX_STATUS_SENT not in live

        assert done[OUTBOX_STATUS_SENT] == 1
        assert OUTBOX_STATUS_QUEUED not in done
        assert OUTBOX_STATUS_FAILED not in done


def test_pending_messages_lists_only_unfinished_rows(app):
    """A queued row has no Notification Log entry, so the page reads the
    outbox directly. Subject is absent because nothing has rendered yet."""
    with app.app_context():
        now = datetime.utcnow()
        db.session.add_all([
            _outbox(OUTBOX_STATUS_QUEUED, now - timedelta(minutes=1)),
            _outbox(OUTBOX_STATUS_SENT, now - timedelta(minutes=2)),
            _outbox(OUTBOX_STATUS_SUPPRESSED, now - timedelta(minutes=3)),
        ])
        db.session.commit()

        pending = pending_messages()
        assert len(pending) == 1
        assert pending[0]["status"] == OUTBOX_STATUS_QUEUED
        assert "subject" not in pending[0]


def test_lookup_by_recipient_is_case_insensitive(app):
    with app.app_context():
        db.session.add(NotificationLog(
            template_key="submitted",
            recipient_email="A@Example.ORG",
            status="SENT",
        ))
        db.session.commit()

        results = lookup_messages(recipient_email="a@example.org")
        assert len(results) == 1
        assert results[0]["recipient_email"] == "A@Example.ORG"


def test_lookup_by_public_id_finds_the_work_items_messages(app, seed_workflow_data):
    with app.app_context():
        portfolio_id = seed_workflow_data["portfolio"].id
        admin_id = seed_workflow_data["admin"].id
        wanted = WorkItem(
            portfolio_id=portfolio_id, public_id="TST2026-TESTDEPT-BUD-1",
            request_kind=REQUEST_KIND_PRIMARY, status=WORK_ITEM_STATUS_DRAFT,
            created_by_user_id=admin_id,
        )
        other = WorkItem(
            portfolio_id=portfolio_id, public_id="TST2026-TESTDEPT-BUD-2",
            request_kind=REQUEST_KIND_PRIMARY, status=WORK_ITEM_STATUS_DRAFT,
            created_by_user_id=admin_id,
        )
        db.session.add_all([wanted, other])
        db.session.flush()

        db.session.add_all([
            NotificationLog(
                template_key="submitted", recipient_email="one@test.local",
                status="SENT", work_item_id=wanted.id,
            ),
            NotificationLog(
                template_key="submitted", recipient_email="two@test.local",
                status="SENT", work_item_id=other.id,
            ),
        ])
        db.session.commit()

        results = lookup_messages(public_id="TST2026-TESTDEPT-BUD-1")
        assert [r["recipient_email"] for r in results] == ["one@test.local"]


def test_lookup_reports_whether_a_body_was_stored(app):
    with app.app_context():
        with_body = NotificationLog(
            template_key="finalized", recipient_email="body@test.local", status="SENT",
        )
        without_body = NotificationLog(
            template_key="finalized", recipient_email="nobody@test.local", status="SENT",
        )
        db.session.add_all([with_body, without_body])
        db.session.flush()
        db.session.add(EmailMessageBody(
            notification_log_id=with_body.id, subject="s",
            body_text="t", body_html="<p>t</p>",
        ))
        db.session.commit()

        assert lookup_messages(recipient_email="body@test.local")[0]["has_body"] is True
        assert lookup_messages(recipient_email="nobody@test.local")[0]["has_body"] is False


def test_finished_rows_do_not_count_as_backlog(app):
    """A SENT row keeps the dispatch_at it was sent on and lingers 90 days.

    Counting terminal rows as backlog is the same alarm failure as counting
    scheduled ones, reached from the other side: every successful send would
    age into the backlog number and hold it near 90 days forever, on a queue
    that is in fact empty.
    """
    with app.app_context():
        now = datetime.utcnow()
        for status in (OUTBOX_STATUS_SENT, OUTBOX_STATUS_FAILED):
            db.session.add(_outbox(status, now - timedelta(days=45)))
        db.session.commit()

        health = get_queue_health(now=now)
        assert health["oldest_due_minutes"] == 0


def test_backlog_ignores_finished_rows_older_than_the_live_one(app):
    """The live row's age is the answer even when a finished row is older."""
    with app.app_context():
        now = datetime.utcnow()
        db.session.add(_outbox(OUTBOX_STATUS_SENT, now - timedelta(days=45)))
        db.session.add(_outbox(OUTBOX_STATUS_QUEUED, now - timedelta(minutes=30)))
        db.session.commit()

        health = get_queue_health(now=now)
        assert 29 <= health["oldest_due_minutes"] <= 31
