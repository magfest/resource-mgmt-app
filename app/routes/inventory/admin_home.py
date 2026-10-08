"""Inventory landing page: links to every inventory page the user can use."""
from . import inventory_bp
from .helpers import render_inventory_page, require_inventory_operator


@inventory_bp.get("/")
@require_inventory_operator
def home():
    return render_inventory_page("inventory/home.html")
