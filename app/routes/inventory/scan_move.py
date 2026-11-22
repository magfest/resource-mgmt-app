"""JSON endpoints for the Move station, which places things by scan.

The first scan sets where things are going; later scans place containers and
assets there. Loose supplies move from a source to the destination by quantity.
Answers carry `ok` and `message`; a refusal is HTTP 200 with `ok: false`.
"""
from flask import g, jsonify

from app import db
from app.inventory.ledger import InventoryError
from app.inventory.moves import (
    ASSETS_ONE_BY_ONE, destination_from, move_create, move_scan, move_supplies, supplies_source,
)
from app.models import InvContainer, InvContainerKind, SupplyItem
from app.models.constants import INV_STATION_MOVE

from . import inventory_bp
from .scan_api import _countable_items, _json_body, _optional_text, api, run_once
from .scan_auth import scan_actor

NOT_A_MOVE_STATION = "This scanner is not set up for moving."
TAP_THE_ITEM = "Tap the item, or scan its barcode."


def _move_device() -> InvContainer | None:
    """Refuse a device on another station; return its home container, None for an operator."""
    principal = g.scan_principal
    if not principal.is_device:
        return None
    if principal.device.station_code != INV_STATION_MOVE:
        raise InventoryError(NOT_A_MOVE_STATION)
    return principal.device.home_container


def _destination(data: dict) -> InvContainer | None:
    """The destination the page sent, or None when it has not set one."""
    code = data.get("destination_code")
    return None if code is None else destination_from(code)


@inventory_bp.post("/scan/api/move/scan")
@api
def scan_move_scan():
    """Set the destination, place a scanned thing on it, or offer kinds for a new label."""
    home = _move_device()
    data = _json_body()

    def scan() -> dict:
        actor = scan_actor()
        return move_scan(_optional_text(data, "code"), _destination(data), actor, home)

    return jsonify(run_once(data.get("client_request_id"), scan))


@inventory_bp.post("/scan/api/move/create")
@api
def scan_move_create():
    """Create a container from a new C- label, in home or on the destination."""
    home = _move_device()
    data = _json_body()

    def create() -> dict:
        actor = scan_actor()
        kind_id = data.get("kind_id")
        kind = db.session.get(InvContainerKind, kind_id) if type(kind_id) is int else None
        return move_create(_optional_text(data, "label_code"), kind, _destination(data),
                           actor, home)

    return jsonify(run_once(data.get("client_request_id"), create))


@inventory_bp.post("/scan/api/move/source")
@api
def scan_move_source():
    """Accept where loose supplies come from. Reads only."""
    _move_device()
    data = _json_body()
    destination = destination_from(data.get("destination_code"))
    return jsonify(ok=True, **supplies_source(_optional_text(data, "code"), destination))


@inventory_bp.post("/scan/api/move/find")
@api
def scan_move_find():
    """Name the loose-stock item a scan or a search text points at. Reads only."""
    _move_device()
    return jsonify(ok=True, items=_countable_items(_json_body(), TAP_THE_ITEM, ASSETS_ONE_BY_ONE))


@inventory_bp.post("/scan/api/move/supplies")
@api
def scan_move_supplies():
    """Move a quantity of one item from the source to the destination."""
    _move_device()
    data = _json_body()

    def supplies() -> dict:
        actor = scan_actor()
        item_id = data.get("item_id")
        item = db.session.get(SupplyItem, item_id) if type(item_id) is int else None
        source = destination_from(data.get("source_code"))
        destination = destination_from(data.get("destination_code"))
        return move_supplies(item, source, destination, data.get("quantity"), actor)

    return jsonify(run_once(data.get("client_request_id"), supplies))
