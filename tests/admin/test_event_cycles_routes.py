"""Event cycle admin form: the venue field wires event_cycles.venue_id.

Nothing else in the app can set this column (see app/routes/spaces/views.py),
so this is the only path that takes the Spaces page out of its empty state.
"""
from app import db
from app.models import EventCycle, ROLE_SUPER_ADMIN, User, UserRole, Venue


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def _make_admin():
    admin = User(id="test:cycleadmin", email="cycleadmin@test.local",
                display_name="Cycle Admin", is_active=True)
    db.session.add(admin)
    db.session.flush()
    db.session.add(UserRole(user_id=admin.id, role_code=ROLE_SUPER_ADMIN))
    db.session.commit()
    return admin


def test_setting_a_venue_on_an_event_cycle_persists(app, client):
    _make_admin()
    _login(client, "test:cycleadmin")

    venue = Venue(code="GAYLORD_NAT", name="Gaylord National")
    db.session.add(venue)
    db.session.flush()
    cycle = EventCycle(code="SMF2027", name="Super MAGFest 2027",
                       is_active=True)
    db.session.add(cycle)
    db.session.commit()

    resp = client.post(f"/admin/config/event-cycles/{cycle.id}", data={
        "code": "SMF2027",
        "name": "Super MAGFest 2027",
        "is_active": "1",
        "venue_id": str(venue.id),
    }, follow_redirects=True)

    assert resp.status_code == 200
    db.session.refresh(cycle)
    assert cycle.venue_id == venue.id


def test_the_spaces_page_renders_rooms_once_the_venue_is_set(app, client):
    """The real point: setting the venue here must reach the Spaces page,
    not just the database column."""
    from app.models import ROLE_SPACE_ADMIN, Space, SPACE_KIND_ROOM

    admin = _make_admin()
    db.session.add(UserRole(user_id=admin.id, role_code=ROLE_SPACE_ADMIN))
    _login(client, "test:cycleadmin")

    venue = Venue(code="GAYLORD_NAT", name="Gaylord National")
    db.session.add(venue)
    db.session.flush()
    room = Space(venue_id=venue.id, name="Expo Hall B", code="EX-B",
                kind=SPACE_KIND_ROOM)
    db.session.add(room)
    cycle = EventCycle(code="SMF2027", name="Super MAGFest 2027",
                       is_active=True, is_default=True)
    db.session.add(cycle)
    db.session.commit()

    before = client.get("/spaces/?event=SMF2027").get_data(as_text=True)
    assert "no venue set" in before.lower()
    assert "Expo Hall B" not in before

    client.post(f"/admin/config/event-cycles/{cycle.id}", data={
        "code": "SMF2027",
        "name": "Super MAGFest 2027",
        "is_active": "1",
        "venue_id": str(venue.id),
    }, follow_redirects=True)

    after = client.get("/spaces/?event=SMF2027").get_data(as_text=True)
    assert "Expo Hall B" in after
