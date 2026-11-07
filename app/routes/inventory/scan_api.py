"""JSON endpoints for scanner pages.

Every route here refuses a PRINT device and answers its own 403, so a shared
scanner never lands on a login page.
"""
from functools import wraps

from flask import current_app, g, jsonify, request
from sqlalchemy.exc import SQLAlchemyError

from app import csrf, db
from app.inventory import labels
from app.inventory.cards import candidate_card, card_for, default_event
from app.inventory.codes import normalize_code
from app.inventory.ledger import InventoryError, create_container
from app.inventory.receive import (
    container_for_code, receive_unit, reprint_unit, undo_message, undo_receive,
)
from app.inventory.receipts import refusal, run_once
from app.inventory.resolver import (
    KIND_AMBIGUOUS, KIND_CONTAINER, KIND_ITEM, KIND_UNIT, KIND_UNUSED, resolve,
)
from app.inventory.shifts import current_shift, end_shift, start_shift, touch
from app.models import InvContainerKind, SupplyItem
from app.models.constants import (
    INV_CHECK_STATIONS, INV_SHIFT_END_ENDED, INV_STATION_NAMES,
)

from . import inventory_bp
from .scan_auth import device_api, require_scanner, scan_actor


MAX_CANDIDATES = 10


def api(f):
    """JSON in and out for a scanner principal; an InventoryError becomes ok=false.

    An ok answer keeps the volunteer's shift alive, reads included. The touch is
    best effort: a failure to save it never turns a good answer into an error.
    """
    @wraps(f)
    def inner(*args, **kwargs):
        try:
            result = f(*args, **kwargs)
        except InventoryError as exc:
            db.session.rollback()
            return jsonify(refusal(exc))
        try:
            if _is_ok(result):
                touch_shift()
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            current_app.logger.exception("Could not record scanner activity")
        return result
    guarded = require_scanner(device_api(inner))
    return csrf.exempt(guarded)


def _is_ok(result) -> bool:
    answer = result.get_json(silent=True) if hasattr(result, "get_json") else None
    return isinstance(answer, dict) and answer.get("ok") is True


def touch_shift() -> None:
    """Mark the device's open shift active. Never opens a shift; no-op for a signed-in user."""
    principal = g.scan_principal
    if principal.is_device:
        shift = current_shift(principal.device)
        if shift is not None:
            touch(shift)


@inventory_bp.post("/scan/api/shift")
@api
def scan_shift_start():
    principal = g.scan_principal
    if not principal.is_device:
        raise InventoryError("Signed-in operators do not pick a name.")
    data = request.get_json(silent=True)
    name = data.get("person_name") if isinstance(data, dict) else None
    if not isinstance(name, str):
        raise InventoryError("Type a name, 1 to 64 characters.")
    shift = start_shift(principal.device, name)
    return jsonify(ok=True, person_name=shift.person_name)


@inventory_bp.post("/scan/api/shift/end")
@api
def scan_shift_end():
    principal = g.scan_principal
    if principal.is_device:
        end_shift(principal.device, INV_SHIFT_END_ENDED)
    return jsonify(ok=True)


def _json_body() -> dict:
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise InventoryError("Send JSON.")
    return data


def _wrong_station(resolution) -> str | None:
    """The check station that owns this scan's model, when this device runs a different one."""
    principal = g.scan_principal
    if not principal.is_device or principal.device.station_code not in INV_CHECK_STATIONS:
        return None
    item = (resolution.unit.item if resolution.kind == KIND_UNIT
            else resolution.item if resolution.kind == KIND_ITEM else None)
    owner = item.check_station if item is not None else None
    if owner and owner != principal.device.station_code:
        return owner
    return None


@inventory_bp.post("/scan/api/resolve")
@api
def scan_resolve():
    """Say what a scanned value is. Reads only, so it needs no shift and stores nothing."""
    raw = _json_body().get("code")
    resolution = resolve(raw if isinstance(raw, str) else None)
    event = default_event()
    out = {"ok": True, "kind": resolution.kind, "code": resolution.code,
           "prefix": resolution.prefix}
    if resolution.kind in (KIND_CONTAINER, KIND_UNIT, KIND_ITEM):
        out["card"] = card_for(resolution, event)
        owner = _wrong_station(resolution)
        if owner:
            out["wrong_station"] = owner
            out["wrong_station_name"] = INV_STATION_NAMES.get(owner, owner)
    elif resolution.kind == KIND_AMBIGUOUS:
        shown = resolution.candidates[:MAX_CANDIDATES]
        out["candidates"] = [{"card": candidate_card(c, event)} for c in shown]
        if len(resolution.candidates) > MAX_CANDIDATES:
            out["more"] = len(resolution.candidates) - MAX_CANDIDATES
    return jsonify(out)


@inventory_bp.post("/scan/api/container")
@api
def scan_container_create():
    """Create a container from an issued, unused C- label under the device's home container."""
    data = _json_body()

    def create() -> dict:
        actor = scan_actor()
        raw_code = data.get("label_code")
        if not isinstance(raw_code, str):
            raise InventoryError("Scan a C- label for a container.")
        code = normalize_code(raw_code)
        parsed = labels.parse(code)
        if parsed is None or parsed[0] != labels.PREFIX_CONTAINER:
            raise InventoryError("Scan a C- label for a container.")
        code = labels.format_code(*parsed)
        if resolve(code).kind != KIND_UNUSED:
            raise InventoryError(f"{code} is not an unused container label.")
        kind_id = data.get("kind_id")
        kind = db.session.get(InvContainerKind, kind_id) if type(kind_id) is int else None
        if kind is None or not kind.is_active:
            raise InventoryError("Pick a container kind.")
        principal = g.scan_principal
        parent = principal.device.home_container if principal.is_device else None
        container = create_container(code, kind, actor, parent=parent)
        return {"code": container.label_code, "container_id": container.id}

    return jsonify(run_once(data.get("client_request_id"), create))


MAX_ITEM_RESULTS = 20


def _optional_text(data: dict, key: str) -> str | None:
    value = data.get(key)
    if value is not None and not isinstance(value, str):
        raise InventoryError("Scan a code.")
    return value


@inventory_bp.post("/scan/api/receive/items")
@api
def scan_receive_items():
    """List active serialized items whose name contains the text. Reads only."""
    text = _optional_text(_json_body(), "q") or ""
    escaped = text.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    query = db.session.query(SupplyItem).filter(
        SupplyItem.is_serialized.is_(True), SupplyItem.is_active.is_(True),
        SupplyItem.item_name.ilike(f"%{escaped}%", escape="\\"))
    rows = query.order_by(SupplyItem.item_name, SupplyItem.id).limit(MAX_ITEM_RESULTS).all()
    return jsonify(ok=True, items=[{"id": i.id, "name": i.item_name} for i in rows])


@inventory_bp.post("/scan/api/receive/location")
@api
def scan_receive_location():
    """Check a scanned container for setup. Reads only."""
    container = container_for_code(_optional_text(_json_body(), "code"))
    return jsonify(ok=True, code=container.label_code, name=container.name,
                   kind=container.kind.name)


@inventory_bp.post("/scan/api/receive")
@api
def scan_receive():
    """Receive one owned unit and queue its label."""
    data = _json_body()

    def receive() -> dict:
        actor = scan_actor()
        item_id = data.get("item_id")
        item = db.session.get(SupplyItem, item_id) if type(item_id) is int else None
        if item is None:
            raise InventoryError("Pick an item type.")
        code = _optional_text(data, "container_code")
        container = container_for_code(code) if code else None
        return receive_unit(item, actor, tag=_optional_text(data, "tag"),
                            serial=_optional_text(data, "serial"), container=container,
                            client_request_id=data.get("client_request_id"))

    return jsonify(run_once(data.get("client_request_id"), receive))


@inventory_bp.post("/scan/api/receive/undo")
@api
def scan_receive_undo():
    """Undo the unit this scanner received last."""
    data = _json_body()

    def undo() -> dict:
        actor = scan_actor()
        unit_id = data.get("unit_id")
        undo_receive(unit_id if type(unit_id) is int else -1, actor)
        return {"unit_id": unit_id, "message": undo_message(unit_id)}

    return jsonify(run_once(data.get("client_request_id"), undo))


@inventory_bp.post("/scan/api/receive/reprint")
@api
def scan_receive_reprint():
    """Queue another label for a unit already received."""
    data = _json_body()

    def reprint() -> dict:
        actor = scan_actor()
        return reprint_unit(data.get("unit_id"), actor,
                            client_request_id=data.get("client_request_id"))

    return jsonify(run_once(data.get("client_request_id"), reprint))
