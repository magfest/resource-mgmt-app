"""JSON endpoints for the Unit station, a one-pass check on a CT60 scanner.

A scan finds the unit, gives it a number if it has none, opens its check, and
the finish places it in the scanner's home container and queues its info label.
Answers carry `ok` and `message` like the check routes; a refusal is HTTP 200.
"""
from flask import g, jsonify

from app import db
from app.inventory import labels
from app.inventory.audit import ACTION_ADD_CODE, audit_who, record_unit_change
from app.inventory.checks import (
    CheckBlocked, blocking_check, last_result, open_check, open_check_for, set_aside, unit_code,
)
from app.inventory.ledger import InventoryError
from app.inventory.printing import unit_number
from app.inventory.receive import AlreadyReceived, check_tag, receive_unit
from app.inventory.resolver import (
    KIND_AMBIGUOUS, KIND_CONTAINER, KIND_UNIT, KIND_UNKNOWN, KIND_UNUSED, resolve,
)
from app.inventory.tagging import _locked_unit, link_tag, tag_candidates
from app.inventory.unit_station import (
    digit_candidates, finish_unit, give_unit_number, place_last, unit_card,
)
from app.models import InvUnit, SupplyItem
from app.models.constants import INV_STATION_UNIT

from . import inventory_bp
from .scan_api import (
    MAX_CANDIDATES, _check_answer, _check_event, _json_body, _optional_text, _own_check, api,
    run_once,
)
from .scan_auth import scan_actor

NOT_A_UNIT_STATION = "This scanner is not set up as a Unit station."
NO_HOME = "This scanner has no home container. Ask an inventory admin."
SCAN_UNIT_FIRST = "Scan a unit first, then its pallet."
UNUSED_U_LABEL = "This U- label is on no unit. Scan the unit's asset tag, or tap No code on it."
NO_SUCH_UNIT = "No unit has that code. Tap No code on it to find the unit."


def _station_only() -> None:
    """Refuse a device enrolled for another station; a signed-in operator passes."""
    principal = g.scan_principal
    if principal.is_device and principal.device.station_code != INV_STATION_UNIT:
        raise InventoryError(NOT_A_UNIT_STATION)


def _home():
    """The container this scanner places units in, or None for an operator.

    A UNIT device always has one; enrollment requires it. A device that lost it
    is refused here rather than finishing checks that place nothing.
    """
    _station_only()
    principal = g.scan_principal
    if not principal.is_device:
        return None
    if principal.device.home_container is None:
        raise InventoryError(NO_HOME)
    return principal.device.home_container


def _begin(unit: InvUnit, actor, event, again: bool = False) -> dict:
    """Open the unit's check, or ask first when it was already checked and `again` is unset."""
    blocked = blocking_check(actor, unit)
    if blocked is not None:
        raise CheckBlocked(blocked)
    resumed = open_check_for(actor)
    card = unit_card(unit)
    if resumed is None and not again:
        prior = last_result(unit, event)
        if prior is not None:
            return {"already_checked": prior, "code": unit_code(unit),
                    "name": unit.item.item_name, "card": card}
    check = open_check(unit, INV_STATION_UNIT, actor, event)
    return {**_check_answer(check, False), "card": card}


def _holder_refusal(held: AlreadyReceived, tag: str | None) -> InventoryError:
    """Name the unit that holds the tag; AlreadyReceived alone says only "Already received"."""
    holder = held.unit
    return InventoryError(
        f"{(tag or '').strip()} is already on {holder.name or held.already['code']}.")


def _unit_id(data: dict) -> int:
    unit_id = data.get("unit_id")
    return unit_id if type(unit_id) is int else -1


@inventory_bp.post("/scan/api/unit/keepalive")
@api
def scan_unit_keepalive():
    """Keep the shift open while a check has no server traffic. The `api` wrapper touches it."""
    _station_only()
    return jsonify(ok=True)


@inventory_bp.post("/scan/api/unit/scan")
@api
def scan_unit_scan():
    """Place the last unit on a scanned pallet, or find a unit and open its check."""
    home = _home()
    data = _json_body()

    def scan() -> dict:
        actor = scan_actor()
        resolution = resolve(_optional_text(data, "code"))
        kind = resolution.kind
        if kind == KIND_CONTAINER:
            last_id = data.get("last_unit_id")
            if type(last_id) is not int:
                raise InventoryError(SCAN_UNIT_FIRST)
            name = place_last(actor, resolution.container.label_code, last_id)
            return {"placed": name, "card": unit_card(db.session.get(InvUnit, last_id))}
        unit = None
        if kind == KIND_UNIT:
            unit = resolution.unit
        elif kind == KIND_AMBIGUOUS:
            units = [c for c in resolution.candidates if isinstance(c, InvUnit)]
            if units and len(units) == len(resolution.candidates):
                unit = next((u for u in units if u.id == _unit_id(data)), None)
                if unit is None:
                    return {"candidates": [unit_card(u) for u in units[:MAX_CANDIDATES]]}
        if unit is not None:
            if unit_number(unit) is None:
                return {"needs_number": True, "card": unit_card(unit),
                        "asset_tag_required": unit.item.needs_asset_tag}
            return _begin(unit, actor, _check_event(), again=data.get("again") is True)
        if kind == KIND_UNUSED and resolution.prefix == labels.PREFIX_UNIT:
            raise InventoryError(UNUSED_U_LABEL)
        if kind == KIND_UNKNOWN:
            try:
                check_tag(resolution.code)
            except InventoryError:
                raise InventoryError(NO_SUCH_UNIT) from None
            return {"which": True, "tag": resolution.code}
        raise InventoryError(NO_SUCH_UNIT)

    return jsonify(run_once(data.get("client_request_id"), scan))


@inventory_bp.post("/scan/api/unit/candidates")
@api
def scan_unit_candidates():
    """List unnumbered units by the last digits of their name, or by name text. Reads only."""
    _home()
    data = _json_body()
    digits, name = _optional_text(data, "digits"), _optional_text(data, "name")
    if digits is not None:
        found = digit_candidates(digits)
    elif name is not None:
        found = tag_candidates(name)
    else:
        raise InventoryError("Type the unit's number or name.")
    return jsonify(ok=True, candidates=[unit_card(u) for u in found])


@inventory_bp.post("/scan/api/unit/link")
@api
def scan_unit_link():
    """Attach an asset tag to a unit and open its check; the finish queues the label."""
    _home()
    data = _json_body()

    def link() -> dict:
        actor = scan_actor()
        event = _check_event()
        try:
            linked = link_tag(_unit_id(data), _optional_text(data, "tag"), actor,
                              client_request_id=data.get("client_request_id"),
                              queue_label=False)
        except AlreadyReceived as held:
            raise _holder_refusal(held, _optional_text(data, "tag")) from None
        unit = db.session.get(InvUnit, linked["unit_id"])
        return _begin(unit, actor, event)

    return jsonify(run_once(data.get("client_request_id"), link))


@inventory_bp.post("/scan/api/unit/give-number")
@api
def scan_unit_give_number():
    """Give a unit a new U- number, record it and open its check."""
    _home()
    data = _json_body()

    def give() -> dict:
        actor = scan_actor()
        event = _check_event()
        unit = _locked_unit(_unit_id(data))
        code = give_unit_number(unit)
        record_unit_change(unit.id, ACTION_ADD_CODE, {"codes": {"old": [], "new": [code]}},
                           audit_who(actor))
        return {**_begin(unit, actor, event), "code": code}

    return jsonify(run_once(data.get("client_request_id"), give))


@inventory_bp.post("/scan/api/unit/set-aside")
@api
def scan_unit_set_aside():
    """Close an open check with no result."""
    _home()
    data = _json_body()

    def aside() -> dict:
        actor = scan_actor()
        set_aside(_own_check(data.get("check_id")), actor)
        return {}

    return jsonify(run_once(data.get("client_request_id"), aside))


@inventory_bp.post("/scan/api/unit/models")
@api
def scan_unit_models():
    """List active serialized models by name, for adding a unit nobody has recorded. Reads only."""
    _home()
    rows = (db.session.query(SupplyItem)
            .filter(SupplyItem.is_serialized.is_(True), SupplyItem.is_active.is_(True))
            .order_by(SupplyItem.item_name, SupplyItem.id).all())
    return jsonify(ok=True, models=[{"id": i.id, "name": i.item_name,
                                       "needs_asset_tag": bool(i.needs_asset_tag)} for i in rows])


@inventory_bp.post("/scan/api/unit/create")
@api
def scan_unit_create():
    """Add a unit of a model nobody has recorded, then open its check.

    add_unit refuses a unit with no name and no identifier, so a missing tag gets a
    generated U- number instead. A model that needs an asset tag is refused without one.
    """
    home = _home()
    data = _json_body()

    def create() -> dict:
        actor = scan_actor()
        event = _check_event()
        item_id = data.get("item_id")
        item = db.session.get(SupplyItem, item_id) if type(item_id) is int else None
        if item is None:
            raise InventoryError("Pick a model.")
        tag = (_optional_text(data, "tag") or "").strip() or None
        if tag is None and item.needs_asset_tag:
            raise InventoryError(f"{item.item_name} needs an asset tag. Scan one.")
        try:
            created = receive_unit(item, actor, tag=tag, container=home,
                                   client_request_id=data.get("client_request_id"),
                                   queue_label=False)
        except AlreadyReceived as held:
            raise _holder_refusal(held, tag) from None
        unit = db.session.get(InvUnit, created["unit_id"])
        return _begin(unit, actor, event, again=True)

    return jsonify(run_once(data.get("client_request_id"), create))


@inventory_bp.post("/scan/api/unit/finish")
@api
def scan_unit_finish():
    """Finish the check, place the unit at the scanner's home and queue its label."""
    home = _home()
    data = _json_body()

    def finish() -> dict:
        actor = scan_actor()
        check = _own_check(data.get("check_id"))
        raw = data.get("answers")
        if not isinstance(raw, dict):
            raise InventoryError("Answer every line.")
        try:
            answers = {int(k): v for k, v in raw.items()}
        except (TypeError, ValueError):
            raise InventoryError("Answer every line.") from None
        # Retiring the unit retires its numbers, so the card is built first.
        card = unit_card(check.unit)
        line_ids = data.get("line_ids")
        out = finish_unit(check, answers, actor, home,
                          failed_condition=_optional_text(data, "failed_condition"),
                          note=_optional_text(data, "note"),
                          line_ids=line_ids if isinstance(line_ids, list) else None,
                          client_request_id=data.get("client_request_id"))
        return {**out, "card": card}

    return jsonify(run_once(data.get("client_request_id"), finish))
