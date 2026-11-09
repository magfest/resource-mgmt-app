"""One page per item, with tabs shown by what the viewer may see.

The rights live in app/inventory/item_rights.py; this module only reads them.
"""
from flask import abort, request

from app import db
from app.inventory.item_rights import item_rights
from app.inventory.stock_report import supplies_on_hand
from app.models import InvUnit, SupplyItem
from app.routes import get_user_ctx

from . import inventory_bp
from .helpers import render_inventory_page, require_item_viewer


def _status_line(item, rights) -> str:
    parts = ["Orderable" if item.is_orderable else "Not orderable",
             "Tracked as assets" if item.is_serialized else "Counted"]
    if rights.sees_stock:
        if item.is_serialized:
            count = db.session.query(InvUnit).filter_by(item_id=item.id).count()
            parts.append(f"{count} asset{'' if count == 1 else 's'}")
        else:
            row = next((r for r in supplies_on_hand() if r["item_id"] == item.id), None)
            parts.append(f"{row['total'] if row else 0} on hand")
    return " · ".join(parts)


@inventory_bp.get("/admin/items/<int:item_id>")
@require_item_viewer
def item_page(item_id: int):
    item = db.session.get(SupplyItem, item_id)
    if item is None:
        abort(404)
    rights = item_rights(get_user_ctx())
    tracked = bool(item.is_serialized)
    tabs = [
        {"id": "about", "label": "About"},
        {"id": "ordering", "label": "Ordering",
         "when": rights.sees_ordering and item.is_orderable},
        {"id": "inventory", "label": "Inventory",
         "when": rights.sees_inventory and tracked},
        {"id": "assets", "label": "Assets",
         "when": rights.sees_inventory and tracked},
        {"id": "on_hand", "label": "On hand",
         "when": rights.sees_stock and not tracked},
    ]
    return render_inventory_page(
        "inventory/item.html", item=item, rights=rights, tabs=tabs,
        status_line=_status_line(item, rights), active_tab=request.args.get("tab"))
