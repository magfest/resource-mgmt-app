"""The SPACE_ADMIN boundary.

The last test is the reason this role exists. Event Ops maintains rooms and
must not hold user management, so a space admin has to be refused on an
admin config page.
"""
import pytest

from app import db
from app.models import ROLE_SPACE_ADMIN, ROLE_SUPER_ADMIN, User, UserRole


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


@pytest.fixture
def users(app):
    rows = [
        User(id="test:admin", email="admin@test.local",
             display_name="Test Admin", is_active=True),
        User(id="test:spaceadmin", email="space@test.local",
             display_name="Test Space Admin", is_active=True),
        User(id="test:nobody", email="nobody@test.local",
             display_name="Test Nobody", is_active=True),
    ]
    db.session.add_all(rows)
    db.session.flush()
    db.session.add(UserRole(user_id="test:admin", role_code=ROLE_SUPER_ADMIN))
    db.session.add(UserRole(user_id="test:spaceadmin",
                            role_code=ROLE_SPACE_ADMIN))
    db.session.commit()


def test_space_admin_reaches_the_spaces_page(client, users):
    _login(client, "test:spaceadmin")
    assert client.get("/spaces/").status_code == 200


def test_super_admin_reaches_the_spaces_page(client, users):
    _login(client, "test:admin")
    assert client.get("/spaces/").status_code == 200


def test_a_user_with_no_role_is_refused(client, users):
    _login(client, "test:nobody")
    assert client.get("/spaces/").status_code == 403


def test_space_admin_is_refused_on_user_management(client, users):
    _login(client, "test:spaceadmin")
    assert client.get("/admin/config/users/").status_code == 403


def test_the_event_ops_menu_shows_for_a_space_admin(client, users):
    _login(client, "test:spaceadmin")
    body = client.get("/spaces/").get_data(as_text=True)
    assert "Event Ops" in body


def test_the_event_ops_menu_is_hidden_from_other_users(client, users):
    """Check the menu itself, on a page this user can load. Asserting a 403
    on /spaces/ would test the guard again and say nothing about the nav."""
    _login(client, "test:admin")
    db.session.query(UserRole).filter_by(
        user_id="test:admin", role_code=ROLE_SUPER_ADMIN).delete()
    db.session.add(UserRole(user_id="test:admin", role_code="APPROVER"))
    db.session.commit()

    home = client.get("/")
    assert home.status_code == 200
    assert "Event Ops" not in home.get_data(as_text=True)


def test_saving_the_user_form_keeps_the_space_admin_role(client, users):
    """`_update_user_roles` clears every role and rebuilds it from form
    fields. Before this task, no branch knew about SPACE_ADMIN, so editing
    an unrelated field on a space admin's account silently deleted it."""
    _login(client, "test:admin")
    resp = client.post("/admin/config/users/test:spaceadmin", data={
        "email": "space@test.local",
        "display_name": "Test Space Admin",
        "is_active": "1",
        "role_space_admin": "1",
    }, follow_redirects=True)
    assert resp.status_code == 200

    remaining = db.session.query(UserRole).filter_by(
        user_id="test:spaceadmin", role_code=ROLE_SPACE_ADMIN).all()
    assert len(remaining) == 1
