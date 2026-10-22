"""Constraints the database enforces on its own, without the ledger."""
import pytest
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import (
    InvCheck, InvCheckAnswer, InvCheckItem, InvContainerKind, InvCount, InvCountLine,
    InvDevice, InvFlag, InvPrepStatus, InvShift, InvStationItem, InvStock, InvUnit,
    InvUnitIdentifier, SupplyItem,
)
from app.models.constants import (
    INV_CHECK_OPEN, INV_IDENT_FESTOPS_LABEL, INV_IDENT_MAGFEST_TAG, INV_IDENT_SERIAL,
    INV_MISSING_FAIL, INV_PREP_CHECKED, INV_STATION_COUNT, INV_STATION_TV_CHECK,
)
from tests.inventory.helpers import make_container


def test_stock_cannot_go_negative(inv):
    bin_ = make_container(inv, "C-0001", kind="BIN")
    db.session.add(InvStock(item_id=inv.pens.id, container_id=bin_.id, quantity=-1))
    with pytest.raises(IntegrityError):
        db.session.flush()


def test_active_festops_label_is_unique_but_vendor_serials_may_repeat(inv):
    pallet = make_container(inv, "C-0001")
    a = InvUnit(item_id=inv.laptop.id, current_container_id=pallet.id)
    b = InvUnit(item_id=inv.laptop.id, current_container_id=pallet.id)
    db.session.add_all([a, b])
    db.session.flush()
    for unit in (a, b):
        db.session.add(InvUnitIdentifier(unit_id=unit.id, kind=INV_IDENT_SERIAL,
                                         value="SN1", normalized_value="SN1"))
    db.session.flush()

    db.session.add(InvUnitIdentifier(unit_id=a.id, kind=INV_IDENT_FESTOPS_LABEL,
                                     value="U-00001", normalized_value="U-00001"))
    db.session.add(InvUnitIdentifier(unit_id=b.id, kind=INV_IDENT_FESTOPS_LABEL,
                                     value="U-00001", normalized_value="U-00001"))
    with pytest.raises(IntegrityError):
        db.session.flush()


def test_new_supply_item_is_orderable_and_counted(inv):
    item = SupplyItem(category_id=inv.category.id, item_name="Tape", unit="roll")
    db.session.add(item)
    db.session.flush()
    assert item.is_orderable is True
    assert item.is_serialized is False


def _two_units(inv):
    pallet = make_container(inv, "C-0001")
    a = InvUnit(item_id=inv.laptop.id, current_container_id=pallet.id)
    b = InvUnit(item_id=inv.laptop.id, current_container_id=pallet.id)
    db.session.add_all([a, b])
    db.session.flush()
    return a, b


def test_two_active_magfest_tags_cannot_share_a_value(inv):
    a, b = _two_units(inv)
    db.session.add(InvUnitIdentifier(unit_id=a.id, kind=INV_IDENT_MAGFEST_TAG,
                                     value="tech00001", normalized_value="TECH00001"))
    db.session.flush()
    db.session.add(InvUnitIdentifier(unit_id=b.id, kind=INV_IDENT_MAGFEST_TAG,
                                     value="TECH00001", normalized_value="TECH00001"))
    with pytest.raises(IntegrityError):
        db.session.flush()


def test_a_retired_magfest_tag_can_be_reused(inv):
    a, b = _two_units(inv)
    db.session.add(InvUnitIdentifier(unit_id=a.id, kind=INV_IDENT_MAGFEST_TAG,
                                     value="TECH00001", normalized_value="TECH00001",
                                     retired_at=db.func.now()))
    db.session.add(InvUnitIdentifier(unit_id=b.id, kind=INV_IDENT_MAGFEST_TAG,
                                     value="TECH00001", normalized_value="TECH00001"))
    db.session.flush()


def test_a_tag_and_a_label_may_share_a_normalized_value(inv):
    a, b = _two_units(inv)
    db.session.add(InvUnitIdentifier(unit_id=a.id, kind=INV_IDENT_MAGFEST_TAG,
                                     value="X1", normalized_value="X1"))
    db.session.add(InvUnitIdentifier(unit_id=b.id, kind=INV_IDENT_FESTOPS_LABEL,
                                     value="X1", normalized_value="X1"))
    db.session.flush()


def test_two_units_may_share_a_serial(inv):
    a, b = _two_units(inv)
    for unit in (a, b):
        db.session.add(InvUnitIdentifier(unit_id=unit.id, kind=INV_IDENT_SERIAL,
                                         value="SN9", normalized_value="SN9"))
    db.session.flush()


def test_prep_status_check_and_shift_rows_save(inv):
    unit, _ = _two_units(inv)
    device = InvDevice(name="Scanner 1", token_hash="h" * 64, enrolled_by_user_id="test:admin")
    db.session.add(device)
    db.session.flush()
    shift = InvShift(device_id=device.id, person_name="Sam")
    check_item = InvCheckItem(item_id=inv.laptop.id, position=1, prompt="Powered on?",
                              missing_means=INV_MISSING_FAIL)
    db.session.add_all([shift, check_item])
    db.session.flush()

    check = InvCheck(unit_id=unit.id, station_code=INV_STATION_TV_CHECK,
                     status=INV_CHECK_OPEN, shift_id=shift.id, device_id=device.id)
    db.session.add(check)
    db.session.flush()
    db.session.add(InvCheckAnswer(check_id=check.id, check_item_id=check_item.id, answer="YES"))
    db.session.add(InvPrepStatus(unit_id=unit.id, status=INV_PREP_CHECKED, check_id=check.id,
                                 shift_id=shift.id, device_id=device.id))
    db.session.add(InvFlag(unit_id=unit.id, station_code=INV_STATION_COUNT, reason="OTHER",
                           shift_id=shift.id, device_id=device.id))
    db.session.add(InvStationItem(station_code=INV_STATION_COUNT, item_id=inv.pens.id,
                                  position=1))
    db.session.flush()
    assert shift.last_activity_at is not None
    assert check.previous_condition is None


def test_a_check_answers_each_line_once(inv):
    unit, _ = _two_units(inv)
    item = InvCheckItem(item_id=inv.laptop.id, position=1, prompt="Powered on?",
                        missing_means=INV_MISSING_FAIL)
    check = InvCheck(unit_id=unit.id, station_code=INV_STATION_TV_CHECK, status=INV_CHECK_OPEN)
    db.session.add_all([item, check])
    db.session.flush()
    db.session.add(InvCheckAnswer(check_id=check.id, check_item_id=item.id, answer="YES"))
    db.session.flush()
    db.session.add(InvCheckAnswer(check_id=check.id, check_item_id=item.id, answer="NO"))
    with pytest.raises(IntegrityError):
        db.session.flush()


def test_discarded_quantity_must_be_positive(inv):
    bin_ = make_container(inv, "C-0002", kind="BIN")
    count = InvCount(container_id=bin_.id)
    db.session.add(count)
    db.session.flush()
    db.session.add(InvCountLine(count_id=count.id, item_id=inv.pens.id, expected_quantity=1,
                                discarded_quantity=0))
    with pytest.raises(IntegrityError):
        db.session.flush()


def test_container_kind_sort_order_is_bounded(inv):
    db.session.add(InvContainerKind(code="HUGE", name="Huge", sort_order=10001, is_active=True))
    with pytest.raises(IntegrityError):
        db.session.flush()


def test_new_columns_have_defaults(inv):
    bin_ = make_container(inv, "C-0003", kind="BIN")
    device = InvDevice(name="S", token_hash="g" * 64, enrolled_by_user_id="test:admin")
    stock = InvStock(item_id=inv.pens.id, container_id=bin_.id, quantity=1)
    db.session.add_all([device, stock])
    db.session.flush()
    assert device.station_code == "LOOKUP"
    assert device.home_container_id is None
    assert stock.is_approximate is False
    assert inv.pens.pack_size is None
    assert inv.pens.check_station is None
