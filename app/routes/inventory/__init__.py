"""
Inventory: FestOps containers, stock, and the admin pages that manage them.

Sits outside app/routes/admin/ because every route there requires
SUPER_ADMIN, and inventory pages are run by INVENTORY_ADMIN leads who hold no
other admin role.
"""
from __future__ import annotations

from flask import Blueprint

inventory_bp = Blueprint("inventory", __name__, url_prefix="/inv")

from . import (  # noqa: E402,F401  (registers routes)
    admin_checklists, allocation_pages, allocation_report,admin_containers, admin_devices, admin_flags, admin_home, admin_item_page, admin_items, admin_kinds,
    admin_legacy_import, admin_where,
    admin_labels, admin_movements, admin_operators, admin_printers, admin_supplies, admin_units,
    my_sign_in, print_station, scan_api, scan_move, scan_pages, scan_unit,
)
