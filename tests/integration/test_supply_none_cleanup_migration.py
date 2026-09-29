"""Rows saved through the Supply admin forms before the prefill fix hold "None"."""
from __future__ import annotations

from app import db
from app.models import SupplyCategory, SupplyItem
from migrations.versions.sc4t8b2m6k1q_supply_form_none_cleanup import clean_supply_junk


def _seed():
    junk_cat = SupplyCategory(code="TAPE", name="Tape", is_active=True, description="None")
    real_cat = SupplyCategory(code="OFFICE", name="Office", is_active=True, description="None needed")
    db.session.add_all([junk_cat, real_cat])
    db.session.flush()
    item = SupplyItem(
        category_id=junk_cat.id, item_name="Spike Tape", unit="roll", is_active=True,
        notes="Low-residue.", order_guidance="None", location_zone=" nan ",
        bin_location="NULL", internal_type="None",
    )
    db.session.add(item)
    db.session.commit()
    return junk_cat.id, real_cat.id, item.id


def test_junk_becomes_null_and_real_text_survives(app):
    junk_cat_id, real_cat_id, item_id = _seed()

    clean_supply_junk(db.session.connection())
    db.session.commit()
    db.session.expire_all()

    item = db.session.get(SupplyItem, item_id)
    assert item.order_guidance is None
    assert item.location_zone is None
    assert item.bin_location is None
    assert item.internal_type is None
    assert item.notes == "Low-residue."
    assert db.session.get(SupplyCategory, junk_cat_id).description is None
    assert db.session.get(SupplyCategory, real_cat_id).description == "None needed"
