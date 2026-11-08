"""Inventory landing page: this week's tiles, then links to every page the user can use."""
from app.inventory.cards import default_event
from app.inventory.printing import queue_summary
from app.inventory.week import week_totals

from . import inventory_bp
from .admin_flags import open_flag_count
from .helpers import render_inventory_page, require_inventory_operator


@inventory_bp.get("/")
@require_inventory_operator
def home():
    event = default_event()
    return render_inventory_page(
        "inventory/home.html", open_flags=open_flag_count(),
        totals=week_totals(event) if event else None, queue=queue_summary())
