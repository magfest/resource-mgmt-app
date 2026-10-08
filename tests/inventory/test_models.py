"""Constraints the database enforces on its own, without the ledger."""
import pytest
from sqlalchemy.exc import IntegrityError

from app import db
from app.models import InvStock, InvUnit, InvUnitIdentifier, SupplyItem
from app.models.constants import INV_IDENT_FESTOPS_LABEL, INV_IDENT_VENDOR_SERIAL
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
        db.session.add(InvUnitIdentifier(unit_id=unit.id, kind=INV_IDENT_VENDOR_SERIAL,
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
