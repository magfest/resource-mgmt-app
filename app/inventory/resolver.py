"""Scan resolver: turn one scanned value into a container, unit, item, or a question.

Ranks, first match wins: FestOps label, MAGFest tag, vendor tag, serial or
MAC, then item barcode. A value that matches more than one unit at its rank
is ambiguous; the resolver never picks one.
"""
from __future__ import annotations

from dataclasses import dataclass

from app import db
from app.inventory import labels
from app.inventory.codes import normalize_code, normalize_mac
from app.models import InvContainer, InvUnit, InvUnitIdentifier, SupplyItem
from app.models.constants import (
    INV_IDENT_FESTOPS_LABEL, INV_IDENT_MAC, INV_IDENT_MAGFEST_TAG,
    INV_IDENT_SERIAL, INV_IDENT_VENDOR_TAG,
)

KIND_CONTAINER = "CONTAINER"
KIND_UNIT = "UNIT"
KIND_ITEM = "ITEM"
KIND_UNUSED = "UNUSED"
KIND_AMBIGUOUS = "AMBIGUOUS"
KIND_UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class Resolution:
    """What a scan means. `candidates` holds every match when kind is AMBIGUOUS.

    `prefix` is set for UNUSED: the label prefix (C, U, or I) waiting to be bound.
    """
    kind: str
    code: str
    container: InvContainer | None = None
    unit: InvUnit | None = None
    item: SupplyItem | None = None
    prefix: str | None = None
    candidates: tuple = ()


def _units_with(kinds: list[str], values: set[str]) -> list[InvUnit]:
    """Units holding an active identifier of these kinds, once each."""
    rows = (db.session.query(InvUnit)
            .join(InvUnitIdentifier, InvUnitIdentifier.unit_id == InvUnit.id)
            .filter(InvUnitIdentifier.kind.in_(kinds),
                    InvUnitIdentifier.normalized_value.in_(values),
                    InvUnitIdentifier.retired_at.is_(None))
            .order_by(InvUnit.id).all())
    return list(dict.fromkeys(rows))


def _from_units(code: str, units: list[InvUnit]) -> Resolution | None:
    if not units:
        return None
    if len(units) == 1:
        return Resolution(KIND_UNIT, code, unit=units[0])
    return Resolution(KIND_AMBIGUOUS, code, candidates=tuple(units))


def _from_items(code: str, items: list[SupplyItem]) -> Resolution | None:
    if not items:
        return None
    if len(items) == 1:
        return Resolution(KIND_ITEM, code, item=items[0])
    return Resolution(KIND_AMBIGUOUS, code, candidates=tuple(items))


def _item_barcode(code: str) -> list[SupplyItem]:
    return (db.session.query(SupplyItem).filter_by(barcode=code)
            .order_by(SupplyItem.id).all())


def _was_retired(code: str) -> bool:
    return (db.session.query(InvUnitIdentifier.id)
            .filter(InvUnitIdentifier.kind == INV_IDENT_FESTOPS_LABEL,
                    InvUnitIdentifier.normalized_value == code,
                    InvUnitIdentifier.retired_at.isnot(None))
            .first() is not None)


def _resolve_label(code: str, prefix: str) -> Resolution:
    if prefix == labels.PREFIX_CONTAINER:
        container = db.session.query(InvContainer).filter_by(label_code=code).first()
        if container is not None:
            return Resolution(KIND_CONTAINER, code, container=container)
    elif prefix == labels.PREFIX_UNIT:
        found = _from_units(code, _units_with([INV_IDENT_FESTOPS_LABEL], {code}))
        if found:
            return found
        # A retired label must not read as unused; binding it again would reuse a dead code.
        if _was_retired(code):
            return Resolution(KIND_UNKNOWN, code)
    else:
        found = _from_items(code, _item_barcode(code))
        if found:
            return found
    # An issued code with no row behind it is a printed label waiting for its first scan.
    if labels.was_issued(code):
        return Resolution(KIND_UNUSED, code, prefix=prefix)
    return Resolution(KIND_UNKNOWN, code)


def resolve(raw: str | None) -> Resolution:
    """Resolve a scanned or typed value. Never creates or changes a row."""
    code = normalize_code(raw)
    if not code:
        return Resolution(KIND_UNKNOWN, code)
    parsed = labels.parse(code)
    if parsed is not None:
        return _resolve_label(code, parsed[0])

    serial_values = {code}
    mac = normalize_mac(code)
    if mac:
        serial_values.add(mac)
    ranks = (
        ([INV_IDENT_MAGFEST_TAG], {code}),
        ([INV_IDENT_VENDOR_TAG], {code}),
        ([INV_IDENT_SERIAL, INV_IDENT_MAC], serial_values),
    )
    for kinds, values in ranks:
        found = _from_units(code, _units_with(kinds, values))
        if found:
            return found
    return _from_items(code, _item_barcode(code)) or Resolution(KIND_UNKNOWN, code)
