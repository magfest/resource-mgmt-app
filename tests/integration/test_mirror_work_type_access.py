"""Tests for mirroring BUDGET membership access onto other work types."""
from __future__ import annotations

import pytest

from app import db
from app.models import (
    Department,
    DepartmentMembership,
    DepartmentMembershipWorkTypeAccess,
    Division,
    DivisionMembership,
    DivisionMembershipWorkTypeAccess,
    EventCycle,
    User,
    WorkType,
)
from app.services.membership_access import mirror_work_type_access


@pytest.fixture
def seeded(app):
    cycle = EventCycle(code="MIR27", name="Mirror Event", is_active=True, sort_order=1)
    other_cycle = EventCycle(code="OLD26", name="Old Event", is_active=True, sort_order=2)
    db.session.add_all([cycle, other_cycle])
    wts = {
        code: WorkType(code=code, name=code.title(), is_active=True)
        for code in ("BUDGET", "TECHOPS", "SUPPLY")
    }
    db.session.add_all(wts.values())
    division = Division(code="DIV", name="Div", is_active=True)
    db.session.add(division)
    db.session.flush()
    dept = Department(code="DPT", name="Dept", is_active=True, division_id=division.id)
    db.session.add(dept)
    for uid in ("editor", "viewer", "divhead", "manual", "none", "old"):
        db.session.add(User(id=f"test:{uid}", email=f"{uid}@test.local",
                            display_name=uid, is_active=True))
    db.session.flush()

    def dept_member(uid, cycle_, access):
        m = DepartmentMembership(user_id=f"test:{uid}", department_id=dept.id,
                                 event_cycle_id=cycle_.id)
        db.session.add(m)
        db.session.flush()
        for code, (view, edit) in access.items():
            db.session.add(DepartmentMembershipWorkTypeAccess(
                department_membership_id=m.id, work_type_id=wts[code].id,
                can_view=view, can_edit=edit,
            ))
        return m

    members = {
        "editor": dept_member("editor", cycle, {"BUDGET": (True, True)}),
        "viewer": dept_member("viewer", cycle, {"BUDGET": (True, False)}),
        # Granted TechOps view by hand; the backfill must not raise it to edit.
        "manual": dept_member("manual", cycle, {"BUDGET": (True, True),
                                                "TECHOPS": (True, False)}),
        "none": dept_member("none", cycle, {}),
        "old": dept_member("old", other_cycle, {"BUDGET": (True, True)}),
    }
    div_m = DivisionMembership(user_id="test:divhead", division_id=division.id,
                               event_cycle_id=cycle.id, is_division_head=True)
    db.session.add(div_m)
    db.session.flush()
    db.session.add(DivisionMembershipWorkTypeAccess(
        division_membership_id=div_m.id, work_type_id=wts["BUDGET"].id,
        can_view=True, can_edit=True,
    ))
    members["divhead"] = div_m
    db.session.commit()
    return {"cycle": cycle, "wts": wts, "members": members}


def _access(membership, wt):
    db.session.refresh(membership)
    wta = membership.get_work_type_access(wt.id)
    return None if wta is None else (wta.can_view, wta.can_edit)


def test_copies_budget_level_to_each_target(seeded):
    wts, m = seeded["wts"], seeded["members"]
    mirror_work_type_access(seeded["cycle"], "BUDGET", ["TECHOPS", "SUPPLY"], apply=True)

    for code in ("TECHOPS", "SUPPLY"):
        assert _access(m["editor"], wts[code]) == (True, True)
        assert _access(m["viewer"], wts[code]) == (True, False)
        assert _access(m["divhead"], wts[code]) == (True, True)
        assert _access(m["none"], wts[code]) is None


def test_existing_target_row_is_left_alone(seeded):
    wts, m = seeded["wts"], seeded["members"]
    summary = mirror_work_type_access(seeded["cycle"], "BUDGET", ["TECHOPS", "SUPPLY"], apply=True)

    assert _access(m["manual"], wts["TECHOPS"]) == (True, False)
    assert _access(m["manual"], wts["SUPPLY"]) == (True, True)
    assert summary.skipped_existing == 1


def test_other_event_is_untouched(seeded):
    wts, m = seeded["wts"], seeded["members"]
    mirror_work_type_access(seeded["cycle"], "BUDGET", ["TECHOPS"], apply=True)

    assert _access(m["old"], wts["TECHOPS"]) is None


def test_dry_run_writes_nothing_and_reports_counts(seeded):
    wts, m = seeded["wts"], seeded["members"]
    summary = mirror_work_type_access(seeded["cycle"], "BUDGET", ["TECHOPS", "SUPPLY"], apply=False)

    assert _access(m["editor"], wts["TECHOPS"]) is None
    # editor, viewer, manual(SUPPLY only) on departments; divhead on division.
    assert summary.department_rows_added == 5
    assert summary.division_rows_added == 2


def test_second_run_adds_nothing(seeded):
    mirror_work_type_access(seeded["cycle"], "BUDGET", ["TECHOPS", "SUPPLY"], apply=True)
    summary = mirror_work_type_access(seeded["cycle"], "BUDGET", ["TECHOPS", "SUPPLY"], apply=True)

    assert summary.department_rows_added == 0
    assert summary.division_rows_added == 0


def test_cli_defaults_to_dry_run(app, seeded):
    wts, m = seeded["wts"], seeded["members"]
    result = app.test_cli_runner().invoke(args=["mirror-work-type-access", "MIR27"])

    assert result.exit_code == 0, result.output
    assert "DRY RUN" in result.output
    assert _access(m["editor"], wts["TECHOPS"]) is None


def test_cli_apply_writes(app, seeded):
    wts, m = seeded["wts"], seeded["members"]
    result = app.test_cli_runner().invoke(
        args=["mirror-work-type-access", "MIR27", "--apply"])

    assert result.exit_code == 0, result.output
    assert _access(m["editor"], wts["TECHOPS"]) == (True, True)


def test_cli_unknown_event_exits_1(app, seeded):
    result = app.test_cli_runner().invoke(args=["mirror-work-type-access", "NOPE"])

    assert result.exit_code == 1
