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
from app.inventory.printing import (
    REPRINT_MODES, job_answer, last_job_for, print_container, print_item, reprint,
    reprint_options, unit_number,
)
from app.inventory.receipts import refusal, run_once
from app.inventory.resolver import (
    KIND_AMBIGUOUS, KIND_CONTAINER, KIND_ITEM, KIND_UNIT, KIND_UNKNOWN, KIND_UNUSED,
    Resolution, resolve,
)
from app.inventory.zpl import can_print_code
from app.inventory.shifts import current_shift, end_shift, start_shift, touch
from app.models import InvContainerKind, InvUnit, SupplyItem
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


def _can_create_tag(code: str) -> bool:
    """Only a printable code that is not a FestOps label can become an owned unit's tag."""
    return bool(code) and labels.parse(code) is None and can_print_code(code)


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
        out["candidates"] = [{"card": candidate_card(c, event),
                              "unit_id": c.id if isinstance(c, InvUnit) else None}
                             for c in shown]
        if len(resolution.candidates) > MAX_CANDIDATES:
            out["more"] = len(resolution.candidates) - MAX_CANDIDATES
    elif resolution.kind == KIND_UNKNOWN:
        out["can_create_tag"] = _can_create_tag(resolution.code)
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


NOTHING_ATTACHED = "This label has nothing attached to it yet."
NO_MATCH = "Nothing matches that code."
PICK_ONE = "Choose which one to print."


def _label_target(data: dict) -> Resolution:
    """Resolve the code a label screen names. Two units sharing a code need `unit_id`."""
    raw = _optional_text(data, "code")
    unit_id = data.get("unit_id")
    if unit_id is not None and type(unit_id) is not int:
        raise InventoryError(PICK_ONE)
    resolution = resolve(raw)
    kind = resolution.kind
    if kind == KIND_UNIT:
        if unit_id is not None and unit_id != resolution.unit.id:
            raise InventoryError(PICK_ONE)
        return resolution
    if kind == KIND_AMBIGUOUS:
        match = next((c for c in resolution.candidates
                      if isinstance(c, InvUnit) and c.id == unit_id), None)
        if match is None:
            raise InventoryError(PICK_ONE)
        return Resolution(KIND_UNIT, resolution.code, unit=match)
    if kind == KIND_UNUSED:
        raise InventoryError(NOTHING_ATTACHED)
    if kind == KIND_UNKNOWN and not _can_create_tag(resolution.code):
        raise InventoryError(NO_MATCH)
    return resolution


@inventory_bp.post("/scan/api/label/options")
@api
def scan_label_options():
    """Say what printing this code offers. Reads only."""
    resolution = _label_target(_json_body())
    if resolution.kind == KIND_UNKNOWN:
        return jsonify(ok=True, kind=KIND_UNKNOWN, code=resolution.code, can_create_tag=True)
    if resolution.kind == KIND_UNIT:
        unit = resolution.unit
        out = reprint_options(unit)
        return jsonify(ok=True, kind=KIND_UNIT, unit_id=unit.id, title=unit.item.item_name,
                       code=unit_number(unit), **out)
    if resolution.kind == KIND_CONTAINER:
        container = resolution.container
        return jsonify(ok=True, kind=KIND_CONTAINER, code=container.label_code,
                       title=container.name or container.kind.name,
                       inactive=not container.is_active,
                       has_history=last_job_for(container=container) is not None)
    item = resolution.item
    return jsonify(ok=True, kind=KIND_ITEM, code=item.barcode or resolution.code,
                   title=item.item_name, inactive=not item.is_active)


@inventory_bp.post("/scan/api/label/print")
@api
def scan_label_print():
    """Queue a label for a unit, container or item."""
    data = _json_body()

    def queue() -> dict:
        actor = scan_actor()
        resolution = _label_target(data)
        crid = data.get("client_request_id")
        if resolution.kind == KIND_UNIT:
            mode = data.get("mode")
            if mode not in REPRINT_MODES:
                raise InventoryError("Pick how to print the label.")
            job = reprint(resolution.unit, mode, actor, client_request_id=crid)
        elif resolution.kind == KIND_CONTAINER:
            job = print_container(resolution.container, actor, client_request_id=crid)
        elif resolution.kind == KIND_ITEM:
            job = print_item(resolution.item, actor, client_request_id=crid)
        else:
            raise InventoryError(NO_MATCH)
        return job_answer(job)

    return jsonify(run_once(data.get("client_request_id"), queue))


@inventory_bp.post("/scan/api/label/new-tag")
@api
def scan_label_new_tag():
    """Create an owned unit for an asset tag no unit holds, and queue its label."""
    data = _json_body()

    def create() -> dict:
        actor = scan_actor()
        tag = _optional_text(data, "tag")
        resolution = resolve(tag)
        if resolution.kind != KIND_UNKNOWN or not _can_create_tag(resolution.code):
            raise InventoryError("That code already belongs to something. Look it up instead.")
        item_id = data.get("item_id")
        item = db.session.get(SupplyItem, item_id) if type(item_id) is int else None
        if item is None:
            raise InventoryError("Pick an item type.")
        return receive_unit(item, actor, tag=tag, serial=_optional_text(data, "serial"),
                            client_request_id=data.get("client_request_id"))

    return jsonify(run_once(data.get("client_request_id"), create))
