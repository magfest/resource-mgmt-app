"""The page where an operator gets a PIN and prints a card."""
import json
import re

from app import db
from app.inventory.sign_in import user_for_pin
from app.models import InvPrintJob, SecurityAuditLog
from app.models.constants import INV_JOB_SIGNIN_CARD
from tests.inventory.helpers import login


def _pin_from(response) -> str:
    return re.search(r'data-pin="(\d{6})"', response.get_data(as_text=True)).group(1)


def _sign_in_rows():
    rows = db.session.query(SecurityAuditLog).all()
    return [r for r in rows if json.loads(r.details or "{}").get("source") == "inventory_sign_in"]


def test_operator_gets_a_pin_shown_once(client, inv):
    login(client, "test:operator")
    r = client.post("/inv/me/sign-in/pin")
    pin = _pin_from(r)
    assert r.headers["Cache-Control"] == "no-store"
    assert "Your PIN is shown once. Write it down." in r.get_data(as_text=True)
    assert user_for_pin(pin).id == "test:operator"
    page = client.get("/inv/me/sign-in").get_data(as_text=True)
    assert pin not in page
    assert "PIN made" in page
    assert "Get a new PIN" in page


def test_page_before_any_credential(client, inv):
    login(client, "test:operator")
    page = client.get("/inv/me/sign-in").get_data(as_text=True)
    assert "No PIN yet" in page and "No card yet" in page
    assert "Get my PIN" in page and "Print my card" in page


def test_pin_never_lands_in_the_session(client, inv):
    login(client, "test:operator")
    pin = _pin_from(client.post("/inv/me/sign-in/pin"))
    with client.session_transaction() as sess:
        assert pin not in repr(dict(sess))


def test_nobody_without_a_role_is_refused(client, inv):
    login(client, "test:nobody")
    assert client.get("/inv/me/sign-in").status_code == 403
    assert client.post("/inv/me/sign-in/pin").status_code == 403
    assert client.post("/inv/me/sign-in/card").status_code == 403


def test_print_my_card_queues_and_records(client, card_route):
    login(client, "test:operator")
    r = client.post("/inv/me/sign-in/card")
    assert r.status_code == 302
    jobs = db.session.query(InvPrintJob).filter_by(job_type=INV_JOB_SIGNIN_CARD).all()
    assert len(jobs) == 1
    changes = [json.loads(x.details)["change"] for x in _sign_in_rows()]
    assert changes == ["card_printed"]
    page = client.get("/inv/me/sign-in").get_data(as_text=True)
    assert "Your card is printing on Card printer." in page
    assert "Card printed" in page


def test_print_my_card_without_a_printer_flashes_the_reason(client, inv):
    login(client, "test:operator")
    r = client.post("/inv/me/sign-in/card", follow_redirects=True)
    assert r.status_code == 200
    assert db.session.query(InvPrintJob).count() == 0
    assert "Your card is printing" not in r.get_data(as_text=True)


def test_second_pin_is_logged_as_a_reset(client, inv):
    login(client, "test:operator")
    client.post("/inv/me/sign-in/pin")
    client.post("/inv/me/sign-in/pin")
    changes = [json.loads(x.details)["change"] for x in _sign_in_rows()]
    assert changes == ["pin_issued", "pin_reset"]


def test_audit_holds_no_values(client, inv):
    login(client, "test:operator")
    pin = _pin_from(client.post("/inv/me/sign-in/pin"))
    rows = _sign_in_rows()
    assert rows
    for row in rows:
        assert pin not in row.details
        assert set(json.loads(row.details)) == {"source", "target_user_id", "change"}
