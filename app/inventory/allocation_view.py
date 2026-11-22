"""Filtering and grouping of department lines for the allocation page's two tabs.

Pure functions over `DemandLine`; nothing here queries the database.
"""
from dataclasses import dataclass

from app.inventory.demand import LINE_OPEN, LINE_REMOVED, DemandLine

LINES_SHORT, LINES_ALL, LINES_NOTES = "short", "all", "notes"
# Dropdown options are cut harder than the 80-character line words so they fit a phone.
_OPTION_WORDS = 40


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
    """One department's rows under its heading.

    `lines` lists every row, REMOVED rows included. The heading's counts cover
    only rows with a live line; `on_removed` counts the assets on REMOVED rows.
    """

    name: str
    lines: list[DemandLine]
    line_count: int
    requested: int
    allocated: int
    short: int
    on_removed: int


def group_by_department(lines) -> list[DepartmentGroup]:
    """Group lines by department name, sorted case-insensitively; lines keep their order."""
    by_department: dict[int, list[DemandLine]] = {}
    for line in lines:
        by_department.setdefault(line.department_id, []).append(line)
    groups = []
    for members in by_department.values():
        live = [line for line in members if line.state != LINE_REMOVED]
        groups.append(DepartmentGroup(
            name=members[0].department_name, lines=members, line_count=len(live),
            requested=sum(line.requested for line in live),
            allocated=sum(line.allocated for line in live),
            short=sum(short_count(line) for line in live),
            on_removed=sum(line.allocated for line in members if line.state == LINE_REMOVED)))
    return sorted(groups, key=lambda group: group.name.casefold())


def departments(lines) -> list[tuple[int, str]]:
    """Return (id, name) for every department that has a line or allocation, sorted by name."""
    found = {line.department_id: line.department_name for line in lines}
    return sorted(found.items(), key=lambda pair: pair[1].casefold())


def short_order(public_id: str | None) -> str:
    """Return the request ID without its event and department codes: `SUP-3`, `TEC-7`."""
    if not public_id:
        return ""
    return "-".join(public_id.split("-")[-2:])


def cut_words(text: str) -> str:
    """Cut dropdown words to 40 characters, ending in "…" when cut."""
    return text if len(text) <= _OPTION_WORDS else text[:_OPTION_WORDS - 1] + "…"


def line_option(department: str, public_id: str | None, line_number: int | None, words: str,
                left: int = 0) -> str:
    """Name a line in an asset dropdown; ` · {k} left` shows only while it has room."""
    text = f"{department} · {short_order(public_id)} line {line_number} · {cut_words(words)}"
    return f"{text} · {left} left" if left > 0 else text


def option_text(line: DemandLine) -> str:
    """Name one of this event's lines in an asset dropdown."""
    return line_option(line.department_name, line.public_id, line.line_number, line.words,
                       line.left)


@dataclass
class ChoiceGroup:
    department: str
    options: list[tuple[int, str]]


def asset_choices(lines, department_id: int | None) -> list[ChoiceGroup]:
    """Group the open lines with room, of one department when given, by department name."""
    by_department: dict[int, list[DemandLine]] = {}
    for line in lines:
        if line.state == LINE_OPEN and line.left > 0 and (
                department_id is None or line.department_id == department_id):
            by_department.setdefault(line.department_id, []).append(line)
    groups = [ChoiceGroup(department=members[0].department_name,
                          options=[(line.work_line_id, option_text(line)) for line in members])
              for members in by_department.values()]
    return sorted(groups, key=lambda group: group.department.casefold())
