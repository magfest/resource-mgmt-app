"""item_rights maps a user's roles to what the item page shows and allows."""
from dataclasses import fields

from app.inventory.item_rights import ItemRights, is_supply_admin, item_rights
from app.models import User, UserRole
from app.routes import UserContext


def ctx_for(user_id):
    roles = tuple(r.role_code for r in UserRole.query.filter_by(user_id=user_id))
    return UserContext(
        user_id=user_id, user=User.query.get(user_id), roles=roles,
        is_super_admin="SUPER_ADMIN" in roles, approval_group_ids=set(),
    )


def test_supply_only_admin_sees_ordering_not_inventory(inv, supply_admin):
    r = item_rights(ctx_for("test:supplyadmin"))
    assert r.can_view and r.can_edit_shared and r.can_set_orderable and r.can_edit_ordering
    assert r.sees_ordering
    assert not (r.sees_inventory or r.can_set_tracked or r.sees_stock or r.can_edit_inventory)


def test_inventory_admin_sees_inventory_not_ordering(inv):
    r = item_rights(ctx_for("test:invadmin"))
    assert r.can_edit_shared and r.can_set_tracked and r.can_edit_inventory and r.sees_stock
    assert r.can_view and r.sees_inventory
    assert not (r.sees_ordering or r.can_set_orderable or r.can_edit_ordering)


def test_operator_views_only(inv):
    r = item_rights(ctx_for("test:operator"))
    assert r.can_view and r.sees_inventory and r.sees_stock
    assert not (r.can_edit_shared or r.can_edit_inventory or r.sees_ordering)
    assert not (r.can_set_tracked or r.can_set_orderable or r.can_edit_ordering)


def test_no_supply_work_type_means_no_supply_admin(inv):
    assert is_supply_admin(ctx_for("test:invadmin")) is False


def test_nobody_cannot_view(inv):
    r = item_rights(ctx_for("test:nobody"))
    assert r.can_view is False
    assert not any(getattr(r, f.name) for f in fields(ItemRights))


def test_super_admin_has_every_right(inv):
    r = item_rights(ctx_for("test:admin"))
    assert all(getattr(r, f.name) for f in fields(ItemRights))


def test_supply_admin_does_not_pass_without_the_role(inv, supply_admin):
    assert is_supply_admin(ctx_for("test:supplyadmin")) is True
    assert is_supply_admin(ctx_for("test:operator")) is False
