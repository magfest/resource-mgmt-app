"""Tag station: link a pre-printed FEST tag to a named unit, and undo it."""
from datetime import datetime

import pytest

from app import db
from app.inventory.audit import ACTION_TAG_LINK, ACTION_TAG_UNDO
from app.inventory.devices import enroll_device
from app.inventory.ledger import Actor, InventoryError, add_unit
from app.inventory.resolver import resolve
from app.inventory.shifts import start_shift
from app.inventory.tagging import link_tag, tag_candidates, undo_tag_link
from app.models import (
    ConfigAuditEvent, InvPrinter, InvPrintJob, InvPrintRoute, InvTagLink, InvUnit,
)
from app.models.constants import INV_JOB_OWNED_UNIT
from tests.inventory.helpers import enrolled_client

ACTOR = Actor(shift_id=None, user_id="test:operator")
_n = [0]


def _rid():
    _n[0] += 1
    return f"tag-{_n[0]}"


@pytest.fixture
def printer(inv):
    station, _ = enroll_device("Print PC", "test:invadmin", station_code="PRINT")
    station.last_seen_at = datetime.utcnow()
    p = InvPrinter(name="Perm", dpi=203, width_in=2, height_in=3,
                   device_id=station.id, device_name="Perm")
    db.session.add(p)
    db.session.flush()
    db.session.add(InvPrintRoute(job_type=INV_JOB_OWNED_UNIT, printer_id=p.id))
    db.session.commit()
    return p


def _named(inv, name="MAG-PNTR-007", **kw):
    unit = add_unit(inv.laptop, None, ACTOR, [], name=name, **kw)
    db.session.commit()
    return unit


def test_link_attaches_the_tag_prints_and_audits(inv, printer):
    unit = _named(inv)
    answer = link_tag(unit.id, "FEST10001", ACTOR, _rid())
    db.session.commit()
    assert resolve("FEST10001").unit.id == unit.id
    assert answer["name"] == "MAG-PNTR-007" and answer["unit_id"] == unit.id
    job = InvPrintJob.query.one()
    assert job.status == "QUEUED" and job.fields["title"] == "MAG-PNTR-007"
    assert job.code == "FEST10001"
    audit = ConfigAuditEvent.query.filter_by(action=ACTION_TAG_LINK).one()
    assert audit.entity_id == str(unit.id)
    assert InvTagLink.query.one().undone_at is None


def test_audit_names_the_shift(inv, printer):
    client_device = enroll_device("Tag PC", "test:invadmin", station_code="TAG")[0]
    shift = start_shift(client_device, "Sam")
    unit = _named(inv)
    link_tag(unit.id, "FEST10001", Actor(shift_id=shift.id, device_id=client_device.id))
    db.session.commit()
    audit = ConfigAuditEvent.query.filter_by(action=ACTION_TAG_LINK).one()
    assert audit.created_by_user_id == f"shift:{shift.id}"


def test_candidates_leave_out_units_that_already_have_a_number(inv):
    free = _named(inv, "Lobby TV")
    _named(inv, "Lobby Sign")
    tagged = add_unit(inv.laptop, None, ACTOR, [("MAGFEST_TAG", "MT-1")], name="Lobby Tagged")
    labelled = add_unit(inv.laptop, None, ACTOR, [("FESTOPS_LABEL", "U-00001")],
                        name="Lobby Labelled")
    retired = _named(inv, "Lobby Gone")
    retired.condition = "RETIRED"
    db.session.commit()
    names = [u.name for u in tag_candidates("lobby")]
    assert names == ["Lobby Sign", "Lobby TV"]
    assert free.id and tagged.id and labelled.id


def test_a_second_tag_on_the_same_unit_is_refused(inv, printer):
    unit = _named(inv)
    link_tag(unit.id, "FEST10001", ACTOR)
    db.session.commit()
    with pytest.raises(InventoryError) as err:
        link_tag(unit.id, "FEST10002", ACTOR)
    assert str(err.value) == "MAG-PNTR-007 already has FEST10001."


def test_a_stale_unit_cannot_take_a_second_tag(inv, printer):
    unit = _named(inv)
    stale = db.session.get(InvUnit, unit.id)
    assert list(stale.identifiers) == []
    link_tag(unit.id, "FEST10001", ACTOR)
    db.session.commit()
    # `stale` still shows no identifiers; the locked re-read must see the first tag.
    with pytest.raises(InventoryError, match="already has FEST10001"):
        link_tag(stale.id, "FEST10002", ACTOR)
    assert InvTagLink.query.count() == 1


def test_a_unit_with_an_mt_tag_is_refused(inv, printer):
    unit = add_unit(inv.laptop, None, ACTOR, [("MAGFEST_TAG", "MT-1")], name="Has MT")
    db.session.commit()
    with pytest.raises(InventoryError, match="Has MT already has MT-1"):
        link_tag(unit.id, "FEST10001", ACTOR)


def test_a_label_shaped_or_held_tag_is_refused_with_receive_messages(inv, printer):
    a, b = _named(inv, "A unit"), _named(inv, "B unit")
    with pytest.raises(InventoryError, match="FestOps label"):
        link_tag(a.id, "U-00001", ACTOR)
    link_tag(a.id, "FEST10001", ACTOR)
    db.session.commit()
    with pytest.raises(InventoryError):
        link_tag(b.id, "FEST10001", ACTOR)


def test_undo_frees_the_tag_and_cancels_a_queued_label(inv, printer):
    unit = _named(inv)
    link_tag(unit.id, "FEST10001", ACTOR)
    db.session.commit()
    message = undo_tag_link(unit.id, ACTOR)
    db.session.commit()
    assert message == "Label cancelled."
    assert resolve("FEST10001").unit is None
    assert InvTagLink.query.one().undone_at is not None
    assert InvPrintJob.query.one().status == "CANCELLED"
    assert ConfigAuditEvent.query.filter_by(action=ACTION_TAG_UNDO).count() == 1
    link_tag(unit.id, "FEST10002", ACTOR)
    db.session.commit()
    assert resolve("FEST10002").unit.id == unit.id


def test_only_the_newest_link_of_this_shift_can_be_undone(inv, printer):
    a, b = _named(inv, "A unit"), _named(inv, "B unit")
    link_tag(a.id, "FEST10001", ACTOR)
    link_tag(b.id, "FEST10002", ACTOR)
    db.session.commit()
    with pytest.raises(InventoryError):
        undo_tag_link(a.id, ACTOR)
    with pytest.raises(InventoryError):
        undo_tag_link(b.id, Actor(user_id="test:invadmin"))
    undo_tag_link(b.id, ACTOR)
    db.session.commit()
    undo_tag_link(a.id, ACTOR)


def test_undo_after_the_window_is_refused(inv, printer):
    unit = _named(inv)
    link_tag(unit.id, "FEST10001", ACTOR)
    link = InvTagLink.query.one()
    link.created_at = datetime(2020, 1, 1)
    db.session.commit()
    with pytest.raises(InventoryError, match="Too late"):
        undo_tag_link(unit.id, ACTOR)


def _tag_client(client, station="TAG"):
    device = enrolled_client(client, station_code=station)
    start_shift(device, "Sam")
    db.session.commit()
    return device


def test_tag_api_round_trip(client, inv, printer):
    _tag_client(client)
    unit = _named(inv, "Hall Printer")
    r = client.post("/inv/scan/api/tag/check", json={"tag": "FEST10001"}).get_json()
    assert r["ok"] is True
    r = client.post("/inv/scan/api/tag/candidates", json={"q": "hall"}).get_json()
    assert r["units"] == [{"id": unit.id, "name": "Hall Printer", "model": "Laptop",
                           "details": "Laptop"}]
    r = client.post("/inv/scan/api/tag/link", json={
        "client_request_id": _rid(), "tag": "FEST10001", "unit_id": unit.id}).get_json()
    assert r["ok"] is True and r["name"] == "Hall Printer"
    r = client.post("/inv/scan/api/tag/undo", json={
        "client_request_id": _rid(), "unit_id": unit.id}).get_json()
    assert r["ok"] is True and r["message"] == "Label cancelled."


def test_tag_api_refuses_a_device_on_another_station(client, inv, printer):
    _tag_client(client, station="RECEIVE")
    unit = _named(inv)
    calls = [("check", {"tag": "FEST10001"}), ("candidates", {"q": "mag"}),
             ("link", {"client_request_id": _rid(), "tag": "FEST10001", "unit_id": unit.id}),
             ("undo", {"client_request_id": _rid(), "unit_id": unit.id})]
    for path, body in calls:
        r = client.post(f"/inv/scan/api/tag/{path}", json=body).get_json()
        assert r["ok"] is False and "not set up" in r["message"], path
    assert InvTagLink.query.count() == 0


def test_unit_page_shows_the_shift_as_a_scanner(client, inv, printer):
    from tests.inventory.helpers import login
    device = _tag_client(client)
    unit = _named(inv)
    r = client.post("/inv/scan/api/tag/link", json={
        "client_request_id": _rid(), "tag": "FEST10001", "unit_id": unit.id}).get_json()
    assert r["ok"] is True
    with client.session_transaction() as sess:
        sess.clear()
    login(client, "test:invadmin")
    page = client.get(f"/inv/admin/units/{unit.id}").get_data(as_text=True)
    assert "Sam (scanner)" in page and "FEST10001" in page
    assert device.id
