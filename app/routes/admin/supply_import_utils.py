"""
Supply catalog bulk import: parse CSV/XLSX uploads, classify rows against
the DB (create/update/error), and apply the classified rows.

Pure parsing/apply logic — no Flask routes; Task 6 wires this into the
admin blueprint. `image_url` is never written here — images are managed
through the per-item admin form only (see app/services/images.py).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

import pandas as pd
from sqlalchemy import func

from app import db
from app.models import (
    SupplyCategory,
    SupplyItem,
    CONFIG_AUDIT_CREATE,
    CONFIG_AUDIT_UPDATE,
)
from .helpers import log_config_change

EXPECTED_COLUMNS = [
    "category_code", "item_name", "unit", "notes", "order_guidance",
    "unit_cost", "qty_on_hand", "location_zone", "bin_location",
    "is_limited", "is_popular", "is_expendable", "notes_required",
    "is_active",
]

# Only these are load-bearing for classification; the rest are optional.
# order_guidance is intentionally NOT in this set -- an uploaded file
# missing that column entirely must still import cleanly (see
# _parse_row_fields: row.get("order_guidance") is None when the column
# is absent, same as any other optional column).
REQUIRED_COLUMNS = {"category_code", "item_name", "unit"}

# Per-column default when the cell is blank. Every boolean column defaults
# to False except is_active, which defaults to True (an omitted/blank cell
# means "still active").
BOOLEAN_DEFAULTS = {
    "is_limited": False,
    "is_popular": False,
    "is_expendable": False,
    "notes_required": False,
    "is_active": True,
}

TRUE_VALUES = {"true", "yes", "1"}
FALSE_VALUES = {"false", "no", "0"}


class ImportParseError(ValueError):
    """The uploaded file could not be read, or is missing required headers."""


@dataclass
class ImportPreview:
    creates: list[dict] = field(default_factory=list)
    updates: list[dict] = field(default_factory=list)
    errors: list[dict] = field(default_factory=list)


def parse_catalog_upload(file_storage) -> list[dict]:
    """Parse an uploaded CSV/XLSX catalog into a list of raw row dicts.

    Raises ImportParseError if the file can't be read or is missing one of
    the required headers (category_code, item_name, unit).
    """
    filename = (getattr(file_storage, "filename", "") or "").lower()
    try:
        if filename.endswith(".xlsx") or filename.endswith(".xls"):
            df = pd.read_excel(file_storage)
        else:
            df = pd.read_csv(file_storage)
    except Exception as exc:
        raise ImportParseError(f"Could not read uploaded file: {exc}") from exc

    df.columns = [str(c).strip().lower() for c in df.columns]

    missing = REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise ImportParseError(
            f"Missing required column(s): {', '.join(sorted(missing))}"
        )

    # Null out blank cells per-cell AFTER to_dict(). A DataFrame-level
    # df.where(pd.notnull(df), None) does NOT null NaN in numeric-dtype
    # columns (verified on pandas 3.x), which would leak float('nan') into
    # the row dicts: blank text cells become the literal string "nan" and
    # blank numeric cells blow up int() conversion downstream.
    records = df.to_dict("records")
    for record in records:
        for key, value in record.items():
            if value is None:
                continue
            if isinstance(value, float) and math.isnan(value):
                record[key] = None
            elif pd.isna(value):
                record[key] = None
    return records


# Blank spreadsheet cells can round-trip through pandas/str() as these
# placeholder tokens; treat them as blank so they never render as
# literal "None" text in the catalog.
_BLANKISH_TOKENS = {"none", "nan", "null"}


def _clean_str(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clean_text(value) -> str | None:
    """Free-text columns only: junk placeholder tokens count as blank.

    Deliberately NOT applied by _parse_bool/_parse_cents/_parse_int —
    a junk token in a boolean/numeric column must stay a loud import
    problem, not silently coerce to the default/NULL.
    """
    text = _clean_str(value)
    if text is not None and text.lower() in _BLANKISH_TOKENS:
        return None
    return text


def _parse_bool(value, default: bool) -> bool:
    text = _clean_str(value)
    if text is None:
        return default
    lowered = text.lower()
    if lowered in TRUE_VALUES:
        return True
    if lowered in FALSE_VALUES:
        return False
    raise ValueError(f"Invalid boolean value: {value!r}")


def _parse_cents(value) -> int | None:
    text = _clean_str(value)
    if text is None:
        return None
    try:
        return int(Decimal(text) * 100)
    except InvalidOperation as exc:
        raise ValueError(f"Invalid unit_cost value: {value!r}") from exc


def _parse_int(value) -> int | None:
    text = _clean_str(value)
    if text is None:
        return None
    try:
        return int(float(text))
    except ValueError as exc:
        raise ValueError(f"Invalid integer value: {value!r}") from exc


def _parse_row_fields(row: dict) -> tuple[dict, list[str]]:
    """Parse the non-identity fields of a row. Returns (parsed, problems)."""
    parsed: dict = {}
    problems: list[str] = []

    try:
        parsed["unit_cost_cents"] = _parse_cents(row.get("unit_cost"))
    except ValueError as exc:
        problems.append(str(exc))
    try:
        parsed["qty_on_hand"] = _parse_int(row.get("qty_on_hand"))
    except ValueError as exc:
        problems.append(str(exc))

    for flag, default in BOOLEAN_DEFAULTS.items():
        try:
            parsed[flag] = _parse_bool(row.get(flag), default)
        except ValueError as exc:
            problems.append(str(exc))

    parsed["notes"] = _clean_text(row.get("notes"))
    parsed["order_guidance"] = _clean_text(row.get("order_guidance"))
    parsed["location_zone"] = _clean_text(row.get("location_zone"))
    parsed["bin_location"] = _clean_text(row.get("bin_location"))
    return parsed, problems


def _identity_problems(category_code: str, item_name: str, unit: str, categories: dict):
    """Validate the row's identity fields. Returns (category_or_None, problems)."""
    problems: list[str] = []
    if not category_code:
        problems.append("category_code is required")
    if not item_name:
        problems.append("item_name is required")
    if not unit:
        problems.append("unit is required")

    category = categories.get(category_code.upper()) if category_code else None
    if category_code and category is None:
        problems.append(f"Unknown category_code: {category_code}")
    return category, problems


def _find_existing_item(category_id: int, item_name: str) -> SupplyItem | None:
    """Find the existing item matching (category, name) case-insensitively.

    No DB uniqueness constraint guarantees a single match, so an ambiguous
    match (two items differing only by case) raises ValueError — the caller
    turns it into a per-row error rather than crashing the whole batch.
    """
    matches = (
        db.session.query(SupplyItem)
        .filter(SupplyItem.category_id == category_id)
        .filter(func.lower(SupplyItem.item_name) == item_name.lower())
        .limit(2)
        .all()
    )
    if len(matches) > 1:
        raise ValueError(
            "Multiple existing items match this category+name "
            "(case-insensitive) — resolve the duplicate in the admin UI first"
        )
    return matches[0] if matches else None


def classify_rows(rows: list[dict]) -> ImportPreview:
    """Classify parsed rows against the DB as creates, updates, or errors."""
    preview = ImportPreview()
    categories = {c.code.upper(): c for c in db.session.query(SupplyCategory).all()}

    for index, row in enumerate(rows):
        row_number = index + 2  # header is row 1; first data row is row 2
        category_code = _clean_str(row.get("category_code")) or ""
        item_name = _clean_str(row.get("item_name")) or ""
        unit = _clean_str(row.get("unit")) or ""

        category, problems = _identity_problems(category_code, item_name, unit, categories)
        parsed_fields, field_problems = _parse_row_fields(row)
        problems.extend(field_problems)

        if problems:
            preview.errors.append({"row_number": row_number, "problems": problems})
            continue

        record = {
            "category_id": category.id,
            "item_name": item_name,
            "unit": unit,
            **parsed_fields,
        }

        try:
            existing = _find_existing_item(category.id, item_name)
        except ValueError as exc:
            preview.errors.append({"row_number": row_number, "problems": [str(exc)]})
            continue
        if existing:
            record["existing_id"] = existing.id
            record["changed_fields"] = [
                field_name for field_name in _WRITABLE_FIELDS
                if record.get(field_name) != getattr(existing, field_name)
            ]
            preview.updates.append(record)
        else:
            preview.creates.append(record)

    return preview


_WRITABLE_FIELDS = (
    "category_id", "item_name", "unit", "notes", "order_guidance",
    "is_limited", "is_popular", "is_expendable", "notes_required",
    "is_active", "unit_cost_cents", "qty_on_hand", "location_zone",
    "bin_location",
)


def apply_import(creates: list[dict], updates: list[dict], user_id) -> tuple[int, int]:
    """Create/update SupplyItem rows from classified import records.

    Never writes image_url — images are admin-form-only. Writes one
    ConfigAuditEvent per created/updated row (same convention as
    data_upload.py's bulk uploads). Caller commits.
    """
    for record in creates:
        item = SupplyItem(**{field: record.get(field) for field in _WRITABLE_FIELDS})
        item.created_by_user_id = user_id
        item.updated_by_user_id = user_id
        db.session.add(item)
        db.session.flush()  # assign item.id before logging the audit row
        log_config_change(
            "supply_item", item.id, CONFIG_AUDIT_CREATE,
            {"source": "bulk_upload"},
            user_id=user_id,
        )

    for record in updates:
        item = db.session.get(SupplyItem, record["existing_id"])
        for field_name in _WRITABLE_FIELDS:
            setattr(item, field_name, record.get(field_name))
        item.updated_by_user_id = user_id
        log_config_change(
            "supply_item", item.id, CONFIG_AUDIT_UPDATE,
            {
                "changed_fields": record.get("changed_fields"),
                "source": "bulk_upload",
            },
            user_id=user_id,
        )

    db.session.flush()
    return len(creates), len(updates)
