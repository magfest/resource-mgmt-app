"""Filtering and grouping of department lines for the allocation page's Lines tab.

Pure functions over `DemandLine`; nothing here queries the database.
"""
from dataclasses import dataclass

from app.inventory.demand import LINE_OPEN, DemandLine

LINES_SHORT, LINES_ALL, LINES_NOTES = "short", "all", "notes"


def short_count(line: DemandLine) -> int:
    """Return how many more assets an open line needs; 0 for any other state."""
    if line.state != LINE_OPEN:
        return 0
    wanted = line.approved if line.approved is not None else line.requested
    return max(0, wanted - line.allocated)


def needs_a_lead(line: DemandLine) -> bool:
    """Return True when assets sit on a line whose request changed under them."""
    return line.allocated > 0 and any(w.startswith("Line changed") for w in line.warnings)


def filter_lines(lines, department_id: int | None, show: str) -> list[DemandLine]:
    """Keep the lines of one department (all when None) that match `show`; unknown `show` means short."""
    kept = [line for line in lines if department_id is None or line.department_id == department_id]
    if show == LINES_ALL:
        return kept
    if show == LINES_NOTES:
        return [line for line in kept if line.notes.strip()]
    return [line for line in kept if short_count(line) > 0 or needs_a_lead(line)]


@dataclass
class DepartmentGroup:
    name: str
    lines: list[DemandLine]
    requested: int
    allocated: int
    short: int


def group_by_department(lines) -> list[DepartmentGroup]:
    """Group lines by department name, sorted case-insensitively; lines keep their order."""
    by_department: dict[int, list[DemandLine]] = {}
    for line in lines:
        by_department.setdefault(line.department_id, []).append(line)
    groups = [
        DepartmentGroup(
            name=members[0].department_name, lines=members,
            # A REMOVED row has no request, so it adds nothing to requested.
            requested=sum(line.requested or 0 for line in members),
            allocated=sum(line.allocated for line in members),
            short=sum(short_count(line) for line in members))
        for members in by_department.values()]
    return sorted(groups, key=lambda group: group.name.casefold())


def departments(lines) -> list[tuple[int, str]]:
    """Return (id, name) for every department that has a line or allocation, sorted by name."""
    found = {line.department_id: line.department_name for line in lines}
    return sorted(found.items(), key=lambda pair: pair[1].casefold())
