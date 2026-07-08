"""
Tests for the supply catalog bulk import parser (app/routes/admin/supply_import_utils.py).

Pure-logic module: parse an uploaded CSV/XLSX, classify rows against the DB
as creates/updates/errors, and apply the classified rows. No routes/HTTP
involved — uploads are built in-memory as CSV via werkzeug's FileStorage.
"""
from __future__ import annotations

import io

import pytest
from werkzeug.datastructures import FileStorage

from app import db
from app.models import (
    ConfigAuditEvent,
    SupplyCategory,
    SupplyItem,
    CONFIG_AUDIT_CREATE,
    CONFIG_AUDIT_UPDATE,
)
from app.routes.admin.supply_import_utils import (
    ImportParseError,
    apply_import,
    classify_rows,
    parse_catalog_upload,
)

CSV_HEADER = (
    "category_code,item_name,unit,notes,unit_cost,qty_on_hand,"
    "location_zone,bin_location,is_limited,is_popular,is_expendable,"
    "notes_required,is_active"
)


def _csv_upload(text: str, filename: str = "catalog.csv") -> FileStorage:
    return FileStorage(
        stream=io.BytesIO(text.encode("utf-8")),
        filename=filename,
        content_type="text/csv",
    )


@pytest.fixture(scope="function")
def import_seed(app):
    """One category (OFFICE) + one existing item, ready for import tests."""
    category = SupplyCategory(code="OFFICE", name="Office Supplies", is_active=True)
    db.session.add(category)
    db.session.flush()

    item = SupplyItem(
        category_id=category.id,
        item_name="Ballpoint pens (box of 12)",
        unit="box",
        unit_cost_cents=300,
        is_active=True,
    )
    db.session.add(item)
    db.session.commit()

    return {"category": category, "item": item}


def _sample_csv_text() -> str:
    rows = [
        "OFFICE,BALLPOINT PENS (box of 12),box,,3.50,50,A1,B2,false,true,true,false,true",
        "OFFICE,Stapler,each,,12.99,10,A2,B3,false,false,false,false,true",
        "NOPE,Mystery widget,each,,1.00,5,A3,B4,false,false,false,false,true",
    ]
    return "\n".join([CSV_HEADER, *rows]) + "\n"


def test_classify_creates_updates_and_errors(app, import_seed):
    upload = _csv_upload(_sample_csv_text())
    rows = parse_catalog_upload(upload)
    preview = classify_rows(rows)

    assert len(preview.creates) == 1
    assert preview.creates[0]["item_name"] == "Stapler"

    assert len(preview.updates) == 1
    update = preview.updates[0]
    assert update["existing_id"] == import_seed["item"].id
    assert update["unit_cost_cents"] == 350
    # Changed-field hints for the preview page: cost changed (300 -> 350),
    # plus the fields the CSV sets that the seeded item doesn't have.
    assert set(update["changed_fields"]) == {
        "item_name",  # case differs from the seeded name
        "is_popular",
        "unit_cost_cents",
        "qty_on_hand",
        "location_zone",
        "bin_location",
    }

    assert len(preview.errors) == 1
    error = preview.errors[0]
    assert error["row_number"] == 4
    assert any("NOPE" in problem for problem in error["problems"])


def test_apply_import_is_idempotent(app, import_seed):
    upload = _csv_upload(_sample_csv_text())
    rows = parse_catalog_upload(upload)
    preview = classify_rows(rows)

    created_count, updated_count = apply_import(
        preview.creates, preview.updates, user_id="test:admin"
    )
    db.session.commit()

    assert created_count == 1
    assert updated_count == 1
    assert db.session.query(SupplyItem).count() == 2

    # Audit trail: one CREATE + one UPDATE ConfigAuditEvent per applied row.
    audit_rows = (
        db.session.query(ConfigAuditEvent)
        .filter_by(entity_type="supply_item")
        .all()
    )
    assert sorted(e.action for e in audit_rows) == sorted(
        [CONFIG_AUDIT_CREATE, CONFIG_AUDIT_UPDATE]
    )
    assert all(e.created_by_user_id == "test:admin" for e in audit_rows)
    assert all("bulk_upload" in (e.changes_json or "") for e in audit_rows)

    upload2 = _csv_upload(_sample_csv_text())
    rows2 = parse_catalog_upload(upload2)
    preview2 = classify_rows(rows2)

    assert preview2.creates == []
    assert len(preview2.updates) == 2
    assert len(preview2.errors) == 1

    created_count2, updated_count2 = apply_import(
        preview2.creates, preview2.updates, user_id="test:admin"
    )
    db.session.commit()

    assert created_count2 == 0
    assert updated_count2 == 2
    assert db.session.query(SupplyItem).count() == 2


def test_ambiguous_case_insensitive_match_is_row_error(app, import_seed):
    """Two existing items whose names differ only by case: an import row
    matching that name is an ambiguous match -> per-row error, not a crash;
    other rows in the same file still classify normally."""
    duplicate = SupplyItem(
        category_id=import_seed["category"].id,
        item_name="BALLPOINT PENS (BOX OF 12)",  # differs only by case
        unit="box",
        is_active=True,
    )
    db.session.add(duplicate)
    db.session.commit()

    upload = _csv_upload(_sample_csv_text())
    rows = parse_catalog_upload(upload)
    preview = classify_rows(rows)

    # Row 2 (the pens row) is now ambiguous -> error with a clear problem.
    ambiguous = [e for e in preview.errors if e["row_number"] == 2]
    assert len(ambiguous) == 1
    assert any("multiple" in p.lower() for p in ambiguous[0]["problems"])

    # The rest of the batch still classifies normally.
    assert len(preview.creates) == 1
    assert preview.creates[0]["item_name"] == "Stapler"
    assert preview.updates == []
    assert any(e["row_number"] == 4 for e in preview.errors)  # unknown category row


def test_blank_notes_cell_creates_item_with_none_notes(app, import_seed):
    """A blank notes cell must land in the DB as NULL, not the string 'nan'
    (pandas parses blank cells as float NaN; str(nan) == 'nan')."""
    text = (
        CSV_HEADER + "\n"
        "OFFICE,Whiteboard,each,,5.00,3,A9,B9,false,false,false,false,true\n"
    )
    rows = parse_catalog_upload(_csv_upload(text))
    preview = classify_rows(rows)

    assert preview.errors == []
    assert len(preview.creates) == 1

    apply_import(preview.creates, preview.updates, user_id="test:admin")
    db.session.commit()

    item = db.session.query(SupplyItem).filter_by(item_name="Whiteboard").one()
    assert item.notes is None
    assert item.location_zone == "A9"


def test_blank_numeric_cells_classify_as_create_not_error(app, import_seed):
    """Blank unit_cost and qty_on_hand are documented as optional: the row
    must classify as a valid create with those fields None, not be rejected
    with a NaN conversion error."""
    text = (
        CSV_HEADER + "\n"
        "OFFICE,Corkboard,each,,,,,,false,false,false,false,true\n"
    )
    rows = parse_catalog_upload(_csv_upload(text))
    preview = classify_rows(rows)

    assert preview.errors == []
    assert len(preview.creates) == 1
    create = preview.creates[0]
    assert create["item_name"] == "Corkboard"
    assert create["unit_cost_cents"] is None
    assert create["qty_on_hand"] is None
    assert create["notes"] is None


def test_import_with_order_guidance_column_sets_it(app, import_seed):
    """order_guidance is an optional column; when present, its value is
    parsed and written to the new SupplyItem.order_guidance field."""
    header = CSV_HEADER + ",order_guidance"
    text = (
        header + "\n"
        "OFFICE,Whiteboard,each,,5.00,3,A9,B9,false,false,false,false,true,"
        "1 board per meeting room\n"
    )
    rows = parse_catalog_upload(_csv_upload(text))
    preview = classify_rows(rows)

    assert preview.errors == []
    assert len(preview.creates) == 1
    assert preview.creates[0]["order_guidance"] == "1 board per meeting room"

    apply_import(preview.creates, preview.updates, user_id="test:admin")
    db.session.commit()

    item = db.session.query(SupplyItem).filter_by(item_name="Whiteboard").one()
    assert item.order_guidance == "1 board per meeting room"


def test_import_without_order_guidance_column_still_succeeds(app, import_seed):
    """order_guidance must be OPTIONAL: a file missing that column entirely
    (not just a blank cell) must still import cleanly, landing as None --
    it is not in REQUIRED_COLUMNS, and row.get() on a missing key returns
    None the same way a blank cell does."""
    text = (
        CSV_HEADER + "\n"
        "OFFICE,Corkboard,each,,,,,,false,false,false,false,true\n"
    )
    rows = parse_catalog_upload(_csv_upload(text))
    preview = classify_rows(rows)

    assert preview.errors == []
    assert len(preview.creates) == 1
    assert preview.creates[0]["order_guidance"] is None

    apply_import(preview.creates, preview.updates, user_id="test:admin")
    db.session.commit()

    item = db.session.query(SupplyItem).filter_by(item_name="Corkboard").one()
    assert item.order_guidance is None


def test_parse_rejects_missing_headers(app, import_seed):
    text = (
        "category_code,item_name,notes\n"
        "OFFICE,Stapler,some notes\n"
    )
    upload = _csv_upload(text)

    with pytest.raises(ImportParseError):
        parse_catalog_upload(upload)
