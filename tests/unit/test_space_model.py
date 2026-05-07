"""Tests for Space and SpaceDepartmentAssignment models."""
import pytest
from datetime import datetime
from app import db
from app.models import EventCycle, Department
from app.models.space import Space, SpaceDepartmentAssignment


@pytest.fixture(scope="function")
def space_seed(app):
    """Minimal fixture: one EventCycle and one Department, enough to test Space."""
    cycle = EventCycle(
        code="TST2026", name="Test Event 2026",
        is_active=True, is_default=True, sort_order=1,
    )
    dept = Department(
        code="TESTDEPT", name="Test Department", is_active=True,
    )
    db.session.add_all([cycle, dept])
    db.session.commit()
    return {"cycle": cycle, "department": dept}


def test_space_create(app, space_seed):
    cycle = space_seed["cycle"]
    space = Space(
        event_cycle_id=cycle.id,
        code="PANELS_4",
        name="Panels 4",
        location="Chesapeake A/B/C",
        square_feet=4200,
        is_active=True,
        created_by_user_id="user_1",
    )
    db.session.add(space)
    db.session.commit()
    assert space.id is not None
    assert space.code == "PANELS_4"
    assert space.event_cycle_id == cycle.id


def test_space_code_unique_per_event(app, space_seed):
    cycle = space_seed["cycle"]
    db.session.add(Space(
        event_cycle_id=cycle.id, code="PANELS_4", name="Panels 4",
        is_active=True, created_by_user_id="u",
    ))
    db.session.commit()
    db.session.add(Space(
        event_cycle_id=cycle.id, code="PANELS_4", name="Dup",
        is_active=True, created_by_user_id="u",
    ))
    with pytest.raises(Exception):  # IntegrityError
        db.session.commit()
    db.session.rollback()


def test_space_dept_assignment(app, space_seed):
    cycle = space_seed["cycle"]
    department = space_seed["department"]
    space = Space(
        event_cycle_id=cycle.id, code="MAIN", name="Main",
        is_active=True, created_by_user_id="u",
    )
    db.session.add(space)
    db.session.flush()

    assignment = SpaceDepartmentAssignment(
        space_id=space.id,
        department_id=department.id,
        assigned_at=datetime.utcnow(),
        assigned_by_user_id="user_1",
    )
    db.session.add(assignment)
    db.session.commit()
    assert assignment.unassigned_at is None
    assert space.assignments[0].department_id == department.id
