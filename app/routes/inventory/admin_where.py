"""Where things are: the container tree with its units and supplies, searchable on one page."""
from app.inventory.cards import default_event
from app.inventory.where import build_where

from . import inventory_bp
from .helpers import render_inventory_page, require_inventory_operator


@inventory_bp.get("/admin/where")
@require_inventory_operator
def where():
    return render_inventory_page("inventory/where.html", tree=build_where(default_event()))
