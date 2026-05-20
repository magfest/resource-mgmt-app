"""Tests for ScheduledNotification model."""
from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import (
    ScheduledNotification,
    SCHED_NOTIF_STATUS_QUEUED,
)


class TestScheduledNotificationModel:
    def test_round_trip_minimal_row(self, app):
        row = ScheduledNotification(
            template_key="deadline_reminder_t_0",
            recipient_email="test@example.com",
            dispatch_at=datetime.utcnow(),
        )
        db.session.add(row)
        db.session.commit()

        fetched = db.session.get(ScheduledNotification, row.id)
        assert fetched.status == SCHED_NOTIF_STATUS_QUEUED
        assert fetched.attempt_count == 0
        assert fetched.created_at is not None
        assert fetched.sent_at is None

    def test_dedup_key_unique_constraint(self, app):
        row1 = ScheduledNotification(
            template_key="deadline_reminder_t_0",
            recipient_email="a@example.com",
            dispatch_at=datetime.utcnow(),
            dedup_key="test:dedup:1",
        )
        db.session.add(row1)
        db.session.commit()

        row2 = ScheduledNotification(
            template_key="deadline_reminder_t_0",
            recipient_email="b@example.com",
            dispatch_at=datetime.utcnow(),
            dedup_key="test:dedup:1",
        )
        db.session.add(row2)
        with pytest.raises(IntegrityError):
            db.session.commit()
        db.session.rollback()

    def test_dedup_key_null_does_not_conflict(self, app):
        """Two rows with NULL dedup_key should both insert (NULLs are not equal)."""
        row1 = ScheduledNotification(
            template_key="deadline_reminder_t_0",
            recipient_email="a@example.com",
            dispatch_at=datetime.utcnow(),
            dedup_key=None,
        )
        row2 = ScheduledNotification(
            template_key="deadline_reminder_t_0",
            recipient_email="b@example.com",
            dispatch_at=datetime.utcnow(),
            dedup_key=None,
        )
        db.session.add_all([row1, row2])
        db.session.commit()
        assert row1.id != row2.id
