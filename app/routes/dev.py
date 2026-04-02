"""
Development routes for testing and debugging.
"""
import os

from flask import Blueprint, render_template, redirect, url_for, request, session, flash, current_app, abort
from sqlalchemy import inspect

from app import db
from app.routes import h, get_user_ctx

dev_bp = Blueprint('dev', __name__)


def _is_development_environment():
    """
    Check if running in development environment.

    Returns True only if APP_ENV is NOT "production".
    This is a strict check - defaults to False if uncertain.
    """
    env = os.environ.get("APP_ENV", "").lower()
    # Must explicitly NOT be production
    # If APP_ENV is not set or is anything other than "production", allow dev tools
    # But we also check the config to be extra safe
    is_prod_env = env == "production"
    is_prod_config = current_app.config.get("SESSION_COOKIE_SECURE", False)  # Only True in prod

    return not is_prod_env and not is_prod_config


def _require_dev_environment():
    """
    Require development environment for dangerous operations.

    Aborts with 404 if in production to hide existence of these endpoints.
    """
    if not _is_development_environment():
        abort(404)


def _require_dev_super_admin():
    """
    Require both development environment AND super admin role.

    This is for dangerous data manipulation tools.
    """
    _require_dev_environment()

    if not h.has_super_admin_role():
        flash("Dev tools require super admin access", "error")
        abort(403)


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
    from app.routes.admin.helpers import safe_redirect_url

    # Security check: only super-admins in beta mode can use this
    if not current_app.config.get("BETA_TESTING_MODE"):
        flash("Role override not available", "error")
        return redirect(safe_redirect_url(request.referrer))

    # Check actual database role, ignoring any current override
    if not h.has_super_admin_role():
        flash("Only super-admins can override roles", "error")
        return redirect(safe_redirect_url(request.referrer))

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
                return redirect(safe_redirect_url(request.referrer))
            session["role_override"] = "approver"
            session["role_override_approval_group_id"] = group_id
            flash(f"Role override: Approver for {group.name} only", "info")
        except (ValueError, IndexError):
            flash("Invalid approval group format", "error")
            return redirect(safe_redirect_url(request.referrer))
    else:
        flash("Unknown role override option", "error")

    return redirect(safe_redirect_url(request.referrer))


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


# ============================================================
# Development Data Tools
# These routes are ONLY available in development environment
# and require super admin access.
# ============================================================

@dev_bp.get("/dev/tools")
def dev_tools_dashboard():
    """
    Dashboard for development data manipulation tools.

    Only available in development environment with super admin access.
    """
    _require_dev_super_admin()

    from app.models import Department, EventCycle, WorkPortfolio

    # Get data for the forms
    departments = Department.query.filter_by(is_active=True).order_by(Department.name).all()
    event_cycles = EventCycle.query.filter_by(is_active=True).order_by(EventCycle.name).all()

    # Get portfolio stats for display
    portfolio_stats = []
    for dept in departments:
        for cycle in event_cycles:
            portfolio = WorkPortfolio.query.filter_by(
                department_id=dept.id,
                event_cycle_id=cycle.id
            ).first()
            if portfolio:
                from app.models import WorkItem
                item_count = WorkItem.query.filter_by(
                    portfolio_id=portfolio.id,
                    is_archived=False
                ).count()
                if item_count > 0:
                    portfolio_stats.append({
                        "department": dept,
                        "event_cycle": cycle,
                        "portfolio": portfolio,
                        "item_count": item_count,
                    })

    return render_template(
        "dev/dev_tools.html",
        departments=departments,
        event_cycles=event_cycles,
        portfolio_stats=portfolio_stats,
        is_dev_environment=_is_development_environment(),
    )


@dev_bp.post("/dev/tools/clear-department-budget")
def clear_department_budget():
    """
    Clear all budget requests for a department in an event cycle.

    This deletes:
    - All WorkLineReview records
    - All BudgetLineDetail records
    - All WorkLine records
    - All WorkItem records
    - The WorkPortfolio itself (optional)

    DANGEROUS: Only available in development environment.
    """
    _require_dev_super_admin()

    from app.models import (
        Department, EventCycle, WorkPortfolio, WorkItem, WorkLine,
        BudgetLineDetail, WorkLineReview, WorkItemComment, WorkItemAuditEvent,
        WorkLineAuditEvent,
    )

    department_id = request.form.get("department_id", type=int)
    event_cycle_id = request.form.get("event_cycle_id", type=int)
    delete_portfolio = request.form.get("delete_portfolio") == "1"

    if not department_id or not event_cycle_id:
        flash("Department and Event Cycle are required", "error")
        return redirect(url_for("dev.dev_tools_dashboard"))

    department = db.session.get(Department, department_id)
    event_cycle = db.session.get(EventCycle, event_cycle_id)

    if not department or not event_cycle:
        flash("Invalid department or event cycle", "error")
        return redirect(url_for("dev.dev_tools_dashboard"))

    # Find the portfolio
    portfolio = WorkPortfolio.query.filter_by(
        department_id=department_id,
        event_cycle_id=event_cycle_id,
    ).first()

    if not portfolio:
        flash(f"No portfolio found for {department.name} / {event_cycle.name}", "warning")
        return redirect(url_for("dev.dev_tools_dashboard"))

    # Get all work items in this portfolio
    work_items = WorkItem.query.filter_by(portfolio_id=portfolio.id).all()

    deleted_counts = {
        "reviews": 0,
        "line_audits": 0,
        "budget_details": 0,
        "lines": 0,
        "item_audits": 0,
        "comments": 0,
        "items": 0,
    }

    for work_item in work_items:
        # Delete line reviews
        for line in work_item.lines:
            reviews = WorkLineReview.query.filter_by(work_line_id=line.id).all()
            for review in reviews:
                db.session.delete(review)
                deleted_counts["reviews"] += 1

            # Delete line audit events
            line_audits = WorkLineAuditEvent.query.filter_by(work_line_id=line.id).all()
            for audit in line_audits:
                db.session.delete(audit)
                deleted_counts["line_audits"] += 1

            # Delete budget detail
            detail = BudgetLineDetail.query.filter_by(work_line_id=line.id).first()
            if detail:
                db.session.delete(detail)
                deleted_counts["budget_details"] += 1

            # Delete line
            db.session.delete(line)
            deleted_counts["lines"] += 1

        # Delete item audit events
        item_audits = WorkItemAuditEvent.query.filter_by(work_item_id=work_item.id).all()
        for audit in item_audits:
            db.session.delete(audit)
            deleted_counts["item_audits"] += 1

        # Delete comments
        comments = WorkItemComment.query.filter_by(work_item_id=work_item.id).all()
        for comment in comments:
            db.session.delete(comment)
            deleted_counts["comments"] += 1

        # Delete work item
        db.session.delete(work_item)
        deleted_counts["items"] += 1

    # Optionally delete the portfolio
    if delete_portfolio:
        db.session.delete(portfolio)

    db.session.commit()

    summary = ", ".join(f"{v} {k}" for k, v in deleted_counts.items() if v > 0)
    flash(
        f"Cleared budget data for {department.name} / {event_cycle.name}: {summary}"
        + (" (portfolio deleted)" if delete_portfolio else ""),
        "success"
    )

    return redirect(url_for("dev.dev_tools_dashboard"))


@dev_bp.post("/dev/tools/reset-work-item-status")
def reset_work_item_status():
    """
    Reset a work item back to DRAFT status.

    This:
    - Sets status to DRAFT
    - Clears checkout info
    - Deletes all WorkLineReview records
    - Sets all lines back to PENDING

    DANGEROUS: Only available in development environment.
    """
    _require_dev_super_admin()

    from app.models import (
        WorkItem, WorkLine, WorkLineReview,
        WORK_ITEM_STATUS_DRAFT, WORK_LINE_STATUS_PENDING,
    )

    work_item_id = request.form.get("work_item_id", type=int)
    public_id = request.form.get("public_id", "").strip()

    # Find work item by ID or public_id
    work_item = None
    if work_item_id:
        work_item = db.session.get(WorkItem, work_item_id)
    elif public_id:
        work_item = WorkItem.query.filter_by(public_id=public_id).first()

    if not work_item:
        flash("Work item not found", "error")
        return redirect(url_for("dev.dev_tools_dashboard"))

    # Reset work item
    work_item.status = WORK_ITEM_STATUS_DRAFT
    work_item.checked_out_by_user_id = None
    work_item.checked_out_at = None
    work_item.checked_out_expires_at = None

    # Delete reviews and reset lines
    deleted_reviews = 0
    for line in work_item.lines:
        reviews = WorkLineReview.query.filter_by(work_line_id=line.id).all()
        for review in reviews:
            db.session.delete(review)
            deleted_reviews += 1
        line.status = WORK_LINE_STATUS_PENDING
        line.needs_requester_action = False

    db.session.commit()

    flash(
        f"Reset {work_item.public_id} to DRAFT status. "
        f"Deleted {deleted_reviews} reviews, reset {len(work_item.lines)} lines to PENDING.",
        "success"
    )

    return redirect(url_for("dev.dev_tools_dashboard"))


@dev_bp.post("/dev/tools/force-finalize")
def force_finalize_work_item():
    """
    Force finalize a work item (approve all lines at requested amounts).

    DANGEROUS: Only available in development environment.
    """
    _require_dev_super_admin()

    from app.models import (
        WorkItem, WorkLineReview,
        WORK_ITEM_STATUS_FINALIZED, WORK_LINE_STATUS_APPROVED,
        REVIEW_STAGE_ADMIN_FINAL, REVIEW_STATUS_APPROVED,
    )

    work_item_id = request.form.get("work_item_id", type=int)
    public_id = request.form.get("public_id", "").strip()

    work_item = None
    if work_item_id:
        work_item = db.session.get(WorkItem, work_item_id)
    elif public_id:
        work_item = WorkItem.query.filter_by(public_id=public_id).first()

    if not work_item:
        flash("Work item not found", "error")
        return redirect(url_for("dev.dev_tools_dashboard"))

    user_ctx = get_user_ctx()

    # Finalize work item
    work_item.status = WORK_ITEM_STATUS_FINALIZED
    work_item.checked_out_by_user_id = None
    work_item.checked_out_at = None
    work_item.checked_out_expires_at = None

    # Approve all lines
    for line in work_item.lines:
        line.status = WORK_LINE_STATUS_APPROVED
        line.needs_requester_action = False

        # Set approved amount to requested amount
        if line.budget_detail:
            line.budget_detail.approved_amount_cents = int(
                line.budget_detail.unit_price_cents * line.budget_detail.quantity
            )

        # Create admin final review if not exists
        existing_review = WorkLineReview.query.filter_by(
            work_line_id=line.id,
            stage=REVIEW_STAGE_ADMIN_FINAL,
        ).first()

        if not existing_review:
            review = WorkLineReview(
                work_line_id=line.id,
                stage=REVIEW_STAGE_ADMIN_FINAL,
                status=REVIEW_STATUS_APPROVED,
                reviewed_by_user_id=user_ctx.user_id,
            )
            db.session.add(review)
        else:
            existing_review.status = REVIEW_STATUS_APPROVED
            existing_review.reviewed_by_user_id = user_ctx.user_id

    db.session.commit()

    flash(f"Force finalized {work_item.public_id} with {len(work_item.lines)} lines approved.", "success")

    return redirect(url_for("dev.dev_tools_dashboard"))


@dev_bp.get("/dev/db-info")
def db_info():
    """
    Show database schema overview.

    Displays summary of all tables with row counts.
    Only available in development environment with super admin access.
    """
    _require_dev_super_admin()

    inspector = inspect(db.engine)

    # Get table overview (just names and row counts)
    tables_overview = []
    total_rows = 0
    for table_name in sorted(inspector.get_table_names()):
        row_count = db.session.execute(
            db.text(f"SELECT COUNT(*) FROM {table_name}")  # nosec B608
        ).scalar()
        column_count = len(inspector.get_columns(table_name))
        tables_overview.append({
            "name": table_name,
            "row_count": row_count,
            "column_count": column_count,
        })
        total_rows += row_count

    # Get alembic version
    try:
        alembic_version = db.session.execute(
            db.text("SELECT version_num FROM alembic_version")
        ).scalar()
    except Exception:
        alembic_version = "Unknown"

    # Get database size (PostgreSQL specific)
    try:
        db_size = db.session.execute(
            db.text("SELECT pg_size_pretty(pg_database_size(current_database()))")
        ).scalar()
    except Exception:
        db_size = "N/A"

    return render_template(
        "dev/db_info.html",
        tables_overview=tables_overview,
        total_tables=len(tables_overview),
        total_rows=total_rows,
        db_size=db_size,
        alembic_version=alembic_version,
        db_url=str(db.engine.url).replace(str(db.engine.url.password or ""), "***") if db.engine.url.password else str(db.engine.url),
        selected_table=None,
        table_detail=None,
    )


@dev_bp.get("/dev/db-info/<table_name>")
def db_table_detail(table_name: str):
    """
    Show detailed information for a specific table.

    Only available in development environment with super admin access.
    """
    _require_dev_super_admin()

    inspector = inspect(db.engine)

    # Validate table exists
    all_tables = inspector.get_table_names()
    if table_name not in all_tables:
        flash(f"Table '{table_name}' not found", "error")
        return redirect(url_for("dev.db_info"))

    # Get table overview for sidebar
    tables_overview = []
    total_rows = 0
    for tbl in sorted(all_tables):
        row_count = db.session.execute(
            db.text(f"SELECT COUNT(*) FROM {tbl}")  # nosec B608
        ).scalar()
        column_count = len(inspector.get_columns(tbl))
        tables_overview.append({
            "name": tbl,
            "row_count": row_count,
            "column_count": column_count,
        })
        total_rows += row_count

    # Get detailed info for selected table
    columns = inspector.get_columns(table_name)
    pk_constraint = inspector.get_pk_constraint(table_name)
    foreign_keys = inspector.get_foreign_keys(table_name)
    indexes = inspector.get_indexes(table_name)

    # Build FK lookup
    fk_lookup = {}
    for fk in foreign_keys:
        for col in fk.get("constrained_columns", []):
            fk_lookup[col] = {
                "referred_table": fk.get("referred_table"),
                "referred_columns": fk.get("referred_columns"),
            }

    column_info = []
    for col in columns:
        col_data = {
            "name": col["name"],
            "type": str(col["type"]),
            "nullable": col.get("nullable", True),
            "default": str(col.get("default")) if col.get("default") else None,
            "is_pk": col["name"] in (pk_constraint.get("constrained_columns") or []),
            "fk": fk_lookup.get(col["name"]),
        }
        column_info.append(col_data)

    row_count = db.session.execute(
        db.text(f"SELECT COUNT(*) FROM {table_name}")  # nosec B608
    ).scalar()

    # Get sample rows (first 10)
    try:
        sample_rows = db.session.execute(
            db.text(f"SELECT * FROM {table_name} LIMIT 10")  # nosec B608
        ).fetchall()
        sample_columns = [col["name"] for col in columns]
    except Exception:
        sample_rows = []
        sample_columns = []

    table_detail = {
        "name": table_name,
        "columns": column_info,
        "indexes": indexes,
        "row_count": row_count,
        "sample_rows": sample_rows,
        "sample_columns": sample_columns,
    }

    # Get alembic version
    try:
        alembic_version = db.session.execute(
            db.text("SELECT version_num FROM alembic_version")
        ).scalar()
    except Exception:
        alembic_version = "Unknown"

    # Get database size
    try:
        db_size = db.session.execute(
            db.text("SELECT pg_size_pretty(pg_database_size(current_database()))")
        ).scalar()
    except Exception:
        db_size = "N/A"

    return render_template(
        "dev/db_info.html",
        tables_overview=tables_overview,
        total_tables=len(tables_overview),
        total_rows=total_rows,
        db_size=db_size,
        alembic_version=alembic_version,
        db_url=str(db.engine.url).replace(str(db.engine.url.password or ""), "***") if db.engine.url.password else str(db.engine.url),
        selected_table=table_name,
        table_detail=table_detail,
    )

