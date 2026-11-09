"""Deployment report page and its CSV download."""
from flask import request

from app.inventory.csv_export import csv_response
from app.inventory.demand import allocation_events, pick_event
from app.inventory.deployment import GROUPS, deployment_rows, group_rows

from . import inventory_bp
from .allocation_pages import _int
from .helpers import render_inventory_page, require_allocation_reader

_HEADER = ["Department", "Room", "Item or service", "Request", "Line", "What they asked for",
           "Requested", "Approved", "Allocated", "Short", "Assets", "Warnings"]


def _group() -> str:
    group = request.args.get("group")
    return group if group in GROUPS else "department"


@inventory_bp.get("/allocation/report")
@require_allocation_reader
def deployment_report():
    events = allocation_events()
    event = pick_event(events, _int(request.args.get("event")))
    group = _group()
    groups = group_rows(deployment_rows(event.id), group) if event else []
    return render_inventory_page("inventory/deployment.html", events=events, event=event,
                                 group=group, groups=groups, group_names=GROUPS)


@inventory_bp.get("/allocation/report.csv")
@require_allocation_reader
def deployment_csv():
    event = pick_event(allocation_events(), _int(request.args.get("event")))
    rows = deployment_rows(event.id) if event else []
    body = [[r.department, r.room, r.what, r.public_id, r.line_number, r.words, r.requested,
             r.approved, r.allocated, r.short, "; ".join(r.assets), "; ".join(r.warnings)]
            for _, grouped in group_rows(rows, _group()) for r in grouped]
    code = event.code if event else "none"
    return csv_response(f"deployment-{code}.csv", _HEADER, body)
