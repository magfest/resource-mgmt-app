"""
Admin routes for site content management.

Allows super admins to edit UI text like tab descriptions, info boxes,
and other content that appears throughout the application.
"""
from __future__ import annotations

from flask import Blueprint, redirect, url_for, request, abort, flash

from app import db
from app.models import SiteContent
from app.routes import h, get_user_ctx
from .helpers import (
    require_super_admin,
    render_budget_admin_page,
    log_config_change,
    track_changes,
)
from app.models.constants import CONFIG_AUDIT_UPDATE, CONFIG_AUDIT_CREATE


site_content_bp = Blueprint('site_content', __name__, url_prefix='/site-content')


# Display style constants
DISPLAY_STYLE_PLAIN = "PLAIN"
DISPLAY_STYLE_INFO_BOX = "INFO_BOX"

# Default content values - used as fallbacks when database has no value
# These match the current hardcoded values in templates
DEFAULT_CONTENT = {
    "budget_tab_lines": {
        "name": "Budget Lines Tab",
        "category": "Budget Tabs",
        "title": "Budget Lines",
        "content": None,
        "display_style": DISPLAY_STYLE_PLAIN,
    },
    "budget_tab_fixed_costs": {
        "name": "Fixed Costs Tab",
        "category": "Budget Tabs",
        "title": "Fixed-Cost Items",
        "content": "Enter quantities for standard items with predetermined pricing. Include a note explaining what the item is for.",
        "display_style": DISPLAY_STYLE_PLAIN,
    },
    "budget_tab_hotel": {
        "name": "Hotel Tab",
        "category": "Budget Tabs",
        "title": "Add Hotel Room Request",
        "content": "This tool estimates room nights and their budget impact. It is not the final rooming list system. Focus on total room nights and room categories for now.",
        "display_style": DISPLAY_STYLE_INFO_BOX,
    },
    "budget_tab_badges": {
        "name": "Badges Tab",
        "category": "Budget Tabs",
        "title": "Badge Requests",
        "content": "Enter the number of badges your department needs for each category. Badge counts are for planning and tracking purposes only.",
        "display_style": DISPLAY_STYLE_PLAIN,
    },
    "budget_tab_notes": {
        "name": "Notes Tab",
        "category": "Budget Tabs",
        "title": "Request Notes",
        "content": "Add notes or context about this budget request for reviewers.",
        "display_style": DISPLAY_STYLE_PLAIN,
    },
    "link_budget_policy": {
        "name": "Budget Policy Link",
        "category": "External Links",
        "title": "Budget Policy",
        "content": None,
        "display_style": DISPLAY_STYLE_PLAIN,
    },
    "link_finance_guide": {
        "name": "Finance Guide Link",
        "category": "External Links",
        "title": "Finance Guide",
        "content": None,
        "display_style": DISPLAY_STYLE_PLAIN,
    },
    "link_division_contacts": {
        "name": "Division Head Contacts Link",
        "category": "External Links",
        "title": "Division Head Contact List",
        "content": None,
        "display_style": DISPLAY_STYLE_PLAIN,
    },
    "link_suite_policy": {
        "name": "Suite Policy Link",
        "category": "External Links",
        "title": "Suite Policy",
        "content": None,
        "display_style": DISPLAY_STYLE_PLAIN,
    },
}


def get_site_content(content_key: str) -> dict:
    """
    Get site content by key, with fallback to defaults.

    Returns a dict with: title, content, display_style
    All values may be None if not set (except display_style defaults to PLAIN).
    """
    # Check database first
    content = SiteContent.query.filter_by(content_key=content_key).first()

    if content:
        return {
            "title": content.title,
            "content": content.content,
            "display_style": content.display_style or DISPLAY_STYLE_PLAIN,
        }

    # Fall back to defaults
    defaults = DEFAULT_CONTENT.get(content_key, {})
    return {
        "title": defaults.get("title"),
        "content": defaults.get("content"),
        "display_style": defaults.get("display_style", DISPLAY_STYLE_PLAIN),
    }


def get_all_site_content() -> list[dict]:
    """
    Get all site content entries, including defaults not yet in database.

    Returns list of dicts with content_key, name, category, and values.
    """
    # Get all from database
    db_content = {c.content_key: c for c in SiteContent.query.all()}

    result = []
    for key, defaults in DEFAULT_CONTENT.items():
        db_entry = db_content.get(key)

        result.append({
            "content_key": key,
            "name": db_entry.name if db_entry else defaults["name"],
            "category": db_entry.category if db_entry else defaults["category"],
            "title": db_entry.title if db_entry else defaults.get("title"),
            "content": db_entry.content if db_entry else defaults.get("content"),
            "display_style": (db_entry.display_style if db_entry else defaults.get("display_style")) or DISPLAY_STYLE_PLAIN,
            "in_database": db_entry is not None,
            "id": db_entry.id if db_entry else None,
            "updated_at": db_entry.updated_at if db_entry else None,
        })

    # Add any database entries not in defaults (custom content)
    for key, content in db_content.items():
        if key not in DEFAULT_CONTENT:
            result.append({
                "content_key": key,
                "name": content.name,
                "category": content.category,
                "title": content.title,
                "content": content.content,
                "display_style": content.display_style or DISPLAY_STYLE_PLAIN,
                "in_database": True,
                "id": content.id,
                "updated_at": content.updated_at,
            })

    # Sort by category then name
    result.sort(key=lambda x: (x["category"] or "", x["name"]))

    return result


def _content_to_dict(content: SiteContent) -> dict:
    """Convert site content to dict for change tracking."""
    return {
        "name": content.name,
        "category": content.category,
        "title": content.title,
        "content": content.content,
        "display_style": content.display_style,
    }


@site_content_bp.get("/")
@require_super_admin
def list_site_content():
    """List all site content entries."""
    content_list = get_all_site_content()

    # Group by category
    categories = {}
    for item in content_list:
        cat = item["category"] or "Other"
        if cat not in categories:
            categories[cat] = []
        categories[cat].append(item)

    return render_budget_admin_page(
        "admin/site_content/list.html",
        content_list=content_list,
        categories=categories,
    )


@site_content_bp.get("/edit/<content_key>")
@require_super_admin
def edit_site_content(content_key: str):
    """Show edit form for site content."""
    # Get from database or use defaults
    content = SiteContent.query.filter_by(content_key=content_key).first()

    defaults = DEFAULT_CONTENT.get(content_key, {})
    if not content and not defaults:
        abort(404, "Content key not found")

    return render_budget_admin_page(
        "admin/site_content/form.html",
        content=content,
        content_key=content_key,
        defaults=defaults,
    )


@site_content_bp.post("/edit/<content_key>")
@require_super_admin
def update_site_content(content_key: str):
    """Update site content (creates if doesn't exist)."""
    user_ctx = get_user_ctx()

    # Get existing or prepare to create
    content = SiteContent.query.filter_by(content_key=content_key).first()
    is_new = content is None

    defaults = DEFAULT_CONTENT.get(content_key, {})
    if is_new and not defaults:
        # For custom keys, require name
        pass

    # Track old values for audit
    old_values = _content_to_dict(content) if content else {}

    # Get form values
    name = (request.form.get("name") or "").strip()
    category = (request.form.get("category") or "").strip() or None
    title = (request.form.get("title") or "").strip() or None
    content_text = (request.form.get("content") or "").strip() or None
    display_style = request.form.get("display_style") or DISPLAY_STYLE_PLAIN

    # Validate display_style
    if display_style not in (DISPLAY_STYLE_PLAIN, DISPLAY_STYLE_INFO_BOX):
        display_style = DISPLAY_STYLE_PLAIN

    # Use defaults for name/category if not provided
    if not name and defaults:
        name = defaults.get("name", content_key)
    if not category and defaults:
        category = defaults.get("category")

    if not name:
        flash("Name is required", "error")
        return redirect(url_for(".edit_site_content", content_key=content_key))

    if is_new:
        content = SiteContent(
            content_key=content_key,
            name=name,
            category=category,
            title=title,
            content=content_text,
            display_style=display_style,
            updated_by_user_id=user_ctx.user_id,
        )
        db.session.add(content)
    else:
        content.name = name
        content.category = category
        content.title = title
        content.content = content_text
        content.display_style = display_style
        content.updated_by_user_id = user_ctx.user_id

    db.session.commit()

    # Log the change
    new_values = _content_to_dict(content)
    changes = track_changes(old_values, new_values) if not is_new else new_values

    log_config_change(
        entity_type="SiteContent",
        entity_id=content.id,
        action=CONFIG_AUDIT_CREATE if is_new else CONFIG_AUDIT_UPDATE,
        changes=changes,
        user_id=user_ctx.user_id,
    )

    flash(f"Site content '{name}' {'created' if is_new else 'updated'}.", "success")
    return redirect(url_for(".list_site_content"))


@site_content_bp.post("/reset/<content_key>")
@require_super_admin
def reset_site_content(content_key: str):
    """Reset site content to defaults (deletes database entry)."""
    content = SiteContent.query.filter_by(content_key=content_key).first()

    if not content:
        flash("Content not in database, already using defaults.", "info")
        return redirect(url_for(".list_site_content"))

    defaults = DEFAULT_CONTENT.get(content_key)
    if not defaults:
        flash("Cannot reset custom content (no defaults available).", "error")
        return redirect(url_for(".list_site_content"))

    name = content.name
    db.session.delete(content)
    db.session.commit()

    flash(f"Reset '{name}' to default values.", "success")
    return redirect(url_for(".list_site_content"))
