"""
Tests for the supply catalog bulk-import routes
(app/routes/admin/supply_items.py: /import, /import/confirm, /import/template).

The parsing/classification/apply logic itself is exercised in
tests/unit/test_supply_import.py against app/routes/admin/supply_import_utils.py;
here we only verify the routes wire that logic together correctly: upload ->
preview page, preview payload -> confirm applies (idempotently), and the
template download round-trips through openpyxl.
"""
from __future__ import annotations

import io
import json

import openpyxl
import pytest

from app import db
from app.models import (
    SupplyCategory,
    SupplyItem,
    User,
    UserRole,
    WorkType,
    ROLE_SUPER_ADMIN,
    ROLE_WORKTYPE_ADMIN,
)
from app.routes.admin.supply_import_utils import EXPECTED_COLUMNS, parse_catalog_upload

IMPORT_URL = "/admin/config/supply-items/import"
CONFIRM_URL = "/admin/config/supply-items/import/confirm"
TEMPLATE_URL = "/admin/config/supply-items/import/template"

CSV_HEADER = (
    "category_code,item_name,unit,notes,unit_cost,qty_on_hand,"
    "location_zone,bin_location,is_limited,is_popular,is_expendable,"
    "notes_required,is_active"
)


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def _seed_admin_and_category(app):
    """Seed a SUPER_ADMIN user, the SUPPLY work type, and one active supply
    category (OFFICE). The supply admin routes gate via require_supply_admin,
    which resolves the SUPPLY WorkType row (404 if not configured), so it
    must exist even for super-admin access."""
    with app.app_context():
        admin = User(
            id="test:admin", email="admin@test.local",
            display_name="Test Admin", is_active=True,
        )
        db.session.add(admin)
        db.session.flush()
        db.session.add(UserRole(user_id=admin.id, role_code=ROLE_SUPER_ADMIN))

        db.session.add(WorkType(code="SUPPLY", name="Supply Orders", is_active=True))

        category = SupplyCategory(code="OFFICE", name="Office Supplies", is_active=True)
        db.session.add(category)
        db.session.commit()
        return category.id


def _seed_supply_worktype_admin(app, user_id="test:supplyadmin"):
    """Seed a user whose ONLY role is WORKTYPE_ADMIN for SUPPLY (not super).

    Requires _seed_admin_and_category to have run first (it creates the
    SUPPLY WorkType row this role scopes to).
    """
    with app.app_context():
        supply_wt = WorkType.query.filter_by(code="SUPPLY").one()
        user = User(
            id=user_id, email="supplyadmin@test.local",
            display_name="Supply Admin", is_active=True,
        )
        db.session.add(user)
        db.session.flush()
        db.session.add(UserRole(
            user_id=user.id,
            role_code=ROLE_WORKTYPE_ADMIN,
            work_type_id=supply_wt.id,
        ))
        db.session.commit()
        return user.id


def _seed_plain_user(app, user_id="test:plain"):
    """Seed a user with no roles at all."""
    with app.app_context():
        user = User(
            id=user_id, email="plain@test.local",
            display_name="Plain User", is_active=True,
        )
        db.session.add(user)
        db.session.commit()
        return user.id


def _sample_csv_text() -> str:
    rows = [
        # Good row -> no existing item named "Stapler" -> classifies as create.
        "OFFICE,Stapler,each,,12.99,10,A2,B3,false,false,false,false,true",
        # Unknown category -> classifies as an error row.
        "NOPE,Mystery widget,each,,1.00,5,A3,B4,false,false,false,false,true",
    ]
    return "\n".join([CSV_HEADER, *rows]) + "\n"


def test_upload_shows_preview_with_one_create_and_one_error(app, client):
    _seed_admin_and_category(app)
    _login(client, "test:admin")

    resp = client.post(
        IMPORT_URL,
        data={"file": (io.BytesIO(_sample_csv_text().encode("utf-8")), "catalog.csv")},
        content_type="multipart/form-data",
    )

    assert resp.status_code == 200
    body = resp.data.decode("utf-8")
    assert "Stapler" in body
    assert "Mystery widget" in body
    # Hidden field handoff for confirm.
    assert 'name="payload"' in body


def test_confirm_creates_then_reconfirm_updates_not_duplicates(app, client):
    category_id = _seed_admin_and_category(app)
    _login(client, "test:admin")

    with app.app_context():
        upload = io.BytesIO(_sample_csv_text().encode("utf-8"))
        upload.filename = "catalog.csv"
        from werkzeug.datastructures import FileStorage
        rows = parse_catalog_upload(
            FileStorage(stream=upload, filename="catalog.csv", content_type="text/csv")
        )
    payload = json.dumps(rows)

    resp1 = client.post(CONFIRM_URL, data={"payload": payload}, follow_redirects=True)
    assert resp1.status_code == 200

    with app.app_context():
        assert SupplyItem.query.filter_by(category_id=category_id).count() == 1
        item = SupplyItem.query.filter_by(item_name="Stapler").one()
        assert item.unit_cost_cents == 1299

    # Re-confirming the same payload should update the existing item, not
    # create a duplicate (the row now matches an existing item by name).
    resp2 = client.post(CONFIRM_URL, data={"payload": payload}, follow_redirects=True)
    assert resp2.status_code == 200

    with app.app_context():
        assert SupplyItem.query.filter_by(category_id=category_id).count() == 1


def test_download_template_is_valid_xlsx_with_expected_headers(app, client):
    _seed_admin_and_category(app)
    _login(client, "test:admin")

    resp = client.get(TEMPLATE_URL)

    assert resp.status_code == 200
    assert "spreadsheetml" in resp.headers["Content-Type"]

    workbook = openpyxl.load_workbook(io.BytesIO(resp.data))
    sheet = workbook.active
    header_row = [cell.value for cell in next(sheet.iter_rows(min_row=1, max_row=1))]
    assert header_row == EXPECTED_COLUMNS


def test_data_upload_index_links_to_supply_items_import(app, client):
    """Part C: the catalog import entry point moved to the central Data
    Upload hub — its index page must advertise the supply-items import."""
    _seed_admin_and_category(app)
    _login(client, "test:admin")

    resp = client.get("/admin/config/data-upload/")

    assert resp.status_code == 200
    assert IMPORT_URL.encode() in resp.data


def test_supply_items_list_no_longer_shows_import_link(app, client):
    """Part C: the list page no longer advertises uploads — Data Upload is
    the single home for the import entry point (the /import route itself
    still exists and is reachable directly)."""
    _seed_admin_and_category(app)
    _login(client, "test:admin")

    resp = client.get("/admin/config/supply-items/")

    assert resp.status_code == 200
    assert IMPORT_URL.encode() not in resp.data


def test_supply_worktype_admin_can_access_list_and_import_form(app, client):
    """Fix round 1: the supply catalog pages are gated by require_supply_admin
    (mirroring Budget's require_budget_admin on expense accounts), so a user
    holding ONLY a SUPPLY WORKTYPE_ADMIN role — not super admin — must get
    200 on both the items list and the import form."""
    _seed_admin_and_category(app)
    user_id = _seed_supply_worktype_admin(app)
    _login(client, user_id)

    list_resp = client.get("/admin/config/supply-items/")
    assert list_resp.status_code == 200

    import_resp = client.get(IMPORT_URL)
    assert import_resp.status_code == 200


def test_plain_user_still_403s_on_supply_admin_pages(app, client):
    """Fix round 1: a user with no roles at all must still be rejected."""
    _seed_admin_and_category(app)
    user_id = _seed_plain_user(app)
    _login(client, user_id)

    assert client.get("/admin/config/supply-items/").status_code == 403
    assert client.get(IMPORT_URL).status_code == 403
