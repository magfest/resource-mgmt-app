"""Operators page: card and PIN status, new cards, PIN resets."""
import json

from app import db
from app.inventory.shifts import current_shift, start_shift
from app.inventory.sign_in import new_pin, user_for_pin
from app.models import InvPrintJob, SecurityAuditLog, User
from app.models.constants import INV_JOB_SIGNIN_CARD
from tests.inventory.helpers import enrolled_client, login


def _changes():
    return [json.loads(r.details).get("change") for r in db.session.query(SecurityAuditLog).all()
            if r.details and "change" in json.loads(r.details)]


def test_page_lists_admins_and_operators_with_status(client, inv):
    new_pin(db.session.get(User, "test:operator"))
    db.session.commit()
    login(client, "test:invadmin")
    page = client.get("/inv/admin/operators").get_data(as_text=True)
    assert "Operator" in page and "Invadmin" in page
    assert "Has a PIN" in page and "Card printed" in page
    assert "Yes, " in page and "Never" in page
    assert "Nobody" not in page
    assert "pick their name" not in page


def test_reset_button_only_for_super_admins(client, inv):
    login(client, "test:invadmin")
    assert "Reset PIN" not in client.get("/inv/admin/operators").get_data(as_text=True)
    login(client, "test:admin")
    assert "Reset PIN" in client.get("/inv/admin/operators").get_data(as_text=True)


def test_inventory_admin_cannot_reset_a_pin(client, inv):
    login(client, "test:invadmin")
    assert client.post("/inv/admin/operators/test:operator/reset-pin").status_code == 403


def test_super_admin_resets_a_pin_and_ends_shifts(client, inv):
    device = enrolled_client(client)
    pin = new_pin(db.session.get(User, "test:operator"))
    start_shift(device, "test:operator")
    db.session.commit()
    login(client, "test:admin")
    r = client.post("/inv/admin/operators/test:operator/reset-pin")
    assert r.status_code == 302
    assert user_for_pin(pin) is None
    assert current_shift(device) is None
    assert _changes() == ["pin_reset"]
    page = client.get("/inv/admin/operators").get_data(as_text=True)
    assert "Operator&#39;s PIN is reset." in page or "Operator's PIN is reset." in page


def test_lead_prints_a_new_card_for_someone(client, card_route):
    login(client, "test:invadmin")
    r = client.post("/inv/admin/operators/test:operator/card")
    assert r.status_code == 302
    assert db.session.query(InvPrintJob).filter_by(job_type=INV_JOB_SIGNIN_CARD).count() == 1
    assert _changes() == ["card_printed"]
    page = client.get("/inv/admin/operators").get_data(as_text=True)
    assert "printing on Card printer" in page


def test_refused_card_flashes_and_changes_nothing(client, inv):
    login(client, "test:invadmin")
    client.post("/inv/admin/operators/test:operator/card")
    assert db.session.query(InvPrintJob).count() == 0
    assert _changes() == []
    page = client.get("/inv/admin/operators").get_data(as_text=True)
    assert "printer" in page.lower()


def test_revoking_the_role_ends_shifts(client, inv):
    from app.models import InvShift
    device = enrolled_client(client)
    start_shift(device, "test:operator")
    db.session.commit()
    login(client, "test:invadmin")
    client.post("/inv/admin/operators/test:operator/revoke")
    shift = db.session.query(InvShift).one()
    assert shift.ended_at is not None and shift.end_reason == "REVOKED"


def test_new_card_only_for_listed_users(client, card_route):
    from app.models import InvCredential
    login(client, "test:invadmin")
    for user_id in ["test:admin", "test:nobody", "test:missing"]:
        assert client.post(f"/inv/admin/operators/{user_id}/card").status_code == 404
    db.session.get(User, "test:operator").is_active = False
    db.session.commit()
    assert client.post("/inv/admin/operators/test:operator/card").status_code == 404
    assert db.session.query(InvPrintJob).count() == 0
    assert db.session.query(InvCredential).count() == 0
    assert _changes() == []


def test_pin_reset_only_for_listed_users(client, inv):
    from app.models import InvCredential
    new_pin(db.session.get(User, "test:nobody"))
    db.session.commit()
    login(client, "test:admin")
    for user_id in ["test:admin", "test:nobody"]:
        assert client.post(f"/inv/admin/operators/{user_id}/reset-pin").status_code == 404
    assert db.session.get(InvCredential, "test:nobody").pin_digest is not None
    assert _changes() == []


def test_print_new_card_asks_first(client, inv):
    login(client, "test:invadmin")
    page = client.get("/inv/admin/operators").get_data(as_text=True)
    assert 'data-confirm="Print a new card for Operator? Their current card stops working."' in page
