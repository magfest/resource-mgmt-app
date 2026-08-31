"""Concurrency guards in the release path."""
from datetime import datetime

from sqlalchemy import text

from app import db
from app.models import (
    DepartmentMembership,
    EventCycle,
    WorkLineReview,
    REVIEW_STAGE_APPROVAL_GROUP,
    REVIEW_STATUS_APPROVED,
    WORK_ITEM_STATUS_SUBMITTED,
    WORK_ITEM_STATUS_FINALIZED,
    WORK_LINE_STATUS_APPROVED,
)


def test_release_does_not_rewrite_a_latch_set_by_another_session(
    app, seed_workflow_data, super_admin_ctx
):
    """The FOR UPDATE must repopulate the instance, not only lock the row.

    Reproduces the losing side of two concurrent releases: the row carries a
    stamp the in-memory instance has never seen, so an unrefreshed instance
    reads NULL and overwrites the winner's timestamp.

    Do NOT commit between the UPDATE and the call. Flask-SQLAlchemy sessions
    use expire_on_commit, so a commit here would expire the instance, the next
    attribute read would reload it, and the staleness this test exists to catch
    would be masked. The uncommitted UPDATE is visible to this same
    transaction's later SELECT, which is exactly what the refresh must pick up.

    A second connection cannot be used: tests run on sqlite:///:memory: behind
    a SingletonThreadPool, so db.engine.connect() hands back the very same
    DBAPI connection and a commit on it would commit this session.
    """
    from app.routes.admin_final.helpers import release_event_budgets

    cycle = db.session.query(EventCycle).first()
    assert cycle.board_approved_at is None
    winner_stamp = datetime(2026, 1, 2, 3, 4, 5)

    db.session.execute(
        text("UPDATE event_cycles SET board_approved_at = :ts WHERE id = :i"),
        {"ts": winner_stamp, "i": cycle.id},
    )
    # No commit. The instance still reads the pre-update value.
    assert cycle.board_approved_at is None

    released_count, error = release_event_budgets(
        cycle, super_admin_ctx, note="Board approved FY27"
    )
    db.session.commit()

    # seed_workflow_data creates no WorkItem, so nothing is held; these
    # two only confirm the call completed normally. The timestamp
    # assertion below is what fails when the refresh fix reverts.
    assert error is None
    assert released_count == 0
    assert cycle.board_approved_at == winner_stamp


def test_finalize_refuses_a_row_another_session_already_finalized(
    app, seed_draft_work_item, super_admin_ctx
):
    """Same defect, same fix, at finalize_work_item's own lock.

    An unrefreshed instance would let a second finalize through even though
    the row already moved to FINALIZED underneath it.

    The setup commit is required to reach a finalizable state. The assertion
    right after it forces that commit's own expiry to resolve before the raw
    UPDATE runs, so the staleness demonstrated below comes from the UPDATE,
    not from the setup commit.
    """
    from app.routes.admin_final.helpers import finalize_work_item

    data = seed_draft_work_item
    item, line = data["work_item"], data["line"]
    item.status = WORK_ITEM_STATUS_SUBMITTED
    line.status = WORK_LINE_STATUS_APPROVED
    line.current_review_stage = REVIEW_STAGE_APPROVAL_GROUP
    db.session.add(WorkLineReview(
        work_line_id=line.id, stage=REVIEW_STAGE_APPROVAL_GROUP,
        approval_group_id=data["approval_group"].id,
        status=REVIEW_STATUS_APPROVED, approved_amount_cents=5000,
        created_by_user_id=data["admin"].id))
    db.session.commit()
    assert item.status == WORK_ITEM_STATUS_SUBMITTED

    db.session.execute(
        text("UPDATE work_items SET status = :s WHERE id = :i"),
        {"s": WORK_ITEM_STATUS_FINALIZED, "i": item.id},
    )
    # No commit. The instance still reads SUBMITTED.
    assert item.status == WORK_ITEM_STATUS_SUBMITTED

    success, error = finalize_work_item(item, super_admin_ctx, note="ok")

    assert not success
    assert error == "Work item is already finalized."


def test_unfinalize_refuses_a_row_another_session_already_moved(
    app, seed_draft_work_item, super_admin_ctx
):
    """Same defect, same fix, at unfinalize_work_item's own lock.

    An unrefreshed instance would let unfinalize proceed even though the row
    already moved out of FINALIZED underneath it.
    """
    from app.routes.admin_final.helpers import unfinalize_work_item

    data = seed_draft_work_item
    item = data["work_item"]
    item.status = WORK_ITEM_STATUS_FINALIZED
    db.session.commit()
    assert item.status == WORK_ITEM_STATUS_FINALIZED

    db.session.execute(
        text("UPDATE work_items SET status = :s WHERE id = :i"),
        {"s": WORK_ITEM_STATUS_SUBMITTED, "i": item.id},
    )
    # No commit. The instance still reads FINALIZED.
    assert item.status == WORK_ITEM_STATUS_FINALIZED

    success, error = unfinalize_work_item(
        item, "changes needed", False, super_admin_ctx
    )

    assert not success
    assert error == "Work item is not finalized."


def test_a_failed_enqueue_does_not_poison_the_release(
    app, seed_draft_work_item, super_admin_ctx, monkeypatch
):
    """A DBAPI error while resolving the template must not abort the release.

    Regression guard, not a red-green test. SQLite has no aborted-transaction
    state, so this passes before and after enqueue_savepoint exists: it
    proves the error is contained and the caller's commit survives on
    SQLite. The Postgres savepoint behaviour itself is covered by reading
    enqueue_savepoint's docstring plus test_savepoint_taken_on_postgres.
    """
    from sqlalchemy.exc import IntegrityError

    from app.routes.admin_final import helpers as admin_helpers

    calls = []

    def boom(*args, **kwargs):
        calls.append(True)
        raise IntegrityError("resolve_template_key", None, Exception("boom"))

    data = seed_draft_work_item
    item = data["work_item"]
    item.status = WORK_ITEM_STATUS_FINALIZED
    cycle = data["cycle"]

    # Without a recipient, _enqueue_emails returns before resolve_template_key
    # ever runs, so the boom patch below would never fire. One membership row
    # is enough to reach the enqueue path this test exists to exercise.
    db.session.add(DepartmentMembership(
        user_id=data["admin"].id,
        department_id=data["department"].id,
        event_cycle_id=cycle.id,
    ))

    monkeypatch.setattr("app.services.notifications.resolve_template_key", boom)

    released, error = admin_helpers.release_event_budgets(
        cycle, super_admin_ctx, note="Board approved FY27"
    )
    assert error is None
    assert released == 1, f"expected the one seeded held budget, got {released}"
    # The commit here proves the session survived the caught error, not the
    # Postgres abort this guards against; SQLite cannot reproduce that abort.
    db.session.commit()
    assert cycle.board_approved_at is not None
    assert calls, "resolve_template_key was never reached; the guard pins nothing"


def test_the_release_wraps_its_enqueue_in_a_savepoint(
    app, seed_draft_work_item, super_admin_ctx, monkeypatch
):
    """Structural guard for a Postgres-only behaviour.

    The failure this protects against cannot be reproduced on SQLite, so the
    test asserts the savepoint is entered rather than what it prevents. See
    test_a_failed_enqueue_does_not_poison_the_release for the contract itself.
    """
    from contextlib import contextmanager

    from app.routes.admin_final import helpers as admin_helpers

    entered = []

    @contextmanager
    def spy():
        entered.append(True)
        yield

    # release_event_budgets imports enqueue_savepoint function-locally, so
    # patching the source module is what takes effect here.
    monkeypatch.setattr("app.services.notifications.enqueue_savepoint", spy)

    data = seed_draft_work_item
    item = data["work_item"]
    item.status = WORK_ITEM_STATUS_FINALIZED
    cycle = data["cycle"]

    # A recipient is not required for the wrap to fire, but seeding one keeps
    # this test on the real enqueue path instead of the empty-recipients exit.
    db.session.add(DepartmentMembership(
        user_id=data["admin"].id,
        department_id=data["department"].id,
        event_cycle_id=cycle.id,
    ))

    released, error = admin_helpers.release_event_budgets(
        cycle, super_admin_ctx, note="Board approved FY27")
    assert error is None
    assert released == 1, f"expected the one seeded held budget, got {released}"
    assert entered, "release_event_budgets did not enter enqueue_savepoint"
