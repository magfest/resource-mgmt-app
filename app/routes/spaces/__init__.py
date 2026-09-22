"""
Spaces: the venue room catalog and per-event department assignments.

Event Ops owns these pages and holds the SPACE_ADMIN role. "Event Ops" is the
team name and appears only in the interface; code says SPACE_ADMIN.

This package sits outside app/routes/admin/ on purpose. Every route in that
blueprint requires SUPER_ADMIN, and a differently guarded route inside it
invites a copied decorator.
"""
from __future__ import annotations

from flask import Blueprint

spaces_bp = Blueprint("spaces", __name__, url_prefix="/spaces")

from . import views, venues, catalog  # noqa: E402,F401  (registers routes on spaces_bp)
