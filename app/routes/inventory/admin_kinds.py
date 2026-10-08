"""Container kinds: an admin-managed list, deactivated rather than deleted."""
import re

from flask import abort, flash, redirect, request, url_for

from app import db
from app.models import InvContainerKind

from . import inventory_bp
from .helpers import render_inventory_page, require_inventory_admin


def _code_from_name(name: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "_", name.upper()).strip("_")[:32]


@inventory_bp.get("/admin/kinds")
@require_inventory_admin
def kinds():
    rows = (db.session.query(InvContainerKind)
            .order_by(InvContainerKind.sort_order.asc().nulls_last(), InvContainerKind.name)
            .all())
    return render_inventory_page("inventory/kinds.html", kinds=rows)


@inventory_bp.post("/admin/kinds")
@require_inventory_admin
def create_kind():
    name = (request.form.get("name") or "").strip()
    code = _code_from_name(name)
    if not name or len(name) > 64 or not code:
        flash("A kind needs a name of 1 to 64 characters.", "error")
    elif db.session.query(InvContainerKind).filter_by(code=code).first():
        flash(f"A kind with code {code} already exists.", "error")
    else:
        db.session.add(InvContainerKind(code=code, name=name, is_active=True,
                                        sort_order=request.form.get("sort_order", type=int)))
        db.session.commit()
        flash(f"Added {name}.", "success")
    return redirect(url_for("inventory.kinds"))


@inventory_bp.post("/admin/kinds/<int:kind_id>")
@require_inventory_admin
def update_kind(kind_id: int):
    kind = db.session.get(InvContainerKind, kind_id)
    if kind is None:
        abort(404)
    name = (request.form.get("name") or "").strip()
    if not name or len(name) > 64:
        flash("A kind needs a name of 1 to 64 characters.", "error")
        return redirect(url_for("inventory.kinds"))
    # The code stays fixed so reports grouped by kind keep their history.
    kind.name = name
    kind.sort_order = request.form.get("sort_order", type=int)
    kind.is_active = request.form.get("is_active") == "1"
    db.session.commit()
    flash(f"Saved {kind.name}.", "success")
    return redirect(url_for("inventory.kinds"))
