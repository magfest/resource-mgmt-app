"""The order-line preview fragment.

Renders exactly what submit would create, by calling the same
expand_to_lines() the submit path calls. It writes nothing: no work item,
no lines, no audit rows.

Validation errors are not shown here. A requester mid-typing has many, and
the preview's job is to answer "how many lines is this", not to nag.
"""
from __future__ import annotations

from flask import abort, render_template, request

from .. import work_bp
from ..helpers import get_portfolio_context, require_portfolio_view
from .form_utils import active_service_types, parse_form
from .line_grain import (
    PURPOSE_BOTH,
    PURPOSE_TEXT,
    PURPOSE_VOICE,
    PlannedLine,
    SERVICE_DESK_PHONE,
    SERVICE_ETHERNET,
    SERVICE_NO_SERVICES,
    SERVICE_PHONE_NUMBER,
    SERVICE_WIFI,
    expand_to_lines,
)
from .spaces import offerable_spaces, space_cards, space_display_names

# Section 3's help text calls these "your team rather than a room"; used as
# the space column for a line whose space_id is NULL.
DEPARTMENT_WIDE_LABEL = "Department-wide"

# A DESK_PHONE whose source names a line that does not exist or a share
# that itself shares (line_grain.expand_to_lines leaves parent_index None
# in both cases). Matches the wording validate() would ask the requester to
# fix, without invoking validate() itself.
PHONE_LINE_NOT_CHOSEN = "Rings a number not yet requested."

_PURPOSE_LABELS = {
    PURPOSE_VOICE: "Voice",
    PURPOSE_TEXT: "Text",
    PURPOSE_BOTH: "Voice & text",
}


def _spec_for(entry: PlannedLine, ring_line_number: int | None) -> str:
    """Build the one-line specification shown for a planned line.

    Presentation only. The decision that this line exists, and what it
    points at, was already made by expand_to_lines(); this only formats
    the fields that decision produced.
    """
    code = entry.service_code
    if code == SERVICE_WIFI:
        return entry.description or "—"
    if code == SERVICE_ETHERNET:
        return f"{entry.location or '—'} — {entry.usage or '—'}"
    if code == SERVICE_PHONE_NUMBER:
        label = _PURPOSE_LABELS.get(entry.purpose, "Purpose not yet chosen")
        if entry.internal_only:
            label += " (internal only)"
        return f"{label} — {entry.usage}" if entry.usage else label
    if code == SERVICE_DESK_PHONE:
        ring = (f"Rings the number on line {ring_line_number}."
               if ring_line_number is not None else PHONE_LINE_NOT_CHOSEN)
        return f"{entry.location} — {ring}" if entry.location else ring
    if code == SERVICE_NO_SERVICES:
        return entry.description or "No services needed for this space."
    # RADIO_CHANNEL, OTHER, and any future department-wide code: location +
    # usage when either is set (RADIO_CHANNEL's shape), else description
    # (OTHER's shape).
    if entry.location or entry.usage:
        return f"{entry.location or '—'} — {entry.usage or '—'}"
    return entry.description or "—"


def build_preview_rows(lines: list[PlannedLine], service_types_by_code: dict,
                       space_names: dict) -> list[dict]:
    """Shape expand_to_lines() output for the fragment template.

    One row per PlannedLine, numbered in the order replace_lines() would
    assign as line_number. Callers pass either a fresh expand_to_lines()
    result (the live preview) or one built from an already-committed
    RequestAnswers (a just-saved redisplay). Never a second recomputation
    of which lines exist.
    """
    rows = []
    for index, entry in enumerate(lines):
        ring_line_number = None
        if entry.service_code == SERVICE_DESK_PHONE and entry.parent_index is not None:
            ring_line_number = entry.parent_index + 1
        service_type = service_types_by_code.get(entry.service_code)
        rows.append({
            "number": index + 1,
            "service_label": service_type.name if service_type else entry.service_code,
            "space_name": (space_names.get(entry.space_id, "this space")
                          if entry.space_id is not None else DEPARTMENT_WIDE_LABEL),
            "spec": _spec_for(entry, ring_line_number),
        })
    return rows


def rows_from_saved_lines(work_item, space_names: dict) -> list[dict]:
    """Row dicts for the no-script fallback, read from a freshly loaded
    work item's persisted lines.

    These rows are what the last replace_lines() call already computed via
    expand_to_lines(); reconstructing them a second time here would be the
    second implementation the module docstring forbids. parent_line is a
    real FK relationship, so a DESK_PHONE's ring target is just read off
    it rather than re-resolved through an index map.

    space_names must come from space_display_names(), not from
    `detail.space.name` directly: a folded room's card shows this event's
    alias (space_cards()), and reading the catalog name here instead would
    show the requester a name that appears nowhere else on the page.
    """
    rows = []
    for line in sorted(work_item.lines, key=lambda l: l.line_number):
        detail = line.techops_detail
        if detail is None:
            continue
        service_code = detail.service_type.code if detail.service_type else ""
        ring_line_number = None
        if service_code == SERVICE_DESK_PHONE and detail.parent_line is not None:
            ring_line_number = detail.parent_line.line_number
        planned = PlannedLine(
            service_code=service_code,
            space_id=detail.space_id,
            location=detail.location,
            usage=detail.usage,
            description=detail.description,
            config=detail.config,
            purpose=detail.purpose,
            internal_only=detail.internal_only,
        )
        rows.append({
            "number": line.line_number,
            "service_label": (detail.service_type.name if detail.service_type
                             else service_code),
            "space_name": (space_names.get(detail.space_id, "this space")
                          if detail.space_id is not None else DEPARTMENT_WIDE_LABEL),
            "spec": _spec_for(planned, ring_line_number),
        })
    return rows


def _draft_for_preview(ctx, public_id):
    """Load the draft a preview call is previewing, or None for a request
    that does not exist yet (the "new request" form).

    Deliberately not edit.py's _load_draft: edit.py already imports from
    this module (build_preview_rows, rows_from_saved_lines), and importing
    back from edit.py here would make that a circular import. This skips
    _load_draft's DRAFT-status and per-item edit-permission checks on
    purpose. Reading which spaces a draft already holds is not a
    mutation, and require_portfolio_view already gates visibility into
    this department's TechOps requests.
    """
    if public_id is None:
        return None
    from app.models import WorkItem
    work_item = (
        WorkItem.query
        .filter_by(public_id=public_id, portfolio_id=ctx.portfolio.id,
                  is_archived=False)
        .first()
    )
    if work_item is None:
        abort(404, f"TechOps work item not found: {public_id}")
    return work_item


@work_bp.post("/<event>/<dept>/techops/preview-lines")
@work_bp.post("/<event>/<dept>/techops/item/<public_id>/preview-lines")
def techops_preview_lines(event: str, dept: str, public_id: str | None = None):
    """Return the order-line preview fragment for the posted form state.

    Runs on every change of a half-finished draft, so it skips validate()
    and must tolerate anything parse_form can produce; it never raises on
    malformed input. Reads a department's spaces, so it uses the same
    portfolio view check the edit route uses rather than being public.

    The set of space ids a post may legitimately name, and the names they
    display under, must match what create.py/edit.py accept on save
    exactly: space_display_names() is shared with both so a room this
    draft already holds but that has since stopped being offerable (an
    archived space, a venue swap) is not silently dropped from the
    preview while the save that follows still creates its line.
    """
    ctx = get_portfolio_context(event, dept, "techops")
    require_portfolio_view(ctx)

    work_item = _draft_for_preview(ctx, public_id)
    cards = space_cards(work_item, ctx.department.id, ctx.event_cycle)
    venue_spaces = offerable_spaces(ctx.event_cycle)
    space_names = space_display_names(venue_spaces, cards)

    answers, _parse_errors = parse_form(request.form, space_names)
    service_types_by_code = {st.code: st for st in active_service_types()}
    rows = build_preview_rows(expand_to_lines(answers), service_types_by_code,
                              space_names)
    return render_template("techops/_order_preview.html", rows=rows, saved=False)
