"""The bulk release refuses when the EFFECTIVE finalized template is dark.

Each test seeds a held budget. Without one, `released == 0` is true by
construction and the assertions below would pass with no guard at all.
"""
from datetime import datetime, timedelta

from app import db
from app.models import (
    EmailTemplate,
    EmailTemplateEventOverride,
    WORK_ITEM_STATUS_FINALIZED,
)


def _hold_one_budget(data):
    """Make the seeded work item a budget awaiting board release."""
    item = data["work_item"]
    item.status = WORK_ITEM_STATUS_FINALIZED
    db.session.flush()
    return item


def _override(cycle, **kwargs):
    template = db.session.query(EmailTemplate).filter_by(
        template_key="finalized").one()
    assert template.is_active is True, "the base row must stay live"
    db.session.add(EmailTemplateEventOverride(
        email_template_id=template.id, event_cycle_id=cycle.id, **kwargs))
    db.session.commit()
    return template


def test_release_refuses_when_the_override_silences_the_template(
    app, seed_draft_work_item, super_admin_ctx
):
    """An event that silenced its own copy must be refused up front.

    The base row stays active, so a guard reading EmailTemplate.is_active
    directly passes here and stamps every held budget before enqueue blocks
    the email.
    """
    from app.routes.admin_final.helpers import release_event_budgets

    data = seed_draft_work_item
    item = _hold_one_budget(data)
    cycle = data["cycle"]
    _override(cycle, is_active=False)

    released, error = release_event_budgets(
        cycle, super_admin_ctx, note="Board approved FY27")

    assert released == 0
    assert error is not None
    assert "finalized" in error
    # Nothing may be stamped: get_held_budgets would never offer it again.
    assert item.board_released_at is None
    assert cycle.board_approved_at is None


def test_release_refuses_when_the_window_has_closed(
    app, seed_draft_work_item, super_admin_ctx
):
    """A closed send window is the third way an email cannot go out.

    enqueue_email returns BLOCKED_WINDOW for this, so without the guard the
    release stamps every budget and silently sends nothing.
    """
    from app.routes.admin_final.helpers import release_event_budgets

    data = seed_draft_work_item
    item = _hold_one_budget(data)
    cycle = data["cycle"]
    _override(cycle, send_window_end=datetime.utcnow() - timedelta(days=1))

    released, error = release_event_budgets(
        cycle, super_admin_ctx, note="Board approved FY27")

    assert released == 0
    assert "send window" in error
    assert item.board_released_at is None


def test_release_proceeds_when_the_window_is_open(
    app, seed_draft_work_item, super_admin_ctx
):
    """The guard must not refuse a live template, or it blocks every release."""
    from app.routes.admin_final.helpers import release_event_budgets

    data = seed_draft_work_item
    item = _hold_one_budget(data)
    cycle = data["cycle"]
    now = datetime.utcnow()
    _override(cycle, send_window_start=now - timedelta(days=1),
              send_window_end=now + timedelta(days=1))

    released, error = release_event_budgets(
        cycle, super_admin_ctx, note="Board approved FY27")

    assert error is None
    assert released == 1
    assert item.board_released_at is not None
