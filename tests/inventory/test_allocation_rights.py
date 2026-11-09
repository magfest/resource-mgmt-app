"""Who may allocate assets and who may read allocations."""
import pytest

from app import db
from app.inventory.allocation_rights import (
    can_allocate, can_read_allocations, is_techops_admin,
)
from app.models import User, UserRole
from app.routes import UserContext


def ctx_for(user_id):
    roles = tuple(r.role_code for r in UserRole.query.filter_by(user_id=user_id))
    return UserContext(
        user_id=user_id, user=db.session.get(User, user_id), roles=roles,
        is_super_admin="SUPER_ADMIN" in roles, approval_group_ids=set(),
    )


@pytest.mark.parametrize("user_id, allocates, reads", [
    ("test:admin", True, True), ("test:invadmin", True, True),
    ("test:supplyadmin", True, True), ("test:techopsadmin", True, True),
    ("test:operator", False, True), ("test:nobody", False, False),
])
def test_who_allocates_and_who_reads(alloc, user_id, allocates, reads):
    ctx = ctx_for(user_id)
    assert can_allocate(ctx) is allocates and can_read_allocations(ctx) is reads


def test_no_techops_work_type_means_no_techops_admin(inv):
    assert is_techops_admin(ctx_for("test:invadmin")) is False
