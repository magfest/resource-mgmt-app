"""Flags page: scanner flags for a lead to read and resolve at a laptop."""
from flask import abort, flash, redirect, request, url_for
from sqlalchemy.orm import selectinload

from app import db
from app.inventory.checks import unit_phrase
from app.inventory.flags import FLAG_WORDS, resolve_flag
from app.inventory.ledger import InventoryError
from app.inventory.printing import local_stamp
from app.models import InvContainer, InvFlag, InvUnit, User
from app.models.constants import INV_STATION_NAMES

from . import inventory_bp
from .helpers import render_inventory_page, require_inventory_operator
from app.routes import get_user_ctx

RESOLVED_LIMIT = 50


def open_flag_count() -> int:
    return db.session.query(InvFlag.id).filter(InvFlag.resolved_at.is_(None)).count()


def _names(flags) -> dict:
    ids = {f.performed_by_user_id for f in flags} | {f.resolved_by_user_id for f in flags}
    ids.discard(None)
    users = db.session.query(User).filter(User.id.in_(ids)).all() if ids else []
    return {u.id: u.display_name or u.id for u in users}


def _row(flag: InvFlag, names: dict, units: dict) -> dict:
    unit = units.get(flag.unit_id)
    return {
        "flag": flag,
        "when": local_stamp(flag.created_at),
        "station": INV_STATION_NAMES.get(flag.station_code, flag.station_code),
        "reason": FLAG_WORDS.get(flag.reason, flag.reason),
        "unit": unit,
        "unit_text": f"{unit_phrase(unit)} ({unit.item.item_name})" if unit else None,
        "container": db.session.get(InvContainer, flag.container_id) if flag.container_id else None,
        "raised_by": names.get(flag.performed_by_user_id) or "Scanner volunteer",
        "resolved_when": local_stamp(flag.resolved_at) if flag.resolved_at else None,
        "resolved_by": names.get(flag.resolved_by_user_id, ""),
    }


def flag_rows(flags) -> list[dict]:
    flags = list(flags)
    names = _names(flags)
    unit_ids = {f.unit_id for f in flags if f.unit_id}
    units = {}
    if unit_ids:
        units = {u.id: u for u in db.session.query(InvUnit).options(
            selectinload(InvUnit.identifiers), selectinload(InvUnit.item))
            .filter(InvUnit.id.in_(unit_ids))}
    return [_row(f, names, units) for f in flags]


@inventory_bp.get("/admin/flags")
@require_inventory_operator
def flags():
    open_flags = (db.session.query(InvFlag).filter(InvFlag.resolved_at.is_(None))
                  .order_by(InvFlag.created_at.desc(), InvFlag.id.desc()).all())
    resolved = (db.session.query(InvFlag).filter(InvFlag.resolved_at.isnot(None))
                .order_by(InvFlag.resolved_at.desc(), InvFlag.id.desc())
                .limit(RESOLVED_LIMIT).all())
    return render_inventory_page("inventory/flags.html", open_rows=flag_rows(open_flags),
                                 resolved_rows=flag_rows(resolved), resolved_limit=RESOLVED_LIMIT)


@inventory_bp.post("/admin/flags/<int:flag_id>/resolve")
@require_inventory_operator
def resolve_flag_route(flag_id: int):
    flag = db.session.get(InvFlag, flag_id)
    if flag is None:
        abort(404)
    try:
        resolve_flag(flag, get_user_ctx().user_id, request.form.get("note"))
    except InventoryError as error:
        db.session.rollback()
        flash(str(error), "error")
    else:
        db.session.commit()
        flash("Flag resolved.", "success")
    return redirect(request.form.get("next") if request.form.get("next", "").startswith("/inv/")
                    else url_for("inventory.flags"))
