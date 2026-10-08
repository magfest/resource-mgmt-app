"""Grant and revoke INVENTORY_OPERATOR without Users admin.

Users admin requires SUPER_ADMIN. Warehouse leads sign up their own crews
here, and this page grants nothing but INVENTORY_OPERATOR.
"""
from flask import flash, redirect, request, url_for
from sqlalchemy import func

from app import db
from app.models import ROLE_INVENTORY_OPERATOR, User, UserRole
from app.security_audit import CATEGORY_ADMIN, EVENT_USER_MODIFY, log_security_event

from . import inventory_bp
from .helpers import render_inventory_page, require_inventory_admin


def _log(user: User, change: str) -> None:
    log_security_event(EVENT_USER_MODIFY, category=CATEGORY_ADMIN, details={
        "source": "inventory_operators",
        "target_user_id": user.id,
        "target_email": user.email,
        change: [ROLE_INVENTORY_OPERATOR],
    })


@inventory_bp.get("/admin/operators")
@require_inventory_admin
def operators():
    rows = (db.session.query(User)
            .join(UserRole, UserRole.user_id == User.id)
            .filter(UserRole.role_code == ROLE_INVENTORY_OPERATOR)
            .order_by(User.display_name).all())
    return render_inventory_page("inventory/operators.html", operators=rows)


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
            _log(user, "revoked")
            db.session.commit()
    return redirect(url_for("inventory.operators"))
