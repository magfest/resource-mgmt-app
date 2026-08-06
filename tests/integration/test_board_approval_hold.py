"""Finalizing holds the budget until the board approves the event topline."""
from datetime import datetime

from app import db
from app.models import (
    WorkLineReview, REVIEW_STAGE_APPROVAL_GROUP, REVIEW_STATUS_APPROVED,
    WORK_ITEM_STATUS_SUBMITTED, WORK_ITEM_STATUS_FINALIZED,
    WORK_LINE_STATUS_APPROVED,
)


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def _ready_to_finalize(data):
    """Put the fixture item in a state can_finalize_work_item accepts."""
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
    return item


def test_finalize_holds_when_board_has_not_approved(app, client, seed_draft_work_item):
    data = seed_draft_work_item
    item = _ready_to_finalize(data)
    assert data["cycle"].board_approved_at is None

    _login(client, "test:admin")
    resp = client.post(f"/admin/final-review/finalize/{item.id}",
                        data={"note": "ok"}, follow_redirects=True)

    db.session.refresh(item)
    assert item.status == WORK_ITEM_STATUS_FINALIZED
    assert item.board_released_at is None
    assert item.finalized_notified_at is None

    # The flash must say the department was NOT told, not the release copy.
    body = resp.get_data(as_text=True)
    assert "is not notified until the FY budget is approved" in body
    assert "The budget is released" not in body


def test_finalize_releases_when_board_already_approved(app, client, seed_draft_work_item):
    """The latch means stragglers need no second action."""
    from app.models import WorkItemAuditEvent, AUDIT_EVENT_BOARD_RELEASE

    data = seed_draft_work_item
    item = _ready_to_finalize(data)
    data["cycle"].board_approved_at = datetime(2026, 8, 1, 12, 0)
    db.session.commit()

    _login(client, "test:admin")
    resp = client.post(f"/admin/final-review/finalize/{item.id}",
                        data={"note": "ok"}, follow_redirects=True)

    db.session.refresh(item)
    assert item.status == WORK_ITEM_STATUS_FINALIZED
    assert item.board_released_at is not None
    # The scheduled command sends, not this request.
    assert item.finalized_notified_at is None

    # The flash must say the budget released, not the held copy.
    body = resp.get_data(as_text=True)
    assert "The budget is released" in body
    assert "a scheduled process will notify the department" in body
    assert "is not notified until the FY budget is approved" not in body

    # This release path is automatic (the latch was already set), unlike
    # release_event_budgets' explicit board-approval action. It must still
    # leave an audit trail.
    events = WorkItemAuditEvent.query.filter_by(
        work_item_id=item.id, event_type=AUDIT_EVENT_BOARD_RELEASE).all()
    assert len(events) == 1
    assert events[0].created_by_user_id == "test:admin"
    assert events[0].reason


def test_supplementary_unpauses_even_while_held(app, client, seed_draft_work_item):
    """A department must not be blocked by the board's calendar.

    Supplementaries unblock when the budget admin finishes, not when the board
    approves. A primary can sit held for weeks.
    """
    from app.models import (
        WorkItem, REQUEST_KIND_SUPPLEMENTARY, WORK_ITEM_STATUS_PAUSED,
    )

    data = seed_draft_work_item
    item = _ready_to_finalize(data)
    supp = WorkItem(
        portfolio_id=item.portfolio_id,
        request_kind=REQUEST_KIND_SUPPLEMENTARY,
        status=WORK_ITEM_STATUS_PAUSED,
        public_id="TST2026-TESTDEPT-BUD-2",
        created_by_user_id=data["admin"].id,
    )
    db.session.add(supp)
    db.session.commit()

    assert data["cycle"].board_approved_at is None

    _login(client, "test:admin")
    client.post(f"/admin/final-review/finalize/{item.id}",
                data={"note": "ok"}, follow_redirects=True)

    db.session.refresh(item)
    db.session.refresh(supp)
    assert item.board_released_at is None      # still held
    assert supp.status != WORK_ITEM_STATUS_PAUSED  # but unblocked


def test_finalize_does_not_release_non_board_release_worktype(app, seed_workflow_data):
    """finalize_work_item() must gate the stamp on uses_board_release itself.

    has_admin_final is True for Contract and Supply too, so the admin-final
    route's BUDGET-only check is not the only thing that could route a
    non-BUDGET item through this function. Calls finalize_work_item()
    directly (bypassing that route guard) on a TechOps item with the event
    latch already set, and confirms board_released_at stays unset.
    """
    from app.models import (
        WorkType, WorkTypeConfig, WorkPortfolio, WorkItem, WorkLine,
        REQUEST_KIND_PRIMARY, ROUTING_STRATEGY_DIRECT,
    )
    from app.routes import UserContext
    from app.routes.admin_final.helpers import finalize_work_item

    data = seed_workflow_data
    data["cycle"].board_approved_at = datetime(2026, 8, 1, 12, 0)
    db.session.commit()

    techops_wt = WorkType(code="TECHOPS", name="TechOps", is_active=True)
    db.session.add(techops_wt)
    db.session.flush()

    db.session.add(WorkTypeConfig(
        work_type_id=techops_wt.id, url_slug="techops",
        public_id_prefix="TEC", line_detail_type="techops",
        routing_strategy=ROUTING_STRATEGY_DIRECT,
        uses_dispatch=True, has_admin_final=True, uses_board_release=False,
    ))

    portfolio = WorkPortfolio(
        work_type_id=techops_wt.id, event_cycle_id=data["cycle"].id,
        department_id=data["department"].id, created_by_user_id=data["admin"].id,
    )
    db.session.add(portfolio)
    db.session.flush()

    item = WorkItem(
        portfolio_id=portfolio.id, request_kind=REQUEST_KIND_PRIMARY,
        status=WORK_ITEM_STATUS_SUBMITTED,
        public_id="TST2026-TESTDEPT-TEC-1",
        created_by_user_id=data["admin"].id,
    )
    db.session.add(item)
    db.session.flush()

    line = WorkLine(
        work_item_id=item.id, line_number=1,
        status=WORK_LINE_STATUS_APPROVED,
        current_review_stage=REVIEW_STAGE_APPROVAL_GROUP,
    )
    db.session.add(line)
    db.session.flush()

    db.session.add(WorkLineReview(
        work_line_id=line.id, stage=REVIEW_STAGE_APPROVAL_GROUP,
        approval_group_id=data["approval_group"].id,
        status=REVIEW_STATUS_APPROVED, approved_amount_cents=0,
        created_by_user_id=data["admin"].id))
    db.session.commit()

    ctx = UserContext(user_id="test:admin", user=None, roles=("SUPER_ADMIN",),
                      is_super_admin=True, approval_group_ids=set())
    success, error = finalize_work_item(item, ctx, "ok")
    db.session.commit()

    assert success, error
    db.session.refresh(item)
    assert item.status == WORK_ITEM_STATUS_FINALIZED
    assert item.board_released_at is None


def test_release_stamps_every_held_budget(app, seed_draft_work_item):
    from app.routes import UserContext
    from app.routes.admin_final.helpers import release_event_budgets, get_held_budgets

    data = seed_draft_work_item
    item = data["work_item"]
    item.status = WORK_ITEM_STATUS_FINALIZED
    db.session.commit()

    assert [i.id for i in get_held_budgets(data["cycle"].id)] == [item.id]

    ctx = UserContext(user_id="test:admin", user=None, roles=("SUPER_ADMIN",),
                      is_super_admin=True, approval_group_ids=set())
    count, err = release_event_budgets(data["cycle"], ctx, "board approved 2026-08-05")
    db.session.commit()

    assert err is None
    assert count == 1
    db.session.refresh(item)
    db.session.refresh(data["cycle"])
    assert item.board_released_at is not None
    assert data["cycle"].board_approved_at is not None
    assert data["cycle"].board_approved_by_user_id == "test:admin"


def test_release_is_idempotent(app, seed_draft_work_item):
    """Running release twice must not re-stamp the item or rewrite the latch.

    The second call is patched to a sentinel `utcnow()` far outside any real
    test run. If the `board_approved_at is None` guard in release_event_budgets
    were dropped or inverted, the latch would pick up that sentinel instead of
    staying at the timestamp the first call recorded, and the assertion below
    would catch it.
    """
    from unittest.mock import patch
    from datetime import datetime as real_datetime
    from app.routes import UserContext
    from app.routes.admin_final.helpers import release_event_budgets

    data = seed_draft_work_item
    item = data["work_item"]
    item.status = WORK_ITEM_STATUS_FINALIZED
    db.session.commit()

    ctx = UserContext(user_id="test:admin", user=None, roles=("SUPER_ADMIN",),
                      is_super_admin=True, approval_group_ids=set())
    release_event_budgets(data["cycle"], ctx, "first")
    db.session.commit()
    db.session.refresh(item)
    db.session.refresh(data["cycle"])
    first_stamp = item.board_released_at
    first_approved_at = data["cycle"].board_approved_at
    assert first_approved_at is not None

    sentinel_now = real_datetime(2099, 1, 1)
    with patch("app.routes.admin_final.helpers.datetime") as mock_dt:
        mock_dt.utcnow.return_value = sentinel_now
        count, err = release_event_budgets(data["cycle"], ctx, "second")
    db.session.commit()

    assert err is None
    assert count == 0
    db.session.refresh(item)
    db.session.refresh(data["cycle"])
    assert item.board_released_at == first_stamp
    assert data["cycle"].board_approved_at == first_approved_at
    assert data["cycle"].board_approved_at != sentinel_now


def test_release_creates_board_release_audit_event(app, seed_draft_work_item):
    """The audit trail is a first-class concern; a refactor must not drop it."""
    from app.models import WorkItemAuditEvent, AUDIT_EVENT_BOARD_RELEASE
    from app.routes import UserContext
    from app.routes.admin_final.helpers import release_event_budgets

    data = seed_draft_work_item
    item = data["work_item"]
    item.status = WORK_ITEM_STATUS_FINALIZED
    db.session.commit()

    ctx = UserContext(user_id="test:admin", user=None, roles=("SUPER_ADMIN",),
                      is_super_admin=True, approval_group_ids=set())
    release_event_budgets(data["cycle"], ctx, "board approved 2026-08-05")
    db.session.commit()

    events = WorkItemAuditEvent.query.filter_by(
        work_item_id=item.id, event_type=AUDIT_EVENT_BOARD_RELEASE).all()
    assert len(events) == 1
    assert events[0].reason == "board approved 2026-08-05"
    assert events[0].created_by_user_id == "test:admin"


def test_release_requires_a_note(app, seed_draft_work_item):
    from app.routes import UserContext
    from app.routes.admin_final.helpers import release_event_budgets

    data = seed_draft_work_item
    ctx = UserContext(user_id="test:admin", user=None, roles=("SUPER_ADMIN",),
                      is_super_admin=True, approval_group_ids=set())
    count, err = release_event_budgets(data["cycle"], ctx, "   ")
    assert count == 0
    assert err is not None


def test_release_does_not_sweep_other_worktypes(app, seed_draft_work_item):
    """Only BUDGET holds for board approval; TechOps/Supply finalize freely.

    get_held_budgets must not pick up a finalized item from a work type where
    WorkTypeConfig.uses_board_release is False, even in the same event.
    """
    from app.models import (
        WorkType, WorkTypeConfig, WorkPortfolio, WorkItem,
        REQUEST_KIND_PRIMARY, ROUTING_STRATEGY_DIRECT,
    )
    from app.routes.admin_final.helpers import get_held_budgets

    data = seed_draft_work_item
    item = data["work_item"]
    item.status = WORK_ITEM_STATUS_FINALIZED
    db.session.commit()

    techops_wt = WorkType(code="TECHOPS", name="TechOps", is_active=True)
    db.session.add(techops_wt)
    db.session.flush()

    db.session.add(WorkTypeConfig(
        work_type_id=techops_wt.id, url_slug="techops",
        public_id_prefix="TEC", line_detail_type="techops",
        routing_strategy=ROUTING_STRATEGY_DIRECT,
        uses_dispatch=True, has_admin_final=False, uses_board_release=False,
    ))

    techops_portfolio = WorkPortfolio(
        work_type_id=techops_wt.id, event_cycle_id=data["cycle"].id,
        department_id=data["department"].id, created_by_user_id=data["admin"].id,
    )
    db.session.add(techops_portfolio)
    db.session.flush()

    techops_item = WorkItem(
        portfolio_id=techops_portfolio.id,
        request_kind=REQUEST_KIND_PRIMARY,
        status=WORK_ITEM_STATUS_FINALIZED,
        public_id="TST2026-TESTDEPT-TEC-1",
        created_by_user_id=data["admin"].id,
    )
    db.session.add(techops_item)
    db.session.commit()

    held_ids = [i.id for i in get_held_budgets(data["cycle"].id)]
    assert held_ids == [item.id]
    assert techops_item.id not in held_ids


def test_board_release_page_lists_held_budgets_and_topline(app, client, seed_draft_work_item):
    data = seed_draft_work_item
    item, line = data["work_item"], data["line"]
    item.status = WORK_ITEM_STATUS_FINALIZED
    line.status = WORK_LINE_STATUS_APPROVED
    line.approved_amount_cents = 5000
    db.session.commit()

    _login(client, "test:admin")
    resp = client.get(f"/admin/final-review/board-release/?event={data['cycle'].code}")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert item.public_id in body
    assert "$50.00" in body


def test_board_release_post_releases_and_redirects(app, client, seed_draft_work_item):
    data = seed_draft_work_item
    item = data["work_item"]
    item.status = WORK_ITEM_STATUS_FINALIZED
    db.session.commit()

    _login(client, "test:admin")
    resp = client.post(
        "/admin/final-review/board-release/",
        data={"event": data["cycle"].code, "note": "board approved 2026-08-05"},
        follow_redirects=True,
    )

    assert resp.status_code == 200
    db.session.refresh(item)
    assert item.board_released_at is not None


def test_unfinalize_clears_release_stamps(app, seed_draft_work_item):
    """Sending a budget back undoes its release, so a re-finalize notifies again."""
    from datetime import datetime as _dt
    from app.routes import UserContext
    from app.routes.admin_final.helpers import unfinalize_work_item

    data = seed_draft_work_item
    item = data["work_item"]
    item.status = WORK_ITEM_STATUS_FINALIZED
    item.board_released_at = _dt(2026, 8, 1, 9, 0)
    item.finalized_notified_at = _dt(2026, 8, 1, 9, 5)
    db.session.commit()

    ctx = UserContext(user_id="test:admin", user=None, roles=("SUPER_ADMIN",),
                      is_super_admin=True, approval_group_ids=set())
    ok, err = unfinalize_work_item(item, "changes needed", False, ctx)
    db.session.commit()

    assert ok, err
    db.session.refresh(item)
    assert item.board_released_at is None
    assert item.finalized_notified_at is None


def test_finish_button_confirm_copy_when_board_has_not_approved(app, client, seed_draft_work_item):
    """The confirm dialog must say the department waits, not that it's told now."""
    data = seed_draft_work_item
    item = _ready_to_finalize(data)
    assert data["cycle"].board_approved_at is None

    _login(client, "test:admin")
    resp = client.get(
        f"/{data['cycle'].code}/{data['department'].code}/budget/item/{item.public_id}"
    )

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Budget Admin Finished" in body
    assert "Finalize Request" not in body
    assert ("The department is not notified until the FY budget is "
            "approved") in body
    assert "releases the budget" not in body


def test_finish_button_confirm_copy_when_board_already_approved(app, client, seed_draft_work_item):
    """The confirm dialog must say the budget releases, not that it waits."""
    data = seed_draft_work_item
    item = _ready_to_finalize(data)
    data["cycle"].board_approved_at = datetime(2026, 8, 1, 12, 0)
    db.session.commit()

    _login(client, "test:admin")
    resp = client.get(
        f"/{data['cycle'].code}/{data['department'].code}/budget/item/{item.public_id}"
    )

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Budget Admin Finished" in body
    assert ("This locks in the approved amounts and releases the budget"
            ) in body
    assert "A scheduled process will notify the department" in body
    assert "is not notified until the FY budget is approved" not in body


def test_detail_page_shows_pending_board_approval(app, client, seed_draft_work_item):
    data = seed_draft_work_item
    item = data["work_item"]
    item.status = WORK_ITEM_STATUS_FINALIZED
    item.board_released_at = None
    db.session.commit()

    _login(client, "test:admin")
    resp = client.get(
        f"/{data['cycle'].code}/{data['department'].code}/budget/item/{item.public_id}"
    )

    assert resp.status_code == 200
    assert "Pending FY Budget Approval" in resp.get_data(as_text=True)


def test_line_status_summary_does_not_hold_non_board_release_worktype(app, seed_draft_work_item):
    """compute_line_status_summary must scope PENDING_BOARD_APPROVAL by uses_board_release.

    It runs for every work type's portfolio and department pages. Only BUDGET
    writes board_released_at, so a finalized TechOps item is never released and
    an unscoped check would mark it held forever.
    """
    from app.models import (
        WorkType, WorkTypeConfig, WorkPortfolio, WorkItem, WorkLine,
        REQUEST_KIND_PRIMARY, ROUTING_STRATEGY_DIRECT,
    )
    from app.routes.work.helpers.computations import compute_line_status_summary

    data = seed_draft_work_item

    techops_wt = WorkType(code="TECHOPS", name="TechOps", is_active=True)
    db.session.add(techops_wt)
    db.session.flush()

    db.session.add(WorkTypeConfig(
        work_type_id=techops_wt.id, url_slug="techops",
        public_id_prefix="TEC", line_detail_type="techops",
        routing_strategy=ROUTING_STRATEGY_DIRECT,
        uses_dispatch=True, has_admin_final=True, uses_board_release=False,
    ))

    portfolio = WorkPortfolio(
        work_type_id=techops_wt.id, event_cycle_id=data["cycle"].id,
        department_id=data["department"].id, created_by_user_id=data["admin"].id,
    )
    db.session.add(portfolio)
    db.session.flush()

    item = WorkItem(
        portfolio_id=portfolio.id, request_kind=REQUEST_KIND_PRIMARY,
        status=WORK_ITEM_STATUS_FINALIZED,
        public_id="TST2026-TESTDEPT-TEC-1",
        created_by_user_id=data["admin"].id,
    )
    db.session.add(item)
    db.session.flush()

    db.session.add(WorkLine(
        work_item_id=item.id, line_number=1, status=WORK_LINE_STATUS_APPROVED,
    ))
    db.session.commit()

    summary = compute_line_status_summary(item)
    assert summary.effective_status == "FINALIZED"
    assert summary.effective_status != "PENDING_BOARD_APPROVAL"


def test_detail_page_agrees_with_summary_when_board_release_disabled(app, client, seed_draft_work_item):
    """The template must read the same scoped fact as compute_line_status_summary.

    A prior version of the template re-derived the hold from
    `status == FINALIZED and not board_released_at`, without the
    `uses_board_release` gate. If that flag were ever False for BUDGET (a
    failed migration backfill, an admin toggle), the item would never reach
    the board-release queue -- both `get_held_budgets` and
    `release_event_budgets` are scoped on the same flag -- so nothing could
    ever stamp `board_released_at`. The old template would then show "Pending
    Board Approval" forever with no path off that state. The view now passes
    `is_pending_board_approval` from `compute_line_status_summary()`, so the
    two can no longer disagree.
    """
    from app.routes.work.helpers.computations import compute_line_status_summary

    data = seed_draft_work_item
    data["work_type_config"].uses_board_release = False
    item = data["work_item"]
    item.status = WORK_ITEM_STATUS_FINALIZED
    item.board_released_at = None
    db.session.commit()

    summary = compute_line_status_summary(item)
    assert summary.effective_status == "FINALIZED"

    _login(client, "test:admin")
    resp = client.get(
        f"/{data['cycle'].code}/{data['department'].code}/budget/item/{item.public_id}"
    )

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Pending FY Budget Approval" not in body
    assert "Finalized" in body


def test_held_budget_card_hides_stage_badge(app, client, seed_draft_work_item):
    """A held budget is locked the same as a released one; its portfolio
    card must not show approval-stage badges (APPROVED/RECOMMENDED) that
    imply review is still in progress.
    """
    from app.models import REVIEW_STAGE_ADMIN_FINAL

    data = seed_draft_work_item
    item, line = data["work_item"], data["line"]
    item.status = WORK_ITEM_STATUS_FINALIZED
    item.board_released_at = None  # held: board has not approved the event
    line.status = WORK_LINE_STATUS_APPROVED
    line.current_review_stage = REVIEW_STAGE_ADMIN_FINAL
    db.session.commit()

    _login(client, "test:admin")
    resp = client.get(f"/{data['cycle'].code}/{data['department'].code}/budget")

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "Pending FY Budget Approval" in body
    assert "APPROVED</span>" not in body


def test_board_release_audit_event_renders_with_pill_not_raw(app, client, seed_draft_work_item):
    """BOARD_RELEASE needs its own audit_log.html branch and null old/new.

    Without the template branch it falls through to the generic
    `<span class="pill">BOARD_RELEASE</span>`. Without nulling old/new at
    the write site, the detail column reads "FINALIZED -> FINALIZED", which
    states a transition that never happened (status doesn't change here).
    """
    from app.models import WorkItemAuditEvent, AUDIT_EVENT_BOARD_RELEASE

    data = seed_draft_work_item
    item = data["work_item"]
    item.status = WORK_ITEM_STATUS_FINALIZED
    item.board_released_at = datetime(2026, 8, 5, 10, 0)
    db.session.add(WorkItemAuditEvent(
        work_item_id=item.id,
        event_type=AUDIT_EVENT_BOARD_RELEASE,
        old_value=None,
        new_value=None,
        reason="board approved 2026-08-05",
        created_by_user_id="test:admin",
    ))
    db.session.commit()

    _login(client, "test:admin")
    resp = client.get(
        f"/{data['cycle'].code}/{data['department'].code}/budget/item/{item.public_id}"
    )

    assert resp.status_code == 200
    body = resp.get_data(as_text=True)
    assert "RELEASED" in body
    assert ">BOARD_RELEASE<" not in body
    assert "FINALIZED</span> &rarr;" not in body
    assert "board approved 2026-08-05" in body


def test_summary_handles_work_type_with_no_config(app, seed_draft_work_item):
    """A WorkType with no WorkTypeConfig row must not raise.

    WorkType.config is a nullable backref (workflow.py:116); dropping the
    `config is not None` guard in compute_line_status_summary would turn a
    missing config into an AttributeError instead of a status string.
    """
    from app.models import WorkType, WorkPortfolio, WorkItem, REQUEST_KIND_PRIMARY
    from app.routes.work.helpers.computations import compute_line_status_summary

    data = seed_draft_work_item

    unconfigured_wt = WorkType(code="AV", name="AV", is_active=True)
    db.session.add(unconfigured_wt)
    db.session.flush()
    assert unconfigured_wt.config is None

    portfolio = WorkPortfolio(
        work_type_id=unconfigured_wt.id, event_cycle_id=data["cycle"].id,
        department_id=data["department"].id, created_by_user_id=data["admin"].id,
    )
    db.session.add(portfolio)
    db.session.flush()

    item = WorkItem(
        portfolio_id=portfolio.id, request_kind=REQUEST_KIND_PRIMARY,
        status=WORK_ITEM_STATUS_FINALIZED,
        public_id="TST2026-TESTDEPT-AV-1",
        created_by_user_id=data["admin"].id,
    )
    db.session.add(item)
    db.session.commit()

    summary = compute_line_status_summary(item)
    assert summary.effective_status == "FINALIZED"
