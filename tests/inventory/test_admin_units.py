"""The laptop units page: search, the unit page, corrections and label printing."""
import pytest

from app import db
from app.inventory.devices import enroll_device
from app.inventory.flags import raise_flag
from app.inventory.ledger import Actor, add_unit, retire_identifier
from app.inventory.resolver import KIND_UNIT, resolve
from app.models import InvPrinter, InvPrintJob, InvPrintRoute, InvUnit, InvUnitIdentifier, InvUnitNote
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


def test_empty_search_lists_everything_and_unknown_finds_nothing(client, unit):
    login(client, "test:operator")
    assert f"/inv/admin/units/{unit.id}" in _search(client, "")
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


def _add_serial(client, unit, value):
    before = db.session.query(InvUnitIdentifier).count()
    resp = client.post(f"/inv/admin/units/{unit.id}/identifiers",
                       data={"kind": "SERIAL", "value": value}, follow_redirects=True)
    assert db.session.query(InvUnitIdentifier).count() == before
    return resp.get_data(as_text=True)


def test_a_serial_another_unit_holds_is_refused_naming_that_unit(client, inv, unit):
    add_unit(inv.laptop, None, ACTOR, [("FESTOPS_LABEL", "U-0002"), ("SERIAL", "SN-OTHER")])
    db.session.commit()
    login(client, "test:invadmin")
    page = _add_serial(client, unit, "sn-other")
    assert "sn-other belongs to U-0002. Nothing was changed." in page


def test_a_model_barcode_is_refused_as_a_serial(client, inv, unit):
    inv.laptop.barcode = "012345678905"
    db.session.commit()
    login(client, "test:invadmin")
    assert "That is the model barcode. Scan the serial (S/N)." in _add_serial(
        client, unit, "012345678905")


def test_a_serial_over_the_length_limit_is_refused(client, unit):
    login(client, "test:invadmin")
    assert "That serial is longer than 64 characters." in _add_serial(client, unit, "S" * 65)


def test_the_units_own_number_is_refused_as_a_serial(client, unit):
    login(client, "test:invadmin")
    assert "U-0001 is a unit number, not a serial. Scan the SERIAL." in _add_serial(
        client, unit, "U-0001")


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
    data = {"name": "MAG-TV-107",
            f"field_{size.id}": "50", f"field_{res.id}": "4k", f"field_{hdmi.id}": ""}
    data.update(extra)
    return data


def test_admin_saves_name_and_fields_in_one_form(client, inv, unit):
    kind = _tv_kind(inv)
    login(client, "test:invadmin")
    resp = client.post(f"/inv/admin/units/{unit.id}/details", data=_details(unit, kind))
    assert resp.status_code == 302
    page = client.get(f"/inv/admin/units/{unit.id}").get_data(as_text=True)
    for text in ("MAG-TV-107", "50 in", "4k", "Name: none to MAG-TV-107"):
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
    assert "Page 1 of 2" in page


def test_no_cut_notice_when_everything_fits(client, inv):
    _many_tvs(inv, 3)
    login(client, "test:operator")
    page = client.get("/inv/admin/units", query_string={"kind": "TV"}).get_data(as_text=True)
    assert "Page 1 of" not in page


def test_unicode_digit_filter_is_ignored(client, inv):
    kind, units = _tvs(inv)
    res = kind.fields[1]
    login(client, "test:operator")
    for junk in ("²", "9" * 40):
        resp = client.get("/inv/admin/units", query_string={"kind": "TV", f"f_{res.id}": junk})
        assert resp.status_code == 200
        assert _ids_in(resp.get_data(as_text=True), units) == [u.id for u in units]


def test_retired_units_are_hidden_until_searched_for_and_the_page_says_so(client, inv):
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


def test_operator_adds_a_note(client, unit):
    login(client, "test:operator")
    resp = client.post(f"/inv/admin/units/{unit.id}/notes",
                       data={"note_type": "PICKER", "body": "Invented note"})
    assert resp.status_code == 302
    note = db.session.query(InvUnitNote).one()
    assert note.note_type == "PICKER" and note.created_by_user_id == "test:operator"
    assert note.body == "Invented note"


def test_forged_note_type_is_refused(client, unit):
    login(client, "test:operator")
    client.post(f"/inv/admin/units/{unit.id}/notes", data={"note_type": "SECRET", "body": "x"})
    assert db.session.query(InvUnitNote).count() == 0


def test_nobody_cannot_add_a_note(client, unit):
    login(client, "test:nobody")
    resp = client.post(f"/inv/admin/units/{unit.id}/notes",
                       data={"note_type": "ADMIN", "body": "x"})
    assert resp.status_code == 403
    assert db.session.query(InvUnitNote).count() == 0


def test_operator_still_cannot_edit_details(client, unit):
    login(client, "test:operator")
    assert client.post(f"/inv/admin/units/{unit.id}/details", data={"name": "X"}).status_code == 403


def test_save_details_leaves_notes_alone(client, unit):
    unit.notes = "Invented earlier"
    db.session.commit()
    login(client, "test:invadmin")
    client.post(f"/inv/admin/units/{unit.id}/details", data={"name": "X"})
    db.session.expire_all()
    assert db.session.get(InvUnit, unit.id).notes == "Invented earlier"
    rows = _audit(unit)
    assert rows and all("notes" not in changes for _, _, changes in rows)


def test_clear_old_notes_route_is_gone(client, unit):
    login(client, "test:invadmin")
    resp = client.post(f"/inv/admin/units/{unit.id}/imported-notes/clear")
    assert resp.status_code in (404, 405)


@pytest.fixture
def bare(inv):
    unit = add_unit(inv.laptop, None, ACTOR, [("SERIAL", "SN-BARE01")], name="MAG-LAMP-003")
    db.session.commit()
    return unit


def _numbers(unit):
    return [i.value for i in db.session.query(InvUnitIdentifier).filter_by(
        unit_id=unit.id, retired_at=None, kind="FESTOPS_LABEL")]


GIVE = "Give this unit a U- number and print"


def test_admin_gives_an_unnumbered_unit_a_u_number_and_prints(client, bare, printer):
    login(client, "test:invadmin")
    base = f"/inv/admin/units/{bare.id}"
    assert GIVE in client.get(base).get_data(as_text=True)
    resp = client.post(f"{base}/give-number", follow_redirects=True)
    (code,) = _numbers(bare)
    assert code.startswith("U-")
    assert f"{code} added and sent to the printer." in resp.get_data(as_text=True)
    (job,) = db.session.query(InvPrintJob).all()
    assert job.unit_id == bare.id and job.code == code
    (action, who, changes), = _audit(bare)
    assert action == "ADD_CODE" and who == "test:invadmin"
    assert f"FestOps label {code}" in changes["codes"]["new"]
    assert GIVE not in client.get(base).get_data(as_text=True)


def test_give_number_is_refused_when_the_unit_has_one(client, unit, printer):
    login(client, "test:invadmin")
    base = f"/inv/admin/units/{unit.id}"
    assert GIVE not in client.get(base).get_data(as_text=True)
    resp = client.post(f"{base}/give-number", follow_redirects=True)
    assert "already has a number" in resp.get_data(as_text=True)
    assert _numbers(unit) == ["U-0001"]
    assert db.session.query(InvPrintJob).count() == 0 and _audit(unit) == []


def test_give_number_is_hidden_and_refused_for_a_retired_unit(client, bare, printer):
    from app.inventory.ledger import set_condition
    set_condition(bare, "RETIRED")
    db.session.commit()
    login(client, "test:invadmin")
    base = f"/inv/admin/units/{bare.id}"
    assert GIVE not in client.get(base).get_data(as_text=True)
    resp = client.post(f"{base}/give-number", follow_redirects=True)
    assert "A retired unit cannot be given a number." in resp.get_data(as_text=True)
    assert _numbers(bare) == [] and db.session.query(InvPrintJob).count() == 0


def test_give_number_without_a_printer_still_adds_the_number(client, bare):
    login(client, "test:invadmin")
    resp = client.post(f"/inv/admin/units/{bare.id}/give-number", follow_redirects=True)
    (code,) = _numbers(bare)
    page = resp.get_data(as_text=True)
    assert f"{code} added, but the label could not be sent." in page
    assert "No printer is set for this label." in page
    assert db.session.query(InvPrintJob).count() == 0
    assert [a for a, _, _ in _audit(bare)] == ["ADD_CODE"]


def test_operator_sees_no_give_number_button(client, bare, printer):
    login(client, "test:operator")
    base = f"/inv/admin/units/{bare.id}"
    assert GIVE not in client.get(base).get_data(as_text=True)
    assert client.post(f"{base}/give-number").status_code == 403
    assert _numbers(bare) == []


def _selects(fn):
    """Count SELECT statements run by fn."""
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


def _placed_tvs(inv, start, count):
    from tests.inventory.helpers import make_container
    for i in range(start, start + count):
        bin_ = make_container(inv, f"C-{9000 + i}", kind="BIN")
        add_unit(inv.laptop, bin_, ACTOR, [("FESTOPS_LABEL", f"PL-{i:03d}")],
                 name=f"Placed TV {i:03d}")
    db.session.commit()


def test_kind_and_search_lists_load_related_rows_up_front(client, inv):
    _tv_kind(inv)
    login(client, "test:operator")
    _placed_tvs(inv, 0, 3)
    for args in ({"kind": "TV"}, {"q": "placed tv"}):
        few = _selects(lambda: client.get("/inv/admin/units", query_string=args))
        _placed_tvs(inv, len(db.session.query(InvUnit).all()), 4)
        more = _selects(lambda: client.get("/inv/admin/units", query_string=args))
        assert more == few, args


def test_kind_list_puts_unnamed_units_last(client, inv):
    _tv_kind(inv)
    plain = add_unit(inv.laptop, None, ACTOR, [("FESTOPS_LABEL", "PL-900")])
    named = add_unit(inv.laptop, None, ACTOR, [("FESTOPS_LABEL", "PL-901")], name="Zed TV")
    first = add_unit(inv.laptop, None, ACTOR, [("FESTOPS_LABEL", "PL-902")], name="Alpha TV")
    db.session.commit()
    login(client, "test:operator")
    page = client.get("/inv/admin/units", query_string={"kind": "TV"}).get_data(as_text=True)
    order = sorted((page.index(f"/inv/admin/units/{u.id}\""), u.id) for u in (plain, named, first))
    assert [uid for _, uid in order] == [first.id, named.id, plain.id]


@pytest.fixture
def nameless_number(inv):
    unit = add_unit(inv.laptop, None, ACTOR, [], name="Lobby Laptop")
    db.session.commit()
    return unit


def test_unit_without_a_number_says_so_and_never_shows_its_database_id(client, nameless_number):
    login(client, "test:operator")
    page = _search(client, "Lobby")
    assert "Lobby Laptop" in page
    assert '<span class="muted">No number yet</span>' in page
    assert f"unit {nameless_number.id}" not in page


def test_unit_page_shows_the_internal_id_as_a_muted_line(client, nameless_number):
    login(client, "test:operator")
    page = client.get(f"/inv/admin/units/{nameless_number.id}").get_data(as_text=True)
    assert (f'<div class="muted">Internal database ID: {nameless_number.id}. '
            "For troubleshooting only; it is never printed or scanned.</div>") in page
    assert f"unit {nameless_number.id}" not in page


def test_flag_on_a_unit_with_no_number_reads_in_words(client, nameless_number):
    raise_flag(ACTOR, "COUNT", "DAMAGED", unit=nameless_number)
    db.session.commit()
    login(client, "test:operator")
    page = client.get("/inv/admin/flags").get_data(as_text=True)
    assert "Lobby Laptop (Laptop)" in page
    assert f"unit {nameless_number.id}" not in page


def test_second_give_number_is_refused_when_the_first_won_the_race(
        client, unit, printer, monkeypatch):
    from app.models import InvLabelSequence
    monkeypatch.setattr("app.routes.inventory.admin_units._can_give_number", lambda u: True)
    login(client, "test:invadmin")
    before = db.session.query(InvLabelSequence.next_value).filter_by(prefix="U").scalar()
    resp = client.post(f"/inv/admin/units/{unit.id}/give-number", follow_redirects=True)
    assert "This unit already has a number." in resp.get_data(as_text=True)
    assert _numbers(unit) == ["U-0001"]
    assert db.session.query(InvPrintJob).count() == 0
    assert db.session.query(InvLabelSequence.next_value).filter_by(prefix="U").scalar() == before


def _list(client, **args):
    return client.get("/inv/admin/units", query_string=args)


def _week_units(inv, count=3, names=None):
    from app.models import EventCycle
    event = EventCycle(code="SMF2027", name="SMF 2027", is_active=True, is_default=True)
    db.session.add(event)
    units = [add_unit(inv.laptop, None, ACTOR, [("FESTOPS_LABEL", f"WK-{i:03d}")],
                      name=(names[i] if names else f"Week unit {i:03d}")) for i in range(count)]
    db.session.commit()
    return event, units


def test_units_page_lists_without_a_search(client, inv):
    _week_units(inv)
    login(client, "test:operator")
    assert _list(client).data.count(b'class="unit-row"') == 3


def test_retired_units_show_only_when_searched_for(client, inv):
    _, units = _week_units(inv)
    units[0].condition = "RETIRED"
    db.session.commit()
    login(client, "test:operator")
    assert _list(client).data.count(b'class="unit-row"') == 2
    assert _list(client, q="WK-000").data.count(b'class="unit-row"') == 1


def test_show_filters_narrow_the_list(client, inv):
    from app.models import InvPrepStatus
    event, units = _week_units(inv, 4)
    nameless = add_unit(inv.laptop, None, ACTOR, [], name="No tag")
    db.session.add_all([
        InvPrepStatus(unit_id=units[0].id, event_cycle_id=event.id, status="CHECKED"),
        InvPrepStatus(unit_id=units[1].id, event_cycle_id=event.id, status="FAILED_CHECK")])
    db.session.commit()
    login(client, "test:operator")

    def ids(show):
        page = _list(client, show=show).get_data(as_text=True)
        return {u.id for u in units + [nameless] if f"/inv/admin/units/{u.id}\"" in page}

    assert ids("seen") == {units[0].id, units[1].id}
    assert ids("not_seen") == {units[2].id, units[3].id, nameless.id}
    assert ids("broken") == {units[1].id}
    assert ids("no_number") == {nameless.id}


def test_show_filters_leave_out_rented_units_and_the_plain_list_keeps_them(client, inv):
    from app.models import InvPrepStatus
    event, (owned,) = _week_units(inv, 1)
    rented = add_unit(inv.laptop, None, ACTOR, [], name="Rented laptop")
    rented.ownership = "RENTED"
    seen_rental = add_unit(inv.laptop, None, ACTOR, [("SERIAL", "SN-RENT-2")], name="Rental two")
    seen_rental.ownership = "RENTED"
    db.session.add(InvPrepStatus(unit_id=seen_rental.id, event_cycle_id=event.id,
                                 status="CHECKED"))
    db.session.commit()
    login(client, "test:operator")

    def ids(**args):
        page = _list(client, **args).get_data(as_text=True)
        return {u.id for u in (owned, rented, seen_rental) if f"/inv/admin/units/{u.id}\"" in page}

    assert ids(show="no_number") == set()
    assert ids(show="not_seen") == {owned.id}
    assert ids(show="seen") == set()
    assert ids() == {owned.id, rented.id, seen_rental.id}


def test_page_shows_week_and_where_and_counts(client, inv):
    from app.models import InvPrepStatus
    from tests.inventory.helpers import make_container
    event, units = _week_units(inv, 2)
    bin_ = make_container(inv, "C-7001", kind="BIN", name="Back shelf")
    units[0].current_container_id = bin_.id
    db.session.add(InvPrepStatus(unit_id=units[0].id, event_cycle_id=event.id, status="CHECKED"))
    db.session.commit()
    login(client, "test:operator")
    page = _list(client).get_data(as_text=True)
    assert "2 units, 1 seen, 0 broken" in page
    assert "C-7001 Back shelf" in page and "Not seen" in page and "Works" in page
    assert ">More filters<" in page


def test_no_default_event_hides_week_column(client, inv):
    add_unit(inv.laptop, None, ACTOR, [("FESTOPS_LABEL", "WK-001")])
    db.session.commit()
    login(client, "test:operator")
    page = _list(client).get_data(as_text=True)
    assert "No default event is set." in page
    assert "This week" not in page and "No number yet</button>" not in page


def test_list_pages_fifty_at_a_time(client, inv):
    _week_units(inv, 55)
    login(client, "test:operator")
    assert _list(client).data.count(b'class="unit-row"') == 50
    assert _list(client, page=2).data.count(b'class="unit-row"') == 5


def test_csv_holds_every_matching_row(client, inv):
    event, _ = _week_units(inv, 55)
    login(client, "test:operator")
    resp = _list(client, format="csv")
    assert resp.mimetype == "text/csv"
    assert resp.headers["Content-Disposition"].startswith(
        'attachment; filename="units-SMF2027-')
    lines = resp.get_data(as_text=True).strip().splitlines()
    assert lines[0] == ("Number,Name,Type,Model,This week,Condition,Where,Open flags,"
                        "Last checked")
    assert len(lines) == 56


def test_csv_neutralises_formulas(client, inv):
    _week_units(inv, 1, names=["=SUM(A1)"])
    login(client, "test:operator")
    assert "'=SUM(A1)" in _list(client, format="csv").get_data(as_text=True)


def test_csv_last_checked_is_warehouse_local_time(client, inv):
    from datetime import datetime
    from app.models import InvPrepStatus
    event, units = _week_units(inv, 1)
    db.session.add(InvPrepStatus(unit_id=units[0].id, event_cycle_id=event.id, status="CHECKED",
                                 created_at=datetime(2026, 12, 8, 2, 30)))
    db.session.commit()
    login(client, "test:operator")
    assert ",2026-12-07 21:30" in _list(client, format="csv").get_data(as_text=True)


def test_local_iso_converts_utc():
    from datetime import datetime
    from app.inventory.printing import local_iso
    assert local_iso(datetime(2026, 12, 8, 2, 30)) == "2026-12-07 21:30"


def test_csv_filename_uses_local_date(client, inv, monkeypatch):
    from datetime import date
    _week_units(inv, 1)
    monkeypatch.setattr("app.routes.inventory.admin_units.local_today",
                        lambda: date(2026, 12, 4))
    login(client, "test:operator")
    assert "units-SMF2027-2026-12-04.csv" in _list(client, format="csv").headers[
        "Content-Disposition"]


def test_csv_leaves_week_cells_blank_without_a_default_event(client, inv):
    add_unit(inv.laptop, None, ACTOR, [("FESTOPS_LABEL", "WK-001")])
    db.session.commit()
    login(client, "test:operator")
    row = _list(client, format="csv").get_data(as_text=True).splitlines()[1].split(",")
    assert row[4] == "" and row[6] == ""


def test_csv_honours_the_show_filter(client, inv):
    from app.models import InvPrepStatus
    event, units = _week_units(inv, 3)
    db.session.add(InvPrepStatus(unit_id=units[1].id, event_cycle_id=event.id,
                                 status="FAILED_CHECK"))
    db.session.commit()
    login(client, "test:operator")
    lines = _list(client, format="csv", show="broken").get_data(as_text=True).splitlines()
    assert len(lines) == 2 and "WK-001" in lines[1]


def test_pager_next_link_keeps_the_filters(client, inv):
    import re
    from html import unescape
    _tv_kind(inv)
    for i in range(55):
        add_unit(inv.laptop, None, ACTOR, [("FESTOPS_LABEL", f"PG-{i:03d}")], name=f"Pager {i:03d}")
    db.session.commit()
    login(client, "test:operator")
    page = _list(client, q="pager", kind="TV", show="not_seen").get_data(as_text=True)
    href = unescape(re.search(r'<a href="([^"]*)">Next</a>', page).group(1))
    assert "q=pager" in href and "kind=TV" in href and "show=not_seen" in href
    assert "page=2" in href


def test_csv_does_not_query_per_unit(client, inv):
    from app.models import EventCycle, InvContainerKind, InvPrepStatus
    from tests.inventory.helpers import make_container
    login(client, "test:operator")
    event = EventCycle(code="SMF2027", name="SMF 2027", is_active=True, is_default=True)
    db.session.add(event)
    db.session.flush()

    def add_placed(count, start):
        # Each unit gets its own container and container kind, so a lazy load of
        # container.kind cannot be satisfied from the session's identity map.
        for i in range(start, start + count):
            kind = InvContainerKind(code=f"K{i}", name=f"Kind {i}", sort_order=i, is_active=True)
            db.session.add(kind)
            db.session.flush()
            inv.kinds[f"K{i}"] = kind
            box = make_container(inv, f"C-72{i:02d}", kind=f"K{i}")
            unit = add_unit(inv.laptop, box, ACTOR, [("FESTOPS_LABEL", f"QC-{i:03d}")])
            db.session.flush()
            # Only a seen unit shows its container, so each one needs a prep row.
            db.session.add(InvPrepStatus(unit_id=unit.id, event_cycle_id=event.id,
                                         status="CHECKED"))
        db.session.commit()

    add_placed(2, 0)
    few = _selects(lambda: _list(client, format="csv"))
    add_placed(4, 2)
    assert _selects(lambda: _list(client, format="csv")) == few


def _page(client, unit):
    return client.get(f"/inv/admin/units/{unit.id}").get_data(as_text=True)


def _prep(event, unit, status, when=None):
    from app.models import InvPrepStatus
    db.session.add(InvPrepStatus(unit_id=unit.id, event_cycle_id=event.id, status=status,
                                 **({"created_at": when} if when else {})))
    db.session.commit()


def test_unit_page_summary_says_works_with_local_time(client, inv):
    from datetime import datetime
    event, (u,) = _week_units(inv, 1)
    _prep(event, u, "CHECKED", datetime(2026, 12, 8, 15, 14))
    login(client, "test:operator")
    page = _page(client, u)
    assert "This week: Works, 2026-12-08 10:14" in page
    assert page.index("This week:") < page.index("Fix this unit")


def test_unit_page_summary_says_broken(client, inv):
    event, (u,) = _week_units(inv, 1)
    _prep(event, u, "FAILED_CHECK")
    login(client, "test:operator")
    assert "This week: Broken," in _page(client, u)


def test_unit_page_summary_not_seen_has_no_time(client, inv):
    _, (u,) = _week_units(inv, 1)
    login(client, "test:operator")
    page = _page(client, u)
    assert "This week: Not seen<" in page
    assert "Where: Not seen this week.<" in page


def test_unit_page_not_seen_names_the_last_recorded_container(client, inv):
    from tests.inventory.helpers import make_container
    _week_units(inv, 0)
    box = make_container(inv, "C-9002", name="Shelf two")
    u = add_unit(inv.laptop, box, ACTOR, [("FESTOPS_LABEL", "PL-002")])
    db.session.commit()
    login(client, "test:operator")
    assert "Where: Not seen this week. Last recorded in C-9002 Shelf two.<" in _page(client, u)


def test_unit_page_summary_omits_this_week_without_a_default_event(client, unit):
    login(client, "test:operator")
    page = _page(client, unit)
    assert "This week:" not in page
    assert "Where: Not placed<" in page
    assert "Condition: In service" in page


def test_unit_page_without_a_default_event_shows_the_container(client, inv):
    from tests.inventory.helpers import make_container
    box = make_container(inv, "C-9003", name="Shelf three")
    u = add_unit(inv.laptop, box, ACTOR, [("FESTOPS_LABEL", "PL-003")])
    db.session.commit()
    login(client, "test:operator")
    assert "Where: C-9003 Shelf three<" in _page(client, u)


def test_units_list_still_says_not_seen_for_a_placed_unit(client, inv):
    from tests.inventory.helpers import make_container
    _week_units(inv, 0)
    box = make_container(inv, "C-9004", name="Shelf four")
    add_unit(inv.laptop, box, ACTOR, [("FESTOPS_LABEL", "PL-004")])
    db.session.commit()
    login(client, "test:operator")
    assert "Last recorded" not in _list(client).get_data(as_text=True)
    assert ",Not seen," in _list(client, format="csv").get_data(as_text=True)


def test_unit_page_summary_shows_where_for_a_placed_unit(client, inv):
    from tests.inventory.helpers import make_container
    event, _ = _week_units(inv, 0)
    box = make_container(inv, "C-9001", name="Shelf nine")
    u = add_unit(inv.laptop, box, ACTOR, [("FESTOPS_LABEL", "PL-001")])
    db.session.commit()
    _prep(event, u, "CHECKED")
    login(client, "test:operator")
    assert "Where: C-9001 Shelf nine<" in _page(client, u)


def test_unit_page_summary_counts_open_flags(client, unit):
    login(client, "test:operator")
    assert "Open flags: 0" in _page(client, unit)
    raise_flag(ACTOR, "LOOKUP", "DAMAGED", unit=unit)
    db.session.commit()
    assert "Open flags: 1" in _page(client, unit)


def test_fix_this_unit_is_closed_on_a_normal_load(client, unit):
    login(client, "test:invadmin")
    page = _page(client, unit)
    assert '<details id="fix-unit">' in page


def test_fix_this_unit_opens_after_a_refused_identifier_or_number(client, inv, unit):
    login(client, "test:invadmin")
    resp = client.post(f"/inv/admin/units/{unit.id}/identifiers",
                       data={"kind": "SERIAL", "value": "U-0099"}, follow_redirects=True)
    assert '<details id="fix-unit" open>' in resp.get_data(as_text=True)
    resp = client.post(f"/inv/admin/units/{unit.id}/give-number", follow_redirects=True)
    assert "already has a number" in resp.get_data(as_text=True)
    assert '<details id="fix-unit" open>' in resp.get_data(as_text=True)
    assert '<details id="fix-unit">' in _page(client, unit)


def test_every_form_still_posts_where_it_did(client, inv):
    bare_unit = add_unit(inv.laptop, None, ACTOR, [("SERIAL", "SN-FORMS1")], name="Forms unit")
    db.session.commit()
    login(client, "test:invadmin")
    page = _page(client, bare_unit)
    base = f"/inv/admin/units/{bare_unit.id}"
    serial = next(i for i in bare_unit.identifiers if i.value == "SN-FORMS1")
    for suffix in ("/details", "/notes", "/print", "/give-number",
                   "/identifiers", "/condition", "/item", f"/identifiers/{serial.id}/retire"):
        assert f'action="/inv{base[4:]}{suffix}"' in page, suffix
