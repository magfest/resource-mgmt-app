"""Check lines for each serialized item; a line says what a missing answer means."""
from flask import abort, flash, redirect, request, url_for

from app import db
from app.models import InvCheckItem, SupplyItem

from . import inventory_bp
from .helpers import render_inventory_page, require_inventory_admin

MISSING_MEANS = ("FAIL", "NOTE")
PROMPT_MAX = InvCheckItem.prompt.type.length


def _lines(item_id: int) -> list[InvCheckItem]:
    return (db.session.query(InvCheckItem).filter_by(item_id=item_id)
            .order_by(InvCheckItem.position, InvCheckItem.id).all())


def _back(item_id: int):
    return redirect(url_for("inventory.checklists", item_id=item_id))


def _read_line_form() -> tuple[str, str, str | None]:
    """Return (prompt, missing_means, error). Error is None when the form is valid."""
    prompt = (request.form.get("prompt") or "").strip()
    missing_means = (request.form.get("missing_means") or "").strip()
    if not prompt or len(prompt) > PROMPT_MAX:
        return prompt, missing_means, f"A line needs a prompt of 1 to {PROMPT_MAX} characters."
    if missing_means not in MISSING_MEANS:
        return prompt, missing_means, "A missing answer must mean FAIL or NOTE."
    return prompt, missing_means, None


@inventory_bp.get("/admin/checklists")
@require_inventory_admin
def checklists():
    # Checks run on assets, and only serialized items have assets.
    items = (db.session.query(SupplyItem).filter_by(is_serialized=True)
             .order_by(SupplyItem.item_name).all())
    item = None
    raw = request.args.get("item_id")
    if raw:
        try:
            item = db.session.get(SupplyItem, int(raw))
        except ValueError:
            item = None
        if item is None or not item.is_serialized:
            abort(404)
    lines = _lines(item.id) if item else []
    return render_inventory_page(
        "inventory/checklists.html", items=items, item=item, lines=lines,
        prompt_max=PROMPT_MAX)


@inventory_bp.post("/admin/checklists/<int:item_id>/lines")
@require_inventory_admin
def add_check_line(item_id: int):
    item = db.session.get(SupplyItem, item_id)
    if item is None or not item.is_serialized:
        abort(404)
    prompt, missing_means, error = _read_line_form()
    if error:
        flash(error, "error")
        return _back(item_id)
    last = max((line.position for line in _lines(item_id)), default=-1)
    db.session.add(InvCheckItem(item_id=item_id, position=last + 1, prompt=prompt,
                                missing_means=missing_means, is_active=True))
    db.session.commit()
    flash("Added the line.", "success")
    return _back(item_id)


@inventory_bp.post("/admin/checklists/lines/<int:line_id>")
@require_inventory_admin
def update_check_line(line_id: int):
    line = db.session.get(InvCheckItem, line_id)
    if line is None:
        abort(404)
    prompt, missing_means, error = _read_line_form()
    if error:
        flash(error, "error")
        return _back(line.item_id)
    line.prompt = prompt
    line.missing_means = missing_means
    line.is_active = request.form.get("is_active") == "1"
    db.session.commit()
    flash("Saved the line.", "success")
    return _back(line.item_id)


@inventory_bp.post("/admin/checklists/lines/<int:line_id>/move")
@require_inventory_admin
def move_check_line(line_id: int):
    line = db.session.get(InvCheckItem, line_id)
    if line is None:
        abort(404)
    direction = request.form.get("direction")
    if direction not in ("up", "down"):
        flash("Move a line up or down.", "error")
        return _back(line.item_id)
    lines = _lines(line.item_id)
    index = lines.index(line)
    target = index - 1 if direction == "up" else index + 1
    if 0 <= target < len(lines):
        lines[index], lines[target] = lines[target], lines[index]
    # Renumber every line so positions stay unique and contiguous from 0.
    for position, row in enumerate(lines):
        row.position = position
    db.session.commit()
    return _back(line.item_id)
