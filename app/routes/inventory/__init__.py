"""
Inventory: FestOps containers, stock, and the admin pages that manage them.

Sits outside app/routes/admin/ because every route there requires
SUPER_ADMIN, and inventory pages are run by INVENTORY_ADMIN leads who hold no
other admin role.
"""
from __future__ import annotations

from flask import Blueprint

inventory_bp = Blueprint("inventory", __name__, url_prefix="/inv")

from . import admin_containers, admin_home, admin_items, admin_kinds, admin_movements, admin_operators  # noqa: E402,F401  (registers routes)
