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
from app.models import User, UserRole, constants


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
