"""The laptop flags page: open and resolved flags, and resolving with a note."""
from app import db
from app.inventory.flags import raise_flag
from app.inventory.ledger import Actor, add_unit
from app.models import InvFlag
from tests.inventory.helpers import login, make_container

ACTOR = Actor(user_id="test:invadmin")


def _flag(inv, reason="DAMAGED", note="Cracked case"):
    unit = add_unit(inv.laptop, None, ACTOR, [("FESTOPS_LABEL", "U-0001")])
    flag = raise_flag(ACTOR, "COUNT", reason, unit=unit, note=note)
    db.session.commit()
    return unit, flag


def _resolve(client, flag, note="Fixed it"):
    return client.post(f"/inv/admin/flags/{flag.id}/resolve", data={"note": note},
                       follow_redirects=True)


def test_raised_flag_shows_and_operator_resolves_it(client, inv):
    unit, flag = _flag(inv)
    login(client, "test:operator")
    page = client.get("/inv/admin/flags").get_data(as_text=True)
    assert "Cracked case" in page and "Damaged" in page and "U-0001" in page
    assert f"/inv/admin/units/{unit.id}" in page
    _resolve(client, flag, "Replaced\r\nthe case")
    row = db.session.get(InvFlag, flag.id)
    assert row.resolved_at is not None and row.resolved_by_user_id == "test:operator"
    assert row.resolution_note == "Replaced\nthe case"
    page = client.get("/inv/admin/flags").get_data(as_text=True)
    assert "Replaced" in page and "Operator" in page


def test_container_flag_links_to_the_container_page(client, inv):
    box = make_container(inv, "C-0001", kind="BIN")
    raise_flag(ACTOR, "COUNT", "OTHER", container=box, note="Wet")
    db.session.commit()
    login(client, "test:operator")
    page = client.get("/inv/admin/flags").get_data(as_text=True)
    assert "C-0001" in page and "Wet" in page


def test_blank_note_is_refused(client, inv):
    _, flag = _flag(inv)
    login(client, "test:operator")
    resp = _resolve(client, flag, "  \r\n ")
    assert "Say what was done." in resp.get_data(as_text=True)
    assert db.session.get(InvFlag, flag.id).resolved_at is None


def test_resolving_twice_is_refused_with_a_message(client, inv):
    _, flag = _flag(inv)
    login(client, "test:operator")
    _resolve(client, flag, "First")
    resp = _resolve(client, flag, "Second")
    assert resp.status_code == 200
    assert "already resolved" in resp.get_data(as_text=True)
    assert db.session.get(InvFlag, flag.id).resolution_note == "First"


def test_home_link_counts_open_flags(client, inv):
    _, flag = _flag(inv)
    login(client, "test:operator")
    assert "Flags (1)" in client.get("/inv/").get_data(as_text=True)
    _resolve(client, flag)
    assert "Flags (0)" in client.get("/inv/").get_data(as_text=True)


def test_user_with_no_inventory_role_gets_403(client, inv):
    _, flag = _flag(inv)
    login(client, "test:nobody")
    assert client.get("/inv/admin/flags").status_code == 403
    assert client.post(f"/inv/admin/flags/{flag.id}/resolve",
                       data={"note": "x"}).status_code == 403
