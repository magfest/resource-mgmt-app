"""Grant and revoke INVENTORY_OPERATOR, and show card and PIN status.

Users admin requires SUPER_ADMIN. Warehouse leads sign up their own crews
here, and this page grants nothing but INVENTORY_OPERATOR. It lists everyone
holding an inventory role with whether they have a PIN and a printed card.
Leads print new cards; only super admins reset a PIN.
"""
from flask import abort, flash, redirect, request, url_for
from sqlalchemy import func

from app import db
from app.inventory.ledger import Actor, InventoryError
from app.inventory.printing import local_day
from app.inventory.shifts import end_user_shifts
from app.inventory.sign_in import clear_pin, credentials_for, issue_card
from app.models import ROLE_INVENTORY_ADMIN, ROLE_INVENTORY_OPERATOR, User, UserRole
from app.routes import get_user_ctx
from app.security_audit import CATEGORY_ADMIN, EVENT_USER_MODIFY, log_security_event

from . import inventory_bp
from .helpers import render_inventory_page, require_inventory_admin
from .my_sign_in import log_sign_in_event


def _log(user: User, change: str) -> None:
    log_security_event(EVENT_USER_MODIFY, category=CATEGORY_ADMIN, details={
        "source": "inventory_operators",
        "target_user_id": user.id,
        "target_email": user.email,
        change: [ROLE_INVENTORY_OPERATOR],
    })


def _listed_users():
    """Query active users holding an inventory role: the people this page lists."""
    return db.session.query(User).filter(
        User.is_active.is_(True),
        User.id.in_(db.session.query(UserRole.user_id).filter(
            UserRole.role_code.in_([ROLE_INVENTORY_OPERATOR, ROLE_INVENTORY_ADMIN]))))


def _listed_user_or_404(user_id: str) -> User:
    """Return a listed user, or abort. Cards and PIN resets serve listed users only."""
    user = _listed_users().filter(User.id == user_id).first()
    if user is None:
        abort(404)
    return user


@inventory_bp.get("/admin/operators")
@require_inventory_admin
def operators():
    users = _listed_users().order_by(User.display_name).all()
    admin_ids = {r.user_id for r in db.session.query(UserRole).filter_by(
        role_code=ROLE_INVENTORY_ADMIN)}
    creds = credentials_for([u.id for u in users])
    rows = []
    for u in users:
        cred = creds.get(u.id)
        pin_at = cred.pin_set_at if cred and cred.pin_digest else None
        card_at = cred.card_printed_at if cred and cred.card_digest else None
        rows.append({
            "user": u,
            "role": "Admin" if u.id in admin_ids else "Operator",
            "pin": f"Yes, {local_day(pin_at)}" if pin_at else "No",
            "card": local_day(card_at) if card_at else "Never",
        })
    return render_inventory_page(
        "inventory/operators.html", rows=rows, can_reset=get_user_ctx().is_super_admin)


@inventory_bp.post("/admin/operators")
@require_inventory_admin
def grant_operator():
    email = (request.form.get("email") or "").strip().lower()
    user = db.session.query(User).filter(func.lower(User.email) == email,
                                         User.is_active.is_(True)).first()
    if user is None:
        flash(f"No active user has the email {email}. They must sign in once first.", "error")
        return redirect(url_for("inventory.operators"))
    held = db.session.query(UserRole).filter_by(
        user_id=user.id, role_code=ROLE_INVENTORY_OPERATOR).first()
    if held is None:
        db.session.add(UserRole(user_id=user.id, role_code=ROLE_INVENTORY_OPERATOR))
        _log(user, "granted")
        db.session.commit()
    flash(f"{user.display_name} can use the scanner.", "success")
    return redirect(url_for("inventory.operators"))


@inventory_bp.post("/admin/operators/<user_id>/revoke")
@require_inventory_admin
def revoke_operator(user_id: str):
    user = db.session.get(User, user_id)
    if user is not None:
        deleted = db.session.query(UserRole).filter_by(
            user_id=user.id, role_code=ROLE_INVENTORY_OPERATOR).delete()
        if deleted:
            end_user_shifts(user.id)
            _log(user, "revoked")
            db.session.commit()
            flash(f"Removed Operator from {user.display_name}.", "success")
    return redirect(url_for("inventory.operators"))


@inventory_bp.post("/admin/operators/<user_id>/card")
@require_inventory_admin
def operator_new_card(user_id: str):
    user = _listed_user_or_404(user_id)
    try:
        job = issue_card(user, Actor(user_id=get_user_ctx().user_id))
    except InventoryError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("inventory.operators"))
    log_sign_in_event(user, "card_printed")
    db.session.commit()
    flash(f"{user.display_name}'s card is printing on {job.printer.name}.", "success")
    return redirect(url_for("inventory.operators"))


@inventory_bp.post("/admin/operators/<user_id>/reset-pin")
@require_inventory_admin
def operator_reset_pin(user_id: str):
    if not get_user_ctx().is_super_admin:
        abort(403)
    user = _listed_user_or_404(user_id)
    clear_pin(user.id)
    end_user_shifts(user.id)
    log_sign_in_event(user, "pin_reset")
    db.session.commit()
    flash(f"{user.display_name}'s PIN is reset. "
          "They can get a new one on My scanner sign-in.", "success")
    return redirect(url_for("inventory.operators"))
