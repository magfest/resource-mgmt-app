"""Typed field values on units, and the per-model defaults that seed them.

A value row sets only the column that matches its field type: choice_id,
number_value, bool_value or text_value. InvModelDefault has the same columns.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

from app import db
from app.inventory.ledger import InventoryError
from app.models import InvField, InvGearKind, InvModelDefault, InvUnit, InvUnitValue
from app.models.constants import (
    INV_FIELD_BOOL, INV_FIELD_CHOICE, INV_FIELD_NUMBER, INV_FIELD_TEXT,
)

_VALUE_COLUMNS = ("choice_id", "number_value", "bool_value", "text_value")
_TRUE = {"yes", "y", "true", "1"}
_FALSE = {"no", "n", "false", "0"}
_MAX_NUMBER = 1000
_MAX_TEXT = 128
_NUMBER_SUFFIXES = ('"', "inch", "in")
_MAX_SHOWN = 3
# Seeded on every gear kind; the label prints it as the Model row.
MODEL_FIELD_CODE = "MODEL"


def active_fields(gear_kind: InvGearKind | None) -> list[InvField]:
    """The kind's active fields in display order; empty when there is no kind."""
    if gear_kind is None:
        return []
    return [f for f in gear_kind.fields if f.is_active]


def parse_value(field: InvField, raw: str | None) -> dict | None:
    """Turn typed text into the value columns to set; None when blank."""
    text = (raw or "").strip()
    if not text:
        return None
    if field.field_type == INV_FIELD_TEXT:
        if len(text) > _MAX_TEXT:
            raise InventoryError(f"{field.name} can be at most {_MAX_TEXT} characters.")
        return {"text_value": text}
    if field.field_type == INV_FIELD_BOOL:
        lowered = text.casefold()
        if lowered in _TRUE:
            return {"bool_value": True}
        if lowered in _FALSE:
            return {"bool_value": False}
        raise InventoryError(f"{field.name} must be yes or no.")
    if field.field_type == INV_FIELD_CHOICE:
        live = [c for c in field.choices if c.is_active]
        for choice in live:
            if choice.label.casefold() == text.casefold():
                return {"choice_id": choice.id}
        options = ", ".join(c.label for c in live)
        raise InventoryError(f"{field.name} must be one of {options}.")
    if field.field_type == INV_FIELD_NUMBER:
        return {"number_value": _parse_number(field, text)}
    raise InventoryError(f"{field.name} has an unknown type.")


def _parse_number(field: InvField, text: str) -> Decimal:
    cleaned = text.casefold()
    for suffix in _NUMBER_SUFFIXES:
        if cleaned.endswith(suffix):
            cleaned = cleaned[: -len(suffix)].strip()
            break
    try:
        number = Decimal(cleaned)
    except InvalidOperation:
        raise InventoryError(f"{field.name} must be a number.") from None
    if not number.is_finite():
        raise InventoryError(f"{field.name} must be a number.")
    if number <= 0 or number > _MAX_NUMBER:
        raise InventoryError(f"{field.name} must be greater than 0 and at most {_MAX_NUMBER}.")
    return number.quantize(Decimal("0.01"))


def _write(row, columns: dict | None) -> bool:
    """Set the value columns on a row; True when anything changed."""
    columns = columns or {}
    changed = False
    for name in _VALUE_COLUMNS:
        new = columns.get(name)
        old = getattr(row, name)
        if old != new:
            setattr(row, name, new)
            changed = True
    return changed


def _set(owner, owner_column: str, model, field: InvField, raw: str | None) -> bool:
    """Create, update or delete the owner's row for a field; True when it changed."""
    columns = parse_value(field, raw)
    row = (db.session.query(model)
           .filter_by(field_id=field.id, **{owner_column: owner.id}).first())
    if columns is None:
        if row is None:
            return False
        db.session.delete(row)
        changed = True
    elif row is None:
        db.session.add(model(field_id=field.id, **{owner_column: owner.id}, **columns))
        changed = True
    else:
        changed = _write(row, columns)
    db.session.flush()
    db.session.expire(owner)
    return changed


def set_unit_value(unit: InvUnit, field: InvField, raw: str | None) -> bool:
    """Set or clear a unit's value for a field; True when it changed."""
    return _set(unit, "unit_id", InvUnitValue, field, raw)


def set_model_default(item, field: InvField, raw: str | None) -> bool:
    """Set or clear the default value for new units of an item; True when it changed."""
    return _set(item, "item_id", InvModelDefault, field, raw)


def apply_model_defaults(unit: InvUnit) -> None:
    """Copy the item's defaults into fields the unit has no value for.

    Defaults are copied once. Changing a default later does not rewrite units.
    """
    have = {v.field_id for v in db.session.query(InvUnitValue.field_id)
            .filter_by(unit_id=unit.id)}
    defaults = db.session.query(InvModelDefault).filter_by(item_id=unit.item_id).all()
    for default in defaults:
        if default.field_id in have:
            continue
        db.session.add(InvUnitValue(
            unit_id=unit.id, field_id=default.field_id,
            **{name: getattr(default, name) for name in _VALUE_COLUMNS}))
    db.session.flush()
    db.session.expire(unit, ["values"])


def _number_text(number: Decimal) -> str:
    text = format(number, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def display_value(field: InvField, value) -> str:
    """Display text for a value row: "50 in", "Yes" or "No", a choice label, or text."""
    if value is None:
        return ""
    if field.field_type == INV_FIELD_NUMBER:
        if value.number_value is None:
            return ""
        number = _number_text(value.number_value)
        return f"{number} {field.unit_label}" if field.unit_label else number
    if field.field_type == INV_FIELD_BOOL:
        if value.bool_value is None:
            return ""
        return "Yes" if value.bool_value else "No"
    if field.field_type == INV_FIELD_CHOICE:
        return value.choice.label if value.choice is not None else ""
    return value.text_value or ""


def unit_field_rows(unit: InvUnit) -> list[tuple[InvField, str]]:
    """Active fields of the unit's gear kind, plus retired fields that hold a value."""
    by_field = {v.field_id: v for v in unit.values}
    kind = unit.item.gear_kind if unit.item is not None else None
    rows = []
    seen = set()
    for field in kind.fields if kind is not None else []:
        if field.is_active or field.id in by_field:
            rows.append((field, display_value(field, by_field.get(field.id))))
            seen.add(field.id)
    for value in unit.values:
        if value.field_id not in seen:
            rows.append((value.field, display_value(value.field, value)))
    return rows


def details_line(unit: InvUnit) -> str:
    """The first three active fields that have a value, joined with commas.

    Commas, not a middle dot: the Zebra printer font has no middle dot and prints a gap.
    """
    texts = [text for field, text in unit_field_rows(unit) if field.is_active and text]
    return ", ".join(texts[:_MAX_SHOWN])


def _label_text(field: InvField, value) -> str:
    if field.field_type == INV_FIELD_BOOL:
        return (field.label_text or field.name) if value.bool_value else ""
    if field.field_type == INV_FIELD_CHOICE:
        choice = value.choice
        return (choice.label_text or choice.label) if choice is not None else ""
    if field.field_type == INV_FIELD_NUMBER:
        if value.number_value is None:
            return ""
        return _number_text(value.number_value) + (field.unit_label or "")
    return value.text_value or ""


def label_specs(unit: InvUnit) -> list[str]:
    """Short label texts of the active on_label fields that have a value, in position order.

    A BOOL prints its label_text on Yes and nothing on No.
    """
    by_field = {v.field_id: v for v in unit.values}
    kind = unit.item.gear_kind if unit.item is not None else None
    specs = []
    for field in kind.fields if kind is not None else []:
        value = by_field.get(field.id)
        if field.is_active and field.on_label and value is not None:
            text = _label_text(field, value)
            if text:
                specs.append(text)
    return specs


def spec_line(unit: InvUnit) -> str:
    """The item name with its label specs, such as `Laser Printer (BW, USB)`."""
    specs = label_specs(unit)
    name = unit.item.item_name
    return f"{name} ({', '.join(specs)})" if specs else name


def model_number(unit: InvUnit) -> str | None:
    """The vendor model number from the MODEL field, or None when blank."""
    for value in unit.values:
        if value.field.code == MODEL_FIELD_CODE and value.text_value:
            return value.text_value
    return None


def label_line(unit: InvUnit) -> str:
    """The printed info label's description on one line; never empty.

    Matches `fields_for_unit` in printing.py: a named unit shows its spec line and
    model, an unnamed one its item name and details.
    """
    if unit.name:
        model = model_number(unit)
        return f"{spec_line(unit)} · Model {model}" if model else spec_line(unit)
    details = details_line(unit)
    name = unit.item.item_name
    return f"{name} · {details}" if details else name
