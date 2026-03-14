"""
Event enablement helpers - functions to check and manage which divisions and
departments participate in which events.

Key behavior:
- No record = ENABLED (backward compatible)
- Division disabled = ALL departments in that division are disabled
- Department can only be enabled if its division is also enabled
"""
from __future__ import annotations

from typing import Set, List, Optional

from sqlalchemy import and_, or_, not_, exists, select
from sqlalchemy.orm import joinedload

from app import db
from app.models import (
    Division,
    Department,
    EventCycleDivision,
    EventCycleDepartment,
)


# ============================================================
# Division Functions
# ============================================================

def is_division_enabled_for_event(division_id: int, event_cycle_id: int) -> bool:
    """
    Check if a division is enabled for an event.

    Returns True if:
    - No EventCycleDivision record exists (default enabled), OR
    - EventCycleDivision.is_enabled is True
    """
    # Single query: check if explicitly disabled
    disabled = db.session.query(
        EventCycleDivision.id
    ).filter(
        EventCycleDivision.event_cycle_id == event_cycle_id,
        EventCycleDivision.division_id == division_id,
        EventCycleDivision.is_enabled == False,
    ).first()

    return disabled is None


def get_enabled_division_ids_for_event(event_cycle_id: int) -> Set[int]:
    """
    Get the set of division IDs that are enabled for an event.

    Single query using NOT EXISTS for disabled check.
    """
    # Subquery for explicitly disabled divisions
    disabled_subq = db.session.query(EventCycleDivision.division_id).filter(
        EventCycleDivision.event_cycle_id == event_cycle_id,
        EventCycleDivision.is_enabled == False,
    ).subquery()

    # Get active divisions not in the disabled list
    result = db.session.query(Division.id).filter(
        Division.is_active == True,
        ~Division.id.in_(select(disabled_subq.c.division_id)),
    ).all()

    return {r.id for r in result}


def get_division_enablement_record(
    division_id: int,
    event_cycle_id: int,
) -> Optional[EventCycleDivision]:
    """Get the enablement record for a division, if it exists."""
    return EventCycleDivision.query.filter_by(
        event_cycle_id=event_cycle_id,
        division_id=division_id,
    ).first()


def set_division_enablement(
    division_id: int,
    event_cycle_id: int,
    is_enabled: bool,
    note: Optional[str] = None,
    user_id: Optional[str] = None,
) -> EventCycleDivision:
    """
    Set the enablement status for a division in an event.

    Creates or updates the EventCycleDivision record.
    """
    record = EventCycleDivision.query.filter_by(
        event_cycle_id=event_cycle_id,
        division_id=division_id,
    ).first()

    if record is None:
        record = EventCycleDivision(
            event_cycle_id=event_cycle_id,
            division_id=division_id,
            is_enabled=is_enabled,
            note=note,
            created_by_user_id=user_id,
            updated_by_user_id=user_id,
        )
        db.session.add(record)
    else:
        record.is_enabled = is_enabled
        if note is not None:
            record.note = note
        record.updated_by_user_id = user_id

    db.session.flush()
    return record


# ============================================================
# Department Functions
# ============================================================

def is_department_enabled_for_event(department_id: int, event_cycle_id: int) -> bool:
    """
    Check if a department is enabled for an event.

    Single query using LEFT JOINs to check both division and department status.
    """
    # Single query: get department with division info and check enablement
    result = db.session.query(
        Department.id,
        Department.division_id,
        EventCycleDivision.is_enabled.label('div_enabled'),
        EventCycleDepartment.is_enabled.label('dept_enabled'),
    ).outerjoin(
        EventCycleDivision,
        and_(
            EventCycleDivision.division_id == Department.division_id,
            EventCycleDivision.event_cycle_id == event_cycle_id,
        )
    ).outerjoin(
        EventCycleDepartment,
        and_(
            EventCycleDepartment.department_id == Department.id,
            EventCycleDepartment.event_cycle_id == event_cycle_id,
        )
    ).filter(
        Department.id == department_id,
    ).first()

    if result is None:
        return False  # Department doesn't exist

    # Check division enablement (None = enabled, False = disabled)
    if result.division_id is not None and result.div_enabled is False:
        return False

    # Check department enablement (None = enabled, False = disabled)
    if result.dept_enabled is False:
        return False

    return True


def get_enabled_department_ids_for_event(event_cycle_id: int) -> Set[int]:
    """
    Get the set of department IDs that are enabled for an event.

    Single efficient query using subqueries for disabled checks.
    """
    # Subquery: divisions that are explicitly disabled
    disabled_div_subq = db.session.query(
        EventCycleDivision.division_id
    ).filter(
        EventCycleDivision.event_cycle_id == event_cycle_id,
        EventCycleDivision.is_enabled == False,
    ).subquery()

    # Subquery: departments that are explicitly disabled
    disabled_dept_subq = db.session.query(
        EventCycleDepartment.department_id
    ).filter(
        EventCycleDepartment.event_cycle_id == event_cycle_id,
        EventCycleDepartment.is_enabled == False,
    ).subquery()

    # Get active departments where:
    # - Division is NOT disabled (or has no division)
    # - Department is NOT explicitly disabled
    result = db.session.query(Department.id).filter(
        Department.is_active == True,
        # Division not disabled (NULL division_id OR division_id not in disabled list)
        or_(
            Department.division_id.is_(None),
            ~Department.division_id.in_(select(disabled_div_subq.c.division_id)),
        ),
        # Department not explicitly disabled
        ~Department.id.in_(select(disabled_dept_subq.c.department_id)),
    ).all()

    return {r.id for r in result}


def get_enabled_departments_for_event(event_cycle_id: int) -> List[Department]:
    """
    Get all enabled Department objects for an event.

    Single efficient query - no intermediate ID lookup needed.
    """
    # Subquery: divisions that are explicitly disabled
    disabled_div_subq = db.session.query(
        EventCycleDivision.division_id
    ).filter(
        EventCycleDivision.event_cycle_id == event_cycle_id,
        EventCycleDivision.is_enabled == False,
    ).subquery()

    # Subquery: departments that are explicitly disabled
    disabled_dept_subq = db.session.query(
        EventCycleDepartment.department_id
    ).filter(
        EventCycleDepartment.event_cycle_id == event_cycle_id,
        EventCycleDepartment.is_enabled == False,
    ).subquery()

    # Get departments with eager-loaded division for sorting
    return Department.query.outerjoin(
        Division, Department.division_id == Division.id
    ).filter(
        Department.is_active == True,
        or_(
            Department.division_id.is_(None),
            ~Department.division_id.in_(select(disabled_div_subq.c.division_id)),
        ),
        ~Department.id.in_(select(disabled_dept_subq.c.department_id)),
    ).order_by(
        Division.sort_order.asc().nulls_last(),
        Division.name.asc().nulls_last(),
        Department.sort_order.asc(),
        Department.name.asc(),
    ).all()


def get_department_enablement_record(
    department_id: int,
    event_cycle_id: int,
) -> Optional[EventCycleDepartment]:
    """Get the enablement record for a department, if it exists."""
    return EventCycleDepartment.query.filter_by(
        event_cycle_id=event_cycle_id,
        department_id=department_id,
    ).first()


def set_department_enablement(
    department_id: int,
    event_cycle_id: int,
    is_enabled: bool,
    note: Optional[str] = None,
    user_id: Optional[str] = None,
) -> EventCycleDepartment:
    """
    Set the enablement status for a department in an event.

    Creates or updates the EventCycleDepartment record.
    """
    record = EventCycleDepartment.query.filter_by(
        event_cycle_id=event_cycle_id,
        department_id=department_id,
    ).first()

    if record is None:
        record = EventCycleDepartment(
            event_cycle_id=event_cycle_id,
            department_id=department_id,
            is_enabled=is_enabled,
            note=note,
            created_by_user_id=user_id,
            updated_by_user_id=user_id,
        )
        db.session.add(record)
    else:
        record.is_enabled = is_enabled
        if note is not None:
            record.note = note
        record.updated_by_user_id = user_id

    db.session.flush()
    return record


# ============================================================
# Bulk Operations
# ============================================================

def copy_event_enablement(
    source_event_id: int,
    target_event_id: int,
    user_id: Optional[str] = None,
) -> dict:
    """
    Copy all division and department enablement settings from one event to another.

    Uses batch operations for efficiency.
    """
    from datetime import datetime

    # Get all source records in two queries
    source_div_records = EventCycleDivision.query.filter_by(
        event_cycle_id=source_event_id
    ).all()
    source_dept_records = EventCycleDepartment.query.filter_by(
        event_cycle_id=source_event_id
    ).all()

    # Get existing target records in two queries
    existing_div_map = {
        r.division_id: r
        for r in EventCycleDivision.query.filter_by(event_cycle_id=target_event_id).all()
    }
    existing_dept_map = {
        r.department_id: r
        for r in EventCycleDepartment.query.filter_by(event_cycle_id=target_event_id).all()
    }

    now = datetime.utcnow()
    div_count = 0
    dept_count = 0

    # Copy divisions
    for src in source_div_records:
        existing = existing_div_map.get(src.division_id)
        if existing is None:
            record = EventCycleDivision(
                event_cycle_id=target_event_id,
                division_id=src.division_id,
                is_enabled=src.is_enabled,
                note=f"Copied from event {source_event_id}",
                created_by_user_id=user_id,
                updated_by_user_id=user_id,
                created_at=now,
                updated_at=now,
            )
            db.session.add(record)
        else:
            existing.is_enabled = src.is_enabled
            existing.updated_by_user_id = user_id
            existing.updated_at = now
        div_count += 1

    # Copy departments
    for src in source_dept_records:
        existing = existing_dept_map.get(src.department_id)
        if existing is None:
            record = EventCycleDepartment(
                event_cycle_id=target_event_id,
                department_id=src.department_id,
                is_enabled=src.is_enabled,
                note=f"Copied from event {source_event_id}",
                created_by_user_id=user_id,
                updated_by_user_id=user_id,
                created_at=now,
                updated_at=now,
            )
            db.session.add(record)
        else:
            existing.is_enabled = src.is_enabled
            existing.updated_by_user_id = user_id
            existing.updated_at = now
        dept_count += 1

    db.session.flush()

    return {
        "divisions_copied": div_count,
        "departments_copied": dept_count,
    }


def get_all_division_enablement_records(
    event_cycle_id: int,
) -> dict[int, EventCycleDivision]:
    """
    Get all enablement records for divisions in a single query.
    Returns dict mapping division_id -> record.
    """
    records = EventCycleDivision.query.filter_by(
        event_cycle_id=event_cycle_id
    ).all()
    return {r.division_id: r for r in records}


def get_all_department_enablement_records(
    event_cycle_id: int,
) -> dict[int, EventCycleDepartment]:
    """
    Get all enablement records for departments in a single query.
    Returns dict mapping department_id -> record.
    """
    records = EventCycleDepartment.query.filter_by(
        event_cycle_id=event_cycle_id
    ).all()
    return {r.department_id: r for r in records}


def get_all_department_enabled_status(
    event_cycle_id: int,
    departments: List[Department],
    div_enablement_map: dict[int, EventCycleDivision],
    dept_enablement_map: dict[int, EventCycleDepartment],
) -> dict[int, bool]:
    """
    Compute enabled status for all departments using pre-fetched records.
    No additional queries needed.

    Args:
        event_cycle_id: The event cycle ID
        departments: List of Department objects to check
        div_enablement_map: Pre-fetched division enablement records
        dept_enablement_map: Pre-fetched department enablement records

    Returns:
        Dict mapping department_id -> is_enabled
    """
    result = {}
    for dept in departments:
        # Check division enablement
        if dept.division_id is not None:
            div_record = div_enablement_map.get(dept.division_id)
            if div_record is not None and div_record.is_enabled is False:
                result[dept.id] = False
                continue

        # Check department enablement
        dept_record = dept_enablement_map.get(dept.id)
        if dept_record is not None and dept_record.is_enabled is False:
            result[dept.id] = False
        else:
            result[dept.id] = True

    return result


def bulk_set_all_enabled(
    event_cycle_id: int,
    is_enabled: bool,
    user_id: Optional[str] = None,
) -> dict:
    """
    Enable or disable all divisions and departments for an event.

    Uses batch operations for efficiency.
    """
    from datetime import datetime

    now = datetime.utcnow()

    # Get all active divisions and departments in two queries
    all_divisions = Division.query.filter_by(is_active=True).all()
    all_departments = Department.query.filter_by(is_active=True).all()

    # Get existing records in two queries
    existing_div_map = {
        r.division_id: r
        for r in EventCycleDivision.query.filter_by(event_cycle_id=event_cycle_id).all()
    }
    existing_dept_map = {
        r.department_id: r
        for r in EventCycleDepartment.query.filter_by(event_cycle_id=event_cycle_id).all()
    }

    div_count = 0
    dept_count = 0

    # Update divisions
    for div in all_divisions:
        existing = existing_div_map.get(div.id)
        if existing is None:
            record = EventCycleDivision(
                event_cycle_id=event_cycle_id,
                division_id=div.id,
                is_enabled=is_enabled,
                created_by_user_id=user_id,
                updated_by_user_id=user_id,
                created_at=now,
                updated_at=now,
            )
            db.session.add(record)
        else:
            existing.is_enabled = is_enabled
            existing.updated_by_user_id = user_id
            existing.updated_at = now
        div_count += 1

    # Update departments
    for dept in all_departments:
        existing = existing_dept_map.get(dept.id)
        if existing is None:
            record = EventCycleDepartment(
                event_cycle_id=event_cycle_id,
                department_id=dept.id,
                is_enabled=is_enabled,
                created_by_user_id=user_id,
                updated_by_user_id=user_id,
                created_at=now,
                updated_at=now,
            )
            db.session.add(record)
        else:
            existing.is_enabled = is_enabled
            existing.updated_by_user_id = user_id
            existing.updated_at = now
        dept_count += 1

    db.session.flush()

    return {
        "divisions_updated": div_count,
        "departments_updated": dept_count,
    }
