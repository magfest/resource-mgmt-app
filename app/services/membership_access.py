"""Bulk copy of membership work-type access from one work type to others.

Permission boundary: this writes department and division access rows, which
decide who can see and edit requests. It only inserts; an existing row for a
target work type is never changed, so access granted by hand survives a rerun.
"""
from __future__ import annotations

from dataclasses import dataclass

from app import db
from app.models import (
    DepartmentMembership,
    DepartmentMembershipWorkTypeAccess,
    DivisionMembership,
    DivisionMembershipWorkTypeAccess,
    EventCycle,
    WorkType,
)


@dataclass
class MirrorSummary:
    department_rows_added: int = 0
    division_rows_added: int = 0
    skipped_existing: int = 0


def mirror_work_type_access(
    event_cycle: EventCycle,
    source_code: str,
    target_codes: list[str],
    apply: bool,
) -> MirrorSummary:
    """Give each membership in one event the same access to targets as to the source.

    A membership with neither view nor edit on the source gets nothing. Commits
    only when apply is true; otherwise it rolls back and returns the counts.

    Raises:
        ValueError: A work type code does not exist.
    """
    codes = [source_code, *target_codes]
    by_code = {wt.code: wt for wt in WorkType.query.filter(WorkType.code.in_(codes))}
    missing = [c for c in codes if c not in by_code]
    if missing:
        raise ValueError(f"Unknown work type code(s): {', '.join(missing)}")
    source = by_code[source_code]
    targets = [by_code[c] for c in target_codes]

    summary = MirrorSummary()
    kinds = (
        (DepartmentMembership, DepartmentMembershipWorkTypeAccess,
         "department_membership_id", "department_rows_added"),
        (DivisionMembership, DivisionMembershipWorkTypeAccess,
         "division_membership_id", "division_rows_added"),
    )
    for membership_cls, access_cls, fk_name, counter in kinds:
        memberships = membership_cls.query.filter_by(event_cycle_id=event_cycle.id).all()
        for m in memberships:
            src = m.get_work_type_access(source.id)
            if src is None or not (src.can_view or src.can_edit):
                continue
            for target in targets:
                if m.get_work_type_access(target.id) is not None:
                    summary.skipped_existing += 1
                    continue
                db.session.add(access_cls(
                    **{fk_name: m.id},
                    work_type_id=target.id,
                    can_view=src.can_view,
                    can_edit=src.can_edit,
                ))
                setattr(summary, counter, getattr(summary, counter) + 1)

    if apply:
        db.session.commit()
    else:
        db.session.rollback()
    return summary
