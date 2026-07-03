"""
Hotel Rooms Report - overview of all hotel room lines, grouped by pay type.

Hotel rooms have no dedicated model; they are fixed-cost expense accounts with
ui_display_group == "HOTEL_SERVICES" and codes shaped HTL_{ROOM}_{SCENARIO}
(e.g. HTL_EXEC_MAGPAID). Room type and pay type are derived from the code, the
same convention the hotel wizard uses to build the codes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from flask import render_template, abort

from app import db
from app.models import (
    BudgetLineDetail, WorkLine, WorkItem, WorkPortfolio,
    ExpenseAccount, Department, UI_GROUP_HOTEL_SERVICES,
)
from app.routes import get_user_ctx
from app.routes.work.helpers import format_currency
from . import admin_final_bp
from .helpers import (
    require_budget_admin, get_active_event_cycles, get_active_departments,
)
from .report_utils import resolve_report_filters, compute_line_amount_cents
from .report_exports import (
    make_csv_response, format_currency_csv, generate_timestamp_filename,
)

ROOM_TYPE_LABELS = {"STD": "Standard", "EXEC": "Executive", "HOSP": "Hospitality"}
PAY_TYPE_LABELS = {
    "MAGPAID": "MAGFest-paid",
    "HELD": "Third-party held",
    "CRASH": "Self-paid suite for staff",
}
# Display order for pay-type groups; budget-hitting first.
PAY_TYPE_ORDER = ["MAGPAID", "HELD", "CRASH"]

# The hotel wizard prepends "N rooms: " to the description when room_count > 1
# (app/routes/work/work_items/edit.py:902). Room count and nights are not stored
# separately — the line's quantity is total room-nights (rooms * nights). We
# recover the room count from this prefix (default 1) and derive nights as
# room-nights / rooms.
_ROOMS_PREFIX_RE = re.compile(r"^\s*(\d+)\s+rooms:", re.IGNORECASE)


def parse_hotel_account_code(code: str):
    """Return (room_type_label, pay_type_label, pay_type_key) from an HTL code."""
    parts = (code or "").split("_")
    room_key = parts[1] if len(parts) > 1 else ""
    pay_key = parts[2] if len(parts) > 2 else ""
    room_label = ROOM_TYPE_LABELS.get(room_key, room_key or "Unknown")
    pay_label = PAY_TYPE_LABELS.get(pay_key, pay_key or "Other")
    return room_label, pay_label, (pay_key or "OTHER")


def parse_room_count(description) -> int:
    """
    Room count from the wizard's "N rooms: ..." description prefix; default 1.

    Single-room lines have no prefix, so 1 is the correct default. This is a
    best-effort recovery: if a requester hand-edited the description and removed
    the prefix, a multi-room line will read as 1 room.
    """
    if not description:
        return 1
    m = _ROOMS_PREFIX_RE.match(description)
    if m:
        n = int(m.group(1))
        return n if n > 0 else 1
    return 1

def _to_int(quantity) -> int:
    try:
        return int(quantity or 0)
    except (TypeError, ValueError):
        return 0

def derive_rooms_and_nights(quantity, description):
    """
    Return (rooms, nights, room_nights) for a hotel line.

    room_nights is the stored quantity (rooms * nights). rooms is recovered from
    the description prefix; nights = room_nights / rooms, shown as an int when it
    divides evenly (the normal wizard case) and rounded to 1 decimal otherwise.
    """
    room_nights = _to_int(quantity)
    rooms = parse_room_count(description)
    if rooms <= 0:
        rooms = 1
    if room_nights % rooms == 0:
        nights = room_nights // rooms
    else:
        nights = round(room_nights / rooms, 1)
    return rooms, nights, room_nights


@dataclass
class HotelRoomLineRow:
    department_name: str
    work_item_id: int
    work_item_public_id: str
    line_number: int
    account_code: str
    room_type: str
    pay_type: str
    pay_type_key: str
    rooms: int
    nights: object
    room_nights: int
    unit_price_cents: int
    total_cents: int
    line_status: str


def get_hotel_rooms_data(
    event_cycle_id: int, department_id: Optional[int] = None
) -> List[HotelRoomLineRow]:
    q = (
        db.session.query(
            Department.name.label("department_name"),
            WorkItem.id.label("work_item_id"),
            WorkItem.public_id.label("work_item_public_id"),
            WorkLine.line_number.label("line_number"),
            WorkLine.status.label("line_status"),
            ExpenseAccount.code.label("account_code"),
            BudgetLineDetail.quantity.label("quantity"),
            BudgetLineDetail.description.label("description"),
            BudgetLineDetail.unit_price_cents.label("unit_price_cents"),
        )
        .select_from(BudgetLineDetail)
        .join(WorkLine, BudgetLineDetail.work_line_id == WorkLine.id)
        .join(WorkItem, WorkLine.work_item_id == WorkItem.id)
        .join(WorkPortfolio, WorkItem.portfolio_id == WorkPortfolio.id)
        .join(Department, WorkPortfolio.department_id == Department.id)
        .join(ExpenseAccount, BudgetLineDetail.expense_account_id == ExpenseAccount.id)
        .filter(WorkPortfolio.event_cycle_id == event_cycle_id)
        .filter(ExpenseAccount.ui_display_group == UI_GROUP_HOTEL_SERVICES)
        .filter(WorkItem.is_archived == False)
        .filter(WorkPortfolio.is_archived == False)
        .order_by(ExpenseAccount.code.asc(), Department.code.asc(), WorkItem.public_id.asc())
    )
    if department_id:
        q = q.filter(WorkPortfolio.department_id == department_id)

    rows = []
    for r in q.all():
        room_type, pay_type, pay_key = parse_hotel_account_code(r.account_code)
        rooms, nights, room_nights = derive_rooms_and_nights(r.quantity, r.description)
        rows.append(
            HotelRoomLineRow(
                department_name=r.department_name,
                work_item_id=r.work_item_id,
                work_item_public_id=r.work_item_public_id,
                line_number=r.line_number,
                account_code=r.account_code,
                room_type=room_type,
                pay_type=pay_type,
                pay_type_key=pay_key,
                rooms=rooms,
                nights=nights,
                room_nights=room_nights,
                unit_price_cents=r.unit_price_cents,
                total_cents=compute_line_amount_cents(r.unit_price_cents, r.quantity),
                line_status=r.line_status,
            )
        )
    return rows





def group_hotel_rows(rows: List[HotelRoomLineRow]) -> List[dict]:
    """Group rows by pay type, ordered, with per-group room + dollar subtotals."""
    by_key = {}
    for r in rows:
        by_key.setdefault(r.pay_type_key, []).append(r)

    # Known pay types in defined order, then any unexpected keys alphabetically.
    ordered_keys = [k for k in PAY_TYPE_ORDER if k in by_key]
    ordered_keys += sorted(k for k in by_key if k not in PAY_TYPE_ORDER)

    groups = []
    for key in ordered_keys:
        group_rows = by_key[key]
        groups.append({
            "pay_type_key": key,
            "pay_type": group_rows[0].pay_type,
            "rows": group_rows,
            "room_subtotal": sum(r.rooms for r in group_rows),
            "room_nights_subtotal": sum(r.room_nights for r in group_rows),
            "cents_subtotal": sum(r.total_cents for r in group_rows),
        })
    return groups


def build_hotel_summary(rows: List[HotelRoomLineRow]) -> dict:
    """
    Cross-tab of room counts by room type (rows) x pay type (columns), with row
    totals (per room type), column totals (per pay type), a grand total, and a
    dollar total per pay-type column.

    Returned shape is template-friendly: `pay_labels` and the per-column lists
    (`col_rooms`, `col_cents`) are all aligned to the same pay-type order, and
    each `matrix_rows` entry's `cells` list is aligned to that same order.
    """
    # Ordered pay types present: known order first, then any unexpected keys.
    present_pay = {r.pay_type_key for r in rows}
    pay_keys = [k for k in PAY_TYPE_ORDER if k in present_pay]
    pay_keys += sorted(k for k in present_pay if k not in PAY_TYPE_ORDER)
    pay_labels = {}
    for r in rows:
        pay_labels.setdefault(r.pay_type_key, r.pay_type)

    # Ordered room types present: known order (STD/EXEC/HOSP labels) then extras.
    known_room_order = list(ROOM_TYPE_LABELS.values())
    present_room = {r.room_type for r in rows}
    room_types = [rt for rt in known_room_order if rt in present_room]
    room_types += sorted(rt for rt in present_room if rt not in known_room_order)

    cell_rooms = {}                       # (room_type, pay_key) -> room count
    col_rooms = {k: 0 for k in pay_keys}
    col_cents = {k: 0 for k in pay_keys}
    row_rooms = {rt: 0 for rt in room_types}
    grand_rooms = 0
    grand_cents = 0
    for r in rows:
        cell_rooms[(r.room_type, r.pay_type_key)] = (
            cell_rooms.get((r.room_type, r.pay_type_key), 0) + r.rooms
        )
        col_rooms[r.pay_type_key] += r.rooms
        col_cents[r.pay_type_key] += r.total_cents
        row_rooms[r.room_type] += r.rooms
        grand_rooms += r.rooms
        grand_cents += r.total_cents

    matrix_rows = [
        {
            "room_type": rt,
            "cells": [cell_rooms.get((rt, k), 0) for k in pay_keys],
            "total": row_rooms[rt],
        }
        for rt in room_types
    ]

    return {
        "pay_labels": [pay_labels[k] for k in pay_keys],
        "matrix_rows": matrix_rows,
        "col_rooms": [col_rooms[k] for k in pay_keys],
        "col_cents": [col_cents[k] for k in pay_keys],
        "grand_rooms": grand_rooms,
        "grand_cents": grand_cents,
    }


@admin_final_bp.get("/admin/budget/hotel-rooms/")
def hotel_rooms_report():
    user_ctx = get_user_ctx()
    require_budget_admin(user_ctx)

    filters = resolve_report_filters()

    groups = []
    summary = None
    if filters.has_event:
        rows = get_hotel_rooms_data(filters.event_cycle_id, filters.department_id)
        groups = group_hotel_rows(rows)
        summary = build_hotel_summary(rows)

    return render_template(
        "admin_final/hotel_rooms_report.html",
        user_ctx=user_ctx,
        groups=groups,
        summary=summary,
        event_cycles=get_active_event_cycles(),
        departments=get_active_departments(),
        selected_event=filters.event_code,
        selected_dept=filters.dept_code,
        selected_event_cycle=filters.event_cycle,
        selected_department=filters.department,
        format_currency=format_currency,
    )


@admin_final_bp.get("/admin/budget/hotel-rooms/export")
def hotel_rooms_report_export():
    user_ctx = get_user_ctx()
    require_budget_admin(user_ctx)

    filters = resolve_report_filters()
    if not filters.has_event:
        abort(400, "Event cycle is required for export")

    rows = get_hotel_rooms_data(filters.event_cycle_id, filters.department_id)

    headers = [
        "Pay Type", "Department", "Request", "Room Type",
        "Rooms", "Nights", "Room-Nights", "Unit Price", "Total", "Status",
    ]
    csv_rows = []
    for r in rows:
        csv_rows.append([
            r.pay_type, r.department_name, r.work_item_public_id, r.room_type,
            r.rooms, r.nights, r.room_nights,
            format_currency_csv(r.unit_price_cents),
            format_currency_csv(r.total_cents), r.line_status,
        ])

    filename = generate_timestamp_filename(
        "hotel_rooms", filters.event_code,
        filters.dept_code if filters.has_department else None,
    )
    return make_csv_response(filename, headers, csv_rows)
