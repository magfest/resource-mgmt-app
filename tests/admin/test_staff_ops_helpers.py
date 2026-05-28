"""Unit tests for STAFF_OPS permission helpers.

These tests construct UserContext directly (no Flask client, no DB) — the
helpers are pure functions of user_ctx so we don't need request plumbing.
"""
from app.routes import UserContext
from app.models import constants
from app.routes.admin import helpers as admin_helpers


def _make_ctx(user_id="test:helper", *role_codes):
    """Build a UserContext for a hypothetical user with the given roles.

    Note: `UserContext.roles` is `tuple[str, ...]` of role-code strings
    per app/routes/__init__.py:67. `is_super_admin` is precomputed at
    construction time per app/routes/__init__.py:68.
    """
    is_super_admin = constants.ROLE_SUPER_ADMIN in role_codes
    is_staff_ops = is_super_admin or constants.ROLE_STAFF_OPS in role_codes
    return UserContext(
        user_id=user_id,
        user=None,
        roles=tuple(role_codes),
        is_super_admin=is_super_admin,
        is_staff_ops=is_staff_ops,
        approval_group_ids=set(),
    )


def test_is_staff_ops_true_for_staff_ops_role():
    ctx = _make_ctx("u1", constants.ROLE_STAFF_OPS)
    assert admin_helpers.is_staff_ops(ctx) is True


def test_is_staff_ops_true_for_super_admin_via_cascade():
    ctx = _make_ctx("u1", constants.ROLE_SUPER_ADMIN)
    assert admin_helpers.is_staff_ops(ctx) is True


def test_is_staff_ops_false_for_approver():
    ctx = _make_ctx("u1", constants.ROLE_APPROVER)
    assert admin_helpers.is_staff_ops(ctx) is False


def test_is_staff_ops_false_for_no_roles():
    ctx = _make_ctx("u1")
    assert admin_helpers.is_staff_ops(ctx) is False


def test_can_manage_users_true_for_staff_ops():
    ctx = _make_ctx("u1", constants.ROLE_STAFF_OPS)
    assert admin_helpers.can_manage_users(ctx) is True


def test_can_manage_users_false_for_approver_only():
    ctx = _make_ctx("u1", constants.ROLE_APPROVER)
    assert admin_helpers.can_manage_users(ctx) is False


def test_can_assign_roles_only_super_admin():
    staff = _make_ctx("u1", constants.ROLE_STAFF_OPS)
    super_admin = _make_ctx("u2", constants.ROLE_SUPER_ADMIN)
    assert admin_helpers.can_assign_roles(staff) is False
    assert admin_helpers.can_assign_roles(super_admin) is True


def test_can_edit_user_identity_only_super_admin():
    """Staff Ops cannot edit email field regardless of target."""
    staff = _make_ctx("u1", constants.ROLE_STAFF_OPS)
    assert admin_helpers.can_edit_user_identity(staff, "any_target_id") is False


def test_can_edit_user_identity_true_for_super_admin():
    super_admin = _make_ctx("u1", constants.ROLE_SUPER_ADMIN)
    assert admin_helpers.can_edit_user_identity(super_admin, "any_target_id") is True


def test_can_manage_membership_blocks_self_for_staff_ops():
    """The headline invariant: Staff Ops cannot mutate own memberships."""
    ctx = _make_ctx("u1", constants.ROLE_STAFF_OPS)
    assert admin_helpers.can_manage_membership(ctx, "u1") is False


def test_can_manage_membership_allows_other_for_staff_ops():
    ctx = _make_ctx("u1", constants.ROLE_STAFF_OPS)
    assert admin_helpers.can_manage_membership(ctx, "other_user_id") is True


def test_can_manage_membership_allows_self_for_super_admin():
    """Super Admin escape hatch — can self-modify."""
    ctx = _make_ctx("u1", constants.ROLE_SUPER_ADMIN)
    assert admin_helpers.can_manage_membership(ctx, "u1") is True


def test_can_manage_membership_false_for_unprivileged():
    """A user with no relevant roles cannot manage membership at all."""
    ctx = _make_ctx("u1", constants.ROLE_APPROVER)
    assert admin_helpers.can_manage_membership(ctx, "other") is False


def test_user_context_has_is_staff_ops_attribute():
    """UserContext exposes is_staff_ops as a boolean field (parallel to is_super_admin)."""
    ctx = _make_ctx("u1", constants.ROLE_STAFF_OPS)
    assert ctx.is_staff_ops is True


def test_user_context_is_staff_ops_false_for_no_role():
    ctx = _make_ctx("u1")
    assert ctx.is_staff_ops is False


def test_user_context_is_staff_ops_true_for_super_admin_cascade():
    """Cascade: a Super Admin always satisfies is_staff_ops as a field too."""
    ctx = _make_ctx("u1", constants.ROLE_SUPER_ADMIN)
    assert ctx.is_staff_ops is True
