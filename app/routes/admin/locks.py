"""
Admin routes for lock (checkout) management.
"""
from __future__ import annotations

from datetime import datetime

from flask import Blueprint, redirect, url_for, request, flash

from app import db
from app.models import WorkItem, User, Department, EventCycle, WorkPortfolio
from app.routes.budget.helpers import release_expired_checkouts
from .helpers import require_super_admin, render_admin_config_page

locks_bp = Blueprint('locks', __name__, url_prefix='/locks')


def _get_active_locks():
    """Get all work items that are currently checked out (not expired)."""
    now = datetime.utcnow()
    return (
        db.session.query(WorkItem)
        .filter(WorkItem.checked_out_by_user_id.isnot(None))
        .filter(WorkItem.checked_out_expires_at > now)
        .order_by(WorkItem.checked_out_at.desc())
        .all()
    )


def _get_expired_locks():
    """Get all work items with expired checkouts that haven't been cleaned up."""
    now = datetime.utcnow()
    return (
        db.session.query(WorkItem)
        .filter(WorkItem.checked_out_by_user_id.isnot(None))
        .filter(WorkItem.checked_out_expires_at <= now)
        .order_by(WorkItem.checked_out_expires_at.desc())
        .all()
    )


@locks_bp.get("/")
@require_super_admin
def list_locks():
    """List all active and expired locks."""
    active_locks = _get_active_locks()
    expired_locks = _get_expired_locks()

    # Enrich with user and portfolio info
    locks_data = []
    for item in active_locks:
        user = db.session.get(User, item.checked_out_by_user_id)
        portfolio = item.portfolio

        locks_data.append({
            "work_item": item,
            "user": user,
            "department": portfolio.department if portfolio else None,
            "event_cycle": portfolio.event_cycle if portfolio else None,
            "is_expired": False,
            "minutes_remaining": max(0, int((item.checked_out_expires_at - datetime.utcnow()).total_seconds() / 60)),
        })

    for item in expired_locks:
        user = db.session.get(User, item.checked_out_by_user_id)
        portfolio = item.portfolio

        locks_data.append({
            "work_item": item,
            "user": user,
            "department": portfolio.department if portfolio else None,
            "event_cycle": portfolio.event_cycle if portfolio else None,
            "is_expired": True,
            "minutes_remaining": 0,
        })

    return render_admin_config_page(
        "admin/locks/list.html",
        locks=locks_data,
        active_count=len(active_locks),
        expired_count=len(expired_locks),
    )


@locks_bp.post("/<int:work_item_id>/release")
@require_super_admin
def release_lock(work_item_id: int):
    """Force release a specific lock."""
    work_item = db.session.get(WorkItem, work_item_id)
    if not work_item:
        flash("Work item not found.", "error")
        return redirect(url_for(".list_locks"))

    if not work_item.checked_out_by_user_id:
        flash("Work item is not checked out.", "warning")
        return redirect(url_for(".list_locks"))

    # Release the lock
    old_user_id = work_item.checked_out_by_user_id
    work_item.checked_out_by_user_id = None
    work_item.checked_out_at = None
    work_item.checked_out_expires_at = None
    db.session.commit()

    flash(f"Released lock on {work_item.public_id} (was held by {old_user_id}).", "success")
    return redirect(url_for(".list_locks"))


@locks_bp.post("/release-expired")
@require_super_admin
def release_expired():
    """Release all expired locks."""
    count = release_expired_checkouts()
    db.session.commit()

    if count > 0:
        flash(f"Released {count} expired lock(s).", "success")
    else:
        flash("No expired locks to release.", "info")

    return redirect(url_for(".list_locks"))
