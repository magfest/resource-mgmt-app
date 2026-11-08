"""The old tech export importer. Notes and asset tag numbers here are invented."""
from decimal import Decimal

import pytest

from app import db
from app.inventory.ledger import Actor, add_unit
from app.inventory.legacy_import import apply, model_key, preview, read_export
from app.models import InvUnit, InvUnitIdentifier, SupplyItem
from app.models.constants import (
    INV_CONDITION_IN_SERVICE, INV_CONDITION_LOST, INV_CONDITION_REPAIR,
    INV_FIELD_BOOL, INV_FIELD_CHOICE, INV_FIELD_NUMBER, INV_FIELD_TEXT,
    INV_IDENT_MAGFEST_TAG,
)
from tests.inventory.helpers import login, seed_gear_kind

ACTOR = Actor(user_id="test:invadmin")
BOM = "﻿"

TV_HEADER = "id,ID,Asset Tag,Model,Size,Department,Resolution,Status,Room,Notes\n"
PRINTER_HEADER = ("id,ID,Asset Tag,Type,Notes,Inventoried,Department,Status,"
                  "Toner Cartridge\n")


def _csv(header, *rows) -> bytes:
    return (BOM + header + "".join(r + "\n" for r in rows)).encode("utf-8")


MODEL = ("MODEL", "Model", INV_FIELD_TEXT, None)


@pytest.fixture
def kinds(inv):
    return {
        "TV": seed_gear_kind("TV", "TV", [
            MODEL, ("BRAND", "Brand", INV_FIELD_TEXT, None),
            ("SIZE", "Size", INV_FIELD_NUMBER, "in"),
            ("RESOLUTION", "Resolution", INV_FIELD_CHOICE, ["720p", "1080p", "4k"]),
            ("HDMI", "HDMI", INV_FIELD_BOOL, None)]),
        "MONITOR": seed_gear_kind("MONITOR", "Monitor", [
            MODEL, ("SIZE", "Size", INV_FIELD_NUMBER, "in"),
            ("RESOLUTION", "Resolution", INV_FIELD_CHOICE,
             ["1920x1080", "1920x1200", "2560x1440", "4k"])]),
        "PRINTER": seed_gear_kind("PRINTER", "Printer", [
            MODEL, ("TONER", "Toner cartridge", INV_FIELD_TEXT, None)]),
        "LABEL_PRINTER": seed_gear_kind("LABEL_PRINTER", "Label printer", [
            MODEL, ("MEDIA", "Tape or media", INV_FIELD_TEXT, None)]),
        "LAMINATOR": seed_gear_kind("LAMINATOR", "Laminator", [MODEL]),
        "PROJECTOR": seed_gear_kind("PROJECTOR", "Projector", [MODEL]),
        "BARCODE_SCANNER": seed_gear_kind("BARCODE_SCANNER", "Barcode scanner", [MODEL]),
        "CAMERA": seed_gear_kind("CAMERA", "Camera", [MODEL]),
    }


TV_FILE = _csv(
    TV_HEADER,
    "1,MAG-TV-107,MT900001,Roku,50,,4k,Picked Up,,Invented remote note",
    "2,MAG-TV-108,,Roku,50,,4k,Broken (Do not use),,",
    "3,MAG-MON-001,MT900002,Dell U2412M,24,,1920x1200,Missing (Do not use),,",
    "4,MAG-MON-002,,Dell U2412M,24,,1920x1200,Returned,,",
    "5,MAG-MON-003,,Dell U2713HMT,27,,2560x1440,,,",
)


def _values(unit):
    return {v.field.code: v for v in unit.values}


def _unit(name):
    return db.session.query(InvUnit).filter_by(name=name).one()


def test_bom_header_is_read(inv):
    rows = read_export(TV_FILE)
    assert [r.name for r in rows][:2] == ["MAG-TV-107", "MAG-TV-108"]
    assert rows[0].line == 2 and rows[0].tag == "MT900001"
    assert rows[0].model_text == "Roku" and rows[0].notes == "Invented remote note"


def test_preview_lists_each_model_text_once_with_counts(kinds):
    result = preview(TV_FILE, {})
    counts = {(m.key, m.gear_kind_code): m.count for m in result.models}
    assert counts == {("TV|", "TV"): 2, ("MONITOR|Dell U2412M", "MONITOR"): 2,
                      ("MONITOR|Dell U2713HMT", "MONITOR"): 1}
    assert model_key(read_export(TV_FILE)[0]) == "TV|"
    assert result.ready == [] and len(result.review) == 5


def test_apply_creates_named_units_with_fields_tag_condition_and_notes(kinds, inv):
    mapping = {"TV|": "new:TV", "MONITOR|Dell U2412M": "new:Monitor",
               "MONITOR|Dell U2713HMT": "new:Monitor"}
    result = apply(TV_FILE, mapping, inv.category.id, ACTOR)
    assert len(result.ready) == 5 and result.review == []
    unit = _unit("MAG-TV-107")
    assert unit.current_container_id is None
    assert unit.item.item_name == "TV" and unit.item.gear_kind_id == kinds["TV"].id
    tag = db.session.query(InvUnitIdentifier).filter_by(unit_id=unit.id).one()
    assert (tag.kind, tag.value) == (INV_IDENT_MAGFEST_TAG, "MT900001")
    values = _values(unit)
    assert values["BRAND"].text_value == "Roku"
    assert values["SIZE"].number_value == Decimal("50")
    assert values["RESOLUTION"].choice.label == "4k"
    assert "MODEL" not in values
    assert unit.condition == INV_CONDITION_IN_SERVICE
    assert unit.imported_notes == "Invented remote note"
    assert _values(_unit("MAG-MON-003"))["MODEL"].text_value == "Dell U2713HMT"


def test_row_values_win_over_model_defaults(kinds, inv):
    from app.inventory.unit_fields import set_model_default
    tv = SupplyItem(item_name="TV", unit="each", category_id=inv.category.id,
                    is_serialized=True, is_orderable=False, gear_kind_id=kinds["TV"].id)
    db.session.add(tv)
    db.session.flush()
    fields = {f.code: f for f in kinds["TV"].fields}
    set_model_default(tv, fields["SIZE"], "70")
    set_model_default(tv, fields["HDMI"], "yes")
    db.session.commit()
    data = _csv(TV_HEADER, "1,MAG-TV-020,,Insignia,58,,4k,Ready,,",
                "2,MAG-TV-021,,Insignia,,,4k,Ready,,")
    apply(data, {"TV|": str(tv.id)}, None, ACTOR)
    assert _values(_unit("MAG-TV-020"))["SIZE"].number_value == Decimal("58")
    assert _values(_unit("MAG-TV-020"))["HDMI"].bool_value is True
    assert _values(_unit("MAG-TV-021"))["SIZE"].number_value == Decimal("70")


def test_printer_model_text_goes_to_the_model_field_under_the_chosen_type(kinds, inv):
    data = _csv(PRINTER_HEADER,
                '1,MAG-PNTR-007,,MFC-5890cn,,True,,Returned,"LC61C,LC61M,LC61Y,LC61BK,LC65"')
    apply(data, {"PRINTER|MFC-5890cn": "new:Inkjet Printer"}, inv.category.id, ACTOR)
    unit = _unit("MAG-PNTR-007")
    assert unit.item.item_name == "Inkjet Printer"
    assert _values(unit)["MODEL"].text_value == "MFC-5890cn"
    assert _values(unit)["TONER"].text_value == "LC61C,LC61M,LC61Y,LC61BK,LC65"


def test_two_model_texts_mapped_to_one_new_type_create_it_once(kinds, inv):
    data = _csv(PRINTER_HEADER,
                "1,MAG-PNTR-001,,Brother HL-6200DW,,True,,Returned,\"TN820,TN850,TN880\"",
                "2,MAG-PNTR-002,,Brother HL-L2350DW,,True,,Returned,TN730,TN760")
    mapping = {"PRINTER|Brother HL-6200DW": "new:Laser Printer",
               "PRINTER|Brother HL-L2350DW": "new:Laser Printer"}
    apply(data, mapping, inv.category.id, ACTOR)
    items = db.session.query(SupplyItem).filter_by(item_name="Laser Printer").all()
    assert len(items) == 1
    assert {u.item_id for u in db.session.query(InvUnit)} == {items[0].id}


def test_status_maps_to_lasting_condition_only(kinds, inv):
    data = _csv(PRINTER_HEADER,
                "1,MAG-PNTR-003,,Brother HL-L2320D,,True,,Do Not Deploy,TN660",
                "2,MAG-PNTR-004,,Brother HL-L2320D,,True,,Returned,TN660",
                "3,MAG-PNTR-005,,Brother HL-L2320D,,True,,,TN660",
                "4,,,,,False,,,")
    apply(data, {"PRINTER|Brother HL-L2320D": "new:Laser Printer"}, inv.category.id, ACTOR)
    apply(TV_FILE, {"TV|": "new:TV", "MONITOR|Dell U2412M": "new:Monitor",
                    "MONITOR|Dell U2713HMT": "new:Monitor"}, inv.category.id, ACTOR)
    assert db.session.query(InvUnit).filter(InvUnit.name.is_(None)).count() == 0
    assert _unit("MAG-PNTR-003").condition == INV_CONDITION_REPAIR
    assert _unit("MAG-PNTR-004").condition == INV_CONDITION_IN_SERVICE
    assert _unit("MAG-PNTR-005").condition == INV_CONDITION_IN_SERVICE
    assert _unit("MAG-TV-108").condition == INV_CONDITION_REPAIR
    assert _unit("MAG-MON-001").condition == INV_CONDITION_LOST
    assert _unit("MAG-MON-002").condition == INV_CONDITION_IN_SERVICE


def test_blank_unknown_and_duplicate_names_go_to_review(kinds, inv):
    data = _csv(TV_HEADER,
                "1,???,,Samsung,32,,1080p,,,",
                "2,,,LG,40,,1080p,,,",
                "3,Spare TV,,LG,40,,1080p,,,",
                "4,MAG-TV-040,,LG,40,,1080p,,,",
                "5,mag-tv-040 ,,LG,40,,1080p,,,",
                "6,MAG-TV-041,,LG,40,,1080p,,,",
                ",,,,,,,,,",
                "8,,,,,,,,,")
    result = preview(data, {"TV|": "new:TV"}, inv.category.id)
    assert [r.name for r in result.ready] == ["MAG-TV-041"]
    assert result.review == [
        "Row 2 (???): The ID is ???, so the unit has no name.",
        "Row 3 (no ID): The ID is blank.",
        "Row 4 (Spare TV): The ID does not start with a known prefix.",
        "Row 5 (MAG-TV-040): The ID appears more than once in this file.",
        "Row 6 (mag-tv-040): The ID appears more than once in this file.",
    ]


def test_unmapped_held_tag_and_missing_category_go_to_review(kinds, inv):
    add_unit(inv.laptop, None, ACTOR, [(INV_IDENT_MAGFEST_TAG, "MT900002")])
    db.session.commit()
    result = preview(TV_FILE, {"TV|": "new:TV"}, None)
    assert "Row 2 (MAG-TV-107): Choose a category for new types." in result.review
    assert "Row 4 (MAG-MON-001): Tag MT900002 is already on another unit." in result.review
    assert "Row 6 (MAG-MON-003): Choose a type for Monitor Dell U2713HMT." in result.review


def test_a_non_serialized_item_of_the_new_name_sends_rows_to_review(kinds, inv):
    data = _csv(PRINTER_HEADER, "1,MAG-LAM-001,,Amazon Basic Thermal Laminator,,True,,,")
    result = preview(data, {"LAMINATOR|Amazon Basic Thermal Laminator": "new:Blue Pen"},
                     inv.category.id)
    assert result.review == [
        "Row 2 (MAG-LAM-001): A non-serialized item is already named Blue Pen."]


def test_a_second_run_skips_every_name_already_imported(kinds, inv):
    mapping = {"TV|": "new:TV", "MONITOR|Dell U2412M": "new:Monitor",
               "MONITOR|Dell U2713HMT": "new:Monitor"}
    apply(TV_FILE, mapping, inv.category.id, ACTOR)
    again = apply(TV_FILE, mapping, inv.category.id, ACTOR)
    assert again.skipped == 5 and again.ready == [] and again.review == []
    assert db.session.query(InvUnit).count() == 5


def test_new_types_are_not_orderable(kinds, inv):
    data = _csv(PRINTER_HEADER, "1,MAG-LPRNT-001,,PTH110BP,,True,,Returned,")
    apply(data, {"LABEL_PRINTER|PTH110BP": "new:Label Printer"}, inv.category.id, ACTOR)
    item = db.session.query(SupplyItem).filter_by(item_name="Label Printer").one()
    assert item.is_serialized is True and item.is_orderable is False
    assert item.unit == "each" and item.gear_kind_id == kinds["LABEL_PRINTER"].id


def test_an_unparseable_size_is_a_warning_and_the_unit_still_imports(kinds, inv):
    data = _csv(TV_HEADER, "1,MAG-TV-050,,Pioneer,big,,1920x1200,,,")
    result = apply(data, {"TV|": "new:TV"}, inv.category.id, ACTOR)
    assert result.warnings == [
        "Row 2 (MAG-TV-050): Size must be a number. Left blank.",
        "Row 2 (MAG-TV-050): Resolution must be one of 720p, 1080p, 4k. Left blank.",
    ]
    values = _values(_unit("MAG-TV-050"))
    assert "SIZE" not in values and "RESOLUTION" not in values
    assert values["BRAND"].text_value == "Pioneer"


def test_a_file_with_no_tech_rows_says_so(kinds):
    data = _csv("id,ID,Type,Quantity,Department,Notes,Pallet,Status,Counted\n",
                "1,Power Bin 00,25' 14AWG,20,,,,,False")
    result = preview(data, {})
    assert result.not_tech is True and result.review == []


PROJECTOR_HEADER = ("id,ID,Asset Tag,Model Number,Notes,Inventoried,Department,Status\n")
CAMERA_HEADER = "id,ID,Asset Tag,Type,Notes,Status\n"


def test_projector_model_number_column_is_the_model(inv, kinds):
    data = _csv(PROJECTOR_HEADER, "1,MAG-PROJ-000,MT900011,955WH,Invented lens note,,,")
    result = preview(data, {"PROJECTOR|955WH": "new:Projector 955WH"}, None)
    assert result.models[0].key == "PROJECTOR|955WH" and result.models[0].text == "955WH"
    apply(data, {"PROJECTOR|955WH": "new:Projector 955WH"}, inv.category.id, ACTOR)
    assert _values(_unit("MAG-PROJ-000"))["MODEL"].text_value == "955WH"


def test_barcode_scanner_prefix_without_mag_is_recognised(inv, kinds):
    data = _csv(CAMERA_HEADER, "1,BRC-001,,Zebra DS2208,,")
    result = preview(data, {})
    assert result.not_tech is False
    assert [m.gear_kind_code for m in result.models] == ["BARCODE_SCANNER"]
    assert result.review == ["Row 2 (BRC-001): Choose a type for Barcode scanner Zebra DS2208."]


def test_doc_cam_and_mag_cam_are_cameras(inv, kinds):
    data = _csv(CAMERA_HEADER, "1,DOC-CAM-001,,Lumens DC193,,", "2,MAG-CAM-002,,Logitech C920,,")
    result = preview(data, {})
    assert {m.gear_kind_code for m in result.models} == {"CAMERA"}
    assert {m.key for m in result.models} == {"CAMERA|Lumens DC193", "CAMERA|Logitech C920"}


def test_longest_prefix_wins():
    from app.inventory.legacy_import import kind_code_for
    assert kind_code_for("mag-cam-004") == "CAMERA"
    assert kind_code_for("MAG-PROJ-004") == "PROJECTOR"
    assert kind_code_for("MAG-TV-004") == "TV"


def test_dead_is_in_repair_and_disposed_is_skipped(inv, kinds):
    from app.inventory.legacy_import import condition_for
    assert condition_for(" Dead ") == "REPAIR"
    data = _csv(CAMERA_HEADER, "1,MAG-CAM-010,,Logitech C920,,Disposed",
                "2,MAG-CAM-011,,Logitech C920,,  DISPOSED ", "3,MAG-CAM-012,,Logitech C920,,Dead")
    result = preview(data, {"CAMERA|Logitech C920": "new:Camera C920"}, None)
    assert result.skipped == 2 and result.review == [
        "Row 4 (MAG-CAM-012): Choose a category for new types."]
    assert [m.count for m in result.models] == [1]


def test_admin_previews_then_applies_through_the_page(client, kinds, inv):
    login(client, "test:invadmin")
    page = client.get("/inv/admin/import/legacy")
    assert page.status_code == 200
    from io import BytesIO
    response = client.post("/inv/admin/import/legacy", data={
        "action": "preview", "file": (BytesIO(TV_FILE), "tv.csv")},
        content_type="multipart/form-data")
    html = response.get_data(as_text=True)
    assert response.status_code == 200 and "Dell U2412M" in html
    text = TV_FILE.decode("utf-8-sig")
    response = client.post("/inv/admin/import/legacy", data={
        "action": "apply", "file_text": text, "category_id": str(inv.category.id),
        "key-0": "TV|", "type-0": "new", "new-0": "TV",
        "key-1": "MONITOR|Dell U2412M", "type-1": "new", "new-1": "Monitor",
        "key-2": "MONITOR|Dell U2713HMT", "type-2": "new", "new-2": "Monitor"})
    assert response.status_code == 200
    assert db.session.query(InvUnit).count() == 5


def test_operator_cannot_open_the_importer(client, inv):
    login(client, "test:operator")
    assert client.get("/inv/admin/import/legacy").status_code == 403
    assert client.post("/inv/admin/import/legacy", data={"action": "preview"}).status_code == 403


TV_MAPPING = {"TV|": "new:TV", "MONITOR|Dell U2412M": "new:Monitor",
              "MONITOR|Dell U2713HMT": "new:Monitor"}


def test_a_failure_mid_apply_leaves_nothing_behind(kinds, inv, monkeypatch):
    from app.inventory import legacy_import
    from app.inventory.ledger import InventoryError
    real = legacy_import.add_unit
    calls = []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("INSERT INTO inv_units secret values")
        return real(*args, **kwargs)

    monkeypatch.setattr(legacy_import, "add_unit", flaky)
    with pytest.raises(InventoryError) as error:
        apply(TV_FILE, TV_MAPPING, inv.category.id, ACTOR)
    assert str(error.value) == "Nothing was imported. The error was logged."
    assert db.session.query(InvUnit).count() == 0
    assert db.session.query(SupplyItem).filter(
        SupplyItem.item_name.in_(["TV", "Monitor"])).count() == 0


def test_a_name_held_only_by_a_retired_unit_imports(kinds, inv):
    from app.inventory.ledger import set_condition
    from app.models.constants import INV_CONDITION_RETIRED
    old = add_unit(inv.laptop, None, ACTOR, [], name="MAG-TV-107")
    set_condition(old, INV_CONDITION_RETIRED)
    db.session.commit()
    result = apply(TV_FILE, TV_MAPPING, inv.category.id, ACTOR)
    assert result.skipped == 0 and len(result.ready) == 5
    live = db.session.query(InvUnit).filter(InvUnit.name == "MAG-TV-107",
                                            InvUnit.condition != INV_CONDITION_RETIRED)
    assert live.one().item.item_name == "TV"


def test_a_crlf_note_from_the_page_is_stored_with_lf(client, kinds, inv):
    login(client, "test:invadmin")
    text = (TV_HEADER + '1,MAG-TV-060,,LG,40,,1080p,,,"Invented first line\nsecond line"\n')
    response = client.post("/inv/admin/import/legacy", data={
        "action": "apply", "file_text": text.replace("\n", "\r\n"),
        "category_id": str(inv.category.id), "key-0": "TV|", "type-0": "new", "new-0": "TV"})
    assert response.status_code == 200
    assert _unit("MAG-TV-060").imported_notes == "Invented first line\nsecond line"


def test_an_odd_digit_mapping_id_goes_to_review(client, kinds, inv):
    result = preview(TV_FILE, {"TV|": "²", "MONITOR|Dell U2412M": "9" * 30}, None)
    assert "Row 2 (MAG-TV-107): The chosen type is not a TV type." in result.review
    assert "Row 4 (MAG-MON-001): The chosen type is not a Monitor type." in result.review
    login(client, "test:invadmin")
    response = client.post("/inv/admin/import/legacy", data={
        "action": "preview", "file_text": TV_FILE.decode("utf-8-sig"),
        "category_id": "²", "key-0": "TV|", "type-0": "²"})
    assert response.status_code == 200


def test_an_oversize_cell_is_refused_as_not_csv(kinds):
    from app.inventory.ledger import InventoryError
    data = _csv(TV_HEADER, "1,MAG-TV-070,,LG,40,,1080p,,," + "x" * 140000)
    with pytest.raises(InventoryError) as error:
        preview(data, {})
    assert str(error.value) == "This file could not be read as a CSV."
