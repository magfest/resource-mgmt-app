import pytest

from app import db
from app.inventory.ledger import Actor, InventoryError, add_unit
from app.inventory.names import find_by_name, name_key, set_unit_name
from app.inventory.unit_fields import (
    apply_model_defaults, details_line, parse_value, set_model_default,
    set_unit_value, unit_field_rows,
)
from app.models import InvUnit
from app.models.constants import (
    INV_CONDITION_RETIRED, INV_FIELD_BOOL, INV_FIELD_CHOICE, INV_FIELD_NUMBER,
    INV_FIELD_TEXT,
)
from tests.inventory.helpers import seed_gear_kind

ACTOR = Actor(user_id="test:invadmin")


def _unit(inv, item=None):
    unit = InvUnit(item_id=(item or inv.laptop).id)
    db.session.add(unit)
    db.session.flush()
    return unit


def _tv(inv):
    kind = seed_gear_kind("TV", "TV", [
        ("BRAND", "Brand", INV_FIELD_TEXT, None),
        ("SIZE", "Size", INV_FIELD_NUMBER, "in"),
        ("RESOLUTION", "Resolution", INV_FIELD_CHOICE, ["720p", "1080p", "4k"]),
        ("HDMI", "HDMI", INV_FIELD_BOOL, None),
    ])
    inv.laptop.gear_kind_id = kind.id
    db.session.flush()
    return kind


def test_name_key_ignores_case_and_spacing():
    assert name_key("  mag-PNTR-007 ") == name_key("MAG-PNTR-007") == "mag-pntr-007"
    assert name_key("a   b") == "a b"
    assert name_key("   ") is None and name_key(None) is None


def test_a_second_unit_cannot_take_a_name_in_another_case(inv):
    a, b = _unit(inv), _unit(inv)
    assert set_unit_name(a, "MAG-PNTR-007") is True
    with pytest.raises(InventoryError) as error:
        set_unit_name(b, "mag-pntr-007 ")
    assert str(error.value) == "MAG-PNTR-007 is already the name of another unit."
    assert set_unit_name(a, " MAG-PNTR-007") is False


def test_blank_clears_and_long_names_are_refused(inv):
    a = _unit(inv)
    set_unit_name(a, "Front desk")
    assert set_unit_name(a, "  ") is True
    assert a.name is None and a.name_key is None
    with pytest.raises(InventoryError):
        set_unit_name(a, "x" * 65)


def test_a_retired_unit_frees_its_name(inv):
    a, b = _unit(inv), _unit(inv)
    set_unit_name(a, "Spare")
    a.condition = INV_CONDITION_RETIRED
    db.session.flush()
    set_unit_name(b, "spare")
    db.session.flush()
    assert [u.id for u in find_by_name("spare")] == [b.id]


def test_find_by_name_matches_substrings_in_name_order(inv):
    a, b = _unit(inv), _unit(inv)
    set_unit_name(b, "Lobby TV")
    set_unit_name(a, "Atrium tv")
    db.session.flush()
    assert [u.id for u in find_by_name("TV")] == [a.id, b.id]
    assert find_by_name("  ") == []


def test_size_accepts_inch_marks_and_refuses_words(inv):
    size = _tv(inv).fields[1]
    assert parse_value(size, '45"') == {"number_value": 45}
    assert parse_value(size, "27 in") == {"number_value": 27}
    assert parse_value(size, "") is None
    for bad in ("big", "0", "1001"):
        with pytest.raises(InventoryError) as error:
            parse_value(size, bad)
        assert "Size" in str(error.value)
    with pytest.raises(InventoryError) as error:
        parse_value(size, "big")
    assert str(error.value) == "Size must be a number."


def test_choice_matches_case_insensitively_and_refuses_unknown(inv):
    resolution = _tv(inv).fields[2]
    four_k = resolution.choices[2]
    assert parse_value(resolution, "4K") == {"choice_id": four_k.id}
    with pytest.raises(InventoryError) as error:
        parse_value(resolution, "8k")
    assert str(error.value) == "Resolution must be one of 720p, 1080p, 4k."


def test_bool_and_text_parsing(inv):
    brand, _, _, hdmi = _tv(inv).fields
    assert parse_value(hdmi, "Y") == {"bool_value": True}
    assert parse_value(hdmi, "0") == {"bool_value": False}
    with pytest.raises(InventoryError):
        parse_value(hdmi, "maybe")
    assert parse_value(brand, "  Roku ") == {"text_value": "Roku"}
    with pytest.raises(InventoryError):
        parse_value(brand, "x" * 129)


def test_set_unit_value_reports_change_and_blank_clears(inv):
    kind = _tv(inv)
    unit = _unit(inv)
    brand = kind.fields[0]
    assert set_unit_value(unit, brand, "Roku") is True
    assert set_unit_value(unit, brand, "Roku") is False
    assert set_unit_value(unit, brand, "") is True
    db.session.flush()
    assert unit.values == []


def test_new_unit_takes_model_defaults_and_keeps_later_values(inv):
    kind = seed_gear_kind("PRINTER", "Printer", [("TONER", "Toner cartridge", INV_FIELD_TEXT, None)])
    inv.laptop.gear_kind_id = kind.id
    toner = kind.fields[0]
    assert set_model_default(inv.laptop, toner, "TN820") is True
    unit = add_unit(inv.laptop, None, ACTOR, [("MAGFEST_TAG", "MAG-1")], name="P1")
    db.session.flush()
    assert [(f.code, text) for f, text in unit_field_rows(unit)] == [("TONER", "TN820")]
    set_model_default(inv.laptop, toner, "TN900")
    apply_model_defaults(unit)
    db.session.flush()
    assert [text for _, text in unit_field_rows(unit)] == ["TN820"]


def test_details_line_uses_first_three_set_fields(inv):
    kind = _tv(inv)
    unit = _unit(inv)
    brand, size, resolution, hdmi = kind.fields
    set_unit_value(unit, brand, "Roku")
    set_unit_value(unit, size, "50")
    set_unit_value(unit, resolution, "4k")
    set_unit_value(unit, hdmi, "yes")
    db.session.flush()
    assert details_line(unit) == "Roku, 50 in, 4k"


ON = {"on_label": True}


def _printer_kind(inv):
    kind = seed_gear_kind("PRINTER", "Printer", [
        ("MODEL", "Model", INV_FIELD_TEXT, None),
        ("PRINT", "Print", INV_FIELD_CHOICE, [("Black and white", "BW"), ("Color", "C")], ON),
        ("USB", "USB", INV_FIELD_BOOL, None, {"on_label": True, "label_text": "USB"}),
        ("DUPLEX", "Duplex", INV_FIELD_BOOL, None,
         {"on_label": True, "label_text": "DUPLEX"}),
        ("TONER", "Toner cartridge", INV_FIELD_TEXT, None),
    ])
    inv.laptop.item_name = "Laser Printer"
    inv.laptop.gear_kind_id = kind.id
    db.session.flush()
    return kind


def test_label_specs_follow_position_and_skip_no_and_blank(inv):
    from app.inventory.unit_fields import label_specs, model_number, spec_line
    kind = _printer_kind(inv)
    unit = _unit(inv)
    model, prints, usb, duplex, toner = kind.fields
    set_unit_value(unit, duplex, "no")
    set_unit_value(unit, usb, "yes")
    set_unit_value(unit, prints, "black and white")
    set_unit_value(unit, toner, "TN820")
    set_unit_value(unit, model, "MFC-L2820DW")
    assert label_specs(unit) == ["BW", "USB"]
    assert spec_line(unit) == "Laser Printer (BW, USB)"
    assert model_number(unit) == "MFC-L2820DW"


def test_spec_line_without_specs_is_the_bare_type(inv):
    from app.inventory.unit_fields import label_specs, model_number, spec_line
    _printer_kind(inv)
    unit = _unit(inv)
    assert label_specs(unit) == []
    assert spec_line(unit) == "Laser Printer"
    assert model_number(unit) is None


def test_tv_spec_line(inv):
    from app.inventory.unit_fields import spec_line
    kind = seed_gear_kind("TV", "TV", [
        ("MODEL", "Model", INV_FIELD_TEXT, None),
        ("BRAND", "Brand", INV_FIELD_TEXT, None),
        ("SIZE", "Size", INV_FIELD_NUMBER, "in", ON),
        ("RESOLUTION", "Resolution", INV_FIELD_CHOICE, ["720p", "1080p", "4k"], ON),
        ("HDMI", "HDMI", INV_FIELD_BOOL, None, {"on_label": True, "label_text": "HDMI"}),
        ("DISPLAYPORT", "DisplayPort", INV_FIELD_BOOL, None,
         {"on_label": True, "label_text": "DP"}),
    ])
    inv.laptop.item_name = "TV"
    inv.laptop.gear_kind_id = kind.id
    db.session.flush()
    unit = _unit(inv)
    _, brand, size, resolution, hdmi, dp = kind.fields
    set_unit_value(unit, brand, "Roku")
    set_unit_value(unit, size, "50")
    set_unit_value(unit, resolution, "4k")
    set_unit_value(unit, hdmi, "yes")
    set_unit_value(unit, dp, "no")
    assert spec_line(unit) == "TV (50in, 4k, HDMI)"


def test_retired_field_with_value_still_shows(inv):
    kind = _tv(inv)
    unit = _unit(inv)
    brand = kind.fields[0]
    set_unit_value(unit, brand, "Roku")
    db.session.flush()
    brand.is_active = False
    db.session.flush()
    rows = unit_field_rows(unit)
    assert ("BRAND", "Roku") in [(f.code, t) for f, t in rows]
    assert len(rows) == 4


def test_add_unit_needs_a_name_when_it_has_no_identifiers(inv):
    with pytest.raises(InventoryError):
        add_unit(inv.laptop, None, ACTOR, [])
    unit = add_unit(inv.laptop, None, ACTOR, [], name="Loaner 3")
    assert unit.name == "Loaner 3" and unit.name_key == "loaner 3"
