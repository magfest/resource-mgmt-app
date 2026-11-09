"""What each role may see and change on an item's page.

Supply admins own ordering; inventory admins own tracking and stock; both may
edit the shared fields. Every item route checks these rights on the server.
"""
from __future__ import annotations

from dataclasses import dataclass

from app.models import ROLE_WORKTYPE_ADMIN, UserRole, WorkType


def is_supply_admin(user_ctx) -> bool:
    """Super admin, or WORKTYPE_ADMIN on SUPPLY; False when SUPPLY does not exist."""
    if user_ctx.is_super_admin:
        return True
    supply = WorkType.query.filter_by(code="SUPPLY").first()
    if supply is None:
        return False
    return UserRole.query.filter_by(
        user_id=user_ctx.user_id, role_code=ROLE_WORKTYPE_ADMIN,
        work_type_id=supply.id,
    ).first() is not None


@dataclass(frozen=True)
class ItemRights:
    can_view: bool
    can_edit_shared: bool
    can_set_orderable: bool
    can_set_tracked: bool
    sees_ordering: bool
    can_edit_ordering: bool
    sees_inventory: bool
    can_edit_inventory: bool
    sees_stock: bool


def item_rights(user_ctx) -> ItemRights:
    # Imported here: the routes helpers import this module for require_item_viewer.
    from app.routes.inventory.helpers import is_inventory_admin, is_inventory_operator

    supply = is_supply_admin(user_ctx)
    admin = is_inventory_admin(user_ctx)
    op = is_inventory_operator(user_ctx)
    return ItemRights(
        can_view=supply or op,
        can_edit_shared=supply or admin,
        can_set_orderable=supply,
        can_set_tracked=admin,
        sees_ordering=supply,
        can_edit_ordering=supply,
        sees_inventory=op,
        can_edit_inventory=admin,
        sees_stock=op,
    )
