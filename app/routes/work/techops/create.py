"""
TechOps request creation — the "New Request" sectioned form.

GET renders the empty (or default-populated) form. POST validates and
creates the WorkItem + TechOpsRequestDetail + per-service WorkLines.
Save Draft leaves the request in DRAFT; Submit calls the engine
submit_work_item helper to transition to SUBMITTED. A no-services-needed
affirmation is just another line by the time this module sees it.
expand_to_lines() (line_grain.py) emits it, and replace_lines() persists
it like any other, on save, not only at submit.
"""
from types import SimpleNamespace

from flask import abort, flash, redirect, render_template, request, url_for

from app import db
from app.models import (
    WorkItem,
    REQUEST_KIND_PRIMARY,
    WORK_ITEM_STATUS_DRAFT,
)
from app.routes import get_user_ctx
from .. import work_bp
from ..helpers import (
    generate_public_id_for_portfolio,
    get_portfolio_context,
    require_portfolio_edit,
    require_portfolio_view,
)
from .form_utils import (
    ACTION_ADD_SPACE,
    ACTION_SUBMIT,
    active_service_types,
    department_wide_redisplay,
    panel_entries,
    parse_form,
    replace_lines,
    replace_spaces,
    upsert_request_detail,
    validate,
)
from .line_grain import SOURCE_NEW, expand_to_lines
from .preview import build_preview_rows
from .spaces import (
    offerable_spaces,
    phone_line_share_options,
    picker_candidates,
    redisplay_cards,
    space_cards,
    space_display_names,
)


def _do_submit(work_item, user_ctx):
    """Run the submit lifecycle: call submit_work_item, queue the submit
    notification, commit once, then announce on Slack.

    Lines, including a no-services-needed affirmation, are already on the
    item by the time this runs. replace_lines() wrote them from
    expand_to_lines() earlier in the same request, not here.

    The notification is queued inside the submit transaction; the outbox rows
    and the SUBMITTED status land together or not at all. The announcement is
    a webhook call and runs after the commit.
    """
    from app.routes.work.helpers.lifecycle import submit_work_item

    submit_work_item(work_item, user_ctx)

    # Queue before the commit. notify only INSERTs into email_outbox, so one
    # commit covers the status change and its emails.
    from app.services.notifications import (
        announce_work_item_event,
        notify_work_item_submitted,
    )
    notify_work_item_submitted(work_item)

    db.session.commit()

    # Slack after the commit; it is a webhook call, not a local INSERT.
    announce_work_item_event(work_item, 'submitted')

    return True


@work_bp.get("/<event>/<dept>/techops/new")
def techops_request_new(event: str, dept: str):
    """Render the New TechOps Request form."""
    ctx = get_portfolio_context(event, dept, "techops")
    perms = require_portfolio_view(ctx)

    if not perms.can_create_primary:
        abort(403, "You do not have permission to create a TechOps request for this department.")

    user_ctx = get_user_ctx()
    user = user_ctx.user

    # No work item exists yet, so cards come only from the department's
    # assignments; every other offerable space is a picker candidate.
    cards = space_cards(None, ctx.department.id, ctx.event_cycle)
    venue_spaces = offerable_spaces(ctx.event_cycle)

    return render_template(
        "techops/work_item_form.html",
        ctx=ctx,
        perms=perms,
        work_item=None,
        request_detail=None,
        existing_lines_by_code={},
        service_types=active_service_types(),
        # Defaults: account display name + email. Help text in the template
        # explains why someone might change these for a specific request.
        default_contact_name=user.display_name if user else "",
        default_contact_email=user.email if user else "",
        cards=cards,
        share_options=phone_line_share_options(cards),
        source_new=SOURCE_NEW,
        offerable_spaces=venue_spaces,
        picker_options=picker_candidates(cards, venue_spaces),
        # No work item exists yet, so there is nothing saved to preview.
        # preview_saved False: "as of last save" would be false on a
        # request that has never been saved at all (round-1 review, item 7).
        preview_rows=[],
        preview_saved=False,
    )


@work_bp.post("/<event>/<dept>/techops/new")
def techops_request_create(event: str, dept: str):
    """Process the New TechOps Request form."""
    ctx = get_portfolio_context(event, dept, "techops")
    perms = require_portfolio_edit(ctx)

    if not perms.can_create_primary:
        abort(403, "You do not have permission to create a TechOps request for this department.")

    user_ctx = get_user_ctx()

    # A dict, not a set: parse_form fills SpaceAnswer.display_name from it,
    # so an error can name the room. The name is this event's alias where
    # one exists, which is why it comes from space_cards rather than from
    # Space.name. No work item exists yet, so cards come only from the
    # department's assignments; work_item=None means no held-but-unofferable
    # extras either. `venue_spaces` (Space objects, not just names) backs
    # both the offerable-name lookup below and redisplay_cards' picker-add
    # branch, so a space named only through the picker still resolves.
    cards = space_cards(None, ctx.department.id, ctx.event_cycle)
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
        # Re-render with submitted values preserved (no redirect) so the
        # user sees their input + the errors inline. PRG only applies to
        # successful mutations.
        #
        # `cards` reflects the last saved draft; `answers` is what the
        # requester just typed. redisplay_cards() overlays the posted
        # answer fields onto the card identity from `cards` and adds a
        # fresh card for anything the picker just added, so
        # _space_card.html can render from `card` alone and never
        # silently discard the requester's typing on a validation
        # failure. department_wide_redisplay() does the same for section
        # 3's RADIO_CHANNEL/OTHER entries, which have no card concept.
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
            work_item=None,
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
            # Nothing was written on a validation failure; these rows are
            # what was just typed, from the same expand_to_lines() the
            # preview endpoint and the eventual save both call.
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

    work_item = WorkItem(
        portfolio_id=ctx.portfolio.id,
        request_kind=REQUEST_KIND_PRIMARY,
        status=WORK_ITEM_STATUS_DRAFT,
        public_id=generate_public_id_for_portfolio(ctx.portfolio),
        created_by_user_id=user_ctx.user_id,
    )
    db.session.add(work_item)
    db.session.flush()

    upsert_request_detail(work_item, answers, user_ctx)
    replace_spaces(work_item, answers)
    replace_lines(work_item, answers)
    db.session.commit()

    if answers.action == ACTION_ADD_SPACE:
        # The picker's own submit, on a request that did not exist before
        # this POST. Redirect to the edit route rather than re-rendering
        # this "new request" page in place: `techops_request_new`'s GET
        # requires `can_create_primary`, which is now false because the
        # draft this POST just created exists, so a refresh of the old
        # URL 403s the requester out of the request they just started.
        # replace_spaces() persists every space including an unanswered
        # one (answer is nullable — see its docstring), so the picked
        # card is on the row already and the edit GET renders it through
        # the ordinary space_cards() path with nothing rebuilt in memory.
        return redirect(url_for(
            "work.techops_request_edit",
            event=event, dept=dept, public_id=work_item.public_id,
        ))

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

    # A draft save, from the bottom "Save Draft" button or a card's own
    # "Save this space". Either way this stays a long form the requester
    # is still filling in, so it returns to the edit form rather than the
    # read-only detail page. save_space_id (set only by a per-card save)
    # carries the requester back to the card they were just on instead of
    # the top of the form.
    flash("Draft saved.", "success")
    edit_url = url_for(
        "work.techops_request_edit",
        event=event, dept=dept, public_id=work_item.public_id,
    )
    if answers.save_space_id is not None:
        edit_url = f"{edit_url}?open={answers.save_space_id}"
    return redirect(edit_url)
