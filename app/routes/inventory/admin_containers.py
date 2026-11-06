"""Container tree: create in batches, rename, re-kind, and move by label code."""
from collections import defaultdict

from flask import abort, flash, redirect, request, url_for
from sqlalchemy import func

from app import db
from app.inventory.codes import normalize_code
from app.inventory.labels import PREFIX_CONTAINER, allocate
from app.inventory.ledger import InventoryError, create_container, move_container
from app.models import InvContainer, InvContainerKind, InvStock, InvUnit

from . import inventory_bp
from .helpers import admin_actor, render_inventory_page, require_inventory_admin

MAX_CONTAINER_BATCH = 50


def _parent_from_form() -> tuple[InvContainer | None, str | None]:
    """Resolve the parent_code form field; blank means top level."""
    code = normalize_code(request.form.get("parent_code"))
    if not code:
        return None, None
    parent = db.session.query(InvContainer).filter_by(label_code=code).first()
    if parent is None:
        return None, f"No container has the label {code}."
    return parent, None


@inventory_bp.get("/admin/containers")
@require_inventory_admin
def containers():
    rows = db.session.query(InvContainer).order_by(InvContainer.label_code).all()
    children = defaultdict(list)
    for c in rows:
        children[c.parent_id].append(c)
    stock_lines = dict(db.session.query(InvStock.container_id, func.count(InvStock.id))
                       .filter(InvStock.quantity > 0).group_by(InvStock.container_id).all())
    unit_counts = dict(db.session.query(InvUnit.current_container_id, func.count(InvUnit.id))
                       .group_by(InvUnit.current_container_id).all())
    kinds = (db.session.query(InvContainerKind).filter_by(is_active=True)
             .order_by(InvContainerKind.sort_order.asc().nulls_last()).all())
    return render_inventory_page(
        "inventory/containers.html", roots=children[None], children=children,
        stock_lines=stock_lines, unit_counts=unit_counts, kinds=kinds,
        edit_id=request.args.get("edit", type=int))


@inventory_bp.post("/admin/containers")
@require_inventory_admin
def create_containers():
    kind = db.session.get(InvContainerKind, request.form.get("kind_id", type=int) or 0)
    count = request.form.get("count", type=int) or 1
    parent, error = _parent_from_form()
    if kind is None or not kind.is_active:
        error = "Choose an active kind."
    elif not 1 <= count <= MAX_CONTAINER_BATCH:
        error = f"Create 1 to {MAX_CONTAINER_BATCH} containers at a time."
    if error:
        flash(error, "error")
        return redirect(url_for("inventory.containers"))
    # A name only makes sense for one container; a batch would give every label the same name.
    name = ((request.form.get("name") or "").strip()[:128] or None) if count == 1 else None
    actor = admin_actor()
    codes = allocate(PREFIX_CONTAINER, count)
    try:
        for code in codes:
            create_container(code, kind, actor, parent=parent, name=name)
    except InventoryError as exc:
        # The whole batch rolls back, including the allocated codes, so no label
        # is skipped and no half-made batch is left behind.
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("inventory.containers"))
    db.session.commit()
    flash(f"Created {', '.join(codes)}. Print their labels on the Labels page.", "success")
    return redirect(url_for("inventory.containers"))


@inventory_bp.post("/admin/containers/<int:container_id>")
@require_inventory_admin
def update_container(container_id: int):
    container = db.session.get(InvContainer, container_id)
    if container is None:
        abort(404)
    kind = db.session.get(InvContainerKind, request.form.get("kind_id", type=int) or 0)
    parent, error = _parent_from_form()
    if error:
        flash(error, "error")
        return redirect(url_for("inventory.containers"))
    try:
        if (parent.id if parent else None) != container.parent_id:
            move_container(container, parent, admin_actor())
    except InventoryError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("inventory.containers"))
    if kind is not None:
        container.kind_id = kind.id
    container.name = (request.form.get("name") or "").strip()[:128] or None
    db.session.commit()
    flash(f"Saved {container.label_code}.", "success")
    return redirect(url_for("inventory.containers"))
