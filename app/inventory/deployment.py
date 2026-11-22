"""Deployment report rows: every demand line of an event, with its assets and room.

Reads demand and allocations; it changes nothing. TechOps leads use it to plan
deployment, grouped by department, room, or item or service.
"""
from __future__ import annotations

from dataclasses import dataclass

from app import db
from app.inventory.demand import (
    LINE_OPEN, SOURCE_SUPPLY, SOURCE_TECHOPS, asset_warning, demand_lines, selections,
)
from app.inventory.checks import unit_code, unit_phrase
from app.inventory.unit_fields import details_line
from app.models import Space

GROUPS = {"department": "Department", "room": "Room", "what": "Item or service"}

NO_ROOM = "No room"


@dataclass(frozen=True)
class ReportRow:
    department: str
    room: str
    what: str
    public_id: str
    line_number: str
    words: str
    requested: str
    approved: str
    allocated: int
    short: int
    assets: list[str]
    warnings: list[str]


def _asset_text(unit) -> str:
    code, details = unit_code(unit), details_line(unit)
    parts = [code, unit.name, f"({details})" if details else None]
    return " ".join(p for p in parts if p) or unit_phrase(unit)


def _rooms(lines, event_cycle_id: int) -> dict[int, dict]:
    """Map department id to its spaces: `names` by space id, and the single-room name."""
    # Imported here: app/inventory/* must not import app.routes.* at module level.
    from app.routes.spaces.queries import spaces_for_department
    out = {}
    for dept_id in {line.department_id for line in lines}:
        entries = spaces_for_department(dept_id, event_cycle_id)
        out[dept_id] = {
            "names": {e["space"].id: e["display_name"] for e in entries},
            "only": entries[0]["display_name"] if len(entries) == 1 else "",
        }
    return out


def deployment_rows(event_cycle_id: int) -> list[ReportRow]:
    """Return one row per demand line of every selection, REMOVED rows once each.

    A REMOVED row can match an item and its TechOps service; the service wins.
    """
    pairs, removed_at = [], {}
    for selection in selections(event_cycle_id):
        for line in demand_lines(event_cycle_id, selection):
            if line.work_line_id is None:
                key = line.allocations[0].id
                if key in removed_at:
                    if selection.kind == "service":
                        pairs[removed_at[key]] = (selection, line)
                    continue
                removed_at[key] = len(pairs)
            pairs.append((selection, line))
    rooms = _rooms([line for _, line in pairs], event_cycle_id)
    # A TechOps space the department does not hold falls back to the space's own name.
    missing = {line.space_id for _, line in pairs if line.source == SOURCE_TECHOPS
               and line.space_id and line.space_id not in rooms[line.department_id]["names"]}
    fallback = ({s.id: s.name for s in db.session.query(Space).filter(Space.id.in_(missing))}
                if missing else {})
    rows = []
    for selection, line in pairs:
        held = rooms[line.department_id]
        if line.source == SOURCE_TECHOPS and line.space_id:
            room = held["names"].get(line.space_id) or fallback.get(line.space_id, "")
        elif line.source == SOURCE_SUPPLY:
            room = held["only"]
        else:
            room = ""
        removed = line.requested is None
        target = line.approved if line.approved is not None else line.requested
        rows.append(ReportRow(
            department=line.department_name, room=room, what=selection.name,
            public_id=line.public_id or "",
            line_number="" if line.line_number is None else str(line.line_number),
            words=line.notes or line.words,
            requested="" if removed else str(line.requested),
            approved="" if removed else ("not reviewed" if line.approved is None
                                         else str(line.approved)),
            allocated=line.allocated,
            short=max(0, target - line.allocated) if line.state == LINE_OPEN else 0,
            assets=[_asset_text(a.unit) for a in line.allocations],
            warnings=line.warnings + [w for a in line.allocations
                                      if (w := asset_warning(a.unit))]))
    return rows


def _group_name(row: ReportRow, group: str) -> str:
    if group == "room":
        return row.room or NO_ROOM
    return row.department if group == "department" else row.what


def group_rows(rows: list[ReportRow], group: str) -> list[tuple[str, list[ReportRow]]]:
    """Group rows under headings sorted by name; "No room" sorts last."""
    grouped: dict[str, list[ReportRow]] = {}
    for row in rows:
        grouped.setdefault(_group_name(row, group), []).append(row)
    names = sorted(grouped, key=lambda n: (n == NO_ROOM, n.casefold()))
    return [(n, sorted(grouped[n], key=lambda r: (r.department.casefold(), r.public_id,
                                                  int(r.line_number or 0)))) for n in names]
