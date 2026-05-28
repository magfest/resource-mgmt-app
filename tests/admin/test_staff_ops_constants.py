"""Tests for STAFF_OPS-related constants."""
from app.models import constants


def test_role_staff_ops_constant_exists():
    """STAFF_OPS role code must exist and have the expected string value."""
    assert hasattr(constants, "ROLE_STAFF_OPS")
    assert constants.ROLE_STAFF_OPS == "STAFF_OPS"


def test_config_audit_delete_constant_exists():
    """DELETE action for ConfigAuditEvent must exist."""
    assert hasattr(constants, "CONFIG_AUDIT_DELETE")
    assert constants.CONFIG_AUDIT_DELETE == "DELETE"
