"""Read-only facts for the About tab of an asset page."""
from __future__ import annotations

from app import db
from app.inventory.checks import active_check_items
from app.inventory.describe import TAG_ORDER
from app.inventory.last_check import check_word, latest_check_rows
from app.inventory.people import actor_name, names_for
from app.inventory.printing import local_when
from app.inventory.tree import place_name, top_of, unplaced_text
from app.inventory.unit_fields import unit_field_rows
from app.models import InvMovement
from app.models.constants import (
    INV_IDENT_FESTOPS_LABEL, INV_IDENT_MAC, INV_IDENT_MAGFEST_TAG, INV_IDENT_SERIAL,
    INV_IDENT_VENDOR_TAG, INV_OWNERSHIP_RENTED,
)

_FIXED_WORDS = {INV_IDENT_MAGFEST_TAG: "Asset tag", INV_IDENT_FESTOPS_LABEL: "Label",
                INV_IDENT_SERIAL: "Serial number", INV_IDENT_VENDOR_TAG: "Vendor tag",
                INV_IDENT_MAC: "MAC address"}


def where_sentence(unit) -> str:
    container = unit.current_container
    if container is None:
        return "Not placed yet"
    if container.parent is None:
        return unplaced_text(container)
    name = place_name(container)
    shown = name if name == container.label_code else f"{name} ({container.label_code})"
    return f"{shown}, in the {place_name(top_of(container))}"


def placed_line(unit) -> str | None:
    if unit.current_container_id is None:
        return None
    movement = (db.session.query(InvMovement)
                .filter(InvMovement.unit_id == unit.id,
                        InvMovement.to_container_id == unit.current_container_id)
                .order_by(InvMovement.created_at.desc(), InvMovement.id.desc()).first())
    if movement is None:
        return None
    names = names_for([movement.performed_by_user_id])
    who = actor_name(movement.performed_by_user_id, movement.shift, names)
    return f"Placed {local_when(movement.created_at)} by {who}"


def check_line(unit) -> dict:
    """The asset's latest check from any event: `word` is WORKS, BROKEN or None."""
    prep = latest_check_rows([unit.id]).get(unit.id)
    word = check_word(prep)
    if word is None:
        return {"word": None, "text": "Never"}
    names = names_for([prep.performed_by_user_id])
    who = actor_name(prep.performed_by_user_id, prep.shift, names)
    return {"word": word, "text": f"checked {local_when(prep.created_at)} by {who}"}


def _owner_rows(unit) -> list[tuple[str, str]]:
    if unit.ownership != INV_OWNERSHIP_RENTED:
        return [("Owned by", "FestOps")]
    rows = [("Rented from", unit.vendor_name or "Vendor not recorded")]
    if unit.rental_reference:
        rows.append(("Rental reference", unit.rental_reference))
    return rows


def added_line(unit) -> str:
    """When and by whom the asset was added, from its oldest movement."""
    movement = (db.session.query(InvMovement).filter(InvMovement.unit_id == unit.id)
                .order_by(InvMovement.created_at, InvMovement.id).first())
    if movement is None:
        return f"Added {local_when(unit.created_at)}"
    names = names_for([movement.performed_by_user_id])
    who = actor_name(movement.performed_by_user_id, movement.shift, names)
    return f"Added {local_when(movement.created_at)} by {who}"


def _identifier_word(kind: str) -> str:
    return _FIXED_WORDS[kind]


def find_by_rows(unit) -> list[tuple[str, str]]:
    rows = [("Name", unit.name)] if unit.name else []
    active = [i for i in unit.identifiers if i.retired_at is None]
    for kind in TAG_ORDER:
        for identifier in active:
            if identifier.kind != kind:
                continue
            shown = (identifier.normalized_value if kind == INV_IDENT_MAGFEST_TAG
                     else identifier.value)
            rows.append((_identifier_word(kind), shown))
    return rows


def detail_rows(unit) -> list[tuple[str, str]]:
    """The item settings that decide how this asset behaves, then its own field values.

    The settings come from its Supply item: the asset type picks its fields, the
    asset-tag rule and checklist decide what the Check station asks. Ownership
    decides whether it is counted on home.
    """
    item = unit.item
    lines = len(active_check_items(item))
    check = (f"{lines} checklist line{'' if lines == 1 else 's'}" if lines
             else "Works or Broken")
    rows = [("Item", item.item_name),
            ("Asset type", item.gear_kind.name if item.gear_kind else "Not set"),
            ("Needs an asset tag", "Yes" if item.needs_asset_tag else "No"),
            ("Check", check)]
    rows += _owner_rows(unit)
    return rows + [(field.name, text) for field, text in unit_field_rows(unit) if text]
