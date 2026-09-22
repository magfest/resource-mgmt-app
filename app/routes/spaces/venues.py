"""Venue management. Space admins own this, not super admins."""
from __future__ import annotations

from flask import abort, flash, redirect, request, url_for

from app import db
from app.models import CONFIG_AUDIT_CREATE, CONFIG_AUDIT_UPDATE, Venue
from app.routes import h
from . import spaces_bp
from .helpers import flash_if_too_long, render_space_admin_page, require_space_admin


@spaces_bp.get("/venues/")
@require_space_admin
def list_venues():
    venues = db.session.query(Venue).order_by(Venue.name).all()
    return render_space_admin_page(
        "spaces/venues.html",
        venues=venues,
        edit_id=request.args.get("edit", type=int),
    )


@spaces_bp.post("/venues/new")
@require_space_admin
def create_venue():
    # Imported here, not at module level: app/routes/spaces/ is not an admin
    # package, and a module-level admin import from a non-admin route
    # breaks the `h` helper proxy.
    from app.routes.admin.helpers import log_config_change

    code = (request.form.get("code") or "").strip().upper()
    name = (request.form.get("name") or "").strip()

    if not code or not name:
        flash("Code and name are required", "error")
        return redirect(url_for("spaces.list_venues"))
    # The code prefixes a room code wherever one travels outside the app.
    # Four characters is too long for that, so three is the rule.
    if not (len(code) == 3 and code.isalpha()):
        flash("A venue code is exactly three letters", "error")
        return redirect(url_for("spaces.list_venues"))
    if flash_if_too_long(name, "name"):
        return redirect(url_for("spaces.list_venues"))

    if db.session.query(Venue).filter_by(code=code).first():
        flash(f"A venue with code '{code}' already exists", "error")
        return redirect(url_for("spaces.list_venues"))

    venue = Venue(
        code=code,
        name=name,
        notes=(request.form.get("notes") or "").strip() or None,
        is_active=True,
        created_by_user_id=h.get_active_user_id(),
        updated_by_user_id=h.get_active_user_id(),
    )
    db.session.add(venue)
    db.session.flush()
    log_config_change("venue", venue.id, CONFIG_AUDIT_CREATE)
    db.session.commit()

    flash(f"Added {venue.name}", "success")
    return redirect(url_for("spaces.list_venues"))


@spaces_bp.post("/venues/<int:venue_id>")
@require_space_admin
def update_venue(venue_id: int):
    from app.routes.admin.helpers import log_config_change

    venue = db.session.get(Venue, venue_id)
    if venue is None:
        abort(404)

    # The code is not editable here; it is the join key spaces hang off of.
    name = (request.form.get("name") or venue.name).strip()
    if flash_if_too_long(name, "name"):
        return redirect(url_for("spaces.list_venues"))
    venue.name = name
    venue.notes = (request.form.get("notes") or "").strip() or None
    venue.is_active = request.form.get("is_active") == "1"
    venue.updated_by_user_id = h.get_active_user_id()

    log_config_change("venue", venue.id, CONFIG_AUDIT_UPDATE)
    db.session.commit()

    flash(f"Saved {venue.name}", "success")
    return redirect(url_for("spaces.list_venues"))
