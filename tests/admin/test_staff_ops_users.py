"""Route-level tests: Staff Ops access to /admin/config/users.

Per feedback_test_admin_not_dev_admin: use session login with id "test:admin"
(matching the test fixture pattern in conftest.py), NOT the deprecated
authenticated_client fixture.

URL prefix is `/admin/config/users/` — see app/routes/admin/__init__.py:24
(`admin_config_bp` mounts at `/admin/config`) plus users.py:34
(`users_bp` mounts at `/users`).

Form fields use `"1"` for booleans, not `"on"` — see users.py:186 and :323.
"""
import pytest

from app import db
from app.models import User, UserRole, constants, SecurityAuditLog


def _seed_staff_ops(client, db_session):
    """Create a Staff-Ops-only test user and log them in via session.

    Uses session cookie directly (no dev-login route involved) — matches
    the pattern used by conftest.py's `authenticated_client` fixture and
    permitted by `require_user_admin`/`require_super_admin` (they check
    session['active_user_id'] is non-empty).
    """
    staff = User(
        id="test:staff_ops",
        email="staff_ops@test.local",
        display_name="Staff Ops Tester",
        is_active=True,
    )
    db.session.add(staff)
    db.session.add(UserRole(user_id=staff.id, role_code=constants.ROLE_STAFF_OPS))
    db.session.commit()
    with client.session_transaction() as sess:
        sess["active_user_id"] = staff.id
    return staff


def _seed_super_admin(client, db_session):
    """Create a Super-Admin test user and log them in."""
    admin = User(
        id="test:admin",
        email="admin@test.local",
        display_name="Admin",
        is_active=True,
    )
    db.session.add(admin)
    db.session.add(UserRole(user_id=admin.id, role_code=constants.ROLE_SUPER_ADMIN))
    db.session.commit()
    with client.session_transaction() as sess:
        sess["active_user_id"] = admin.id
    return admin


def test_staff_ops_can_list_users(client, db_session):
    _seed_staff_ops(client, db_session)
    resp = client.get("/admin/config/users/")
    assert resp.status_code == 200


def test_staff_ops_can_get_create_user_form(client, db_session):
    _seed_staff_ops(client, db_session)
    resp = client.get("/admin/config/users/new")
    assert resp.status_code == 200


def test_staff_ops_can_create_user_without_roles(client, db_session):
    """Staff Ops creates a user; submitted role checkbox is ignored."""
    _seed_staff_ops(client, db_session)
    resp = client.post(
        "/admin/config/users/",
        data={
            "email": "newuser@test.local",
            "display_name": "New User",
            "is_active": "1",
            "role_super_admin": "1",   # try to grant super admin — should be ignored
        },
        follow_redirects=False,
    )
    assert resp.status_code in (200, 302)
    created = db.session.query(User).filter_by(email="newuser@test.local").first()
    assert created is not None
    # Confirm NO roles were granted (role_super_admin was ignored)
    roles = db.session.query(UserRole).filter_by(user_id=created.id).all()
    assert roles == [], f"Expected no roles, got {[r.role_code for r in roles]}"


def test_staff_ops_update_ignores_submitted_email(client, db_session):
    """Even if Staff Ops POSTs email=new@x.com, backend must NOT update it."""
    _seed_staff_ops(client, db_session)
    victim = User(
        id="victim:email", email="original@x.com",
        display_name="Victim", is_active=True,
    )
    db.session.add(victim)
    db.session.commit()

    resp = client.post(
        f"/admin/config/users/{victim.id}",
        data={
            "email": "hacked@x.com",
            "display_name": "Renamed",
            "is_active": "1",
        },
        follow_redirects=False,
    )
    assert resp.status_code in (200, 302)

    db.session.refresh(victim)
    # Email NOT changed
    assert victim.email == "original@x.com"
    # display_name WAS changed (allowed for Staff Ops)
    assert victim.display_name == "Renamed"


def test_staff_ops_update_ignores_submitted_role_fields(client, db_session):
    """Staff Ops cannot grant roles even if form fields are posted."""
    _seed_staff_ops(client, db_session)
    victim = User(
        id="victim:role", email="role@x.com",
        display_name="V Role", is_active=True,
    )
    db.session.add(victim)
    db.session.commit()

    resp = client.post(
        f"/admin/config/users/{victim.id}",
        data={
            "email": "role@x.com",        # keep email the same (avoid validation error)
            "display_name": "V Role",
            "is_active": "1",
            "role_super_admin": "1",       # try to grant super admin
        },
        follow_redirects=False,
    )
    assert resp.status_code in (200, 302)

    # Verify NO roles granted
    roles = db.session.query(UserRole).filter_by(user_id=victim.id).all()
    assert roles == [], f"Expected no roles, got {[r.role_code for r in roles]}"


def test_staff_ops_can_archive_user(client, db_session):
    _seed_staff_ops(client, db_session)
    victim = User(
        id="victim:arch", email="arch@x.com",
        display_name="V Arch", is_active=True,
    )
    db.session.add(victim)
    db.session.commit()

    resp = client.post(
        f"/admin/config/users/{victim.id}/archive",
        follow_redirects=False,
    )
    assert resp.status_code in (200, 302)
    db.session.refresh(victim)
    assert victim.is_active is False


def test_super_admin_email_change_still_works(client, db_session):
    """Regression: Super Admin can still update email."""
    _seed_super_admin(client, db_session)
    victim = User(
        id="victim:supadm", email="orig3@x.com",
        display_name="V3", is_active=True,
    )
    db.session.add(victim)
    db.session.commit()

    resp = client.post(
        f"/admin/config/users/{victim.id}",
        data={
            "email": "new3@x.com",
            "display_name": "V3",
            "is_active": "1",
        },
        follow_redirects=False,
    )
    assert resp.status_code in (200, 302)
    db.session.refresh(victim)
    assert victim.email == "new3@x.com"


def test_super_admin_can_still_grant_roles(client, db_session):
    """Regression: Super Admin can still grant roles via the form."""
    _seed_super_admin(client, db_session)
    victim = User(
        id="victim:roles", email="roles@x.com",
        display_name="V Roles", is_active=True,
    )
    db.session.add(victim)
    db.session.commit()

    resp = client.post(
        f"/admin/config/users/{victim.id}",
        data={
            "email": "roles@x.com",
            "display_name": "V Roles",
            "is_active": "1",
            "role_super_admin": "1",
        },
        follow_redirects=False,
    )
    assert resp.status_code in (200, 302)

    role_codes = [
        r.role_code for r in
        db.session.query(UserRole).filter_by(user_id=victim.id).all()
    ]
    assert "SUPER_ADMIN" in role_codes


# ============================================================
# Task 4b: can_modify_user guard tests
#
# Closes security gap from Task 4: Staff Ops could mutate Super Admin
# records (display_name, is_active, archive, restore). Only email and role
# grants were previously gated. Concrete attack: Staff Ops archives the
# sole Super Admin -> org locked out of role assignment.
#
# Guard rules (app/routes/admin/helpers.py:can_modify_user):
#   - Super Admin: yes, including self.
#   - Staff Ops:   yes, EXCEPT self OR target holds SUPER_ADMIN.
#   - Anyone else: no.
# ============================================================


def _seed_super_admin_target(email="target_sa@x.com", user_id="target:sa"):
    """Create an unauthenticated Super Admin user as a mutation target."""
    target = User(
        id=user_id, email=email,
        display_name="Target SuperAdmin", is_active=True,
    )
    db.session.add(target)
    db.session.add(UserRole(user_id=target.id, role_code=constants.ROLE_SUPER_ADMIN))
    db.session.commit()
    return target


def test_staff_ops_cannot_update_super_admin(client, db_session):
    """Staff Ops POSTing /update against a Super Admin target -> 403, row unchanged."""
    _seed_staff_ops(client, db_session)
    target = _seed_super_admin_target()

    resp = client.post(
        f"/admin/config/users/{target.id}",
        data={
            "email": "target_sa@x.com",
            "display_name": "Hacked Name",
            "is_active": "1",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 403

    db.session.refresh(target)
    assert target.display_name == "Target SuperAdmin"
    assert target.is_active is True


def test_staff_ops_cannot_archive_super_admin(client, db_session):
    """Staff Ops POSTing /archive against a Super Admin target -> 403, is_active unchanged."""
    _seed_staff_ops(client, db_session)
    target = _seed_super_admin_target(email="archtgt@x.com", user_id="target:arch_sa")
    assert target.is_active is True

    resp = client.post(
        f"/admin/config/users/{target.id}/archive",
        follow_redirects=False,
    )
    assert resp.status_code == 403

    db.session.refresh(target)
    assert target.is_active is True


def test_staff_ops_cannot_restore_super_admin(client, db_session):
    """Staff Ops POSTing /restore against an archived Super Admin -> 403, is_active unchanged."""
    _seed_staff_ops(client, db_session)
    target = User(
        id="target:rest_sa", email="resttgt@x.com",
        display_name="Archived SA", is_active=False,
    )
    db.session.add(target)
    db.session.add(UserRole(user_id=target.id, role_code=constants.ROLE_SUPER_ADMIN))
    db.session.commit()

    resp = client.post(
        f"/admin/config/users/{target.id}/restore",
        follow_redirects=False,
    )
    assert resp.status_code == 403

    db.session.refresh(target)
    assert target.is_active is False


def test_staff_ops_cannot_modify_self(client, db_session):
    """Staff Ops cannot update, archive, or restore their own user record."""
    staff = _seed_staff_ops(client, db_session)

    # update
    resp = client.post(
        f"/admin/config/users/{staff.id}",
        data={
            "email": staff.email,
            "display_name": "Self Rename",
            "is_active": "1",
        },
        follow_redirects=False,
    )
    assert resp.status_code == 403

    # archive
    resp = client.post(
        f"/admin/config/users/{staff.id}/archive",
        follow_redirects=False,
    )
    assert resp.status_code == 403

    # Now flip to inactive via direct DB so restore is meaningful.
    staff.is_active = False
    db.session.commit()

    # restore
    resp = client.post(
        f"/admin/config/users/{staff.id}/restore",
        follow_redirects=False,
    )
    assert resp.status_code == 403

    db.session.refresh(staff)
    assert staff.display_name == "Staff Ops Tester"
    assert staff.is_active is False  # archive call was blocked, then we set it False manually


def test_staff_ops_can_modify_regular_user(client, db_session):
    """Staff Ops can update, archive, and restore a target with no SUPER_ADMIN role."""
    _seed_staff_ops(client, db_session)
    target = User(
        id="target:regular", email="reg@x.com",
        display_name="Regular Target", is_active=True,
    )
    db.session.add(target)
    db.session.commit()

    # update display_name
    resp = client.post(
        f"/admin/config/users/{target.id}",
        data={
            "email": "reg@x.com",
            "display_name": "Renamed Regular",
            "is_active": "1",
        },
        follow_redirects=False,
    )
    assert resp.status_code in (200, 302)
    db.session.refresh(target)
    assert target.display_name == "Renamed Regular"

    # archive
    resp = client.post(
        f"/admin/config/users/{target.id}/archive",
        follow_redirects=False,
    )
    assert resp.status_code in (200, 302)
    db.session.refresh(target)
    assert target.is_active is False

    # restore
    resp = client.post(
        f"/admin/config/users/{target.id}/restore",
        follow_redirects=False,
    )
    assert resp.status_code in (200, 302)
    db.session.refresh(target)
    assert target.is_active is True


def test_super_admin_can_modify_super_admin(client, db_session):
    """Escape-hatch: Super Admin can update, archive, and restore another Super Admin."""
    _seed_super_admin(client, db_session)  # actor is "test:admin", a SUPER_ADMIN
    target = _seed_super_admin_target(email="peer_sa@x.com", user_id="target:peer_sa")

    # update display_name
    resp = client.post(
        f"/admin/config/users/{target.id}",
        data={
            "email": "peer_sa@x.com",
            "display_name": "Peer Renamed",
            "is_active": "1",
        },
        follow_redirects=False,
    )
    assert resp.status_code in (200, 302)
    db.session.refresh(target)
    assert target.display_name == "Peer Renamed"

    # archive
    resp = client.post(
        f"/admin/config/users/{target.id}/archive",
        follow_redirects=False,
    )
    assert resp.status_code in (200, 302)
    db.session.refresh(target)
    assert target.is_active is False

    # restore
    resp = client.post(
        f"/admin/config/users/{target.id}/restore",
        follow_redirects=False,
    )
    assert resp.status_code in (200, 302)
    db.session.refresh(target)
    assert target.is_active is True


# ============================================================
# Task 5: Page-access auditing
#
# - GET /admin/config/users/         -> SecurityAuditLog row (USER_VIEW, ADMIN)
# - GET /admin/config/users/<id>     -> SecurityAuditLog row with target_user_id
#
# Note: rendered-HTML test for the STAFF_OPS role checkbox is deferred to
# Task 6 (form template work). _get_role_context() includes the entry, but
# the current form.html hard-codes checkboxes and does not iterate
# role_codes — adding the checkbox here would create a dead UI until the
# Task 6 wiring lands.
# ============================================================


def test_staff_ops_user_list_writes_audit_row(client, db_session):
    """GET /admin/config/users/ as Staff Ops writes a USER_VIEW audit row."""
    staff = _seed_staff_ops(client, db_session)

    resp = client.get("/admin/config/users/")
    assert resp.status_code == 200

    rows = (
        db.session.query(SecurityAuditLog)
        .filter(
            SecurityAuditLog.event_type == "USER_VIEW",
            SecurityAuditLog.event_category == "ADMIN",
            SecurityAuditLog.user_id == staff.id,
        )
        .all()
    )
    assert len(rows) >= 1, (
        f"Expected at least one USER_VIEW audit row for actor {staff.id}, "
        f"got {[(r.event_type, r.user_id) for r in rows]}"
    )


def test_staff_ops_user_edit_get_writes_audit_row(client, db_session):
    """GET /admin/config/users/<id> as Staff Ops writes a USER_VIEW row with target_user_id."""
    import json

    staff = _seed_staff_ops(client, db_session)
    victim = User(
        id="victim:audit", email="audit@x.com",
        display_name="Audit Victim", is_active=True,
    )
    db.session.add(victim)
    db.session.commit()

    resp = client.get(f"/admin/config/users/{victim.id}")
    assert resp.status_code == 200

    rows = (
        db.session.query(SecurityAuditLog)
        .filter(
            SecurityAuditLog.event_type == "USER_VIEW",
            SecurityAuditLog.event_category == "ADMIN",
            SecurityAuditLog.user_id == staff.id,
        )
        .all()
    )
    # Find the row whose details JSON references our victim
    matching = [
        r for r in rows
        if r.details and json.loads(r.details).get("target_user_id") == victim.id
    ]
    assert len(matching) >= 1, (
        f"Expected at least one USER_VIEW audit row with target_user_id={victim.id}; "
        f"got details={[r.details for r in rows]}"
    )


