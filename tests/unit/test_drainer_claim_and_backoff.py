"""Claim, reap, and the two separate retry ladders."""
from datetime import datetime, timedelta

from sqlalchemy.dialects import postgresql

from app import db
from app.models import EmailOutbox
from app.models.constants import (
    OUTBOX_STATUS_QUEUED, OUTBOX_STATUS_RENDER_BLOCKED, OUTBOX_STATUS_SENDING,
)
from app.services.email_drainer import (
    _due_rows_subquery, claim_due_rows, reap_stale_claims, transport_backoff,
)


def _row(**kw):
    base = dict(template_key="submitted", recipient_email="a@example.org",
                dispatch_at=datetime.utcnow() - timedelta(minutes=1),
                status=OUTBOX_STATUS_QUEUED, created_at=datetime.utcnow())
    base.update(kw)
    return EmailOutbox(**base)


def test_claim_ignores_future_rows(app):
    with app.app_context():
        db.session.add(_row())
        db.session.add(_row(dispatch_at=datetime.utcnow() + timedelta(days=30)))
        db.session.commit()
        claimed = claim_due_rows("run-1", batch_size=10)
        assert len(claimed) == 1


def test_claim_includes_render_blocked_rows(app):
    """A blocked row is due work waiting on a person, not a dead row."""
    with app.app_context():
        db.session.add(_row(status=OUTBOX_STATUS_RENDER_BLOCKED,
                            blocked_since=datetime.utcnow() - timedelta(hours=2)))
        db.session.commit()
        assert len(claim_due_rows("run-2", batch_size=10)) == 1


def test_reaper_does_not_burn_a_transport_attempt_on_a_blocked_row(app):
    """A crash during a render retry must not consume a delivery attempt."""
    with app.app_context():
        stale = datetime.utcnow() - timedelta(minutes=45)
        db.session.add(_row(status=OUTBOX_STATUS_SENDING, claimed_at=stale,
                            blocked_since=stale, attempt_count=0))
        db.session.commit()
        reap_stale_claims()
        row = db.session.query(EmailOutbox).one()
        assert row.status == OUTBOX_STATUS_RENDER_BLOCKED
        assert row.attempt_count == 0


def test_transport_backoff_doubles():
    assert transport_backoff(1) == timedelta(minutes=20)
    assert transport_backoff(4) == timedelta(minutes=160)


def test_claim_subquery_compiles_with_skip_locked_on_postgres(app):
    """FOR UPDATE SKIP LOCKED is what stops two overlapping runs (Heroku
    Scheduler jitter) from both claiming the same row. It compiles away
    silently on SQLite, so nothing in the SQLite-run suite would catch its
    removal; this pins the Postgres-compiled SQL instead."""
    with app.app_context():
        subq = _due_rows_subquery(datetime.utcnow(), batch_size=10)
        compiled = str(subq.compile(dialect=postgresql.dialect()))
        assert "FOR UPDATE SKIP LOCKED" in compiled
