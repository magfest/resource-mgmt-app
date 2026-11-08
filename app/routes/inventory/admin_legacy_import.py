"""Import the old tech exports: upload, match each model text to a type, then apply."""
from __future__ import annotations

from flask import request

from app import db
from app.inventory import legacy_import
from app.inventory.ledger import InventoryError
from app.models import InvGearKind, SupplyCategory, SupplyItem

from . import inventory_bp
from .helpers import admin_actor, render_inventory_page, require_inventory_admin

# The file text returns in a form field, which Flask's MAX_FORM_MEMORY_SIZE caps at
# 500,000 bytes. Browsers send that text with CRLF, so the cap counts CRLF line ends.
MAX_FILE_BYTES = 500_000
TEMPLATE = "inventory/legacy_import.html"


def _mapping_from_form() -> dict[str, str]:
    mapping = {}
    index = 0
    while f"key-{index}" in request.form:
        key = request.form[f"key-{index}"]
        choice = (request.form.get(f"type-{index}") or "").strip()
        if choice == "new":
            mapping[key] = legacy_import.NEW_PREFIX + (request.form.get(f"new-{index}") or "")
        elif choice:
            mapping[key] = choice
        index += 1
    return mapping


def _category_id() -> int | None:
    return legacy_import.parse_id(request.form.get("category_id"))


def _file_bytes() -> bytes:
    upload = request.files.get("file")
    data = upload.read(MAX_FILE_BYTES + 1) if upload and upload.filename else None
    if data is None:
        data = (request.form.get("file_text") or "").encode("utf-8")
    # Browsers submit textarea text with CRLF; notes are stored with LF.
    data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    if len(data) + data.count(b"\n") > MAX_FILE_BYTES:
        raise InventoryError("The file is larger than 500 KB.")
    if not data.strip():
        raise InventoryError("Choose an export file to preview.")
    return data


def _types_by_kind() -> dict[int, list[SupplyItem]]:
    items = (db.session.query(SupplyItem)
             .filter(SupplyItem.is_serialized.is_(True), SupplyItem.gear_kind_id.isnot(None))
             .order_by(SupplyItem.item_name).all())
    grouped: dict[int, list[SupplyItem]] = {}
    for item in items:
        grouped.setdefault(item.gear_kind_id, []).append(item)
    return grouped


def _page(**ctx):
    kinds = {k.code: k for k in db.session.query(InvGearKind)}
    categories = (db.session.query(SupplyCategory).filter_by(is_active=True)
                  .order_by(SupplyCategory.name).all())
    return render_inventory_page(
        TEMPLATE, kinds=kinds, categories=categories, types_by_kind=_types_by_kind(),
        not_tech_message=legacy_import.NOT_TECH, new_prefix=legacy_import.NEW_PREFIX,
        any_new=any(v.startswith(legacy_import.NEW_PREFIX) for v in ctx["mapping"].values()),
        **ctx)


@inventory_bp.get("/admin/import/legacy")
@require_inventory_admin
def legacy_import_page():
    return _page(result=None, mapping={}, category_id=None, file_text="")


@inventory_bp.post("/admin/import/legacy")
@require_inventory_admin
def legacy_import_post():
    action = request.form.get("action")
    mapping, category_id = _mapping_from_form(), _category_id()
    state = {"mapping": mapping, "category_id": category_id}
    try:
        data = _file_bytes()
        file_text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return _page(result=None, error="The file is not UTF-8 text. Export it again as CSV.",
                     file_text="", **state)
    except InventoryError as error:
        return _page(result=None, error=str(error), file_text="", **state)
    try:
        if action == "apply":
            result = legacy_import.apply(data, mapping, category_id, admin_actor())
            return _page(result=result, applied=True, file_text=file_text, **state)
        result = legacy_import.preview(data, mapping, category_id)
    except InventoryError as error:
        return _page(result=None, error=str(error), file_text=file_text, **state)
    return _page(result=result, applied=False, file_text=file_text, **state)
