"""Planning rows for every submitted TechOps line in one event.

The worklist page, its CSV and the admin home's Scale card all read this
module, so the three cannot disagree on a count. Status and team come from
the line's APPROVAL_GROUP review, the same source as the request page's
progress strip. Nothing here reads the Flask request.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from itertools import groupby

from sqlalchemy.orm import joinedload

from app import db
from app.models import (
    ApprovalGroup,
    Department,
    TechOpsLineDetail,
    TechOpsServiceType,
    WorkItem,
    WorkLine,
    WorkLineReview,
    WorkPortfolio,
    WorkType,
    REVIEW_STAGE_APPROVAL_GROUP,
    REVIEW_STATUS_APPROVED,
    REVIEW_STATUS_APPROVED_NEEDS_REVIEW,
    REVIEW_STATUS_NEEDS_ADJUSTMENT,
    REVIEW_STATUS_NEEDS_INFO,
    REVIEW_STATUS_PENDING,
    REVIEW_STATUS_REJECTED,
    WORK_ITEM_STATUS_FINALIZED,
    WORK_ITEM_STATUS_SUBMITTED,
)
from app.routes.spaces.queries import event_space_names
from .line_grain import SERVICE_ETHERNET, SERVICE_NO_SERVICES, SERVICE_PAYMENTS

STATUS_APPROVED = "approved"
STATUS_PENDING = "pending"
STATUS_WAITING = "waiting"
STATUS_REJECTED = "rejected"
STATUS_LABELS = {
    STATUS_APPROVED: "Approved",
    STATUS_PENDING: "Pending",
    STATUS_WAITING: "Waiting on requester",
    STATUS_REJECTED: "Rejected",
}

# APPROVED_NEEDS_REVIEW cannot occur on a group-final work type; it maps to
# approved so a row stamped before TechOps went group-final still shows.
_STATUS_BY_REVIEW = {
    REVIEW_STATUS_PENDING: STATUS_PENDING,
    REVIEW_STATUS_NEEDS_INFO: STATUS_WAITING,
    REVIEW_STATUS_NEEDS_ADJUSTMENT: STATUS_WAITING,
    REVIEW_STATUS_APPROVED: STATUS_APPROVED,
    REVIEW_STATUS_APPROVED_NEEDS_REVIEW: STATUS_APPROVED,
    REVIEW_STATUS_REJECTED: STATUS_REJECTED,
}

STATUS_FILTERS = ("not_rejected", "approved", "pending", "all")
_STATUSES_SHOWN = {
    "not_rejected": {STATUS_APPROVED, STATUS_PENDING, STATUS_WAITING},
    "approved": {STATUS_APPROVED},
    "pending": {STATUS_PENDING, STATUS_WAITING},
    "all": set(STATUS_LABELS),
}

GROUP_BYS = ("room", "service", "department", "team")
DEPARTMENT_WIDE = "Department-wide"
TEAM_ALL = "all"

# The form offers drop counts as buckets (line_grain.NETWORK_COUNTS), not a
# number, so a total can only be a range. None marks an open upper bound.
_DROP_RANGES = {"1-2": (1, 2), "3-5": (3, 5), "6-10": (6, 10), "10+": (10, None)}


@dataclass(frozen=True)
class WorklistFilters:
    team_codes: tuple[str, ...] = ()
    status: str = "not_rejected"
    service_code: str | None = None
    dept_code: str | None = None


@dataclass
class WorklistRow:
    line: WorkLine
    detail: TechOpsLineDetail
    work_item: WorkItem
    department: Department
    team: ApprovalGroup
    status: str
    space_id: int | None
    room_name: str

    @property
    def status_label(self) -> str:
        return STATUS_LABELS[self.status]


@dataclass
class WorklistGroup:
    label: str
    rows: list[WorklistRow]

    @property
    def approved(self) -> int:
        return sum(1 for r in self.rows if r.status == STATUS_APPROVED)

    @property
    def pending(self) -> int:
        return sum(1 for r in self.rows if r.status in (STATUS_PENDING, STATUS_WAITING))


@dataclass
class ServiceTotal:
    service: TechOpsServiceType
    approved: int = 0
    pending: int = 0
    drop_ranges: list[tuple[int, int | None]] = field(default_factory=list)
    checkout_points: int = 0

    @property
    def estimate_label(self) -> str | None:
        if self.service.code == SERVICE_ETHERNET and self.drop_ranges:
            low = sum(lo for lo, _ in self.drop_ranges)
            high = sum(hi if hi is not None else lo for lo, hi in self.drop_ranges)
            open_ended = any(hi is None for _, hi in self.drop_ranges)
            return f"{low} to {high}{'+' if open_ended else ''} drops, requester estimates"
        if self.service.code == SERVICE_PAYMENTS and self.checkout_points:
            noun = "checkout point" if self.checkout_points == 1 else "checkout points"
            return f"{self.checkout_points} {noun}"
        return None


def parse_filters(args, *, own_team_codes, team_codes, service_codes, dept_codes):
    """Read worklist query parameters; an unknown value falls back to its default.

    `args` is any mapping with .get, such as request.args. Returns the
    filters, the group-by key, and the team choice the dropdown shows: ""
    for the staffer's own teams, "all", or one team code.
    """
    team = (args.get("team") or "").strip().upper()
    if team == TEAM_ALL.upper():
        team_choice, team_filter = TEAM_ALL, ()
    elif team in team_codes:
        team_choice, team_filter = team, (team,)
    else:
        team_choice, team_filter = "", tuple(own_team_codes)

    status = (args.get("status") or "").strip().lower()
    service = (args.get("service") or "").strip().upper()
    dept = (args.get("dept") or "").strip().upper()
    group_by = (args.get("group") or "").strip().lower()

    filters = WorklistFilters(
        team_codes=team_filter,
        status=status if status in STATUS_FILTERS else "not_rejected",
        service_code=service if service in service_codes else None,
        dept_code=dept if dept in dept_codes else None,
    )
    return filters, (group_by if group_by in GROUP_BYS else "room"), team_choice


def worklist_rows(event_cycle, filters: WorklistFilters) -> list[WorklistRow]:
    """Every line on a submitted or finalized TechOps request in this event."""
    query = (
        db.session.query(WorkLine, WorkLineReview, WorkItem, Department, ApprovalGroup)
        .join(WorkLineReview, db.and_(
            WorkLineReview.work_line_id == WorkLine.id,
            WorkLineReview.stage == REVIEW_STAGE_APPROVAL_GROUP,
        ))
        .join(WorkItem, WorkItem.id == WorkLine.work_item_id)
        .join(WorkPortfolio, WorkPortfolio.id == WorkItem.portfolio_id)
        .join(WorkType, WorkType.id == WorkPortfolio.work_type_id)
        .join(Department, Department.id == WorkPortfolio.department_id)
        .join(ApprovalGroup, ApprovalGroup.id == WorkLineReview.approval_group_id)
        .join(TechOpsLineDetail, TechOpsLineDetail.work_line_id == WorkLine.id)
        .join(TechOpsServiceType, TechOpsServiceType.id == TechOpsLineDetail.service_type_id)
        .filter(
            WorkType.code == "TECHOPS",
            WorkPortfolio.event_cycle_id == event_cycle.id,
            WorkItem.is_archived.is_(False),
            WorkItem.status.in_((WORK_ITEM_STATUS_SUBMITTED, WORK_ITEM_STATUS_FINALIZED)),
            TechOpsServiceType.code != SERVICE_NO_SERVICES,
        )
        .options(
            joinedload(WorkLine.techops_detail).joinedload(TechOpsLineDetail.service_type),
            joinedload(WorkLine.techops_detail).joinedload(TechOpsLineDetail.parent_line),
        )
    )
    if filters.team_codes:
        query = query.filter(ApprovalGroup.code.in_(filters.team_codes))
    if filters.service_code:
        query = query.filter(TechOpsServiceType.code == filters.service_code)
    if filters.dept_code:
        query = query.filter(Department.code == filters.dept_code)

    shown = _STATUSES_SHOWN[filters.status]
    picked = []
    for line, review, item, dept, team in query.all():
        status = _STATUS_BY_REVIEW.get(review.status, STATUS_PENDING)
        if status in shown:
            picked.append((line, item, dept, team, status))

    names = event_space_names(
        event_cycle.id, (line.techops_detail.space_id for line, *_ in picked))
    rows = [
        WorklistRow(
            line=line, detail=line.techops_detail, work_item=item, department=dept,
            team=team, status=status, space_id=line.techops_detail.space_id,
            room_name=names.get(line.techops_detail.space_id, DEPARTMENT_WIDE),
        )
        for line, item, dept, team, status in picked
    ]
    rows.sort(key=lambda r: _sort_key(r, "room"))
    return rows


def _room_key(row):
    # Department-wide sorts after every named room.
    return (row.space_id is None, row.room_name.casefold(), row.space_id or 0)


def _service_key(row):
    service = row.detail.service_type
    return (service.sort_order or 0, service.name)


def _team_key(row):
    return (row.team.sort_order or 0, row.team.name)


def _leading_key(row, group_by):
    if group_by == "service":
        return _service_key(row)
    if group_by == "department":
        return (row.department.name.casefold(), row.department.id)
    if group_by == "team":
        return _team_key(row)
    return _room_key(row)


def _sort_key(row, group_by):
    return (
        _leading_key(row, group_by), _room_key(row), _service_key(row),
        row.department.name.casefold(), row.work_item.public_id, row.line.line_number,
    )


def _group_label(row, group_by) -> str:
    if group_by == "service":
        return row.detail.service_type.name
    if group_by == "department":
        return row.department.name
    if group_by == "team":
        return row.team.name
    return row.room_name


def group_rows(rows, group_by: str) -> list[WorklistGroup]:
    """Split rows into groups keyed by id, so two same-named rooms stay apart."""
    ordered = sorted(rows, key=lambda r: _sort_key(r, group_by))
    return [
        WorklistGroup(label=_group_label(members[0], group_by), rows=members)
        for members in (
            list(g) for _, g in groupby(ordered, key=lambda r: _leading_key(r, group_by))
        )
    ]


def worklist_totals(rows) -> list[ServiceTotal]:
    """Approved and pending counts per service, plus requester estimates.

    Rejected rows count toward nothing, even when the status filter shows them.
    """
    totals: dict[int, ServiceTotal] = {}
    for row in rows:
        if row.status == STATUS_REJECTED:
            continue
        service = row.detail.service_type
        total = totals.setdefault(service.id, ServiceTotal(service=service))
        if row.status == STATUS_APPROVED:
            total.approved += 1
        else:
            total.pending += 1
        config = row.detail.config or {}
        if service.code == SERVICE_ETHERNET and config.get("rough_count") in _DROP_RANGES:
            total.drop_ranges.append(_DROP_RANGES[config["rough_count"]])
        if service.code == SERVICE_PAYMENTS and row.detail.quantity:
            total.checkout_points += row.detail.quantity
    return sorted(totals.values(),
                  key=lambda t: (t.service.sort_order or 0, t.service.name))
