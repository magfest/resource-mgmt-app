"""department_home.html's Spaces subsection.

The first consumer of spaces_for_department() outside app/routes/spaces/;
see app/routes/spaces/queries.py. Read-only here: no edit controls, no
links to change an assignment.
"""
from app import db
from app.models import SPACE_KIND_ROOM, Space, SpaceAssignment, Venue


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def test_page_renders_the_subsection_with_an_assigned_space(
    app, client, seed_workflow_data
):
    cycle = seed_workflow_data["cycle"]
    dept = seed_workflow_data["department"]

    venue = Venue(code="GLN", name="Gaylord National")
    db.session.add(venue)
    db.session.flush()
    room = Space(venue_id=venue.id, name="Expo Hall B", code="EX-B",
                kind=SPACE_KIND_ROOM)
    db.session.add(room)
    db.session.flush()
    db.session.add(SpaceAssignment(space_id=room.id, event_cycle_id=cycle.id,
                                   department_id=dept.id))
    db.session.commit()

    _login(client, "test:admin")
    response = client.get(f"/{cycle.code}/{dept.code}/")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Spaces" in body
    assert "Expo Hall B" in body
    assert "EX-B" in body


def test_page_renders_the_subsection_with_no_spaces(
    app, client, seed_workflow_data
):
    cycle = seed_workflow_data["cycle"]
    dept = seed_workflow_data["department"]

    _login(client, "test:admin")
    response = client.get(f"/{cycle.code}/{dept.code}/")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "Spaces" in body
    assert "No spaces assigned yet." in body
