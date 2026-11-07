from app import db
from app.inventory.labels import parse
from app.inventory.ledger import Actor, add_unit
from app.models import InvLabelSequence
from tests.inventory.helpers import login, make_container


def test_new_batch_allocates_codes_and_returns_zpl(client, inv):
    login(client, "test:invadmin")
    html = client.post("/inv/admin/labels/new", data={
        "prefix": "U", "count": "2", "dpi": "300"}).get_data(as_text=True)
    assert "U-00001" in html and "U-00002" in html
    assert "^PW600" in html


def test_reprint_uses_existing_codes_and_refuses_unknown(client, inv):
    make_container(inv, "C-0001", name="Main Warehouse", kind="LOCATION")
    db.session.commit()
    login(client, "test:invadmin")
    html = client.post("/inv/admin/labels/reprint", data={
        "codes": "c-0001\nC-0999", "dpi": "203"}).get_data(as_text=True)
    assert "Main Warehouse" in html and "^PW406" in html
    assert "Not printed" in html and "C-0999" in html
    assert "^FDC-0999" not in html


def test_only_the_labels_page_may_reach_browser_print(client, inv):
    login(client, "test:invadmin")
    labels_csp = client.get("/inv/admin/labels").headers["Content-Security-Policy"]
    other_csp = client.get("/inv/admin/kinds").headers["Content-Security-Policy"]
    assert "connect-src 'self' http://localhost:9100" in labels_csp
    assert "localhost:9100" not in other_csp


def test_the_batch_page_may_reach_browser_print(client, inv):
    login(client, "test:invadmin")
    response = client.post("/inv/admin/labels/new", data={"prefix": "U", "count": "1"})
    assert "connect-src 'self' http://localhost:9100" in response.headers["Content-Security-Policy"]


def test_creating_containers_lands_on_the_reprint_form(client, inv):
    login(client, "test:invadmin")
    response = client.post("/inv/admin/containers", data={
        "kind_id": str(inv.kinds["PALLET"].id), "count": "2"})
    assert response.status_code == 302
    assert "/inv/admin/labels" in response.headers["Location"]
    html = client.get(response.headers["Location"]).get_data(as_text=True)
    area = html[html.index("<textarea"):html.index("</textarea>")]
    assert "C-0001" in area and "C-0002" in area


def test_a_batch_of_only_unknown_codes_prints_nothing(client, inv):
    login(client, "test:invadmin")
    html = client.post("/inv/admin/labels/reprint",
                       data={"codes": "C-0999"}).get_data(as_text=True)
    assert "Nothing to print." in html and "send-zpl" not in html


def test_unicode_digits_are_not_a_label_code():
    assert parse("I-\u0661\u0662") is None


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


def test_reprint_finds_a_unit_by_its_magfest_tag(client, inv):
    add_unit(inv.laptop, None, Actor(user_id="test:invadmin"),
             [("FESTOPS_LABEL", "U-00001"), ("MAGFEST_TAG", "FEST00001"),
              ("SERIAL", "SN-1")])
    db.session.commit()
    login(client, "test:invadmin")
    for code in ("fest00001", "u-00001"):
        html = client.post("/inv/admin/labels/reprint", data={"codes": code}).get_data(as_text=True)
        assert "^FDFEST00001^FS" in html and "^FDSN-1^FS" in html
        assert "Return to MAGFest FestOps" in html and "^FDPrinted^FS" in html


def test_a_tag_no_barcode_can_hold_does_not_stop_the_batch(client, inv):
    actor = Actor(user_id="test:invadmin")
    for tag in ("A" * 21, "BAD>TAG", "GOOD1"):
        add_unit(inv.laptop, None, actor, [("MAGFEST_TAG", tag)])
    db.session.commit()
    login(client, "test:invadmin")
    response = client.post("/inv/admin/labels/reprint", data={
        "codes": f"{'A' * 21}\nBAD>TAG\nGOOD1", "dpi": "300"})
    html = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "^FDGOOD1^FS" in html
    assert "a barcode cannot hold these codes" in html
    assert "A" * 21 in html and f"^FD{'A' * 21}" not in html


def test_an_operator_can_reprint_and_send_but_not_reserve_new_labels(client, inv):
    make_container(inv, "C-0001", name="Main Warehouse", kind="LOCATION")
    db.session.commit()
    login(client, "test:operator")
    page = client.get("/inv/admin/labels")
    assert page.status_code == 200
    html = page.get_data(as_text=True)
    assert "Reprint" in html and "Reserve and show" not in html
    batch = client.post("/inv/admin/labels/reprint", data={"codes": "C-0001"})
    assert batch.status_code == 200
    assert "Send to print queue" in batch.get_data(as_text=True)
    assert client.post("/inv/admin/labels/new", data={"prefix": "U", "count": "1"}).status_code == 403
    home = client.get("/inv/").get_data(as_text=True)
    assert "/inv/admin/labels" in home and "/inv/admin/print-queue" in home
    login(client, "test:invadmin")
    assert "Reserve and show" in client.get("/inv/admin/labels").get_data(as_text=True)
