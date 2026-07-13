"""Homepage must hide event-disabled departments — for super admins too."""
from app import db
from app.models import Department, DivisionMembership, EventCycleDepartment


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def test_super_admin_homepage_hides_disabled_departments(client, seed_workflow_data):
    data = seed_workflow_data

    # Put the seeded dept + a second dept into the seeded division, give the
    # super admin a division membership so both appear via division access.
    data["department"].division_id = data["division"].id
    dept2 = Department(code="DEPT2", name="Second Department",
                       is_active=True, division_id=data["division"].id)
    db.session.add(dept2)
    db.session.add(DivisionMembership(
        user_id="test:admin",
        division_id=data["division"].id,
        event_cycle_id=data["cycle"].id,
    ))
    db.session.flush()

    # Disable dept2 for this event.
    db.session.add(EventCycleDepartment(
        event_cycle_id=data["cycle"].id,
        department_id=dept2.id,
        is_enabled=False,
    ))
    db.session.commit()

    _login(client, "test:admin")
    resp = client.get("/")
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)

    assert "Test Department" in html          # enabled: shown
    assert "Second Department" not in html    # event-disabled: hidden
