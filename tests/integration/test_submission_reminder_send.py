"""
Tests for send_submission_reminders.

Covers:
- Dry-run sends no emails but returns a populated summary.
- Live-run calls send_email() once per (dept, recipient) with right args.
- send_email() exceptions are contained — run completes, exception
  counted as a miss.
- Live-run commits NotificationLog rows per-recipient (regression: CLI
  commands don't get the implicit request-end commit HTTP routes do).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app import db
from app.models import (
    Department,
    EventCycle,
    EmailTemplate,
    WorkType,
    WorkTypeConfig,
    User,
    DepartmentMembership,
    ROUTING_STRATEGY_DIRECT,
)
from app.services.notifications import send_submission_reminders


@pytest.fixture
def seeded(app):
    """
    Seed an event cycle, BUDGET work type, two departments with two members
    each, and the submission_reminder template row.

    Neither department has a portfolio, so both will be audience targets.
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


def test_dry_run_sends_no_emails_but_reports_targets(seeded):
    with patch("app.services.notifications.send_email") as mock_send:
        summary = send_submission_reminders(seeded["cycle"], dry_run=True)

    assert mock_send.call_count == 0, (
        f"Dry-run must not call send_email; got {mock_send.call_count} calls"
    )
    assert summary.dry_run is True
    assert summary.targets_total == 2
    assert summary.targets_with_recipients == 2
    assert summary.emails_sent == 0
    assert summary.emails_attempted == 0


def test_live_run_calls_send_email_per_recipient(seeded):
    with patch(
        "app.services.notifications.send_email",
        return_value=True,
    ) as mock_send:
        summary = send_submission_reminders(seeded["cycle"], dry_run=False)

    # Two depts * two recipients each = 4 calls.
    assert mock_send.call_count == 4
    assert summary.emails_sent == 4
    assert summary.emails_attempted == 4
    assert summary.dry_run is False

    # Spot-check the kwargs of the first call. All calls share subject/body
    # since render-per-department uses the same template, but recipient
    # differs per call.
    recipients_called = {c.kwargs["to"] for c in mock_send.call_args_list}
    assert recipients_called == {
        "a1@test.local", "a2@test.local",
        "b1@test.local", "b2@test.local",
    }
    # All calls use the right template_key.
    template_keys = {c.kwargs["template_key"] for c in mock_send.call_args_list}
    assert template_keys == {"submission_reminder"}
    # Subject should contain the event name.
    subjects = {c.kwargs["subject"] for c in mock_send.call_args_list}
    assert all("Reminder Test Event" in s for s in subjects)


def test_send_email_exception_is_contained(seeded):
    """
    If send_email raises for one recipient, the run continues for others
    and that one recipient is counted as a miss.
    """
    def fake_send(**kwargs):
        if kwargs["to"] == "a1@test.local":
            raise RuntimeError("simulated SES outage for one recipient")
        return True

    with patch(
        "app.services.notifications.send_email",
        side_effect=fake_send,
    ) as mock_send:
        summary = send_submission_reminders(seeded["cycle"], dry_run=False)

    # All 4 calls were attempted; 3 succeeded, 1 raised.
    assert mock_send.call_count == 4
    assert summary.emails_attempted == 4
    assert summary.emails_sent == 3


def test_live_run_commits_per_recipient(seeded):
    """
    Regression test: the orchestrator must commit per-recipient so the
    NotificationLog rows that send_email() adds actually persist. CLI
    commands don't get an implicit commit at request-end like HTTP routes
    do; without the explicit commit, the rows are discarded on process
    exit and the audit trail is lost.

    A deeper test that asserts NotificationLog rows are actually written
    is blocked by a pre-existing schema quirk (NotificationLog.id is a
    bare BigInteger PK without a SQLite Integer variant per
    feedback_sqlite_bigint_pk; SQLite won't autoincrement it). Spying on
    db.session.commit instead is enough to catch "someone removed the
    commit call" — the regression we're guarding against.
    """
    with patch("app.services.notifications.send_email", return_value=True):
        with patch.object(db.session, "commit") as mock_commit:
            summary = send_submission_reminders(seeded["cycle"], dry_run=False)

    # 2 departments * 2 recipients each = 4 recipients. The orchestrator
    # should commit exactly once per recipient (not end-of-run, so a
    # mid-run crash still leaves a clear audit trail).
    assert mock_commit.call_count == 4, (
        f"Expected commit per recipient (4 total). Got {mock_commit.call_count}. "
        f"If this drops to 0, the orchestrator lost its per-recipient commit — "
        f"NotificationLog rows would be discarded on CLI exit."
    )
    assert summary.emails_sent == 4