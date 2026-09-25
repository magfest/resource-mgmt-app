"""
TechOps draft edit — render the same sectioned form pre-populated with
existing values, then on POST delete-and-recreate the line rows.

Drafts have no audit/review history (lines aren't reviewed until
submission), so destructive replace-on-save is safe and avoids the
diff-and-update plumbing that would only be needed if reviewers had
already touched anything.
"""
from types import SimpleNamespace

from flask import abort, flash, redirect, render_template, request, url_for
from sqlalchemy.orm import joinedload, selectinload

from app import db
from app.models import (
    TechOpsLineDetail,
    WorkItem,
    WorkLine,
    WORK_ITEM_STATUS_DRAFT,
)
from app.routes import get_user_ctx
from .. import work_bp
from ..helpers import (
    build_work_item_perms,
    get_portfolio_context,
    require_portfolio_view,
)
from .create import _do_submit
from .form_utils import (
    ACTION_ADD_SPACE,
    ACTION_SUBMIT,
    active_service_types,
    audit_draft_edit,
    capture_form_snapshot,
    capture_state_snapshot,
    department_wide_redisplay,
    panel_entries,
    parse_form,
    replace_lines,
    replace_spaces,
    upsert_request_detail,
    validate,
)
from .line_grain import SOURCE_NEW, expand_to_lines
from .preview import build_preview_rows, rows_from_saved_lines
from .spaces import (
    offerable_spaces,
    phone_line_share_options,
    picker_candidates,
    redisplay_cards,
    space_cards,
    space_display_names,
)


def _load_draft(event: str, dept: str, public_id: str):
    """Load a TechOps draft work item with everything the edit form needs."""
    ctx = get_portfolio_context(event, dept, "techops")
    require_portfolio_view(ctx)

    work_item = (
        WorkItem.query
        .filter_by(
            public_id=public_id,
            portfolio_id=ctx.portfolio.id,
            is_archived=False,
        )
        .options(
            selectinload(WorkItem.lines)
                .joinedload(WorkLine.techops_detail)
                .joinedload(TechOpsLineDetail.service_type),
            joinedload(WorkItem.techops_detail),
        )
        .first()
    )

    if not work_item:
        abort(404, f"TechOps work item not found: {public_id}")

    if work_item.status != WORK_ITEM_STATUS_DRAFT:
        abort(409, "Only DRAFT TechOps requests can be edited.")

    perms = build_work_item_perms(work_item, ctx)
    if not perms.can_edit:
        abort(403, "You do not have permission to edit this TechOps request.")

    return work_item, ctx, perms


@work_bp.get("/<event>/<dept>/techops/item/<public_id>/edit")
def techops_request_edit(event: str, dept: str, public_id: str):
    """Render the TechOps edit form pre-populated with the draft's values."""
    work_item, ctx, perms = _load_draft(event, dept, public_id)

    # Build {service_code: [row_dict, ...]} for the form template. Single-
    # line services collect into a 1-element list; per-instance services
    # (instance_noun set) collect into one entry per existing WorkLine, in
    # line_number order, so the repeating-group section pre-fills.
    existing_lines_by_code: dict[str, list[dict]] = {}
    for line in sorted(work_item.lines, key=lambda l: l.line_number):
        d = line.techops_detail
        if not d or not d.service_type:
            continue
        st = d.service_type
        bucket = existing_lines_by_code.setdefault(st.code, [])
        if st.instance_noun:
            bucket.append({
                "location": d.location or "",
                "usage": d.usage or "",
                "config": d.config or {},
            })
        else:
            # Single-line service — keep only the first (there should be
            # at most one line per single-line service code).
            if not bucket:
                bucket.append({"description": d.description or ""})

    rd = work_item.techops_detail
    cards = space_cards(work_item, ctx.department.id, ctx.event_cycle)
    venue_spaces = offerable_spaces(ctx.event_cycle)
    return render_template(
        "techops/work_item_form.html",
        ctx=ctx,
        perms=perms,
        work_item=work_item,
        request_detail=rd,
        existing_lines_by_code=existing_lines_by_code,
        service_types=active_service_types(),
        default_contact_name=(rd.primary_contact_name if rd else ""),
        default_contact_email=(rd.primary_contact_email if rd else ""),
        cards=cards,
        share_options=phone_line_share_options(cards),
        source_new=SOURCE_NEW,
        offerable_spaces=venue_spaces,
        picker_options=picker_candidates(cards, venue_spaces),
        # A clean read of a draft's own persisted lines, not a
        # recomputation: this is what the last replace_lines() call wrote.
        # space_display_names(), not raw Space.name: a folded room shows
        # this event's alias on the card, and the preview must match.
        preview_rows=rows_from_saved_lines(
            work_item, space_display_names(venue_spaces, cards)),
        preview_saved=True,
    )


@work_bp.post("/<event>/<dept>/techops/item/<public_id>/edit")
def techops_request_update(event: str, dept: str, public_id: str):
    """Process the TechOps edit form (delete-and-recreate semantics)."""
    work_item, ctx, perms = _load_draft(event, dept, public_id)
    user_ctx = get_user_ctx()

    # A dict, not a set: parse_form fills SpaceAnswer.display_name from it,
    # so an error can name the room. The name is this event's alias where
    # one exists, which is why it comes from space_cards rather than from
    # Space.name. `venue_spaces` (Space objects) backs both the offerable-
    # name lookup below and redisplay_cards' picker-add branch, so a space
    # named only through the picker still resolves.
    cards = space_cards(work_item, ctx.department.id, ctx.event_cycle)
    venue_spaces = offerable_spaces(ctx.event_cycle)
    offerable_by_id = {s.id: s for s in venue_spaces}
    offerable = space_display_names(venue_spaces, cards)
    answers, parse_errors = parse_form(request.form, offerable)
    errors = parse_errors + validate(answers, has_space_cards=bool(answers.spaces))

    if errors:
        # The submit-refusal panel renders only for a refused SUBMIT, not
        # for a draft save that also happens to fail (a missing contact
        # name, say). A refused submit and a successful save both land back
        # on this form, so without the panel the two are indistinguishable.
        show_error_panel = answers.action == ACTION_SUBMIT
        # The panel replaces the flash for a refused submit; flashing the
        # same errors here too showed every problem twice, once as a flash
        # and once as the panel's own bullet. A draft-save failure has no
        # panel, so it still flashes, same as any other save-time error
        # (a permission failure, say) that the panel does not cover.
        if not show_error_panel:
            for err in errors:
                flash(err, "error")
        # `cards` reflects the draft as last saved to the database; on a
        # validation failure `answers` is what the requester just typed
        # and has not been persisted. redisplay_cards() overlays the
        # posted answer fields onto the card identity from `cards` and
        # adds a fresh card for anything the picker just added, so
        # _space_card.html can render from `card` alone and never
        # silently discard the requester's typing. department_wide_redisplay()
        # does the same for section 3's RADIO_CHANNEL/OTHER entries, which
        # have no card concept.
        display_cards = redisplay_cards(cards, answers, offerable_by_id)
        service_types_list = active_service_types()
        blocking_errors = panel_entries(errors) if show_error_panel else []
        blocked_space_ids = {
            entry["space_id"] for entry in blocking_errors
            if entry["space_id"] is not None
        }
        return render_template(
            "techops/work_item_form.html",
            ctx=ctx,
            perms=perms,
            work_item=work_item,
            request_detail=SimpleNamespace(
                primary_contact_name=answers.primary_contact_name,
                primary_contact_email=answers.primary_contact_email,
                additional_notes=answers.additional_notes,
                no_services_needed=answers.no_services_needed,
            ),
            existing_lines_by_code=department_wide_redisplay(answers.department_wide),
            service_types=service_types_list,
            default_contact_name=answers.primary_contact_name,
            default_contact_email=answers.primary_contact_email,
            cards=display_cards,
            share_options=phone_line_share_options(display_cards),
            source_new=SOURCE_NEW,
            offerable_spaces=venue_spaces,
            picker_options=picker_candidates(display_cards, venue_spaces),
            # Nothing was written; these rows are what was just typed, not
            # yet the draft on disk.
            preview_rows=build_preview_rows(
                expand_to_lines(answers),
                {st.code: st for st in service_types_list},
                offerable,
            ),
            preview_saved=False,
            show_error_panel=show_error_panel,
            blocking_errors=blocking_errors,
            blocked_space_ids=blocked_space_ids,
        )

    # Capture the pre-edit state from the ORM before delete-and-recreate.
    # The after-snapshot is built from the parsed form data instead of
    # re-querying work_item — replace_lines's delete-and-recreate leaves
    # work_item.lines stale in memory, so reading it back would give an
    # incorrect snapshot.
    before_snapshot = capture_state_snapshot(work_item)

    upsert_request_detail(work_item, answers, user_ctx)
    replace_spaces(work_item, answers)
    replace_lines(work_item, answers)

    after_snapshot = capture_form_snapshot(answers)
    audit_draft_edit(work_item, before_snapshot, after_snapshot, user_ctx)

    db.session.commit()

    if answers.action == ACTION_ADD_SPACE:
        # The picker's own submit: render the form again instead of the
        # usual redirect to the read-only detail page, so the requester
        # stays on an editable form and sees the new card. Unlike
        # create.py, this draft already existed before the POST, so
        # there is no create-route permission gate for a refresh to trip
        # over and no redirect is needed. replace_spaces() now persists
        # every space including an unanswered one (answer is nullable),
        # so the picked card is already on the row space_cards() reads;
        # redisplay_cards() still runs to overlay anything else just
        # typed, same as the error-redisplay branch above.
        fresh_cards = space_cards(work_item, ctx.department.id, ctx.event_cycle)
        display_cards = redisplay_cards(fresh_cards, answers, offerable_by_id)
        service_types_list = active_service_types()
        return render_template(
            "techops/work_item_form.html",
            ctx=ctx,
            perms=perms,
            work_item=work_item,
            request_detail=work_item.techops_detail,
            existing_lines_by_code=department_wide_redisplay(answers.department_wide),
            service_types=service_types_list,
            default_contact_name=answers.primary_contact_name,
            default_contact_email=answers.primary_contact_email,
            cards=display_cards,
            share_options=phone_line_share_options(display_cards),
            source_new=SOURCE_NEW,
            offerable_spaces=venue_spaces,
            picker_options=picker_candidates(display_cards, venue_spaces),
            # replace_lines() just committed from this same `answers`, so
            # recomputing from it matches what is now on disk without
            # reading work_item.lines, whose in-memory collection
            # delete-and-recreate can leave stale (see before_snapshot's
            # comment above).
            preview_rows=build_preview_rows(
                expand_to_lines(answers),
                {st.code: st for st in service_types_list},
                offerable,
            ),
            preview_saved=True,
        )

    if answers.action == ACTION_SUBMIT:
        if not _do_submit(work_item, user_ctx):
            return redirect(url_for(
                "work.techops_work_item_detail",
                event=event, dept=dept, public_id=work_item.public_id,
            ))
        flash(
            "TechOps request submitted! TechOps will reach out if any clarifications are needed.",
            "success",
        )
        return redirect(url_for(
            "work.techops_work_item_detail",
            event=event, dept=dept, public_id=work_item.public_id,
        ))

    # A draft save (bottom "Save Draft" or a card's own "Save this
    # space") stays on the form instead of the read-only detail page,
    # since a long form gets saved often and a requester mid-answer
    # should not be thrown off it. save_space_id (only set by a per-card
    # save) carries the requester back to the card they were on.
    flash("Draft updated.", "success")
    edit_url = url_for(
        "work.techops_request_edit",
        event=event, dept=dept, public_id=work_item.public_id,
    )
    if answers.save_space_id is not None:
        edit_url = f"{edit_url}?open={answers.save_space_id}"
    return redirect(edit_url)
