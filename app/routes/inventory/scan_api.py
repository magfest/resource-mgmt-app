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
from app.inventory.receipts import refusal, run_once
from app.inventory.resolver import (
    KIND_AMBIGUOUS, KIND_CONTAINER, KIND_ITEM, KIND_UNIT, KIND_UNUSED, resolve,
)
from app.inventory.shifts import current_shift, end_shift, start_shift, touch
from app.models import InvContainerKind
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
