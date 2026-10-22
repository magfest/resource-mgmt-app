from app import db
from app.inventory.describe import describe_actor
from app.inventory.ledger import Actor, add_stock, move_stock
from app.models import InvDevice, InvMovement, InvShift, InvStock
from tests.inventory.helpers import login, make_container

ACTOR = Actor(user_id="test:invadmin")


def test_log_filters_by_container_label(client, inv):
    a = make_container(inv, "C-0001", kind="BIN")
    b = make_container(inv, "C-0002", kind="BIN")
    c = make_container(inv, "C-0003", kind="BIN")
    add_stock(inv.pens, a, 5, ACTOR)
    move_stock(inv.pens, a, b, 2, ACTOR)
    add_stock(inv.pens, c, 9, ACTOR)
    db.session.commit()

    login(client, "test:invadmin")
    html = client.get("/inv/admin/movements?container=c-0002").get_data(as_text=True)
    assert "2 Blue Pen" in html
    assert "9 Blue Pen" not in html


def test_check_command_reports_a_tampered_balance(app, inv):
    bin_ = make_container(inv, "C-0001", kind="BIN")
    add_stock(inv.pens, bin_, 5, ACTOR)
    db.session.commit()
    InvStock.query.one().quantity = 7
    db.session.commit()

    result = app.test_cli_runner().invoke(args=["inv-check-balances"])
    assert result.exit_code == 1
    assert "cached 7, log 5" in result.output


def test_a_shift_actor_is_named_by_the_person_on_the_shift(inv):
    bin_ = make_container(inv, "C-0001", kind="BIN")
    device = InvDevice(name="Scanner 1", token_hash="h" * 64, enrolled_by_user_id="test:admin")
    db.session.add(device)
    db.session.flush()
    shift = InvShift(device_id=device.id, person_name="Sam")
    db.session.add(shift)
    db.session.flush()
    add_stock(inv.pens, bin_, 3, Actor(shift_id=shift.id, device_id=device.id))
    movement = InvMovement.query.one()
    assert movement.shift_id == shift.id
    assert describe_actor(movement) == "Sam on Scanner 1"


def test_unparseable_since_flashes_an_error(client, inv):
    login(client, "test:invadmin")
    response = client.get("/inv/admin/movements?since=notadate", follow_redirects=True)
    assert response.status_code == 200
    assert "Since must be a date" in response.get_data(as_text=True)


def test_unknown_container_label_is_reported(client, inv):
    login(client, "test:invadmin")
    html = client.get("/inv/admin/movements?container=C-9999").get_data(as_text=True)
    assert "No container has the label C-9999." in html
