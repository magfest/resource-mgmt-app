"""The scheduled command owns finalized-email delivery.

No request sends these. A bulk board release fans out one SES call per
department, which does not fit inside Heroku's 30-second request limit.
"""
from datetime import datetime
from unittest.mock import patch

import pytest

from app import db
from app.models import (
    User,
    DepartmentMembership,
    EmailTemplate,
    NotificationLog,
    WorkItem,
    WORK_ITEM_STATUS_FINALIZED,
)
from app.services.notifications import FinalizedNotifyResult


@pytest.fixture
def seed_finalized_template(app):
    """Seed the 'finalized' EmailTemplate row.

    db.create_all() builds tables from the ORM but skips Alembic data
    migrations (see k1l2m3n4o5p6_add_email_templates_table.py), so any test
    that reaches the CLI's template preflight or a real send must seed this
    row itself.
    """
    db.session.add(EmailTemplate(
        template_key='finalized',
        name='Budget Finalized',
        description='test seed',
        subject='[MAGFest Budget] Finalized - {{ work_item.public_id }}',
        body_text='Your budget request has been finalized.\n',
        is_active=True,
        version=1,
    ))
    db.session.commit()


def _add_department_member(data, suffix):
    """Add one DepartmentMembership recipient to seed_workflow_data's department."""
    user = User(
        id=f"test:member-{suffix}", email=f"member{suffix}@test.local",
        display_name=f"Member {suffix}", is_active=True,
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


def _add_second_finalized_item(data, public_id_suffix):
    """Add a second released-but-unnotified WorkItem to the same portfolio.

    Used for tests where two items must go through the CLI's per-item loop
    (e.g. one item's failure must not affect a prior item's stamp).
    """
    item = WorkItem(
        portfolio_id=data["portfolio"].id,
        status=WORK_ITEM_STATUS_FINALIZED,
        public_id=f"TST2026-TESTDEPT-BUD-{public_id_suffix}",
        created_by_user_id=data["admin"].id,
        board_released_at=datetime(2026, 8, 5, 10, 0),
    )
    db.session.add(item)
    db.session.commit()
    return item


def test_dry_run_sends_nothing(app, seed_draft_work_item, seed_finalized_template):
    data = seed_draft_work_item
    item = data["work_item"]
    item.status = WORK_ITEM_STATUS_FINALIZED
    item.board_released_at = datetime(2026, 8, 5, 10, 0)
    db.session.commit()

    with patch("app.services.notifications.notify_work_item_finalized") as mock:
        result = app.test_cli_runner().invoke(args=["send-board-release-emails"])

    assert result.exit_code == 0
    assert mock.call_count == 0
    db.session.refresh(item)
    assert item.finalized_notified_at is None
    assert item.public_id in result.output


def test_send_notifies_and_stamps(app, seed_draft_work_item, seed_finalized_template):
    data = seed_draft_work_item
    item = data["work_item"]
    item.status = WORK_ITEM_STATUS_FINALIZED
    item.board_released_at = datetime(2026, 8, 5, 10, 0)
    db.session.commit()

    with patch(
        "app.services.notifications.notify_work_item_finalized",
        return_value=FinalizedNotifyResult(sent=1, attempted=1),
    ) as mock:
        result = app.test_cli_runner().invoke(args=["send-board-release-emails", "--send"])

    assert result.exit_code == 0
    assert mock.call_count == 1
    db.session.refresh(item)
    assert item.finalized_notified_at is not None


def test_unreleased_budgets_are_skipped(app, seed_draft_work_item, seed_finalized_template):
    """A finalized budget still waiting on the board must not be emailed."""
    data = seed_draft_work_item
    item = data["work_item"]
    item.status = WORK_ITEM_STATUS_FINALIZED
    item.board_released_at = None
    db.session.commit()

    with patch("app.services.notifications.notify_work_item_finalized") as mock:
        result = app.test_cli_runner().invoke(args=["send-board-release-emails", "--send"])

    assert result.exit_code == 0
    assert mock.call_count == 0


def test_missing_template_exits_2(app, seed_draft_work_item):
    """No 'finalized' EmailTemplate row at all: refuse before touching any item."""
    data = seed_draft_work_item
    item = data["work_item"]
    item.status = WORK_ITEM_STATUS_FINALIZED
    item.board_released_at = datetime(2026, 8, 5, 10, 0)
    db.session.commit()

    result = app.test_cli_runner().invoke(args=["send-board-release-emails", "--send"])

    assert result.exit_code == 2
    assert "finalized" in result.output
    db.session.refresh(item)
    assert item.finalized_notified_at is None


def test_inactive_template_exits_2(app, seed_draft_work_item, seed_finalized_template):
    data = seed_draft_work_item
    item = data["work_item"]
    item.status = WORK_ITEM_STATUS_FINALIZED
    item.board_released_at = datetime(2026, 8, 5, 10, 0)
    template = EmailTemplate.query.filter_by(template_key='finalized').first()
    template.is_active = False
    db.session.commit()

    result = app.test_cli_runner().invoke(args=["send-board-release-emails", "--send"])

    assert result.exit_code == 2
    db.session.refresh(item)
    assert item.finalized_notified_at is None


def test_zero_send_leaves_item_unstamped_and_retries(app, seed_draft_work_item, seed_finalized_template):
    """CRITICAL regression case: a department with no members sends 0 emails
    without raising. The CLI must not stamp finalized_notified_at, must
    exit 3, and must still pick the item up on a second run.

    notify_work_item_finalized is deliberately NOT patched here — that's
    what let the original bug (Finding 1) hide behind three green tests
    that all mocked it away.
    """
    data = seed_draft_work_item
    item = data["work_item"]
    item.status = WORK_ITEM_STATUS_FINALIZED
    item.board_released_at = datetime(2026, 8, 5, 10, 0)
    db.session.commit()
    # No DepartmentMembership rows exist for this department/event, so the
    # real notify_work_item_finalized() call returns sent=0, attempted=0.

    result = app.test_cli_runner().invoke(args=["send-board-release-emails", "--send"])

    assert result.exit_code == 3
    db.session.refresh(item)
    assert item.finalized_notified_at is None

    # A second scheduled run must still see the item as pending.
    result2 = app.test_cli_runner().invoke(args=["send-board-release-emails"])
    assert item.public_id in result2.output


def test_partial_send_leaves_item_unstamped(app, seed_draft_work_item, seed_finalized_template):
    """Two recipients, one rate-limited: sent < attempted must not stamp.

    This is the Finding 1 tradeoff: stamping on any(sent > 0) would let the
    rate-limited member never get retried. Runs the real send_email() with
    EMAIL_HOURLY_LIMIT=1 so the first recipient's send genuinely counts
    against the limit and the second is genuinely rate-limited (not a
    scripted return value) — only the SES boundary (boto3) is mocked, so
    this can never reach AWS.
    """
    data = seed_draft_work_item
    item = data["work_item"]
    item.status = WORK_ITEM_STATUS_FINALIZED
    item.board_released_at = datetime(2026, 8, 5, 10, 0)
    _add_department_member(data, "a")
    _add_department_member(data, "b")
    db.session.commit()

    app.config["EMAIL_ENABLED"] = True
    app.config["EMAIL_HOURLY_LIMIT"] = 1

    with patch("app.services.email.boto3.client") as mock_boto:
        mock_boto.return_value.send_email.return_value = {"MessageId": "test-1"}
        result = app.test_cli_runner().invoke(args=["send-board-release-emails", "--send"])

    assert result.exit_code == 3
    db.session.refresh(item)
    assert item.finalized_notified_at is None
    statuses = sorted(
        log.status for log in NotificationLog.query.filter_by(work_item_id=item.id)
    )
    assert statuses == ["RATE_LIMITED", "SENT"]


def test_full_send_stamps_item(app, seed_draft_work_item, seed_finalized_template):
    """Baseline: every recipient sends successfully, so the item stamps and
    exits 0. Runs the real send_email() end to end; only the SES boundary
    (boto3) is mocked, so this can never reach AWS.
    """
    data = seed_draft_work_item
    item = data["work_item"]
    item.status = WORK_ITEM_STATUS_FINALIZED
    item.board_released_at = datetime(2026, 8, 5, 10, 0)
    _add_department_member(data, "a")
    _add_department_member(data, "b")
    db.session.commit()

    app.config["EMAIL_ENABLED"] = True

    with patch("app.services.email.boto3.client") as mock_boto:
        mock_boto.return_value.send_email.return_value = {"MessageId": "test-1"}
        result = app.test_cli_runner().invoke(args=["send-board-release-emails", "--send"])

    assert result.exit_code == 0
    db.session.refresh(item)
    assert item.finalized_notified_at is not None
    statuses = [
        log.status for log in NotificationLog.query.filter_by(work_item_id=item.id)
    ]
    assert statuses == ["SENT", "SENT"]


def test_all_recipients_rate_limited_leaves_item_unstamped(
    app, seed_draft_work_item, seed_finalized_template,
):
    """Defect 1 regression: send_email() returns True for a rate-limited
    call (email.py:243, "so callers don't retry immediately"), which is not
    delivery. EMAIL_HOURLY_LIMIT=0 rate-limits every recipient before any
    SES call, through the real send path (no send_email mock). The item
    must stay unstamped and the run must exit non-zero.
    """
    data = seed_draft_work_item
    item = data["work_item"]
    item.status = WORK_ITEM_STATUS_FINALIZED
    item.board_released_at = datetime(2026, 8, 5, 10, 0)
    _add_department_member(data, "a")
    _add_department_member(data, "b")
    db.session.commit()

    app.config["EMAIL_ENABLED"] = True
    app.config["EMAIL_HOURLY_LIMIT"] = 0

    result = app.test_cli_runner().invoke(args=["send-board-release-emails", "--send"])

    assert result.exit_code == 3
    db.session.refresh(item)
    assert item.finalized_notified_at is None
    statuses = [
        log.status for log in NotificationLog.query.filter_by(work_item_id=item.id)
    ]
    assert statuses == ["RATE_LIMITED", "RATE_LIMITED"]


def test_email_disabled_leaves_item_unstamped(
    app, seed_draft_work_item, seed_finalized_template,
):
    """Defect 1 regression: send_email() returns True when EMAIL_ENABLED is
    false (email.py:227, logged SUPPRESSED), which is not delivery. Under
    pytest EMAIL_ENABLED already defaults to false (app/__init__.py reads it
    from an unset env var), so this is the CLI's default test-time state,
    not an override. The item must stay unstamped and exit non-zero.
    """
    data = seed_draft_work_item
    item = data["work_item"]
    item.status = WORK_ITEM_STATUS_FINALIZED
    item.board_released_at = datetime(2026, 8, 5, 10, 0)
    _add_department_member(data, "a")
    _add_department_member(data, "b")
    db.session.commit()

    assert app.config["EMAIL_ENABLED"] is False

    result = app.test_cli_runner().invoke(args=["send-board-release-emails", "--send"])

    assert result.exit_code == 3
    db.session.refresh(item)
    assert item.finalized_notified_at is None
    statuses = [
        log.status for log in NotificationLog.query.filter_by(work_item_id=item.id)
    ]
    assert statuses == ["SUPPRESSED", "SUPPRESSED"]


def test_later_item_failure_does_not_discard_earlier_item_stamp(
    app, seed_draft_work_item, seed_finalized_template,
):
    """Defect 2 regression: a single commit after the loop lets one poisoned
    item roll back every stamp already earned by earlier items.

    The first item's notify call succeeds normally. The second's writes a
    row that violates a NOT NULL constraint and flushes it directly —
    a genuine DB-level failure, not a plain Python exception — which is
    what actually poisons a SQLAlchemy session (a bare `raise` inside a
    mock never touches the DB, so it can't reproduce the poisoning and
    would pass whether or not the fix is applied). The first item's stamp
    must survive the second item's failure.
    """
    data = seed_draft_work_item
    item1 = data["work_item"]
    item1.status = WORK_ITEM_STATUS_FINALIZED
    item1.board_released_at = datetime(2026, 8, 5, 10, 0)
    item2 = _add_second_finalized_item(data, "2")
    db.session.commit()
    item1_id = item1.id

    def _notify_side_effect(work_item):
        if work_item.id == item1_id:
            return FinalizedNotifyResult(sent=1, attempted=1)
        # recipient_email is NOT NULL; this flush fails at the DB level and
        # leaves the session needing an explicit rollback before further use,
        # exactly like a real constraint violation or connection error would.
        db.session.add(NotificationLog(
            recipient_email=None,
            template_key="finalized",
            work_item_id=work_item.id,
        ))
        db.session.flush()
        raise AssertionError("unreachable: the flush above must raise first")

    with patch(
        "app.services.notifications.notify_work_item_finalized",
        side_effect=_notify_side_effect,
    ):
        result = app.test_cli_runner().invoke(args=["send-board-release-emails", "--send"])

    assert result.exit_code == 3
    db.session.refresh(item1)
    db.session.refresh(item2)
    assert item1.finalized_notified_at is not None
    assert item2.finalized_notified_at is None
