"""The Where things are page."""
from app import db
from app.inventory.ledger import Actor, add_unit
from tests.inventory.helpers import login, make_container

ACTOR = Actor(user_id="test:invadmin")


def _unit(inv, ident, container=None):
    unit = add_unit(inv.laptop, None, ACTOR, [("FESTOPS_LABEL", ident)])
    if container is not None:
        unit.current_container_id = container.id
    db.session.commit()
    return unit


def test_operator_sees_the_page(inv, client):
    login(client, "test:operator")
    body = client.get("/inv/admin/where").get_data(as_text=True)
    assert "Where things are" in body
    assert 'id="where-search"' in body


def test_nobody_is_refused(inv, client):
    login(client, "test:nobody")
    assert client.get("/inv/admin/where").status_code == 403


def test_unplaced_and_not_placed_rows(inv, client):
    warehouse = make_container(inv, "C-9001", "LOCATION", name="Warehouse")
    _unit(inv, "U-0001", warehouse)
    _unit(inv, "U-0002")
    login(client, "test:operator")
    body = client.get("/inv/admin/where").get_data(as_text=True)
    assert "In the Warehouse, exact location not assigned (1)" in body
    assert "Not placed yet (1)" in body
    assert "Nobody has scanned these yet" in body


def test_unit_row_links_to_its_page(inv, client):
    warehouse = make_container(inv, "C-9001", "LOCATION", name="Warehouse")
    pallet = make_container(inv, "C-9012", "PALLET", parent=warehouse, name="Pallet 3")
    unit = _unit(inv, "U-0001", pallet)
    login(client, "test:operator")
    body = client.get("/inv/admin/where").get_data(as_text=True)
    assert f"/inv/admin/units/{unit.id}" in body


def test_home_links_the_page(inv, client):
    login(client, "test:operator")
    assert "/inv/admin/where" in client.get("/inv/").get_data(as_text=True)


def test_no_inline_handlers(inv, client):
    warehouse = make_container(inv, "C-9001", "LOCATION", name="Warehouse")
    _unit(inv, "U-0001", warehouse)
    login(client, "test:operator")
    body = client.get("/inv/admin/where").get_data(as_text=True)
    assert "onclick=" not in body and "onchange=" not in body
