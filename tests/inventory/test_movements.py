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
    assert "No location or container has the label C-9999." in html


def test_log_page_loads_related_rows_in_bulk(client, inv):
    from sqlalchemy import event
    for n in range(8):
        add_stock(inv.pens, make_container(inv, f"C-{n + 1:04d}", kind="BIN"), 1, ACTOR)
    db.session.commit()
    db.session.expunge_all()
    login(client, "test:invadmin")
    statements = []

    def record(conn, cursor, statement, *rest):
        statements.append(statement)

    engine = db.engine
    event.listen(engine, "before_cursor_execute", record)
    try:
        html = client.get("/inv/admin/movements").get_data(as_text=True)
    finally:
        event.remove(engine, "before_cursor_execute", record)
    assert html.count("1 Blue Pen") == 8
    container_loads = [s for s in statements if "FROM inv_containers" in s]
    assert len(container_loads) <= 3


def test_a_discard_reads_as_a_discard_with_its_reason(inv):
    from app.inventory.describe import describe_movement
    from app.inventory.ledger import discard_stock
    bin_ = make_container(inv, "C-0001", kind="BIN")
    add_stock(inv.pens, bin_, 10, ACTOR)
    texts = {}
    for reason in ("DRIED_OUT", "BROKEN", "EXPIRED", "OTHER"):
        texts[reason] = describe_movement(discard_stock(inv.pens, bin_, 1, reason, ACTOR))
    assert texts["DRIED_OUT"] == "Discarded 1 Blue Pen from C-0001: dried out"
    assert texts["BROKEN"] == "Discarded 1 Blue Pen from C-0001: broken"
    assert texts["EXPIRED"] == "Discarded 1 Blue Pen from C-0001: expired"
    assert texts["OTHER"] == "Discarded 1 Blue Pen from C-0001: other reason"


def test_movements_filter_by_action(client, inv):
    from app.inventory.ledger import add_unit, move_unit
    from app.inventory.describe import describe_movement
    a = make_container(inv, "C-0001", kind="BIN")
    b = make_container(inv, "C-0002", kind="BIN")
    unit = add_unit(inv.laptop, a, ACTOR, [("FESTOPS_LABEL", "U-0301")])
    move_unit(unit, b, ACTOR)
    move_unit(unit, a, ACTOR, action="ADJUST")
    db.session.commit()
    login(client, "test:invadmin")
    moves = {m.action: describe_movement(m) for m in InvMovement.query.all() if m.action != "ADD"}
    only = client.get("/inv/admin/movements?action=MOVE").get_data(as_text=True)
    assert moves["MOVE"] in only and moves["ADJUST"] not in only
    junk = client.get("/inv/admin/movements?action=JUNK")
    text = junk.get_data(as_text=True)
    assert junk.status_code == 200 and moves["MOVE"] in text and moves["ADJUST"] in text


def test_movements_form_keeps_the_chosen_action(client, inv):
    login(client, "test:invadmin")
    html = client.get("/inv/admin/movements?action=MOVE").get_data(as_text=True)
    assert '<option value="MOVE" selected>' in html
    assert '<option value="">All actions</option>' in html
    assert 'value="ADJUST" selected' not in html
