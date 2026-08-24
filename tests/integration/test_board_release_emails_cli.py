"""The scheduled command owns finalized-email delivery.

The command queues outbox rows and exits; it never calls SES. No request does
this work, because a bulk board release fans out one row per department member
plus a Slack webhook per item, which does not fit inside Heroku's 30-second
request limit.
"""
from datetime import datetime
from unittest.mock import patch

import pytest

from app import db
from app.models import (
    User,
    DepartmentMembership,
    EmailOutbox,
    EmailTemplate,
    NotificationLog,
    WorkItem,
)
from app.models.constants import (
    OUTBOX_STATUS_QUEUED,
    WORK_ITEM_STATUS_FINALIZED,
)
from app.services import notifications


@pytest.fixture
def seed_finalized_template(app):
    """Seed the 'finalized' EmailTemplate row.

    db.create_all() builds tables from the ORM but skips Alembic data
    migrations (see k1l2m3n4o5p6_add_email_templates_table.py), so any test
    that reaches the CLI's template preflight must seed this row itself.
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


def _release(data, at=datetime(2026, 8, 5, 10, 0)):
    """Put the seeded work item in the state the command selects on."""
    item = data["work_item"]
    item.status = WORK_ITEM_STATUS_FINALIZED
    item.board_released_at = at
    db.session.commit()
    return item


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
    """Add a second released-but-unnotified WorkItem to the same portfolio."""
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


def test_dry_run_queues_nothing(app, seed_draft_work_item, seed_finalized_template):
    data = seed_draft_work_item
    item = _release(data)
    _add_department_member(data, "a")

    result = app.test_cli_runner().invoke(args=["send-board-release-emails"])

    assert result.exit_code == 0
    db.session.rollback()
    assert db.session.query(EmailOutbox).count() == 0
    db.session.refresh(item)
    assert item.finalized_notified_at is None
    assert item.public_id in result.output


def test_send_queues_one_row_per_member_and_stamps(
    app, seed_draft_work_item, seed_finalized_template,
):
    data = seed_draft_work_item
    item = _release(data)
    _add_department_member(data, "a")
    _add_department_member(data, "b")

    with patch("app.services.email.send_via_ses") as ses:
        result = app.test_cli_runner().invoke(args=["send-board-release-emails", "--send"])

    assert result.exit_code == 0
    assert ses.call_count == 0, "the command must queue, never send"

    db.session.rollback()
    rows = db.session.query(EmailOutbox).all()
    assert len(rows) == 2
    assert {r.status for r in rows} == {OUTBOX_STATUS_QUEUED}
    assert {r.template_key for r in rows} == {"finalized"}
    assert {r.recipient_email for r in rows} == {
        "membera@test.local", "memberb@test.local",
    }
    db.session.refresh(item)
    assert item.finalized_notified_at is not None


def test_second_run_queues_nothing_more(
    app, seed_draft_work_item, seed_finalized_template,
):
    """Idempotency. The stamp is what stops a second run, so this fails loudly
    if the stamp moves back behind a delivery condition."""
    data = seed_draft_work_item
    item = _release(data)
    _add_department_member(data, "a")

    app.test_cli_runner().invoke(args=["send-board-release-emails", "--send"])
    db.session.rollback()
    first_count = db.session.query(EmailOutbox).count()
    db.session.refresh(item)
    first_stamp = item.finalized_notified_at

    result2 = app.test_cli_runner().invoke(args=["send-board-release-emails", "--send"])

    assert result2.exit_code == 0
    assert "No released budgets awaiting notification." in result2.output
    db.session.rollback()
    assert db.session.query(EmailOutbox).count() == first_count == 1
    db.session.refresh(item)
    assert item.finalized_notified_at == first_stamp


def test_department_without_members_is_stamped(
    app, seed_draft_work_item, seed_finalized_template,
):
    """A department with no members queues nothing and is still stamped.

    Departments without members are expected rather than exceptional. Leaving
    them unstamped re-lists them every run and fills the pending table with
    rows nobody can act on.
    """
    data = seed_draft_work_item
    item = _release(data)

    result = app.test_cli_runner().invoke(args=["send-board-release-emails", "--send"])

    assert result.exit_code == 0
    db.session.rollback()
    assert db.session.query(EmailOutbox).count() == 0
    db.session.refresh(item)
    assert item.finalized_notified_at is not None

    result2 = app.test_cli_runner().invoke(args=["send-board-release-emails"])
    assert item.public_id not in result2.output


def test_unreleased_budgets_are_skipped(app, seed_draft_work_item, seed_finalized_template):
    """A finalized budget still waiting on the board must not be emailed."""
    data = seed_draft_work_item
    item = data["work_item"]
    item.status = WORK_ITEM_STATUS_FINALIZED
    item.board_released_at = None
    _add_department_member(data, "a")

    result = app.test_cli_runner().invoke(args=["send-board-release-emails", "--send"])

    assert result.exit_code == 0
    db.session.rollback()
    assert db.session.query(EmailOutbox).count() == 0


def test_missing_template_exits_2(app, seed_draft_work_item):
    """No 'finalized' EmailTemplate row at all: refuse before touching any item."""
    data = seed_draft_work_item
    item = _release(data)

    result = app.test_cli_runner().invoke(args=["send-board-release-emails", "--send"])

    assert result.exit_code == 2
    assert "finalized" in result.output
    db.session.refresh(item)
    assert item.finalized_notified_at is None


def test_inactive_template_exits_2(app, seed_draft_work_item, seed_finalized_template):
    data = seed_draft_work_item
    item = _release(data)
    template = EmailTemplate.query.filter_by(template_key='finalized').first()
    template.is_active = False
    db.session.commit()

    result = app.test_cli_runner().invoke(args=["send-board-release-emails", "--send"])

    assert result.exit_code == 2
    db.session.refresh(item)
    assert item.finalized_notified_at is None


def test_slack_announcement_fires_after_the_stamp(
    app, seed_draft_work_item, seed_finalized_template,
):
    """The Slack post moved out of notify_work_item_finalized in the outbox
    rebuild. Nothing else fails if this call goes missing, so this test is the
    only thing standing between a dropped line and a silently quiet channel.

    Ordering is asserted by recording observed events, not by probing session
    state at announce time. A state probe was tried first and cannot work
    here: the formatter's lazy loads autoflush the pending stamp, and on
    SQLite's shared in-memory connection a flushed row reads back exactly like
    a committed one. The probe passed against a deliberately mis-ordered
    build. This version fails against it.

    The first recorded event must be the commit, and the stamp must be durable
    once that commit returns, which is what ties the ordering to THIS item's
    commit rather than to any earlier one.
    """
    data = seed_draft_work_item
    item = _release(data)
    _add_department_member(data, "a")
    item_id = item.id

    events = []
    real_commit = db.session.commit

    def _spy_commit():
        real_commit()
        stamp = db.session.query(WorkItem.finalized_notified_at).filter(
            WorkItem.id == item_id
        ).scalar()
        events.append(("commit", stamp))

    def _probe(**kwargs):
        events.append(("announce", None))

    with patch.object(db.session, "commit", side_effect=_spy_commit), \
            patch("app.services.notifications.is_slack_enabled", return_value=True), \
            patch(
                "app.services.notifications.send_slack_message",
                side_effect=_probe,
            ) as slack:
        result = app.test_cli_runner().invoke(args=["send-board-release-emails", "--send"])

    assert result.exit_code == 0
    assert slack.call_count == 1
    assert slack.call_args.kwargs["template_key"] == "finalized"
    assert slack.call_args.kwargs["work_item_id"] == item.id

    assert [e[0] for e in events[:2]] == ["commit", "announce"], (
        f"the announcement must follow the item's commit; observed {events}"
    )
    assert events[0][1] is not None, "the first commit must have persisted the stamp"


def test_announcement_failure_does_not_abort_the_remaining_items(
    app, seed_draft_work_item, seed_finalized_template,
):
    """The announce sits outside the per-item try, so it must not raise.

    A formatter that blew up on the first item would otherwise skip every
    remaining item in `pending` for that run. Those items stay unstamped, so
    the next run picks them up, but the failure is silent until someone reads
    the Scheduler log.
    """
    data = seed_draft_work_item
    item1 = _release(data)
    item2 = _add_second_finalized_item(data, "2")
    _add_department_member(data, "a")

    def _boom(_):
        raise RuntimeError("formatter reached a detached relation")

    with patch("app.services.notifications.is_slack_enabled", return_value=True), \
            patch.dict(
                notifications._ANNOUNCEMENT_FORMATTERS,
                {"finalized": _boom},
            ):
        result = app.test_cli_runner().invoke(args=["send-board-release-emails", "--send"])

    assert result.exit_code == 0
    db.session.rollback()
    db.session.refresh(item1)
    db.session.refresh(item2)
    assert item1.finalized_notified_at is not None
    assert item2.finalized_notified_at is not None


def test_later_item_failure_does_not_discard_earlier_item_stamp(
    app, seed_draft_work_item, seed_finalized_template,
):
    """A single commit after the loop would let one poisoned item roll back
    every stamp already earned by earlier items.

    The second item's notify writes a row that violates a NOT NULL constraint
    and flushes it directly. That is a genuine DB-level failure, which is what
    actually poisons a SQLAlchemy session; a bare `raise` inside a mock never
    touches the DB and would pass with or without the per-item commit.
    """
    data = seed_draft_work_item
    item1 = _release(data)
    item2 = _add_second_finalized_item(data, "2")
    item1_id = item1.id

    def _notify_side_effect(work_item):
        if work_item.id == item1_id:
            return 0
        # recipient_email is NOT NULL; this flush fails at the DB level and
        # leaves the session needing an explicit rollback before further use.
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

    assert result.exit_code == 1
    db.session.rollback()
    db.session.refresh(item1)
    db.session.refresh(item2)
    assert item1.finalized_notified_at is not None
    assert item2.finalized_notified_at is None
