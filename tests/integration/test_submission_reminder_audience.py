"""
Tests for get_departments_needing_submission_reminder.

Covers:
- The done/not-done discriminator across PRIMARY/SUPPLEMENTARY/no-portfolio cases.
- The EventCycleDepartment.is_enabled flag (no row = enabled).
- Departments with no human members surfaced (not silently dropped).
- DivisionMembership members are NOT pulled into recipients (avoids spam to
  division heads who'd otherwise see one reminder per dept in their division).
"""
from __future__ import annotations

import pytest

from app import db
from app.models import (
    Department,
    Division,
    DivisionMembership,
    EventCycle,
    EventCycleDepartment,
    WorkType,
    WorkTypeConfig,
    WorkPortfolio,
    WorkItem,
    User,
    DepartmentMembership,
    REQUEST_KIND_PRIMARY,
    REQUEST_KIND_SUPPLEMENTARY,
    WORK_ITEM_STATUS_DRAFT,
    WORK_ITEM_STATUS_SUBMITTED,
    ROUTING_STRATEGY_DIRECT,
)
from app.services.notifications import (
    get_departments_needing_submission_reminder,
    ReminderTarget,
)


def _seed_budget_worktype():
    """Seed the BUDGET WorkType + config (used by all tests in this module)."""
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
    db.session.flush()
    return wt


def _seed_event_cycle(code="TST2026"):
    cycle = EventCycle(
        code=code, name=f"Test Event {code}",
        is_active=True, is_default=True, sort_order=1,
    )
    db.session.add(cycle)
    db.session.flush()
    return cycle


def _seed_department(code, name, division=None):
    dept = Department(
        code=code, name=name, is_active=True,
        division_id=division.id if division else None,
    )
    db.session.add(dept)
    db.session.flush()
    return dept


def _seed_user_and_membership(user_id_suffix, dept, cycle):
    """Create a user with email and a dept membership for this event."""
    user = User(
        id=f"test:{user_id_suffix}",
        email=f"{user_id_suffix}@test.local",
        display_name=user_id_suffix.title(),
        is_active=True,
    )
    db.session.add(user)
    db.session.flush()
    membership = DepartmentMembership(
        user_id=user.id,
        department_id=dept.id,
        event_cycle_id=cycle.id,
    )
    db.session.add(membership)
    db.session.flush()
    return user


def _seed_portfolio_with_work_item(dept, cycle, work_type, request_kind, status, admin):
    """Create a portfolio and one work item in the given state."""
    portfolio = WorkPortfolio(
        work_type_id=work_type.id,
        event_cycle_id=cycle.id,
        department_id=dept.id,
        created_by_user_id=admin.id,
    )
    db.session.add(portfolio)
    db.session.flush()
    work_item = WorkItem(
        portfolio_id=portfolio.id,
        request_kind=request_kind,
        status=status,
        public_id=f"{cycle.code}-{dept.code}-BUD-1",
        created_by_user_id=admin.id,
    )
    db.session.add(work_item)
    db.session.flush()
    return portfolio, work_item


@pytest.fixture
def admin(app):
    """An admin user used as created_by on work items."""
    user = User(
        id="test:admin-audience",
        email="admin-audience@test.local",
        display_name="Audience Admin",
        is_active=True,
    )
    db.session.add(user)
    db.session.commit()
    return user


def test_excludes_dept_with_primary_submitted_includes_others(app, admin):
    """
    Single-fixture combinatorial test: 4 departments, only A, C, D should
    appear in the returned list. B is excluded because it has a PRIMARY out
    of DRAFT.

    Dept A: PRIMARY in DRAFT          -> RETURNED (still needs to submit)
    Dept B: PRIMARY in SUBMITTED      -> EXCLUDED
    Dept C: SUPPLEMENTARY in SUBMITTED, no PRIMARY -> RETURNED (PRIMARY is what counts)
    Dept D: no portfolio at all       -> RETURNED (loudest case)
    """
    cycle = _seed_event_cycle()
    wt = _seed_budget_worktype()

    dept_a = _seed_department("AAA", "Dept A")
    dept_b = _seed_department("BBB", "Dept B")
    dept_c = _seed_department("CCC", "Dept C")
    dept_d = _seed_department("DDD", "Dept D")

    # Give each surviving dept at least one recipient so we can assert emails too.
    _seed_user_and_membership("user-a", dept_a, cycle)
    _seed_user_and_membership("user-c", dept_c, cycle)
    _seed_user_and_membership("user-d", dept_d, cycle)

    _seed_portfolio_with_work_item(
        dept_a, cycle, wt, REQUEST_KIND_PRIMARY, WORK_ITEM_STATUS_DRAFT, admin,
    )
    _seed_portfolio_with_work_item(
        dept_b, cycle, wt, REQUEST_KIND_PRIMARY, WORK_ITEM_STATUS_SUBMITTED, admin,
    )
    _seed_portfolio_with_work_item(
        dept_c, cycle, wt, REQUEST_KIND_SUPPLEMENTARY, WORK_ITEM_STATUS_SUBMITTED, admin,
    )
    # Dept D intentionally gets no portfolio.

    db.session.commit()

    targets = get_departments_needing_submission_reminder(cycle)
    codes = [t.department_code for t in targets]

    assert codes == ["AAA", "CCC", "DDD"], (
        f"Expected exactly A, C, D in that order (sorted by code), got {codes}"
    )
    assert all(isinstance(t, ReminderTarget) for t in targets)
    # Spot-check that recipients are populated for at least one target.
    target_a = next(t for t in targets if t.department_code == "AAA")
    assert "user-a@test.local" in target_a.recipient_emails


def test_event_cycle_department_disabled_excludes_dept(app, admin):
    """
    EventCycleDepartment.is_enabled=False excludes a department; absence of
    a row keeps it included.
    """
    cycle = _seed_event_cycle()
    wt = _seed_budget_worktype()  # noqa: F841 — referenced via DB state, not name

    dept_e = _seed_department("EEE", "Dept E (disabled)")
    dept_f = _seed_department("FFF", "Dept F (no row, default enabled)")

    _seed_user_and_membership("user-e", dept_e, cycle)
    _seed_user_and_membership("user-f", dept_f, cycle)

    # Explicitly disable Dept E for this event; Dept F gets no row.
    db.session.add(EventCycleDepartment(
        event_cycle_id=cycle.id,
        department_id=dept_e.id,
        is_enabled=False,
    ))
    db.session.commit()

    targets = get_departments_needing_submission_reminder(cycle)
    codes = [t.department_code for t in targets]

    assert "EEE" not in codes, "Disabled department leaked into reminder list"
    assert "FFF" in codes, "Default-enabled department was incorrectly excluded"


def test_empty_recipient_dept_surfaced_with_empty_list(app, admin):
    """
    A target dept with no memberships must appear in the returned list with
    recipient_emails == []. Silently dropping these would hide a config gap.
    """
    cycle = _seed_event_cycle()
    wt = _seed_budget_worktype()  # noqa: F841

    dept_g = _seed_department("GGG", "Dept G (no members)")
    # Deliberately no _seed_user_and_membership for dept_g.

    db.session.commit()

    targets = get_departments_needing_submission_reminder(cycle)

    target_g = next((t for t in targets if t.department_code == "GGG"), None)
    assert target_g is not None, (
        "Dept with no members must still appear in targets so caller can warn"
    )
    assert target_g.recipient_emails == [], (
        f"Expected empty recipient list, got {target_g.recipient_emails!r}"
    )


def test_excludes_dept_with_extension_granted_on_draft_primary(app, admin):
    """
    A department whose only PRIMARY BUDGET is still in DRAFT but has been
    granted an extension must NOT receive the reminder. The extension is
    the budget team's signal that this department is permitted to submit
    late; the automated reminder should not contradict it.

    Dept I: PRIMARY in DRAFT, extension_granted=True   -> EXCLUDED
    Dept J: PRIMARY in DRAFT, extension_granted=False  -> RETURNED (control)
    """
    cycle = _seed_event_cycle()
    wt = _seed_budget_worktype()

    dept_i = _seed_department("III", "Dept I (extension granted)")
    dept_j = _seed_department("JJJ", "Dept J (no extension)")

    _seed_user_and_membership("user-i", dept_i, cycle)
    _seed_user_and_membership("user-j", dept_j, cycle)

    _, wi_i = _seed_portfolio_with_work_item(
        dept_i, cycle, wt, REQUEST_KIND_PRIMARY, WORK_ITEM_STATUS_DRAFT, admin,
    )
    wi_i.extension_granted = True
    _seed_portfolio_with_work_item(
        dept_j, cycle, wt, REQUEST_KIND_PRIMARY, WORK_ITEM_STATUS_DRAFT, admin,
    )
    db.session.commit()

    targets = get_departments_needing_submission_reminder(cycle)
    codes = [t.department_code for t in targets]

    assert "III" not in codes, (
        "Dept with extension_granted=True on draft primary must be excluded "
        "from reminders."
    )
    assert "JJJ" in codes, (
        "Control dept without extension must still be included."
    )


def test_division_membership_does_not_contribute_recipients(app, admin):
    """
    DivisionMembership members must NOT appear in reminder recipients.

    Why: a division head with N departments under them would otherwise
    receive N near-identical reminder emails. Direct DepartmentMembership
    members are sufficient to drive the submit action.
    """
    cycle = _seed_event_cycle()
    wt = _seed_budget_worktype()  # noqa: F841

    # Division with one department; one direct dept member and one div-level head.
    div = Division(code="OPS", name="Operations Division", is_active=True)
    db.session.add(div)
    db.session.flush()

    dept_h = _seed_department("HHH", "Dept H (in division)", division=div)

    # Direct dept member — SHOULD appear.
    _seed_user_and_membership("dept-direct", dept_h, cycle)

    # Division head — should NOT appear.
    div_head = User(
        id="test:div-head",
        email="div-head@test.local",
        display_name="Division Head",
        is_active=True,
    )
    db.session.add(div_head)
    db.session.flush()
    db.session.add(DivisionMembership(
        user_id=div_head.id,
        division_id=div.id,
        event_cycle_id=cycle.id,
    ))
    db.session.commit()

    targets = get_departments_needing_submission_reminder(cycle)
    target_h = next((t for t in targets if t.department_code == "HHH"), None)
    assert target_h is not None
    assert "dept-direct@test.local" in target_h.recipient_emails, (
        "Direct department members must still receive reminders."
    )
    assert "div-head@test.local" not in target_h.recipient_emails, (
        "DivisionMembership members must NOT receive the per-department "
        "reminder (would cause one-email-per-dept spam to division heads)."
    )
