"""Department demand for pre-allocated assets: Supply order lines and TechOps lines.

This module reads Supply and TechOps requests and never changes them. "Approved"
exists only once a reviewer decided: a Supply quantity at finalize, a TechOps line
at APPROVED; anything else reads as not reviewed. A deleted line survives as its
allocation's snapshot, shown as a REMOVED row under the allocation's department.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import and_, or_
from sqlalchemy.orm import Load, contains_eager, joinedload

from app import db
from app.inventory.checks import unit_code, unit_phrase
from app.models import (
    EventCycle, InvAllocation, InvGearKind, InvUnit, InvUnitValue, SupplyItem,
    SupplyOrderLineDetail, TechOpsLineDetail, TechOpsServiceType, WorkItem, WorkLine,
    WorkPortfolio, WorkType,
)
from app.models.constants import (
    INV_CONDITION_FOUND, INV_CONDITION_IN_SERVICE, INV_CONDITION_LOST, INV_CONDITION_REPAIR,
    INV_CONDITION_RETIRED, WORK_ITEM_STATUS_DRAFT, WORK_LINE_STATUS_APPROVED,
    WORK_LINE_STATUS_REJECTED,
)

LINE_OPEN = "OPEN"
LINE_REJECTED = "REJECTED"
LINE_DRAFT = "DRAFT"
LINE_ARCHIVED = "ARCHIVED"
LINE_REMOVED = "REMOVED"

SOURCE_SUPPLY = "SUPPLY"
SOURCE_TECHOPS = "TECHOPS"

_STATE_WARNINGS = {
    LINE_REJECTED: "Line changed: rejected",
    LINE_DRAFT: "Line changed: back in draft",
    LINE_ARCHIVED: "Line changed: archived",
    LINE_REMOVED: "Line changed: removed",
}

# Not CONDITION_WORDS: those are headings ("In repair"), and these sit mid-sentence.
_CONDITION_WARNINGS = {
    INV_CONDITION_REPAIR: "is in repair",
    INV_CONDITION_RETIRED: "is retired",
    INV_CONDITION_LOST: "is lost",
    INV_CONDITION_FOUND: "is found but not checked",
}

_MAX_WORDS = 80


@dataclass(frozen=True)
class Selection:
    kind: str
    id: int
    name: str
    item_ids: tuple[int, ...]


@dataclass
class DemandLine:
    """One department line, or one REMOVED allocation whose line was deleted.

    A REMOVED row has no work line, request or quantities; its words are the
    allocation's line_label snapshot.
    """
    work_line_id: int | None
    event_cycle_id: int
    department_id: int
    department_name: str
    public_id: str | None
    line_number: int | None
    words: str
    requested: int | None
    approved: int | None
    state: str
    source: str | None
    item_id: int | None
    service_type_id: int | None
    space_id: int | None
    allocations: list[InvAllocation]

    @property
    def allocated(self) -> int:
        return len(self.allocations)

    @property
    def left(self) -> int:
        return max(0, self.requested - self.allocated) if self.state == LINE_OPEN else 0

    @property
    def is_full(self) -> bool:
        return self.state == LINE_OPEN and self.allocated >= self.requested

    @property
    def warnings(self) -> list[str]:
        out = [_STATE_WARNINGS[self.state]] if self.state in _STATE_WARNINGS else []
        if self.state == LINE_OPEN:
            # A cut line outranks the approval: the department now asks for fewer.
            if self.allocated > self.requested:
                out.append(f"Line changed: now asks for {self.requested}")
            elif self.approved is not None and self.allocated > self.approved:
                out.append(f"Over the approved {self.approved}")
        return out


@dataclass(frozen=True)
class Shortfall:
    requested: int
    approved: int
    allocated: int
    free: int


def _items_with_supply_demand(event_cycle_id: int, item_ids: list[int]) -> set[int]:
    """Return the items with a Supply line `demand_lines` would show for the event."""
    lines = (_line_query()
             .filter(WorkPortfolio.event_cycle_id == event_cycle_id,
                     SupplyOrderLineDetail.item_id.in_(item_ids)).all())
    held = {line_id for (line_id,) in db.session.query(InvAllocation.work_line_id).filter(
        InvAllocation.work_line_id.in_([line.id for line in lines]))}
    return {line.supply_detail.item_id for line in lines
            if _state(line) == LINE_OPEN or line.id in held}


def selections(event_cycle_id: int | None) -> list[Selection]:
    """List allocatable items by name, then the TechOps services they fill.

    A service appears only when at least one such item names it. An item that
    names a service is listed itself only when the event has Supply demand for it;
    otherwise its lines show under the service.
    """
    items = (db.session.query(SupplyItem)
             .filter(SupplyItem.is_active.is_(True), SupplyItem.is_serialized.is_(True),
                     SupplyItem.needs_pre_allocation.is_(True))
             .all())
    items.sort(key=lambda i: (i.item_name.casefold(), i.id))
    linked = [i.id for i in items if i.techops_service_type_id is not None]
    with_demand = (_items_with_supply_demand(event_cycle_id, linked)
                   if linked and event_cycle_id is not None else set())
    out = [Selection("item", i.id, i.item_name, (i.id,)) for i in items
           if i.techops_service_type_id is None or i.id in with_demand]
    by_service: dict[int, list[int]] = {}
    for item in items:
        if item.techops_service_type_id is not None:
            by_service.setdefault(item.techops_service_type_id, []).append(item.id)
    if by_service:
        services = (db.session.query(TechOpsServiceType)
                    .filter(TechOpsServiceType.id.in_(by_service))
                    .order_by(TechOpsServiceType.sort_order.is_(None),
                              TechOpsServiceType.sort_order, TechOpsServiceType.name)
                    .all())
        out += [Selection("service", s.id, f"TechOps: {s.name}", tuple(by_service[s.id]))
                for s in services]
    return out


def find_selection(kind: str | None, selection_id: int | None,
                   event_cycle_id: int | None) -> Selection | None:
    """Return the selection asked for, or the first one when no kind is given."""
    listed = selections(event_cycle_id)
    if kind is None:
        return listed[0] if listed else None
    return next((s for s in listed if s.kind == kind and s.id == selection_id), None)


def allocation_events() -> list[EventCycle]:
    """List events with a submitted Supply or TechOps request, or with any allocation."""
    requested = (db.session.query(WorkPortfolio.event_cycle_id)
                 .join(WorkItem, WorkItem.portfolio_id == WorkPortfolio.id)
                 .join(WorkType, WorkType.id == WorkPortfolio.work_type_id)
                 .filter(WorkType.code.in_((SOURCE_SUPPLY, SOURCE_TECHOPS)),
                         WorkItem.status != WORK_ITEM_STATUS_DRAFT))
    allocated = db.session.query(InvAllocation.event_cycle_id)
    # The is_(None) key puts unsorted events last on SQLite and Postgres alike.
    return (db.session.query(EventCycle)
            .filter(or_(EventCycle.id.in_(requested), EventCycle.id.in_(allocated)))
            .order_by(EventCycle.sort_order.is_(None), EventCycle.sort_order, EventCycle.code)
            .all())


def pick_event(events: list[EventCycle], event_id: int | None) -> EventCycle | None:
    """Return the event asked for, else the default event, else the first; all from `events`."""
    return (next((e for e in events if e.id == event_id), None)
            or next((e for e in events if e.is_default), None)
            or (events[0] if events else None))


def line_words(line: WorkLine) -> str:
    """Describe what a Supply or TechOps line asks for, in at most 80 characters."""
    supply, techops = line.supply_detail, line.techops_detail
    if supply is not None:
        parts = [supply.requester_notes if (supply.requester_notes or "").strip()
                 else supply.item.item_name]
    elif techops is not None:
        parts = [techops.location, techops.usage, techops.description]
        if techops.service_type.code == "PRINTER":
            # Imported here: the techops package loads every TechOps route module on import.
            from app.routes.work.techops.labels import PRINTER_VOLUME
            parts.append(PRINTER_VOLUME.get((techops.config or {}).get("volume")))
        if not any((p or "").strip() for p in parts):
            parts = [techops.service_type.name]
    else:
        parts = []
    text = ", ".join(" ".join(p.split()) for p in parts if p and p.strip())
    return text if len(text) <= _MAX_WORDS else text[:_MAX_WORDS - 1] + "…"


def line_label(line: DemandLine) -> str:
    return f"{line.department_name} · {line.words}"


def _line_query():
    # TechOpsLineDetail has a second key to work_lines (parent_line_id), so each
    # join names its ON clause.
    return (db.session.query(WorkLine)
            .join(WorkItem, WorkItem.id == WorkLine.work_item_id)
            .join(WorkPortfolio, WorkPortfolio.id == WorkItem.portfolio_id)
            .outerjoin(SupplyOrderLineDetail, SupplyOrderLineDetail.work_line_id == WorkLine.id)
            .outerjoin(TechOpsLineDetail, TechOpsLineDetail.work_line_id == WorkLine.id)
            .options(contains_eager(WorkLine.work_item).contains_eager(WorkItem.portfolio)
                     .joinedload(WorkPortfolio.department),
                     contains_eager(WorkLine.supply_detail)
                     .joinedload(SupplyOrderLineDetail.item),
                     contains_eager(WorkLine.techops_detail)
                     .joinedload(TechOpsLineDetail.service_type)))


def _unit_loads(start):
    """Loader options for what asset_warning, unit_code and details_line read."""
    return [start.selectinload(InvUnit.identifiers),
            start.selectinload(InvUnit.values).selectinload(InvUnitValue.field),
            start.selectinload(InvUnit.values).selectinload(InvUnitValue.choice),
            start.selectinload(InvUnit.item).selectinload(SupplyItem.gear_kind)
            .selectinload(InvGearKind.fields)]


def _allocation_query():
    return (db.session.query(InvAllocation)
            .join(InvUnit, InvUnit.id == InvAllocation.unit_id)
            .options(joinedload(InvAllocation.department),
                     *_unit_loads(contains_eager(InvAllocation.unit)))
            .order_by(InvAllocation.id))


def _state(line: WorkLine) -> str:
    item = line.work_item
    if item.status == WORK_ITEM_STATUS_DRAFT:
        return LINE_DRAFT
    if item.is_archived or item.portfolio.is_archived:
        return LINE_ARCHIVED
    if line.status == WORK_LINE_STATUS_REJECTED:
        return LINE_REJECTED
    return LINE_OPEN


def _from_line(line: WorkLine, allocations: list[InvAllocation]) -> DemandLine | None:
    supply, techops = line.supply_detail, line.techops_detail
    if supply is not None:
        source, requested, approved = (SOURCE_SUPPLY, supply.quantity_requested,
                                       supply.quantity_approved)
    elif techops is not None:
        # APPROVED_NEEDS_REVIEW still waits on an admin, so it reads as not reviewed.
        source, requested = SOURCE_TECHOPS, techops.quantity or 1
        approved = 1 if line.status == WORK_LINE_STATUS_APPROVED else None
    else:
        return None
    portfolio = line.work_item.portfolio
    return DemandLine(
        work_line_id=line.id, event_cycle_id=portfolio.event_cycle_id,
        department_id=portfolio.department_id, department_name=portfolio.department.name,
        public_id=line.work_item.public_id, line_number=line.line_number,
        words=line_words(line), requested=requested, approved=approved, state=_state(line),
        source=source, item_id=supply.item_id if supply else None,
        service_type_id=techops.service_type_id if techops else None,
        space_id=techops.space_id if techops else None, allocations=allocations)


def _removed(row: InvAllocation) -> DemandLine:
    return DemandLine(
        work_line_id=None, event_cycle_id=row.event_cycle_id, department_id=row.department_id,
        department_name=row.department.name, public_id=None, line_number=None,
        words=row.line_label, requested=None, approved=None, state=LINE_REMOVED, source=None,
        item_id=None, service_type_id=None, space_id=None, allocations=[row])


def _sort_key(line: DemandLine):
    return (line.is_full, line.department_name.casefold(), line.public_id or "",
            line.line_number or 0, min((a.id for a in line.allocations), default=0))


def demand_lines(event_cycle_id: int, selection: Selection) -> list[DemandLine]:
    """Return the event's lines for one selection, plus REMOVED rows, open and unfull first.

    Lines that are not open show only while something is allocated to them.
    """
    if selection.kind == "item":
        match = SupplyOrderLineDetail.item_id == selection.id
    else:
        match = TechOpsLineDetail.service_type_id == selection.id
    lines = _line_query().filter(WorkPortfolio.event_cycle_id == event_cycle_id, match).all()
    line_ids = [line.id for line in lines]
    rows = _allocation_query().filter(
        InvAllocation.event_cycle_id == event_cycle_id,
        or_(InvAllocation.work_line_id.in_(line_ids),
            and_(InvAllocation.work_line_id.is_(None),
                 InvUnit.item_id.in_(selection.item_ids)))).all()
    by_line: dict[int, list[InvAllocation]] = {}
    out = []
    for row in rows:
        if row.work_line_id is None:
            out.append(_removed(row))
        else:
            by_line.setdefault(row.work_line_id, []).append(row)
    for line in lines:
        demand = _from_line(line, by_line.get(line.id, []))
        if demand is not None and (demand.state == LINE_OPEN or demand.allocations):
            out.append(demand)
    out.sort(key=_sort_key)
    return out


def demand_line(work_line_id: int, fresh: bool = False) -> DemandLine | None:
    """Return one Supply or TechOps line in any state, or None for any other line.

    `fresh` rereads the line, request and detail rows over any copies the
    session already holds. The allocation rules pass it under the line lock.
    """
    query = _line_query().filter(WorkLine.id == work_line_id)
    if fresh:
        query = query.execution_options(populate_existing=True)
    line = query.one_or_none()
    if line is None:
        return None
    rows = _allocation_query().filter(InvAllocation.work_line_id == work_line_id).all()
    return _from_line(line, rows)


def _free_count(item_ids: tuple[int, ...]) -> int:
    held = db.session.query(InvAllocation.id).filter(InvAllocation.unit_id == InvUnit.id).exists()
    return (db.session.query(InvUnit)
            .filter(InvUnit.item_id.in_(item_ids),
                    InvUnit.condition == INV_CONDITION_IN_SERVICE, ~held)
            .count())


def shortfall(selection: Selection, lines: list[DemandLine]) -> Shortfall:
    """Total the open lines, every allocation shown, and the selection's free assets.

    Free counts assets allocated to nothing in any event, so it can exceed what
    this event's lines could take.
    """
    open_lines = [line for line in lines if line.state == LINE_OPEN]
    return Shortfall(
        requested=sum(line.requested for line in open_lines),
        approved=sum(line.approved for line in open_lines if line.approved is not None),
        allocated=sum(line.allocated for line in lines),
        free=_free_count(selection.item_ids))


def selection_assets(selection: Selection, unallocated_only: bool) -> list[InvUnit]:
    """List the selection's assets that are in service or hold an allocation in any event.

    An allocated asset in repair stays listed so a lead can release it.
    """
    held = db.session.query(InvAllocation.id).filter(InvAllocation.unit_id == InvUnit.id).exists()
    in_service = InvUnit.condition == INV_CONDITION_IN_SERVICE
    units = (db.session.query(InvUnit)
             .options(*_unit_loads(Load(InvUnit)))
             .filter(InvUnit.item_id.in_(selection.item_ids),
                     and_(in_service, ~held) if unallocated_only else or_(in_service, held))
             .all())
    # Assets with no number sort after numbered ones, by name.
    units.sort(key=lambda u: (unit_code(u) is None, unit_code(u) or "",
                              (u.name or "").casefold(), u.id))
    return units


def asset_warning(unit: InvUnit) -> str | None:
    """Warn when an asset is not in service, or return None."""
    text = _CONDITION_WARNINGS.get(unit.condition)
    return f"{unit_phrase(unit)} {text}" if text else None
