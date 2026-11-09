"""Inventory landing page: tiles counted from a chosen date, then links to every page."""
from flask import request

from app.inventory.last_check import check_totals, parse_since, since_start
from app.inventory.printing import local_day, queue_summary

from . import inventory_bp
from .admin_flags import open_flag_count
from .helpers import render_inventory_page, require_inventory_operator


@inventory_bp.get("/")
@require_inventory_operator
def home():
    day, fell_back = parse_since(request.args.get("since"))
    start = since_start(day)
    return render_inventory_page(
        "inventory/home.html", open_flags=open_flag_count(), totals=check_totals(start),
        since=day.isoformat(), since_word=local_day(start), fell_back=fell_back,
        queue=queue_summary())
