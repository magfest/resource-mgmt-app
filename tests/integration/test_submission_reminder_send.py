"""Tests for send_submission_reminders and the CLI command that drives it.

The orchestrator queues outbox rows; it renders nothing and sends nothing.
Covers the dry-run summary, one row per recipient, per-recipient failure
isolation, the commit that keeps the rows alive past CLI exit, and the
command's own exit codes and output.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app import db
from app.models import (
    Department,
    EventCycle,
    EmailOutbox,
    EmailTemplate,
    WorkType,
    WorkTypeConfig,
    User,
    DepartmentMembership,
    ROUTING_STRATEGY_DIRECT,
)
from app.models.constants import OUTBOX_STATUS_QUEUED
from app.services import notifications
from app.services.notifications import send_submission_reminders


@pytest.fixture
def seeded(app):
    """
    Seed an event cycle, BUDGET work type, two departments with two members
    each, and the submission_reminder template row.

    Neither department has a portfolio, so both will be audience targets. The
    template row is seeded for the CLI's preflight; the orchestrator itself
    never reads it, because rendering moved to the drainer.
    """
    cycle = EventCycle(
        code="REM2026", name="Reminder Test Event",
        is_active=True, is_default=True, sort_order=1,
    )
    db.session.add(cycle)

    wt = WorkType(code="BUDGET", name="Budget", is_active=True)
    db.session.add(wt)
    db.session.flush()
    wtc = WorkTypeConfig(
        work_type_id=wt.id, url_slug="budget",
        public_id_prefix="BUD", line_detail_type="budget",
        routing_strategy=ROUTING_STRATEGY_DIRECT,
        uses_dispatch=True, has_admin_final=True,
    )
    db.session.add(wtc)

    dept_a = Department(code="AAA", name="Dept A", is_active=True)
    dept_b = Department(code="BBB", name="Dept B", is_active=True)
    db.session.add_all([dept_a, dept_b])
    db.session.flush()

    users = []
    for suffix, dept in [
        ("a1", dept_a), ("a2", dept_a),
        ("b1", dept_b), ("b2", dept_b),
    ]:
        u = User(
            id=f"test:{suffix}",
            email=f"{suffix}@test.local",
            display_name=suffix.upper(),
            is_active=True,
        )
        db.session.add(u)
        db.session.flush()
        db.session.add(DepartmentMembership(
            user_id=u.id,
            department_id=dept.id,
            event_cycle_id=cycle.id,
        ))
        users.append(u)

    # Seed the email template (the conftest's db.create_all skips data migrations).
    db.session.add(EmailTemplate(
        template_key='submission_reminder',
        name='Budget Submission Reminder',
        description='test',
        subject='[MAGFest Budget] Reminder: {{ event_cycle.name }} budget due Sunday May 24',
        body_text=(
            "Your department hasn't submitted its {{ event_cycle.name }} budget yet.\n"
            "Department: {{ department.name }} ({{ department.code }})\n"
            "Open: {{ base_url }}/work/{{ event_cycle.code }}/{{ department.code }}/budget/\n"
        ),
        is_active=True,
        version=1,
    ))

    db.session.commit()
    return {
        "cycle": cycle, "dept_a": dept_a, "dept_b": dept_b,
        "users": users,
    }


def test_dry_run_queues_nothing_but_reports_targets(seeded):
    summary = send_submission_reminders(seeded["cycle"], dry_run=True)

    db.session.rollback()
    assert db.session.query(EmailOutbox).count() == 0
    assert summary.dry_run is True
    assert summary.targets_total == 2
    assert summary.targets_with_recipients == 2
    assert summary.rows_queued == 0
    assert summary.recipients_total == 0


def test_live_run_queues_one_row_per_recipient(seeded):
    with patch("app.services.email.send_via_ses") as ses:
        summary = send_submission_reminders(seeded["cycle"], dry_run=False)

    assert ses.call_count == 0, "the orchestrator must queue, never send"
    # Two depts * two recipients each = 4 rows.
    assert summary.rows_queued == 4
    assert summary.recipients_total == 4
    assert summary.dry_run is False

    db.session.rollback()
    rows = db.session.query(EmailOutbox).all()
    assert len(rows) == 4
    assert {r.status for r in rows} == {OUTBOX_STATUS_QUEUED}
    assert {r.template_key for r in rows} == {"submission_reminder"}
    assert {r.recipient_email for r in rows} == {
        "a1@test.local", "a2@test.local",
        "b1@test.local", "b2@test.local",
    }
    assert {r.event_cycle_id for r in rows} == {seeded["cycle"].id}
    assert {r.department_id for r in rows} == {
        seeded["dept_a"].id, seeded["dept_b"].id,
    }
    # A reminder belongs to a department, not to any one request.
    assert {r.work_item_id for r in rows} == {None}


def test_enqueue_exception_is_contained(seeded):
    """One recipient's failed INSERT costs that recipient only."""
    real_enqueue = notifications.enqueue_email

    def fail_one(*args, **kwargs):
        if args[1] == "a1@test.local":
            raise RuntimeError("simulated INSERT failure for one recipient")
        return real_enqueue(*args, **kwargs)

    with patch("app.services.notifications.enqueue_email", side_effect=fail_one):
        summary = send_submission_reminders(seeded["cycle"], dry_run=False)

    assert summary.recipients_total == 4
    assert summary.rows_queued == 3

    db.session.rollback()
    queued = {r.recipient_email for r in db.session.query(EmailOutbox).all()}
    assert queued == {"a2@test.local", "b1@test.local", "b2@test.local"}


def test_live_run_commits_the_rows(seeded):
    """The orchestrator must commit. CLI commands get no implicit commit at
    request teardown the way HTTP routes do; without it the queued rows are
    discarded on process exit and nothing is ever sent.
    """
    send_submission_reminders(seeded["cycle"], dry_run=False)

    # Anything still uncommitted disappears here, which is exactly what
    # happens when a CLI process exits.
    db.session.rollback()

    assert db.session.query(EmailOutbox).count() == 4


# ============================================================
# CLI: flask send-submission-reminders
# ============================================================
#
# This runs unattended under Heroku Scheduler. An untested branch here fails
# at 03:00 with nobody reading the output, so the command gets its own
# coverage rather than relying on the orchestrator tests above.


def test_cli_dry_run_queues_nothing(app, seeded):
    result = app.test_cli_runner().invoke(
        args=["send-submission-reminders", "REM2026"]
    )

    assert result.exit_code == 0
    assert "DRY RUN" in result.output
    assert "Would queue: 4 emails" in result.output
    db.session.rollback()
    assert db.session.query(EmailOutbox).count() == 0


def test_cli_send_queues_a_row_per_recipient(app, seeded):
    with patch("app.services.email.send_via_ses") as ses:
        result = app.test_cli_runner().invoke(
            args=["send-submission-reminders", "REM2026", "--send"]
        )

    assert result.exit_code == 0
    assert ses.call_count == 0
    assert "Queued: 4 / 4 rows" in result.output
    db.session.rollback()
    assert db.session.query(EmailOutbox).count() == 4


def test_cli_second_run_same_day_queues_nothing_and_still_exits_zero(app, seeded):
    """The removed exit code 3 lived here.

    The dedup key is scoped to the calendar day, so the Scheduler's second run
    legitimately queues nothing. Under the old contract that was a partial-send
    failure and exited 3, which would have reported a healthy run as broken.
    """
    runner = app.test_cli_runner()
    runner.invoke(args=["send-submission-reminders", "REM2026", "--send"])

    result = runner.invoke(args=["send-submission-reminders", "REM2026", "--send"])

    assert result.exit_code == 0
    assert "Queued: 0 / 4 rows" in result.output
    assert "Already queued today: 4" in result.output
    db.session.rollback()
    assert db.session.query(EmailOutbox).count() == 4


def test_cli_missing_template_exits_2(app, seeded):
    """The preflight refuses before queueing anything.

    An operator can silence reminders by deactivating the template row; the
    command must exit loudly rather than queue rows the drainer cannot render.
    """
    template = EmailTemplate.query.filter_by(
        template_key='submission_reminder'
    ).one()
    template.is_active = False
    db.session.commit()

    result = app.test_cli_runner().invoke(
        args=["send-submission-reminders", "REM2026", "--send"]
    )

    assert result.exit_code == 2
    db.session.rollback()
    assert db.session.query(EmailOutbox).count() == 0
