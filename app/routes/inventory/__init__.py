"""
Inventory: FestOps containers, stock, labels, and the TC60 scanner.

Sits outside app/routes/admin/ because every route there requires
SUPER_ADMIN, and inventory pages are run by INVENTORY_ADMIN leads who hold no
other admin role. Scanner routes live under /inv/scan/ and are the only
routes that honor a shared device's cookie.
"""
from __future__ import annotations

from flask import Blueprint

inventory_bp = Blueprint("inventory", __name__, url_prefix="/inv")

from . import admin_home, admin_items, admin_kinds, admin_operators  # noqa: E402,F401  (registers routes)
