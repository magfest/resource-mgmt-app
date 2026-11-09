"""Who may allocate assets to order lines, and who may read allocations.

Four roles allocate: super admin, TechOps admin, Supply admin and inventory
admin. Inventory operators read. The routes check these on every write.
"""
from __future__ import annotations

from app.inventory.item_rights import is_supply_admin
from app.models import ROLE_WORKTYPE_ADMIN, UserRole, WorkType


def is_techops_admin(user_ctx) -> bool:
    """Super admin, or WORKTYPE_ADMIN on TECHOPS; False when TECHOPS does not exist."""
    if user_ctx.is_super_admin:
        return True
    techops = WorkType.query.filter_by(code="TECHOPS").first()
    if techops is None:
        return False
    return UserRole.query.filter_by(
        user_id=user_ctx.user_id, role_code=ROLE_WORKTYPE_ADMIN,
        work_type_id=techops.id,
    ).first() is not None


def can_allocate(user_ctx) -> bool:
    # Imported here: the routes helpers import app.inventory modules at load time.
    from app.routes.inventory.helpers import is_inventory_admin

    return (is_techops_admin(user_ctx) or is_inventory_admin(user_ctx)
            or is_supply_admin(user_ctx))


def can_read_allocations(user_ctx) -> bool:
    from app.routes.inventory.helpers import is_inventory_operator

    return can_allocate(user_ctx) or is_inventory_operator(user_ctx)
