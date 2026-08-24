"""Two events, same department, same day. Both must produce rows.

Department rows are global. Without event_cycle_id in the dedup key the
second event's rows collide with the first event's and are silently dropped.
"""
from app import db
from app.models import DepartmentMembership, EmailOutbox, User
from app.services.notifications import send_submission_reminders


def _add_member(seed_workflow_data, cycles):
    """Give the seeded department one member in each of `cycles`."""
    user = User(
        id="test:dept-member", email="dept-member@test.local",
        display_name="Dept Member", is_active=True,
    )
    db.session.add(user)
    db.session.flush()
    for cycle in cycles:
        db.session.add(DepartmentMembership(
            user_id=user.id,
            department_id=seed_workflow_data["department"].id,
            event_cycle_id=cycle.id,
        ))
    db.session.commit()
    return user


def test_two_event_cycles_same_day_both_enqueue(app, seed_workflow_data):
    first = seed_workflow_data["cycle"]
    second = seed_workflow_data["second_event_cycle"]
    _add_member(seed_workflow_data, [first, second])

    send_submission_reminders(first, dry_run=False)
    send_submission_reminders(second, dry_run=False)

    db.session.rollback()  # drop the read snapshot; the runs committed already
    rows = db.session.query(EmailOutbox).all()
    assert len(rows) == 2
    assert {r.event_cycle_id for r in rows} == {first.id, second.id}


def test_same_event_twice_in_one_day_queues_once(app, seed_workflow_data):
    """The other half of the same key: a same-day re-run must not double-send."""
    first = seed_workflow_data["cycle"]
    _add_member(seed_workflow_data, [first])

    run_one = send_submission_reminders(first, dry_run=False)
    run_two = send_submission_reminders(first, dry_run=False)

    assert run_one.rows_queued == 1
    assert run_two.rows_queued == 0
    assert run_two.recipients_total == 1

    db.session.rollback()
    assert db.session.query(EmailOutbox).count() == 1
