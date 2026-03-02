"""
Development routes for testing and debugging.
"""
from flask import Blueprint, render_template, redirect, url_for, request, session, flash, current_app

from app import db
from app.routes import h, get_user_ctx

dev_bp = Blueprint('dev', __name__)


def _require_dev_login_enabled():
    """Check if dev login is enabled, abort with 404 if not."""
    if not current_app.config.get("DEV_LOGIN_ENABLED"):
        from flask import abort
        abort(404)


@dev_bp.get("/dev/login")
def dev_login():
    _require_dev_login_enabled()

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
    _require_dev_login_enabled()

    from app.models import User

    h.ensure_demo_users()

    chosen = (request.form.get("user_id") or "").strip()
    if not chosen:
        return redirect(url_for("dev.dev_login"))

    u = db.session.get(User, chosen)
    if not u or not u.is_active:
        return "Unknown or inactive user", 400

    # Session fixation prevention: clear session before setting new auth
    session.clear()
    session["active_user_id"] = u.id
    return redirect(url_for("dev.dev_login"))


@dev_bp.post("/dev/role-override")
def set_role_override():
    """Set a role override for testing (super-admins only, beta mode only)."""
    from flask import current_app
    from app.models import ApprovalGroup

    # Security check: only super-admins in beta mode can use this
    if not current_app.config.get("BETA_TESTING_MODE"):
        flash("Role override not available", "error")
        return redirect(request.referrer or url_for("home.index"))

    # Check actual database role, ignoring any current override
    if not h.has_super_admin_role():
        flash("Only super-admins can override roles", "error")
        return redirect(request.referrer or url_for("home.index"))

    override = request.form.get("role_override", "").strip()

    if not override or override == "normal":
        # Clear override - return to normal permissions
        session.pop("role_override", None)
        session.pop("role_override_approval_group_id", None)
        flash("Role override cleared - using normal permissions", "info")
    elif override == "none":
        session["role_override"] = "none"
        session.pop("role_override_approval_group_id", None)
        flash("Role override: No special permissions (regular user)", "info")
    elif override.startswith("approver:"):
        # Format: approver:GROUP_ID
        try:
            group_id = int(override.split(":")[1])
            group = db.session.get(ApprovalGroup, group_id)
            if not group:
                flash("Invalid approval group", "error")
                return redirect(request.referrer or url_for("home.index"))
            session["role_override"] = "approver"
            session["role_override_approval_group_id"] = group_id
            flash(f"Role override: Approver for {group.name} only", "info")
        except (ValueError, IndexError):
            flash("Invalid approval group format", "error")
            return redirect(request.referrer or url_for("home.index"))
    else:
        flash("Unknown role override option", "error")

    return redirect(request.referrer or url_for("home.index"))


@dev_bp.get("/dev/impersonate")
def impersonate_user_page():
    """Show user selection page for impersonation."""
    from app.models import User

    # Only real super-admins in beta mode can impersonate
    if not current_app.config.get("BETA_TESTING_MODE"):
        flash("Impersonation not available", "error")
        return redirect(url_for("home.index"))

    if not h.has_super_admin_role():
        flash("Only super-admins can impersonate users", "error")
        return redirect(url_for("home.index"))

    users = (
        db.session.query(User)
        .filter(User.is_active == True)  # noqa: E712
        .order_by(User.display_name.asc())
        .all()
    )

    return render_template(
        "dev/impersonate.html",
        users=users,
        current_user_id=h.get_active_user_id(),
    )


@dev_bp.post("/dev/impersonate")
def impersonate_user():
    """Start impersonating another user."""
    from app.models import User
    from app.security_audit import log_impersonation_start

    # Only real super-admins in beta mode can impersonate
    if not current_app.config.get("BETA_TESTING_MODE"):
        flash("Impersonation not available", "error")
        return redirect(url_for("home.index"))

    if not h.has_super_admin_role():
        flash("Only super-admins can impersonate users", "error")
        return redirect(url_for("home.index"))

    target_user_id = request.form.get("user_id", "").strip()
    if not target_user_id:
        flash("No user selected", "error")
        return redirect(url_for("dev.impersonate_user_page"))

    target_user = db.session.get(User, target_user_id)
    if not target_user:
        flash("User not found", "error")
        return redirect(url_for("dev.impersonate_user_page"))

    # Store the real user's ID so we can return to it
    current_user_id = h.get_active_user_id()
    if not session.get("real_user_id"):
        # Only set real_user_id if not already impersonating
        session["real_user_id"] = current_user_id

    # Log impersonation start (before switching users)
    log_impersonation_start(current_user_id, target_user.id)
    db.session.commit()

    # Switch to the target user
    session["active_user_id"] = target_user.id

    # Clear any role override - impersonation replaces it
    session.pop("role_override", None)
    session.pop("role_override_approval_group_id", None)

    flash(f"Now viewing as: {target_user.display_name}", "info")
    return redirect(url_for("home.index"))


@dev_bp.post("/dev/exit-impersonation")
def exit_impersonation():
    """Stop impersonating and return to real account."""
    from app.security_audit import log_impersonation_end

    real_user_id = session.get("real_user_id")

    if not real_user_id:
        flash("Not currently impersonating anyone", "error")
        return redirect(url_for("home.index"))

    # Get impersonated user ID before restoring
    impersonated_user_id = session.get("active_user_id")

    # Log impersonation end
    log_impersonation_end(real_user_id, impersonated_user_id)
    db.session.commit()

    # Restore the real user
    session["active_user_id"] = real_user_id
    session.pop("real_user_id", None)

    # Clear any role override
    session.pop("role_override", None)
    session.pop("role_override_approval_group_id", None)

    flash("Returned to your account", "info")
    return redirect(url_for("home.index"))

