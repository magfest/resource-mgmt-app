"""
Development routes for testing and debugging.
"""
from datetime import datetime, timedelta

from flask import Blueprint, render_template, redirect, url_for, request, session, flash

from app import db
from app.routes import h, get_user_ctx

dev_bp = Blueprint('dev', __name__)

@dev_bp.get("/dev/login")
def dev_login():
    from app.models import User

    h.ensure_demo_users()

    users = (
        db.session.query(User)
        .filter(User.is_active == True)  # noqa: E712
        .order_by(User.display_name.asc())
        .all()
    )

    return render_template(
        "dev/dev_login.html",
        users=users,
        current_user_id=h.get_active_user_id(),
    )


@dev_bp.post("/dev/login")
def dev_login_post():
    from app.models import User

    h.ensure_demo_users()

    chosen = (request.form.get("user_id") or "").strip()
    if not chosen:
        return redirect(url_for("dev.dev_login"))

    u = db.session.get(User, chosen)
    if not u or not u.is_active:
        return "Unknown or inactive user", 400

    session["active_user_id"] = u.id
    return redirect(url_for("dev.dev_login"))

