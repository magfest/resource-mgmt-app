"""Movement log, newest first, filterable by location or container, item, device, and date."""
from datetime import datetime

from flask import flash, request
from sqlalchemy import or_
from sqlalchemy.orm import selectinload

from app import db
from app.inventory.codes import normalize_code
from app.inventory.describe import describe_actor, describe_movement
from app.models import InvContainer, InvDevice, InvMovement, InvUnit, SupplyItem

from . import inventory_bp
from .helpers import render_inventory_page, require_inventory_admin

PAGE_SIZE = 200


@inventory_bp.get("/admin/movements")
@require_inventory_admin
def movements():
    query = db.session.query(InvMovement)
    container_code = normalize_code(request.args.get("container"))
    if container_code:
        container = db.session.query(InvContainer).filter_by(label_code=container_code).first()
        if container is None:
            flash(f"No location or container has the label {container_code}.", "error")
        cid = container.id if container else -1
        query = query.filter(or_(InvMovement.subject_container_id == cid,
                                 InvMovement.from_container_id == cid,
                                 InvMovement.to_container_id == cid))
    item_id = request.args.get("item", type=int)
    if item_id:
        query = query.filter(InvMovement.item_id == item_id)
    device_id = request.args.get("device", type=int)
    if device_id:
        query = query.filter(InvMovement.device_id == device_id)
    since = request.args.get("since")
    if since:
        try:
            query = query.filter(InvMovement.created_at >= datetime.strptime(since, "%Y-%m-%d"))
        except ValueError:
            flash("Since must be a date (YYYY-MM-DD).", "error")
    rows = (query.options(
                selectinload(InvMovement.item),
                selectinload(InvMovement.unit).selectinload(InvUnit.identifiers),
                selectinload(InvMovement.subject_container),
                selectinload(InvMovement.from_container),
                selectinload(InvMovement.to_container),
                selectinload(InvMovement.shift),
                selectinload(InvMovement.device))
            .order_by(InvMovement.id.desc()).limit(PAGE_SIZE).all())
    return render_inventory_page(
        "inventory/movements.html",
        rows=[(m, describe_movement(m), describe_actor(m)) for m in rows],
        items=db.session.query(SupplyItem).filter_by(is_active=True).order_by(SupplyItem.item_name).all(),
        devices=db.session.query(InvDevice).order_by(InvDevice.name).all(),
        args=request.args, page_size=PAGE_SIZE)
