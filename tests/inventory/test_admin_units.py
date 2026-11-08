"""The laptop units page: search, the unit page, corrections and label printing."""
import pytest

from app import db
from app.inventory.devices import enroll_device
from app.inventory.flags import raise_flag
from app.inventory.ledger import Actor, add_unit, retire_identifier
from app.inventory.resolver import KIND_UNIT, resolve
from app.models import InvPrinter, InvPrintJob, InvPrintRoute, InvUnit, InvUnitIdentifier
from app.models.constants import INV_JOB_OWNED_UNIT, INV_STATION_PRINT
from tests.inventory.helpers import login

ACTOR = Actor(user_id="test:invadmin")


@pytest.fixture
def unit(inv):
    unit = add_unit(inv.laptop, None, ACTOR, [
        ("FESTOPS_LABEL", "U-0001"), ("SERIAL", "SN-ABC123"), ("MAC", "aa:bb:cc:dd:ee:ff")])
    db.session.commit()
    return unit


@pytest.fixture
def printer(inv):
    device, _ = enroll_device("Print PC", "test:invadmin", station_code=INV_STATION_PRINT)
    p = InvPrinter(name="Perm", dpi=203, width_in=2, height_in=1,
                   device_id=device.id, device_name="Perm")
    db.session.add(p)
    db.session.flush()
    db.session.add(InvPrintRoute(job_type=INV_JOB_OWNED_UNIT, printer_id=p.id))
    db.session.commit()


def _search(client, q):
    return client.get("/inv/admin/units", query_string={"q": q}).get_data(as_text=True)


def test_search_by_tag_serial_fragment_and_mac(client, unit):
    login(client, "test:operator")
    for q in ("U-0001", "abc12", "AA-BB-CC-DD-EE-FF", "aabbccddeeff"):
        page = _search(client, q)
        assert f"/inv/admin/units/{unit.id}" in page, q
        assert "Laptop" in page


def test_empty_search_lists_nothing_and_unknown_finds_nothing(client, unit):
    login(client, "test:operator")
    assert f"/inv/admin/units/{unit.id}" not in _search(client, "")
    assert f"/inv/admin/units/{unit.id}" not in _search(client, "zzzz")


def test_retired_identifier_no_longer_resolves(client, unit):
    ident = next(i for i in unit.identifiers if i.kind == "SERIAL")
    retire_identifier(ident)
    db.session.commit()
    assert resolve("SN-ABC123").kind != KIND_UNIT
    login(client, "test:operator")
    assert f"/inv/admin/units/{unit.id}" not in _search(client, "SN-ABC123")


def test_row_shows_open_flag_count_and_unit_page_lists_flags(client, unit):
    raise_flag(ACTOR, "COUNT", "DAMAGED", unit=unit, note="Dented")
    db.session.commit()
    login(client, "test:operator")
    assert "1 open" in _search(client, "U-0001")
    page = client.get(f"/inv/admin/units/{unit.id}").get_data(as_text=True)
    assert "Dented" in page and "SN-ABC123" in page


def test_admin_corrects_serial_and_item_then_prints_new_text(client, inv, unit, printer):
    login(client, "test:invadmin")
    old = next(i for i in unit.identifiers if i.kind == "SERIAL")
    base = f"/inv/admin/units/{unit.id}"
    client.post(f"{base}/identifiers/{old.id}/retire")
    client.post(f"{base}/identifiers", data={"kind": "SERIAL", "value": "SN-NEW999"})
    client.post(f"{base}/item", data={"item_id": str(inv.wap.id)})
    resp = client.post(f"{base}/print", data={"mode": "CURRENT_DETAILS"})
    assert resp.status_code == 302
    jobs = db.session.query(InvPrintJob).all()
    assert len(jobs) == 1
    rows = dict(map(tuple, jobs[0].fields["rows"]))
    assert rows["Serial"] == "SN-NEW999" and rows["Type"] == "Wireless Access Point"
    assert jobs[0].unit_id == unit.id


def test_condition_needs_confirm_wording_and_retires_identifiers(client, unit):
    login(client, "test:invadmin")
    client.post(f"/inv/admin/units/{unit.id}/condition", data={"condition": "REPAIR"})
    assert db.session.get(InvUnit, unit.id).condition == "REPAIR"
    client.post(f"/inv/admin/units/{unit.id}/condition", data={"condition": "RETIRED"})
    db.session.expire_all()
    assert db.session.get(InvUnit, unit.id).condition == "RETIRED"
    assert all(i.retired_at for i in db.session.get(InvUnit, unit.id).identifiers)
    client.post(f"/inv/admin/units/{unit.id}/condition", data={"condition": "BOGUS"})
    assert db.session.get(InvUnit, unit.id).condition == "RETIRED"


def test_operator_prints_but_cannot_correct(client, inv, unit, printer):
    login(client, "test:operator")
    base = f"/inv/admin/units/{unit.id}"
    ident = unit.identifiers[0]
    assert client.get(base).status_code == 200
    assert client.post(f"{base}/print", data={"mode": "CURRENT_DETAILS"}).status_code == 302
    assert db.session.query(InvPrintJob).count() == 1
    assert client.post(f"{base}/identifiers",
                       data={"kind": "SERIAL", "value": "X1"}).status_code == 403
    assert client.post(f"{base}/identifiers/{ident.id}/retire").status_code == 403
    assert client.post(f"{base}/condition", data={"condition": "LOST"}).status_code == 403
    assert client.post(f"{base}/item", data={"item_id": str(inv.wap.id)}).status_code == 403
    db.session.expire_all()
    assert db.session.get(InvUnit, unit.id).condition == "IN_SERVICE"
    assert db.session.get(InvUnit, unit.id).item_id == inv.laptop.id


def test_nobody_gets_403(client, unit):
    login(client, "test:nobody")
    assert client.get("/inv/admin/units").status_code == 403
    assert client.get(f"/inv/admin/units/{unit.id}").status_code == 403


def test_label_shaped_serial_is_refused_and_saves_nothing(client, unit):
    login(client, "test:invadmin")
    before = db.session.query(InvUnitIdentifier).count()
    resp = client.post(f"/inv/admin/units/{unit.id}/identifiers",
                       data={"kind": "SERIAL", "value": "U-0099"}, follow_redirects=True)
    assert "not a serial" in resp.get_data(as_text=True)
    assert db.session.query(InvUnitIdentifier).count() == before


def test_a_new_tag_replaces_the_units_number(client, unit):
    login(client, "test:invadmin")
    client.post(f"/inv/admin/units/{unit.id}/identifiers",
                data={"kind": "MAGFEST_TAG", "value": "TECH412"}, follow_redirects=True)
    assert resolve("TECH412").kind == KIND_UNIT
    assert resolve("U-0001").kind != KIND_UNIT
    active = {i.kind for i in db.session.query(InvUnitIdentifier)
              .filter_by(unit_id=unit.id, retired_at=None)}
    assert active == {"MAGFEST_TAG", "SERIAL", "MAC"}


def _audit(unit):
    import json
    from app.models import ConfigAuditEvent
    return [(e.action, e.created_by_user_id, json.loads(e.changes_json))
            for e in db.session.query(ConfigAuditEvent)
            .filter_by(entity_type="inv_unit", entity_id=str(unit.id)).order_by(ConfigAuditEvent.id)]


def test_each_correction_records_who_and_what_changed(client, inv, unit):
    login(client, "test:invadmin")
    base = f"/inv/admin/units/{unit.id}"
    client.post(f"{base}/identifiers", data={"kind": "MAGFEST_TAG", "value": "TECH412"})
    client.post(f"{base}/item", data={"item_id": str(inv.wap.id)})
    client.post(f"{base}/condition", data={"condition": "REPAIR"})
    events = _audit(unit)
    assert [(a, who) for a, who, _ in events] == [
        ("ADD_CODE", "test:invadmin"), ("ITEM", "test:invadmin"), ("CONDITION", "test:invadmin")]
    codes = events[0][2]["codes"]
    assert "FestOps label U-0001" in codes["old"] and "FestOps label U-0001" not in codes["new"]
    assert "MAGFest tag TECH412" in codes["new"]
    assert events[1][2] == {"item": {"old": "Laptop", "new": "Wireless Access Point"}}
    assert events[2][2] == {"condition": {"old": "IN_SERVICE", "new": "REPAIR"}}
    page = client.get(base).get_data(as_text=True)
    assert "Condition: In service to In repair" in page


def test_retiring_a_unit_records_the_codes_it_retired(client, unit):
    login(client, "test:invadmin")
    client.post(f"/inv/admin/units/{unit.id}/condition", data={"condition": "RETIRED"})
    (action, _, changes), = _audit(unit)
    assert action == "CONDITION" and changes["codes"]["new"] == []
    assert len(changes["codes"]["old"]) == 3


def test_refused_or_unchanged_corrections_record_nothing(client, unit, printer):
    login(client, "test:invadmin")
    base = f"/inv/admin/units/{unit.id}"
    client.post(f"{base}/identifiers", data={"kind": "SERIAL", "value": "U-0099"})
    client.post(f"{base}/condition", data={"condition": "IN_SERVICE"})
    client.post(f"{base}/print", data={"mode": "CURRENT_DETAILS"})
    assert _audit(unit) == []


def _tv_kind(inv):
    from tests.inventory.helpers import seed_gear_kind
    kind = seed_gear_kind("TV", "TV", [
        ("SIZE", "Size", "NUMBER", "in"),
        ("RESOLUTION", "Resolution", "CHOICE", ["720p", "1080p", "4k"]),
        ("HDMI", "HDMI", "BOOL", None)])
    inv.laptop.gear_kind_id = kind.id
    db.session.commit()
    return kind


def _details(unit, kind, **extra):
    size, res, hdmi = kind.fields
    data = {"name": "MAG-TV-107", "notes": "no power cable",
            f"field_{size.id}": "50", f"field_{res.id}": "4k", f"field_{hdmi.id}": ""}
    data.update(extra)
    return data


def test_admin_saves_name_notes_and_fields_in_one_form(client, inv, unit):
    kind = _tv_kind(inv)
    login(client, "test:invadmin")
    resp = client.post(f"/inv/admin/units/{unit.id}/details", data=_details(unit, kind))
    assert resp.status_code == 302
    page = client.get(f"/inv/admin/units/{unit.id}").get_data(as_text=True)
    for text in ("MAG-TV-107", "no power cable", "50 in", "4k", "Name: none to MAG-TV-107"):
        assert text in page, text
    from app.models import ConfigAuditEvent
    rows = db.session.query(ConfigAuditEvent).filter_by(
        entity_type="inv_unit", entity_id=str(unit.id), action="FIELDS").all()
    assert len(rows) == 1


def test_one_bad_field_saves_nothing_and_says_which(client, inv, unit):
    kind = _tv_kind(inv)
    login(client, "test:invadmin")
    data = _details(unit, kind, **{f"field_{kind.fields[0].id}": "big"})
    resp = client.post(f"/inv/admin/units/{unit.id}/details", data=data, follow_redirects=True)
    assert "Size must be a number." in resp.get_data(as_text=True)
    db.session.expire_all()
    assert db.session.get(InvUnit, unit.id).name is None


def test_imported_notes_show_under_their_own_heading(client, unit):
    unit.imported_notes = "Invented pickup note"
    db.session.commit()
    login(client, "test:operator")
    page = client.get(f"/inv/admin/units/{unit.id}").get_data(as_text=True)
    assert "From the old system" in page and "Invented pickup note" in page


def test_operator_sees_fields_but_cannot_save(client, inv, unit):
    kind = _tv_kind(inv)
    login(client, "test:operator")
    page = client.get(f"/inv/admin/units/{unit.id}").get_data(as_text=True)
    assert "Resolution" in page
    resp = client.post(f"/inv/admin/units/{unit.id}/details", data=_details(unit, kind))
    assert resp.status_code == 403


def test_unit_without_gear_kind_points_to_items_page(client, unit):
    login(client, "test:invadmin")
    page = client.get(f"/inv/admin/units/{unit.id}").get_data(as_text=True)
    assert "Set this model's gear kind on the Items page to add fields." in page


def test_units_search_shows_name_before_number(client, unit):
    unit.name = "MAG-TV-107"
    unit.name_key = "mag-tv-107"
    db.session.commit()
    login(client, "test:operator")
    assert "MAG-TV-107" in _search(client, "U-0001")


def _tvs(inv):
    """Three TVs: 50in 4k HDMI, 50in 1080p no HDMI, 70in 4k with no HDMI value."""
    from app.inventory.unit_fields import set_unit_value
    kind = _tv_kind(inv)
    size, res, hdmi = kind.fields
    units = []
    for i, (s, r, h) in enumerate([("50", "4k", "yes"), ("50", "1080p", "no"),
                                   ("70", "4k", None)]):
        u = add_unit(inv.laptop, None, ACTOR, [("FESTOPS_LABEL", f"TV-{i}")])
        set_unit_value(u, size, s)
        set_unit_value(u, res, r)
        if h:
            set_unit_value(u, hdmi, h)
        units.append(u)
    db.session.commit()
    return kind, units


def _ids_in(page, units):
    return [u.id for u in units if f"/inv/admin/units/{u.id}\"" in page]


def test_search_finds_a_unit_by_part_of_its_name(client, unit):
    unit.name = "PNTR-007"
    unit.name_key = "pntr-007"
    db.session.commit()
    login(client, "test:operator")
    for q in ("pntr-007", "NTR-00", "  Pntr "):
        assert f"/inv/admin/units/{unit.id}" in _search(client, q), q


def test_kind_and_choice_filter_lists_matching_units(client, inv):
    kind, units = _tvs(inv)
    res = kind.fields[1]
    four_k = next(c for c in res.choices if c.label == "4k")
    login(client, "test:operator")
    page = client.get("/inv/admin/units", query_string={
        "kind": "TV", f"f_{res.id}": four_k.id}).get_data(as_text=True)
    assert _ids_in(page, units) == [units[0].id, units[2].id]


def test_kind_alone_lists_the_kinds_units(client, inv):
    kind, units = _tvs(inv)
    login(client, "test:operator")
    page = client.get("/inv/admin/units", query_string={"kind": "TV"}).get_data(as_text=True)
    assert _ids_in(page, units) == [u.id for u in units]


def test_bool_filters_split_yes_from_no_and_ignore_unset(client, inv):
    kind, units = _tvs(inv)
    hdmi = kind.fields[2]
    login(client, "test:operator")
    for answer, expected in (("yes", [units[0]]), ("no", [units[1]])):
        page = client.get("/inv/admin/units", query_string={
            "kind": "TV", f"f_{hdmi.id}": answer}).get_data(as_text=True)
        assert _ids_in(page, units) == [u.id for u in expected], answer


def test_filters_combine_and_bad_ones_are_ignored(client, inv):
    kind, units = _tvs(inv)
    res, hdmi = kind.fields[1], kind.fields[2]
    four_k = next(c for c in res.choices if c.label == "4k")
    login(client, "test:operator")
    page = client.get("/inv/admin/units", query_string={
        "kind": "TV", f"f_{res.id}": four_k.id, f"f_{hdmi.id}": "yes",
        "f_99999": "yes", f"f_{kind.fields[0].id}": "50"}).get_data(as_text=True)
    assert _ids_in(page, units) == [units[0].id]
    page = client.get("/inv/admin/units", query_string={
        "kind": "TV", f"f_{hdmi.id}": "maybe", f"f_{res.id}": 99999}).get_data(as_text=True)
    assert _ids_in(page, units) == [u.id for u in units]
    assert client.get("/inv/admin/units", query_string={"kind": "NOPE"}).status_code == 200


def test_filter_selects_appear_once_a_kind_is_chosen(client, inv):
    kind, _ = _tvs(inv)
    login(client, "test:operator")
    page = client.get("/inv/admin/units", query_string={"kind": "TV"}).get_data(as_text=True)
    assert f'name="f_{kind.fields[1].id}"' in page
    assert f'name="f_{kind.fields[2].id}"' in page
    assert f'name="f_{kind.fields[0].id}"' not in page


def _many_tvs(inv, count, prefix="TVN"):
    kind = _tv_kind(inv)
    units = []
    for i in range(count):
        u = add_unit(inv.laptop, None, ACTOR, [("FESTOPS_LABEL", f"{prefix}-{i:03d}")])
        u.name = f"Stage TV {i:03d}"
        u.name_key = u.name.casefold()
        units.append(u)
    db.session.commit()
    return kind, units


def test_q_with_kind_finds_a_match_past_the_first_fifty_name_hits(client, inv):
    from app.inventory.unit_fields import set_unit_value
    kind, units = _many_tvs(inv, 60)
    res = kind.fields[1]
    set_unit_value(units[59], res, "4k")
    db.session.commit()
    four_k = next(c for c in res.choices if c.label == "4k")
    login(client, "test:operator")
    page = client.get("/inv/admin/units", query_string={
        "q": "stage tv", "kind": "TV", f"f_{res.id}": four_k.id}).get_data(as_text=True)
    assert _ids_in(page, units) == [units[59].id]
    page = client.get("/inv/admin/units", query_string={
        "q": "stage tv", "kind": "TV"}).get_data(as_text=True)
    assert "Showing the first 50 of 60." in page


def test_no_cut_notice_when_everything_fits(client, inv):
    _many_tvs(inv, 3)
    login(client, "test:operator")
    page = client.get("/inv/admin/units", query_string={"kind": "TV"}).get_data(as_text=True)
    assert "Showing the first" not in page


def test_unicode_digit_filter_is_ignored(client, inv):
    kind, units = _tvs(inv)
    res = kind.fields[1]
    login(client, "test:operator")
    for junk in ("²", "9" * 40):
        resp = client.get("/inv/admin/units", query_string={"kind": "TV", f"f_{res.id}": junk})
        assert resp.status_code == 200
        assert _ids_in(resp.get_data(as_text=True), units) == [u.id for u in units]


def test_retired_units_are_hidden_with_a_kind_and_the_page_says_so(client, inv):
    from app.inventory.ledger import set_condition
    kind, units = _tvs(inv)
    set_condition(units[0], "RETIRED")
    db.session.commit()
    login(client, "test:operator")
    page = client.get("/inv/admin/units", query_string={"kind": "TV"}).get_data(as_text=True)
    assert "Retired units are hidden." in page
    assert units[0].id not in _ids_in(page, units)
    assert "Retired units are hidden." not in _search(client, "TV-1")


def test_un_retiring_into_a_held_name_is_refused(client, inv, unit):
    from app.inventory.names import set_unit_name
    set_unit_name(unit, "MAG-PNTR-007")
    db.session.commit()
    login(client, "test:invadmin")
    base = f"/inv/admin/units/{unit.id}"
    client.post(f"{base}/condition", data={"condition": "RETIRED"})
    other = add_unit(inv.laptop, None, ACTOR, [], name="MAG-PNTR-007")
    db.session.commit()
    resp = client.post(f"{base}/condition", data={"condition": "IN_SERVICE"},
                       follow_redirects=True)
    assert resp.status_code == 200
    assert ("MAG-PNTR-007 is already the name of another unit. Rename one first."
            in resp.get_data(as_text=True))
    db.session.expire_all()
    assert db.session.get(InvUnit, unit.id).condition == "RETIRED"
    assert db.session.get(InvUnit, other.id).condition == "IN_SERVICE"


def test_admin_clears_old_notes_and_the_change_is_recorded(client, unit):
    unit.imported_notes = "Invented pickup note"
    unit.notes = "Typed note stays"
    db.session.commit()
    login(client, "test:invadmin")
    base = f"/inv/admin/units/{unit.id}"
    page = client.get(base).get_data(as_text=True)
    assert "Clear old notes" in page
    assert 'data-confirm="Clear the notes from the old system? Typed notes are kept."' in page
    resp = client.post(f"{base}/imported-notes/clear")
    assert resp.status_code == 302
    db.session.expire_all()
    cleared = db.session.get(InvUnit, unit.id)
    assert cleared.imported_notes is None and cleared.notes == "Typed note stays"
    assert _audit(unit) == [("NOTES", "test:invadmin",
                             {"Old notes": {"old": "Invented pickup note", "new": None}})]
    page = client.get(base).get_data(as_text=True)
    assert "From the old system" not in page
    assert "Old notes: Invented pickup note to none" in page


def test_operator_cannot_clear_old_notes(client, unit):
    unit.imported_notes = "Invented pickup note"
    db.session.commit()
    login(client, "test:operator")
    base = f"/inv/admin/units/{unit.id}"
    assert "Clear old notes" not in client.get(base).get_data(as_text=True)
    assert client.post(f"{base}/imported-notes/clear").status_code == 403
    db.session.expire_all()
    assert db.session.get(InvUnit, unit.id).imported_notes == "Invented pickup note"


def test_clearing_absent_old_notes_records_nothing(client, unit):
    login(client, "test:invadmin")
    client.post(f"/inv/admin/units/{unit.id}/imported-notes/clear")
    assert _audit(unit) == []
