from app import db
from app.inventory.ledger import Actor, add_unit
from app.models import InvLabelSequence
from tests.inventory.helpers import login, make_container


def test_new_batch_allocates_codes_and_returns_zpl(client, inv):
    login(client, "test:invadmin")
    html = client.post("/inv/admin/labels/new", data={
        "prefix": "U", "count": "2", "dpi": "300"}).get_data(as_text=True)
    assert "U-00001" in html and "U-00002" in html
    assert "^PW675" in html


def test_reprint_uses_existing_codes_and_refuses_unknown(client, inv):
    make_container(inv, "C-0001", name="Main Warehouse", kind="LOCATION")
    login(client, "test:invadmin")
    response = client.post("/inv/admin/labels/zpl", data={
        "codes": "c-0001\nC-0999", "dpi": "203"})
    body = response.get_data(as_text=True)
    assert response.headers["Content-Disposition"].startswith("attachment")
    assert "Main Warehouse" in body and "C-0999" not in body


def test_only_the_labels_page_may_reach_browser_print(client, inv):
    login(client, "test:invadmin")
    labels_csp = client.get("/inv/admin/labels").headers["Content-Security-Policy"]
    other_csp = client.get("/inv/admin/kinds").headers["Content-Security-Policy"]
    assert "connect-src 'self' http://localhost:9100" in labels_csp
    assert "localhost:9100" not in other_csp


def test_reprint_finds_a_unit_label(client, inv):
    add_unit(inv.laptop, None, Actor(user_id="test:invadmin"),
             [("FESTOPS_LABEL", "U-00001")])
    db.session.commit()
    login(client, "test:invadmin")
    html = client.post("/inv/admin/labels/reprint",
                       data={"codes": "u-00001"}).get_data(as_text=True)
    assert "U-00001" in html and inv.laptop.item_name in html


def test_a_bad_count_is_refused_and_reserves_nothing(client, inv):
    login(client, "test:invadmin")
    response = client.post("/inv/admin/labels/new",
                           data={"prefix": "U", "count": "0"})
    assert response.status_code == 302
    row = db.session.get(InvLabelSequence, "U")
    assert row is None or row.next_value == 1
