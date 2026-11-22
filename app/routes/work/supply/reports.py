"""Supply demand reports: one event's requested quantities by item, then by department.

Read-only intake view for the Supply team; it is not inventory. Access is Supply
admins, super admins and members of an active SUPPLY approval group.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass

from flask import Response, abort, flash, render_template, request, url_for
from sqlalchemy import select

from app import db
from app.models import (
    Department,
    EventCycle,
    SupplyCategory,
    SupplyItem,
    SupplyOrderDetail,
    SupplyOrderLineDetail,
    WorkItem,
    WorkLine,
    WorkLineReview,
    WorkPortfolio,
    WorkType,
    REVIEW_STAGE_APPROVAL_GROUP,
    REVIEW_STATUS_APPROVED,
    REVIEW_STATUS_REJECTED,
    WORK_ITEM_STATUS_DRAFT,
    WORK_ITEM_STATUS_FINALIZED,
    WORK_ITEM_STATUS_SUBMITTED,
)
from app.routes import get_user_ctx
from app.routes.work.helpers import is_worktype_admin
from app.routes.work.helpers.checkout import is_work_type_staffer
from .. import work_bp

_STATUS_LABELS = {
    WORK_ITEM_STATUS_DRAFT: "Draft",
    WORK_ITEM_STATUS_SUBMITTED: "Submitted",
    WORK_ITEM_STATUS_FINALIZED: "Finalized",
}


@dataclass(frozen=True)
class DemandRow:
    item_id: int
    item_name: str
    category_name: str
    unit: str
    department_name: str
    public_id: str
    event_code: str
    dept_code: str
    display_status: str
    requested: int
    reviewer: int | None
    final: int | None
    confidence_label: str | None
    notes: str | None


@dataclass(frozen=True)
class ItemDemand:
    item_id: int
    item_name: str
    category_name: str
    unit: str
    departments: int
    requested: int
    reviewer: int | None
    final: int | None


def reviewer_quantity(review_status: str | None, quantity_reviewed: int | None,
                      quantity_requested: int) -> int | None:
    """The quantity the approval group settled on, or None while undecided.

    Lines decided before `quantity_reviewed` existed hold NULL there, and
    approving at the requested quantity was the only option then. An APPROVED
    review with NULL therefore counts the requested quantity; a REJECTED one
    counts 0. Pending and kicked-back lines are not decided yet.
    """
    if review_status == REVIEW_STATUS_APPROVED:
        return quantity_reviewed if quantity_reviewed is not None else quantity_requested
    if review_status == REVIEW_STATUS_REJECTED:
        return quantity_reviewed if quantity_reviewed is not None else 0
    return None


def demand_lines(event_cycle_id: int, *, include_drafts: bool,
                 category_id: int | None = None,
                 item_id: int | None = None) -> list[DemandRow]:
    """Live Supply order lines for one event, in one query.

    Closed orders, removed lines and archived orders are left out. Items that
    were deactivated after being ordered stay in: the demand was real.
    """
    statuses = [WORK_ITEM_STATUS_SUBMITTED, WORK_ITEM_STATUS_FINALIZED]
    if include_drafts:
        statuses.append(WORK_ITEM_STATUS_DRAFT)
    query = (
        select(
            SupplyOrderLineDetail, SupplyItem.item_name, SupplyItem.unit,
            SupplyCategory.name, Department.name, Department.code,
            WorkItem.public_id, WorkItem.status, EventCycle.code,
            WorkLineReview.status,
        )
        .join(WorkLine, WorkLine.id == SupplyOrderLineDetail.work_line_id)
        .join(WorkItem, WorkItem.id == WorkLine.work_item_id)
        .join(SupplyOrderDetail, SupplyOrderDetail.work_item_id == WorkItem.id)
        .join(WorkPortfolio, WorkPortfolio.id == WorkItem.portfolio_id)
        .join(Department, Department.id == WorkPortfolio.department_id)
        .join(EventCycle, EventCycle.id == WorkPortfolio.event_cycle_id)
        .join(SupplyItem, SupplyItem.id == SupplyOrderLineDetail.item_id)
        .join(SupplyCategory, SupplyCategory.id == SupplyItem.category_id)
        .outerjoin(WorkLineReview, db.and_(
            WorkLineReview.work_line_id == WorkLine.id,
            WorkLineReview.stage == REVIEW_STAGE_APPROVAL_GROUP))
        .where(
            WorkPortfolio.event_cycle_id == event_cycle_id,
            WorkItem.status.in_(statuses),
            WorkItem.is_archived.is_(False),
            SupplyOrderDetail.closed_at.is_(None),
            SupplyOrderLineDetail.removed_at.is_(None),
        )
        .order_by(SupplyCategory.name, SupplyItem.item_name, Department.name, WorkItem.public_id)
    )
    if category_id is not None:
        query = query.where(SupplyItem.category_id == category_id)
    if item_id is not None:
        query = query.where(SupplyItem.id == item_id)

    rows = []
    for (detail, item_name, unit, category_name, dept_name, dept_code, public_id,
         status, event_code, review_status) in db.session.execute(query).all():
        rows.append(DemandRow(
            item_id=detail.item_id, item_name=item_name, category_name=category_name,
            unit=unit, department_name=dept_name, public_id=public_id,
            event_code=event_code, dept_code=dept_code,
            display_status=_STATUS_LABELS.get(status, status.title()),
            requested=detail.quantity_requested,
            reviewer=reviewer_quantity(
                review_status, detail.quantity_reviewed, detail.quantity_requested),
            final=detail.quantity_approved if status == WORK_ITEM_STATUS_FINALIZED else None,
            confidence_label=detail.quantity_confidence_label,
            notes=detail.requester_notes,
        ))
    return rows


def _sum_or_none(values) -> int | None:
    present = [v for v in values if v is not None]
    return sum(present) if present else None


def summarize_by_item(rows: list[DemandRow]) -> list[ItemDemand]:
    """Collapse rows to one entry per item, sorted by category then item name."""
    grouped: dict[int, list[DemandRow]] = {}
    for row in rows:
        grouped.setdefault(row.item_id, []).append(row)
    summary = [
        ItemDemand(
            item_id=item_id, item_name=group[0].item_name,
            category_name=group[0].category_name, unit=group[0].unit,
            departments=len({r.dept_code for r in group}),
            requested=sum(r.requested for r in group),
            reviewer=_sum_or_none(r.reviewer for r in group),
            final=_sum_or_none(r.final for r in group),
        )
        for item_id, group in grouped.items()
    ]
    return sorted(summary, key=lambda s: (s.category_name, s.item_name))


# Sheets runs a cell that starts with a formula character; csv_cell neutralizes it.
def _csv_response(headers: list[str], records: list[list], filename: str) -> Response:
    from app.routes.work.techops.worklist_csv import csv_cell

    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(headers)
    for record in records:
        writer.writerow([csv_cell(value) for value in record])
    # The byte-order mark makes Excel read the file as UTF-8.
    return Response(
        "﻿" + out.getvalue(),
        mimetype="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _require_report_reader() -> WorkType:
    user_ctx = get_user_ctx()
    supply_wt = WorkType.query.filter_by(code="SUPPLY").first()
    if supply_wt is None:
        abort(404, "Supply work type not configured.")
    if not (is_worktype_admin(user_ctx, supply_wt.id)
            or is_work_type_staffer(user_ctx, supply_wt)):
        abort(403, "Supply report access required.")
    return supply_wt


def _supply_event_options(supply_wt: WorkType) -> list[EventCycle]:
    return (
        db.session.query(EventCycle)
        .join(WorkPortfolio, WorkPortfolio.event_cycle_id == EventCycle.id)
        .filter(WorkPortfolio.work_type_id == supply_wt.id)
        .distinct()
        .order_by(EventCycle.code)
        .all()
    )


def _resolve_event(supply_wt: WorkType, code: str):
    """Return (cycle or None, unknown flag). No code means the default event.

    The default is the cycle marked is_default, else the newest cycle that has
    Supply orders. An unknown code is an error, never a silent fallback.
    """
    if code:
        cycle = EventCycle.query.filter_by(code=code).first()
        if cycle is None:
            flash(f"Unknown event: {code}", "error")
        return cycle, cycle is None
    cycle = EventCycle.query.filter(EventCycle.is_default.is_(True)).first()
    if cycle is None:
        cycle = (
            db.session.query(EventCycle)
            .join(WorkPortfolio, WorkPortfolio.event_cycle_id == EventCycle.id)
            .filter(WorkPortfolio.work_type_id == supply_wt.id)
            .order_by(EventCycle.id.desc())
            .first()
        )
    return cycle, False


def _filters() -> dict:
    return {
        "category_id": request.args.get("category", type=int),
        "include_drafts": request.args.get("drafts") == "1",
    }


def _carry(event_code: str | None, category_id: int | None, include_drafts: bool) -> dict:
    args = {}
    if event_code:
        args["event"] = event_code
    if category_id is not None:
        args["category"] = category_id
    if include_drafts:
        args["drafts"] = 1
    return args


@work_bp.get("/admin/supply/reports/demand")
def supply_report_demand():
    """Per-item demand totals for one event."""
    supply_wt = _require_report_reader()
    filters = _filters()
    cycle, unknown = _resolve_event(supply_wt, (request.args.get("event") or "").strip())
    rows = []
    if cycle is not None:
        rows = demand_lines(cycle.id, **filters)
    items = summarize_by_item(rows)
    carry = _carry(cycle.code if cycle else None, filters["category_id"],
                   filters["include_drafts"])

    if request.args.get("format") == "csv" and cycle is not None:
        records = [[cycle.code, i.category_name, i.item_name, i.unit, i.departments,
                    i.requested, i.reviewer, i.final] for i in items]
        return _csv_response(
            ["Event", "Category", "Item", "Unit", "Departments", "Requested", "Reviewer", "Final"],
            records, f"supply-demand-{cycle.code}.csv")

    return render_template(
        "supply/report_demand.html",
        items=items, cycle=cycle, carry=carry, selected_event=cycle.code if cycle else "",
        event_options=_supply_event_options(supply_wt),
        categories=SupplyCategory.query.order_by(SupplyCategory.name).all(),
        category_id=filters["category_id"], include_drafts=filters["include_drafts"],
        unknown_event=unknown,
    )


@work_bp.get("/admin/supply/reports/item/<int:item_id>")
def supply_report_item(item_id: int):
    """Every department's quantity and notes for one item in one event."""
    supply_wt = _require_report_reader()
    item = db.session.get(SupplyItem, item_id)
    if item is None:
        abort(404)
    filters = _filters()
    cycle, unknown = _resolve_event(supply_wt, (request.args.get("event") or "").strip())
    rows = []
    if cycle is not None:
        rows = demand_lines(cycle.id, include_drafts=filters["include_drafts"], item_id=item_id)
    carry = _carry(cycle.code if cycle else None, filters["category_id"],
                   filters["include_drafts"])

    if request.args.get("format") == "csv" and cycle is not None:
        records = [[r.event_code, r.item_name, r.unit, r.department_name, r.public_id,
                    r.display_status, r.requested, r.reviewer, r.final,
                    r.confidence_label, r.notes] for r in rows]
        return _csv_response(
            ["Event", "Item", "Unit", "Department", "Order", "Status", "Requested",
             "Reviewer", "Final", "Confidence", "Notes"],
            records, f"supply-item-{item_id}-{cycle.code}.csv")

    return render_template(
        "supply/report_item.html",
        item=item, rows=rows, cycle=cycle, carry=carry,
        total=sum(r.requested for r in rows), unknown_event=unknown,
    )
