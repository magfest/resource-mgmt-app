"""JSON endpoints for scanner pages.

Every route here refuses a PRINT device and answers its own 403, so a shared
scanner never lands on a login page.
"""
from functools import wraps

from flask import g, jsonify, request

from app import csrf, db
from app.inventory.ledger import InventoryError
from app.inventory.shifts import current_shift, end_shift, start_shift, touch
from app.models.constants import INV_SHIFT_END_ENDED

from . import inventory_bp
from .scan_auth import device_api, require_scanner


def api(f):
    """JSON in and out for a scanner principal; an InventoryError becomes ok=false.

    The caller commits on success. Use `touch_shift` after a write to keep the
    volunteer's shift alive.
    """
    @wraps(f)
    def inner(*args, **kwargs):
        try:
            result = f(*args, **kwargs)
        except InventoryError as exc:
            db.session.rollback()
            return jsonify(ok=False, message=str(exc))
        db.session.commit()
        return result
    guarded = require_scanner(device_api(inner))
    return csrf.exempt(guarded)


def touch_shift() -> None:
    """Mark the device's open shift active; no-op for a signed-in user."""
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
