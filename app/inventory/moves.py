"""Move station rules: place containers and assets by scan, move loose supplies between places.

Count never places anything; Move is the only scanner station that does, apart from
Check's put-away. The destination is never stored; the scanner page holds it and sends
it with each request. Move has no undo, because the ledger reversal refuses MOVE rows.
Every function returns the JSON answer without `ok`, refuses with InventoryError, and
never commits.
"""
from __future__ import annotations

from app import db
from app.inventory import labels
from app.inventory.checks import unit_code
from app.inventory.codes import SIGN_IN_CARD, normalize_code
from app.inventory.counts import MAX_QUANTITY, TOO_LARGE, usual_items
from app.inventory.flags import raise_flag
from app.inventory.ledger import (
    Actor, InventoryError, create_container, move_container, move_stock_covering_short,
    mark_found, move_unit,
)
from app.inventory.resolver import (
    KIND_AMBIGUOUS, KIND_CARD, KIND_CONTAINER, KIND_ITEM, KIND_UNIT, KIND_UNUSED, resolve,
)
from app.inventory.tree import display_name
from app.models import InvContainer, InvContainerKind, InvFlag, InvUnit, SupplyItem
from app.models.constants import INV_CONDITION_LOST, INV_STATION_MOVE

SCAN_PLACE_FIRST = "Scan where it's going first: a pallet, a tote or a location."
USE_SUPPLIES = "To move loose items, tap Move supplies."
TWO_ASSETS = "Two assets match. Scan the asset tag instead."
ASSETS_ONE_BY_ONE = "Assets move one by one. Tap Cancel, then scan each asset."
SOURCE_NOT_A_PLACE = "Scan where they are coming from: a pallet, a tote or a location."
LOCATION_ON_SOMETHING = "A location cannot go on something. Pick another kind."
DESTINATION_AGAIN = "Scan where it's going again."
# The same words as scan_api.UNKNOWN_CODE. Copied, since a domain module never imports routes.
UNKNOWN_CODE = "Not a MAGFest number: scan the asset tag"
NOTHING_ATTACHED = "This label has nothing attached to it yet."
NO_HOME = "This scanner has no home location, so it goes at the top level."
NOT_A_CONTAINER_LABEL = "Scan a C- label for a container."
PICK_KIND = "Pick a container kind."
PICK_ITEM = "Pick an item."
WHOLE_QUANTITY = "Quantity must be a whole number above zero."
FLAG_REASON = "WRONG_DATA"


def place_title(container: InvContainer) -> str:
    title = f"{container.kind.name} {container.label_code}"
    return f"{title} {container.name}" if container.name else title


def _inactive(container: InvContainer) -> InventoryError:
    return InventoryError(f"{container.label_code} is inactive.")


def _same_place(container: InvContainer) -> InventoryError:
    return InventoryError(
        f"{container.label_code} is where things are going. Scan where they are coming from.")


def _destination_set(container: InvContainer) -> dict:
    title = place_title(container)
    return {"destination": {"code": container.label_code, "title": title},
            "message": f"Going to {title}. Scan each thing as you put it there."}


def _placed(thing: str, destination: InvContainer, moved: bool) -> dict:
    # A fixed spot is an area, so things go in it; anything else is stacked on.
    word = "in" if destination.kind.is_fixed else "on"
    already = "" if moved else " already"
    return {"moved": moved, "message": f"{thing} is {word} {destination.label_code}{already}."}


def _ambiguous(resolution) -> InventoryError:
    if all(isinstance(c, InvUnit) for c in resolution.candidates):
        return InventoryError(TWO_ASSETS)
    return InventoryError(USE_SUPPLIES)


def destination_from(code: str | None) -> InvContainer:
    """Return the active container the page sent as its destination.

    Raises:
        InventoryError: The code names no active container. The page asks for a new scan.
    """
    resolution = resolve(code if isinstance(code, str) else None)
    if resolution.kind != KIND_CONTAINER or not resolution.container.is_active:
        raise InventoryError(DESTINATION_AGAIN)
    return resolution.container


def _unused_label(code: str, destination: InvContainer | None,
                  home: InvContainer | None) -> dict:
    query = db.session.query(InvContainerKind).filter_by(is_active=True)
    if destination is None:
        where = f"It goes in {display_name(home)}." if home else NO_HOME
    else:
        # A fixed spot cannot sit on a pallet, so a new thing is never offered one.
        query = query.filter_by(is_fixed=False)
        joiner = "in" if destination.kind.is_fixed else "on"
        where = f"It goes {joiner} {destination.label_code}."
    kinds = query.order_by(InvContainerKind.sort_order, InvContainerKind.id).all()
    return {"unused": True, "code": code, "kinds": [{"id": k.id, "name": k.name} for k in kinds],
            "where": where}


def _place_container(container: InvContainer, destination: InvContainer, actor: Actor) -> dict:
    if container.id == destination.id:
        return {"same": True, "message": f"{container.label_code} is where things are going."}
    if container.parent_id == destination.id:
        return _placed(container.label_code, destination, moved=False)
    move_container(container, destination, actor)
    return _placed(container.label_code, destination, moved=True)


def _place_unit(unit: InvUnit, destination: InvContainer, actor: Actor) -> dict:
    thing = unit_code(unit) or unit.item.item_name
    if unit.current_container_id == destination.id:
        placed = _placed(thing, destination, moved=False)
        was_lost = mark_found(unit, actor)
    else:
        was_lost = unit.condition == INV_CONDITION_LOST
        move_unit(unit, destination, actor)
        placed = _placed(thing, destination, moved=True)
    if was_lost:
        placed["message"] += " It was marked lost and is now Found."
    return placed


def move_scan(code: str | None, destination: InvContainer | None, actor: Actor,
              home: InvContainer | None) -> dict:
    """Answer one scan on Move: set the destination, place a thing on it, or refuse.

    A scanned container is the destination when none is set or when its kind is fixed.
    Otherwise it is a thing being moved onto the destination.
    """
    resolution = resolve(code if isinstance(code, str) else None)
    kind = resolution.kind
    if kind == KIND_CARD:
        raise InventoryError(SIGN_IN_CARD)
    if kind == KIND_UNUSED:
        if resolution.prefix == labels.PREFIX_CONTAINER:
            return _unused_label(resolution.code, destination, home)
        raise InventoryError(NOTHING_ATTACHED)
    if kind == KIND_CONTAINER:
        container = resolution.container
        if not container.is_active:
            raise _inactive(container)
        if container.kind.is_fixed or destination is None:
            return _destination_set(container)
        return _place_container(container, destination, actor)
    if kind == KIND_AMBIGUOUS:
        raise _ambiguous(resolution)
    if kind == KIND_UNIT:
        if destination is None:
            raise InventoryError(SCAN_PLACE_FIRST)
        return _place_unit(resolution.unit, destination, actor)
    if kind == KIND_ITEM:
        raise InventoryError(SCAN_PLACE_FIRST if destination is None else USE_SUPPLIES)
    raise InventoryError(UNKNOWN_CODE)


def move_create(label_code: str | None, kind: InvContainerKind | None,
                destination: InvContainer | None, actor: Actor,
                home: InvContainer | None) -> dict:
    """Create a container from a new C- label, in home as the destination or onto the destination.

    The label checks match the Count station's create, so both refuse the same labels.
    """
    if not isinstance(label_code, str):
        raise InventoryError(NOT_A_CONTAINER_LABEL)
    parsed = labels.parse(normalize_code(label_code))
    if parsed is None or parsed[0] != labels.PREFIX_CONTAINER:
        raise InventoryError(NOT_A_CONTAINER_LABEL)
    code = labels.format_code(*parsed)
    if resolve(code).kind != KIND_UNUSED:
        raise InventoryError(f"{code} is not an unused container label.")
    if kind is None or not kind.is_active:
        raise InventoryError(PICK_KIND)
    if destination is None:
        return _destination_set(create_container(code, kind, actor, parent=home))
    if kind.is_fixed:
        raise InventoryError(LOCATION_ON_SOMETHING)
    container = create_container(code, kind, actor, parent=destination)
    return _placed(container.label_code, destination, moved=True)


def supplies_source(code: str | None, destination: InvContainer) -> dict:
    """Accept where loose supplies come from: any active container but the destination.

    A fixed spot may be a source. An unused label is refused; nothing is recorded there.
    """
    resolution = resolve(code if isinstance(code, str) else None)
    if resolution.kind == KIND_CARD:
        raise InventoryError(SIGN_IN_CARD)
    if resolution.kind == KIND_UNUSED:
        raise InventoryError(
            f"Nothing is recorded on {resolution.code} yet. Scan where they are coming from.")
    if resolution.kind != KIND_CONTAINER:
        raise InventoryError(SOURCE_NOT_A_PLACE)
    source = resolution.container
    if not source.is_active:
        raise _inactive(source)
    if source.id == destination.id:
        raise _same_place(source)
    return {"source": {"code": source.label_code, "title": place_title(source)},
            "usual": usual_items()}


def _has_open_flag(container: InvContainer) -> bool:
    return db.session.query(InvFlag.id).filter(
        InvFlag.container_id == container.id, InvFlag.reason == FLAG_REASON,
        InvFlag.resolved_at.is_(None)).first() is not None


def move_supplies(item: SupplyItem | None, source: InvContainer, destination: InvContainer,
                  quantity, actor: Actor) -> dict:
    """Move loose supplies in one movement, accepting a short source.

    A short source drops to zero and the destination gets the full amount. The
    source is flagged WRONG_DATA for a lead to recount; an open flag of that
    reason stands in place of a second one.
    """
    if item is None:
        raise InventoryError(PICK_ITEM)
    if item.is_serialized:
        raise InventoryError(ASSETS_ONE_BY_ONE)
    if not item.is_active:
        raise InventoryError(f"{item.item_name} is inactive.")
    if source.id == destination.id:
        raise _same_place(source)
    for place in (source, destination):
        if not place.is_active:
            raise _inactive(place)
    if type(quantity) is not int or quantity < 1:
        raise InventoryError(WHOLE_QUANTITY)
    if quantity > MAX_QUANTITY:
        raise InventoryError(TOO_LARGE)
    _, shortfall = move_stock_covering_short(item, source, destination, quantity, actor)
    message = (f"Moved {quantity} {item.item_name} from {source.label_code} "
               f"to {destination.label_code}.")
    if shortfall:
        if not _has_open_flag(source):
            held = quantity - shortfall
            raise_flag(actor, INV_STATION_MOVE, FLAG_REASON, container=source,
                       note=f"Recount: moved {quantity} {item.item_name} off, "
                            f"but only {held} was recorded here.")
        message += f" {source.label_code} is flagged for a lead to recount."
    return {"message": message, "short": bool(shortfall)}
