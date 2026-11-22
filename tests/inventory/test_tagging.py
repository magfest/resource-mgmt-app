"""Link a pre-printed FEST tag to a named unit and find unnumbered units by name."""
from contextlib import contextmanager
from datetime import datetime

import pytest

from app import db
from app.inventory.audit import ACTION_TAG_LINK
from app.inventory.devices import enroll_device
from app.inventory.ledger import Actor, InventoryError, add_unit
from app.inventory.resolver import resolve
from app.inventory.shifts import start_shift
from app.inventory.tagging import link_tag, tag_candidates
from app.models import (
    ConfigAuditEvent, InvPrinter, InvPrintJob, InvPrintRoute, InvTagLink, InvUnit,
)
from app.models.constants import INV_JOB_OWNED_UNIT
from tests.inventory.helpers import make_container

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
    client_device = enroll_device("Tag PC", "test:invadmin", station_code="UNIT",
                                  home_container=make_container(inv, "LOC-T", kind="LOCATION"))[0]
    shift = start_shift(client_device, "test:operator")
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
    with pytest.raises(InventoryError, match="That is a label"):
        link_tag(a.id, "U-00001", ACTOR)
    link_tag(a.id, "FEST10001", ACTOR)
    db.session.commit()
    with pytest.raises(InventoryError):
        link_tag(b.id, "FEST10001", ACTOR)


def test_unit_page_shows_the_shift_as_a_scanner(client, inv, printer):
    from tests.inventory.helpers import login
    device = enroll_device("Unit PC", "test:invadmin", station_code="UNIT",
                           home_container=make_container(inv, "LOC-U", kind="LOCATION"))[0]
    shift = start_shift(device, "test:operator")
    unit = _named(inv)
    link_tag(unit.id, "FEST10001", Actor(shift_id=shift.id, device_id=device.id))
    db.session.commit()
    login(client, "test:invadmin")
    page = client.get(f"/inv/admin/units/{unit.id}").get_data(as_text=True)
    assert "Operator (scanner)" in page and "FEST10001" in page


def test_a_tag_taken_between_check_and_insert_gets_the_receive_refusal(inv, printer, monkeypatch):
    from app.inventory import ledger, tagging
    from app.inventory.receive import AlreadyReceived
    from app.models import InvUnitIdentifier
    a, b = _named(inv, "A unit"), _named(inv, "B unit")

    def other_scanner_wins(tag):
        db.session.add(InvUnitIdentifier(unit_id=b.id, kind="MAGFEST_TAG", value=tag,
                                         normalized_value=tag))
        db.session.flush()

    @contextmanager
    def nested():
        with db.session.begin_nested():
            yield

    monkeypatch.setattr(tagging, "check_tag", other_scanner_wins)
    # The unique index decides, as it does when the other insert is in another transaction.
    monkeypatch.setattr(ledger, "_is_taken", lambda *args: False)
    # PostgreSQL's savepoint keeps the session usable after the loss; SQLite skips it.
    monkeypatch.setattr(ledger, "savepoint", nested)
    with pytest.raises(AlreadyReceived) as caught:
        link_tag(a.id, "FEST10001", ACTOR)
    assert caught.value.unit.id == b.id


def _count_selects(fn):
    from sqlalchemy import event
    seen = [0]

    def count(conn, cursor, statement, *args):
        if statement.lstrip().upper().startswith("SELECT"):
            seen[0] += 1

    event.listen(db.engine, "before_cursor_execute", count)
    try:
        fn()
    finally:
        event.remove(db.engine, "before_cursor_execute", count)
    return seen[0]


def test_tag_candidates_load_fields_up_front(inv, printer):
    from app.inventory.unit_fields import model_number, set_unit_value, spec_line
    from tests.inventory.helpers import seed_gear_kind
    kind = seed_gear_kind("PRINTER", "Printer", [
        ("TONER", "Toner cartridge", "TEXT", None, {"on_label": True}),
        ("COLOR", "Color", "BOOL", None, {"on_label": True, "label_text": "Color"})])
    inv.laptop.gear_kind_id = kind.id
    db.session.commit()

    def add(start, count):
        for i in range(start, start + count):
            unit = _named(inv, f"MAG-PNTR-{i:03d}")
            set_unit_value(unit, kind.fields[0], f"T{i}")
            set_unit_value(unit, kind.fields[1], "yes")
        db.session.commit()

    ask = lambda: [(model_number(u), spec_line(u)) for u in tag_candidates("pntr")]
    add(0, 2)
    few = _count_selects(ask)
    add(2, 4)
    more = _count_selects(ask)
    assert len(ask()) == 6
    assert more == few
