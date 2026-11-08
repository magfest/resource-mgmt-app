"""Import the old tech exports (TVs, monitors, printers, label printers, laminators,
projectors, barcode scanners, cameras).

Each row becomes a named unit with its typed fields, any MT asset tag, its lasting
condition and the old notes. A row that needs a person goes to the review list
instead of being guessed. Power bins and other counted stock import elsewhere.
"""
from __future__ import annotations

import csv
import io
from collections import Counter
from dataclasses import dataclass, field

from flask import current_app
from sqlalchemy import func

from app import db
from app.inventory.codes import normalize_code
from app.inventory.ledger import Actor, InventoryError, add_unit, set_condition
from app.inventory.names import MAX_NAME_LENGTH, name_key
from app.inventory.receive import AlreadyReceived, check_tag
from app.inventory.unit_fields import (
    MODEL_FIELD_CODE, active_fields, parse_value, set_unit_value,
)
from app.models import InvGearKind, InvUnit, SupplyCategory, SupplyItem
from app.models.constants import (
    INV_CONDITION_IN_SERVICE, INV_CONDITION_LOST, INV_CONDITION_REPAIR,
    INV_CONDITION_RETIRED, INV_IDENT_MAGFEST_TAG,
)

PREFIX_KINDS = {"MAG-TV-": "TV", "MAG-MON-": "MONITOR", "MAG-PNTR-": "PRINTER",
                "MAG-LPRNT-": "LABEL_PRINTER", "MAG-LAM-": "LAMINATOR",
                "MAG-PROJ-": "PROJECTOR", "BRC-": "BARCODE_SCANNER",
                "DOC-CAM-": "CAMERA", "MAG-CAM-": "CAMERA"}
CONDITION_FOR_STATUS = {"broken (do not use)": INV_CONDITION_REPAIR,
                        "do not deploy": INV_CONDITION_REPAIR,
                        "dead": INV_CONDITION_REPAIR,
                        "missing (do not use)": INV_CONDITION_LOST}
# A disposed unit left the fleet, so its row is skipped even when the name is new.
SKIPPED_STATUS = "disposed"
NEW_PREFIX = "new:"
NOT_CSV = "This file could not be read as a CSV."
APPLY_FAILED = "Nothing was imported. The error was logged."
# Wider ids are refused before they reach an integer column.
MAX_ID_DIGITS = 9
NOT_TECH = ("No rows in this file look like tech units. "
            "Power bins and other stock import separately.")
MAX_TYPE_NAME = 256
# The TV export's Model column holds brands, so every TV shares one key.
TV_KIND = "TV"

# The exports carry both "id", a row number, and "ID", the unit name. Only the
# exact "ID" is the name; every other header is trimmed and casefolded.
_NAME_HEADER = "ID"
_HEADERS = {"asset tag": "tag", "model": "model_text", "model number": "model_text",
            "type": "model_text",
            "size": "size", "resolution": "resolution", "toner cartridge": "toner",
            "status": "status", "notes": "notes"}


@dataclass(frozen=True)
class LegacyRow:
    line: int
    name: str
    tag: str
    model_text: str
    size: str
    resolution: str
    toner: str
    status: str
    notes: str


_ROW_TEXT = ("name", "tag", "model_text", "size", "resolution", "toner", "status", "notes")


@dataclass(frozen=True)
class ModelText:
    key: str
    gear_kind_code: str
    text: str
    count: int


@dataclass
class LegacyPreview:
    ready: list[LegacyRow] = field(default_factory=list)
    review: list[str] = field(default_factory=list)
    skipped: int = 0
    warnings: list[str] = field(default_factory=list)
    models: list[ModelText] = field(default_factory=list)
    not_tech: bool = False


@dataclass(frozen=True)
class _NewType:
    name: str
    gear_kind_id: int


@dataclass
class _Planned:
    row: LegacyRow
    target: SupplyItem | _NewType
    values: list


def read_export(data: bytes) -> list[LegacyRow]:
    """Parse an export into rows, dropping any row with nothing in the columns read.

    The row number and ignored columns such as Inventoried do not count, so a
    row holding only those is blank.

    Raises:
        InventoryError: The file is not UTF-8 text, or the csv module refuses it.
    """
    try:
        # The exports start with a byte order mark, which utf-8-sig strips.
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise InventoryError("The file is not UTF-8 text. Export it again as CSV.") from None
    try:
        return _rows(csv.reader(io.StringIO(text)))
    except csv.Error:
        # The csv module refuses a cell over 131072 characters by default.
        raise InventoryError(NOT_CSV) from None


def _rows(reader) -> list[LegacyRow]:
    header = next(reader, None) or []
    columns = {}
    model_columns = []
    for index, raw in enumerate(header):
        if raw == _NAME_HEADER:
            columns.setdefault("name", index)
            continue
        target = _HEADERS.get(raw.strip().casefold())
        if target == "model_text":
            model_columns.append(index)
        elif target is not None:
            columns.setdefault(target, index)
    rows = []
    # line is the spreadsheet row: the header is row 1.
    for line, values in enumerate(reader, start=2):

        def get(column):
            index = columns.get(column)
            return values[index].strip() if index is not None and index < len(values) else ""

        # The first non-empty of Model, Model Number and Type, in column order.
        model_text = next((values[i].strip() for i in model_columns
                           if i < len(values) and values[i].strip()), "")
        row = LegacyRow(line=line, name=" ".join(get("name").split()), tag=get("tag"),
                        model_text=model_text, size=get("size"),
                        resolution=get("resolution"), toner=get("toner"),
                        status=get("status"), notes=get("notes"))
        if any(getattr(row, name) for name in _ROW_TEXT):
            rows.append(row)
    return rows


def parse_id(raw: str | None) -> int | None:
    """Read a form id; None unless it is 1 to 9 ASCII digits.

    str.isdigit alone accepts characters such as "²" that int() refuses.
    """
    text = (raw or "").strip()
    if text.isascii() and text.isdigit() and len(text) <= MAX_ID_DIGITS:
        return int(text)
    return None


def kind_code_for(name: str) -> str | None:
    upper = name.upper()
    # Longest first, so a short prefix never claims a name a longer one fits.
    for prefix, code in sorted(PREFIX_KINDS.items(), key=lambda p: len(p[0]), reverse=True):
        if upper.startswith(prefix):
            return code
    return None


def model_key(row: LegacyRow) -> str:
    """The mapping key: the gear kind code and the trimmed model text, TV rows sharing one."""
    code = kind_code_for(row.name) or ""
    if code == TV_KIND:
        return f"{TV_KIND}|"
    return f"{code}|{row.model_text}"


def condition_for(status: str) -> str:
    return CONDITION_FOR_STATUS.get(status.strip().casefold(), INV_CONDITION_IN_SERVICE)


def _label(row: LegacyRow) -> str:
    return f"Row {row.line} ({row.name or 'no ID'})"


def _name_problem(row: LegacyRow, name_counts: Counter) -> str | None:
    if not row.name:
        return "The ID is blank."
    if row.name == "???":
        return "The ID is ???, so the unit has no name."
    if kind_code_for(row.name) is None:
        return "The ID does not start with a known prefix."
    if name_counts[name_key(row.name)] > 1:
        return "The ID appears more than once in this file."
    if len(row.name) > MAX_NAME_LENGTH:
        return f"The ID is longer than {MAX_NAME_LENGTH} characters."
    return None


def _tag_problem(row: LegacyRow, tag_counts: Counter) -> str | None:
    if not row.tag:
        return None
    if tag_counts[normalize_code(row.tag)] > 1:
        return f"Tag {row.tag} appears more than once in this file."
    try:
        check_tag(row.tag)
    except AlreadyReceived:
        return f"Tag {row.tag} is already on another unit."
    except InventoryError:
        return f"Tag {row.tag} cannot be used as a MAGFest tag."
    return None


def _describe(kind: InvGearKind, text: str) -> str:
    if kind.code == TV_KIND:
        return "TV rows"
    return f"{kind.name} {text}" if text else f"{kind.name} rows with no model"


class _Resolver:
    """Turn each mapping value into a type, once per key; new types are shared by name."""

    def __init__(self, mapping: dict[str, str], category_id: int | None,
                 kinds_for_new: dict[str, set[int]]):
        self.mapping = mapping
        self.category = (db.session.get(SupplyCategory, category_id)
                         if category_id is not None else None)
        self.kinds_for_new = kinds_for_new
        self.new_types: dict[str, _NewType] = {}
        self.cache: dict[str, tuple] = {}

    def resolve(self, key: str, kind: InvGearKind, text: str):
        if key not in self.cache:
            self.cache[key] = self._resolve(key, kind, text)
        return self.cache[key]

    def _resolve(self, key, kind, text):
        value = (self.mapping.get(key) or "").strip()
        if not value:
            return None, f"Choose a type for {_describe(kind, text)}."
        if value.startswith(NEW_PREFIX):
            return self._new(_type_name(value), kind)
        item_id = parse_id(value)
        item = db.session.get(SupplyItem, item_id) if item_id is not None else None
        if item is None or not item.is_serialized or item.gear_kind_id != kind.id:
            return None, f"The chosen type is not a {kind.name} type."
        return item, None

    def _new(self, name: str, kind: InvGearKind):
        if not name:
            return None, "Enter a name for the new type."
        if len(name) > MAX_TYPE_NAME:
            return None, f"A type name can be at most {MAX_TYPE_NAME} characters."
        existing = (db.session.query(SupplyItem)
                    .filter(func.lower(SupplyItem.item_name) == name.lower())
                    .order_by(SupplyItem.id).first())
        if existing is not None:
            if not existing.is_serialized:
                return None, f"A non-serialized item is already named {existing.item_name}."
            if existing.gear_kind_id != kind.id:
                return None, f"{existing.item_name} is not a {kind.name} type."
            return existing, None
        if len(self.kinds_for_new.get(name.casefold(), ())) > 1:
            return None, f"{name} is chosen for more than one gear kind."
        if self.category is None:
            return None, "Choose a category for new types."
        new = self.new_types.setdefault(name.casefold(), _NewType(name, kind.id))
        return new, None


def _type_name(value: str) -> str:
    return " ".join(value[len(NEW_PREFIX):].split())


def _field_values(row: LegacyRow, kind: InvGearKind) -> tuple[list, list[str]]:
    """The (field, raw) pairs to write and the warnings for values left blank."""
    fields = {f.code: f for f in active_fields(kind)}
    model_code = "BRAND" if kind.code == TV_KIND else MODEL_FIELD_CODE
    columns = [(model_code, "Model", row.model_text), ("SIZE", "Size", row.size),
               ("RESOLUTION", "Resolution", row.resolution),
               ("TONER", "Toner cartridge", row.toner)]
    values, warnings = [], []
    for code, column, raw in columns:
        if not raw:
            continue
        target = fields.get(code)
        if target is None:
            warnings.append(f"{_label(row)}: {kind.name} has no {column} field. Left blank.")
            continue
        try:
            parse_value(target, raw)
        except InventoryError as error:
            warnings.append(f"{_label(row)}: {error} Left blank.")
            continue
        values.append((target, raw))
    return values, warnings


def _plan(data: bytes, mapping: dict[str, str],
          category_id: int | None) -> tuple[LegacyPreview, list[_Planned]]:
    rows = read_export(data)
    result = LegacyPreview()
    if not any(kind_code_for(r.name) for r in rows):
        result.not_tech = True
        return result, []
    kinds = {k.code: k for k in db.session.query(InvGearKind)}
    name_counts = Counter(name_key(r.name) for r in rows if r.name)
    tag_counts = Counter(normalize_code(r.tag) for r in rows if r.tag)
    keys = [k for k in name_counts if k]
    live = {k for (k,) in db.session.query(InvUnit.name_key)
            .filter(InvUnit.name_key.in_(keys), InvUnit.condition != INV_CONDITION_RETIRED)}

    review: list[tuple[int, str]] = []
    candidates = []
    for row in rows:
        if row.status.strip().casefold() == SKIPPED_STATUS:
            result.skipped += 1
            continue
        problem = _name_problem(row, name_counts)
        if problem is None and name_key(row.name) in live:
            result.skipped += 1
            continue
        kind = kinds.get(kind_code_for(row.name) or "")
        if problem is None and kind is None:
            problem = f"No gear kind {kind_code_for(row.name)} exists."
        if problem is not None:
            review.append((row.line, f"{_label(row)}: {problem}"))
            continue
        candidates.append((row, kind))

    model_counts = Counter((model_key(r), k.code, "" if k.code == TV_KIND else r.model_text)
                           for r, k in candidates)
    order = {k.code: (k.position, k.id) for k in kinds.values()}
    result.models = [ModelText(key, code, text, count) for (key, code, text), count in
                     sorted(model_counts.items(), key=lambda m: (order[m[0][1]], m[0][2]))]

    kinds_for_new: dict[str, set[int]] = {}
    for row, kind in candidates:
        value = (mapping.get(model_key(row)) or "").strip()
        if value.startswith(NEW_PREFIX) and _type_name(value):
            kinds_for_new.setdefault(_type_name(value).casefold(), set()).add(kind.id)
    resolver = _Resolver(mapping, category_id, kinds_for_new)

    planned = []
    for row, kind in candidates:
        problem = _tag_problem(row, tag_counts)
        target = None
        if problem is None:
            target, problem = resolver.resolve(model_key(row), kind, row.model_text)
        if problem is not None:
            review.append((row.line, f"{_label(row)}: {problem}"))
            continue
        values, warnings = _field_values(row, kind)
        result.warnings.extend(warnings)
        result.ready.append(row)
        planned.append(_Planned(row, target, values))
    result.review = [text for _, text in sorted(review, key=lambda r: r[0])]
    return result, planned


def preview(data: bytes, mapping: dict[str, str],
            category_id: int | None = None) -> LegacyPreview:
    """Sort the file's rows into ready, skipped and review without writing anything."""
    return _plan(data, mapping, category_id)[0]


def apply(data: bytes, mapping: dict[str, str], category_id: int | None,
          actor: Actor) -> LegacyPreview:
    """Create a unit for every ready row and commit once.

    Raises:
        InventoryError: A write failed. Everything is rolled back, so nothing is imported.
    """
    result, planned = _plan(data, mapping, category_id)
    try:
        created: dict[_NewType, SupplyItem] = {}
        for plan in planned:
            item = plan.target
            if isinstance(item, _NewType):
                if item not in created:
                    created[item] = SupplyItem(
                        item_name=item.name, unit="each", category_id=category_id,
                        is_serialized=True, is_orderable=False, is_expendable=False,
                        gear_kind_id=item.gear_kind_id, created_by_user_id=actor.user_id)
                    db.session.add(created[item])
                    db.session.flush()
                item = created[item]
            row = plan.row
            identifiers = [(INV_IDENT_MAGFEST_TAG, row.tag)] if row.tag else []
            # add_unit copies the model defaults first, so the row's values win.
            unit = add_unit(item, None, actor, identifiers, name=row.name)
            for target, raw in plan.values:
                set_unit_value(unit, target, raw)
            condition = condition_for(row.status)
            if condition != INV_CONDITION_IN_SERVICE:
                set_condition(unit, condition)
            unit.imported_notes = row.notes or None
        db.session.commit()
    except Exception as error:
        db.session.rollback()
        current_app.logger.exception("Old tech import failed")
        # The error text can hold SQL and row values, so it goes to the log only.
        raise InventoryError(APPLY_FAILED) from error
    return result
