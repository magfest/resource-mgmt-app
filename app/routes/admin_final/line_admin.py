"""
Admin line tools: change a line's expense account, or add a line to an
in-review request. Budget-admin-only. Both actions route the affected
line through the approval-group stage so nothing skips review.
"""
from flask import render_template, redirect, url_for, request, abort, flash

from app import db
from app.models import (
    ApprovalGroup,
    ConfidenceLevel,
    ExpenseAccount,
    FrequencyOption,
    PriorityLevel,
    SPEND_TYPE_MODE_SINGLE_LOCKED,
)
from app.routes import get_user_ctx
from app.routes.work.helpers import (
    format_currency,
    get_allowed_spend_types,
    get_confidence_levels,
    get_frequency_options,
    get_priority_levels,
)
from . import admin_final_bp
from .helpers import (
    admin_add_line,
    change_line_expense_account,
    require_budget_admin,
)
from .hotel_rooms_report import derive_rooms_and_nights
from .line_pricing import (
    KIND_HOTEL,
    KIND_STANDARD,
    build_account_pricing_map,
    classify_account,
    resolve_line_pricing,
    strip_rooms_prefix,
)
from .reviews import _get_work_item_and_line


def _get_admin_expense_accounts(event_cycle_id: int) -> list:
    """All active accounts, including fixed-cost, hotel, and badge.

    Admins bypass department visibility. Inactive accounts stay out; an
    inactive account is a retired configuration decision, and reintroducing
    one from here would silently undo it.
    """
    return ExpenseAccount.query.filter_by(is_active=True).order_by(
        ExpenseAccount.sort_order.asc(), ExpenseAccount.name.asc()
    ).all()


def _get_budget_approval_groups(work_type_id: int) -> list:
    return ApprovalGroup.query.filter_by(
        work_type_id=work_type_id, is_active=True,
    ).order_by(ApprovalGroup.name.asc()).all()


def _validate_account_selection(ctx, form):
    """
    Validate expense_account_id / spend_type_id / approval_group_id.
    Returns (account, spend_type, group, errors).
    """
    errors = []
    account = spend_type = group = None

    account_id_str = (form.get("expense_account_id") or "").strip()
    if not account_id_str:
        errors.append("Expense account is required.")
    else:
        try:
            account = ExpenseAccount.query.get(int(account_id_str))
        except ValueError:
            account = None
        if not account or not account.is_active:
            errors.append("Invalid expense account.")
            account = None

    if account:
        if account.spend_type_mode == SPEND_TYPE_MODE_SINGLE_LOCKED:
            spend_type = account.default_spend_type
            if not spend_type:
                errors.append("Expense account has no default spend type configured.")
        else:
            allowed = {st.id: st for st in get_allowed_spend_types(account)}
            spend_type_id_str = (form.get("spend_type_id") or "").strip()
            if not spend_type_id_str:
                errors.append("Spend type is required.")
            else:
                try:
                    spend_type = allowed.get(int(spend_type_id_str))
                except ValueError:
                    spend_type = None
                if not spend_type:
                    errors.append("Selected spend type is not allowed for this expense account.")

    group_id_str = (form.get("approval_group_id") or "").strip()
    if not group_id_str:
        errors.append("Review group is required.")
    else:
        try:
            group = ApprovalGroup.query.get(int(group_id_str))
        except ValueError:
            group = None
        if not group or not group.is_active or group.work_type_id != ctx.work_type.id:
            errors.append("Invalid review group.")
            group = None

    return account, spend_type, group, errors


def _parse_note(form):
    """Required note; CRLF-normalized before length check."""
    from app.routes.admin.helpers import MAX_FREEFORM_TEXT_LENGTH
    raw = (form.get("note") or "").replace("\r\n", "\n").replace("\r", "\n")
    errors = []
    if not raw.strip():
        errors.append("A note explaining this change is required.")
    if len(raw) > MAX_FREEFORM_TEXT_LENGTH:
        errors.append(f"Note is too long (max {MAX_FREEFORM_TEXT_LENGTH:,} characters).")
    return raw.strip(), errors


def _queue_group_notification(work_item, group_id):
    """Queue the reviewer notification, mirroring dispatch_to_queue's pattern.

    Call this BEFORE the caller's commit. It only INSERTs into email_outbox,
    so the notification and the line change land in one transaction. The Slack
    announcement is _announce_group_notification, after the commit.
    """
    from app.services.notifications import notify_work_item_dispatched
    notify_work_item_dispatched(work_item, [group_id])


def _announce_group_notification(work_item):
    """Post the Slack announcement. Call this AFTER the caller's commit.

    Split from _queue_group_notification because this is a webhook call;
    inside the transaction it would hold the line's row locks for an HTTP
    round trip.
    """
    from app.services.notifications import announce_work_item_event
    announce_work_item_event(work_item, 'dispatched')


# ============================================================
# Change Expense Account
# ============================================================

@admin_final_bp.get("/<event>/<dept>/<work_type_slug>/item/<public_id>/line/<int:line_num>/change-account")
@admin_final_bp.get("/<event>/<dept>/budget/item/<public_id>/line/<int:line_num>/change-account")
def line_change_account(event: str, dept: str, public_id: str, line_num: int, work_type_slug: str = "budget"):
    """Form: move a line to a different expense account + review group."""
    user_ctx = get_user_ctx()
    require_budget_admin(user_ctx)
    work_item, line, ctx = _get_work_item_and_line(event, dept, public_id, line_num, work_type_slug)

    detail = line.budget_detail
    if not detail:
        abort(404, "Line has no budget details.")

    expense_accounts = _get_admin_expense_accounts(ctx.event_cycle.id)

    # A line already on a hotel account has a meaningful rooms/nights split.
    # Preserve it. A non-hotel quantity has no such split, so guessing one
    # (e.g. defaulting to 1/1) would produce a plausible-looking wrong
    # booking. Only derive when the line's current account is hotel-kind,
    # using the same parsing the hotel rooms report trusts.
    current_rooms = current_nights = None
    if classify_account(detail.expense_account, ctx.event_cycle.id) == KIND_HOTEL:
        current_rooms, current_nights, _ = derive_rooms_and_nights(
            detail.quantity, detail.description
        )

    # Built once and passed as both account_pricing (for the form script) and
    # the source of current_price_is_override below, so the two can never
    # disagree about what the account's current default price is.
    account_pricing = build_account_pricing_map(expense_accounts, ctx.event_cycle.id)

    # account_default_unit_price_cents is only written by the admin tools, so
    # every requester-created fixed-cost/hotel/badge line has it NULL; using
    # it here would treat all of them as "not an override" and let
    # refreshPricing() silently reset their stored price on page load.
    # Compare against the account's live default instead, the same value the
    # form script itself would write.
    info = account_pricing.get(detail.expense_account_id)
    current_price_is_override = (
        info is not None
        and info["kind"] != KIND_STANDARD
        and detail.unit_price_cents != info["default_unit_price_cents"]
    )

    # Same inputs as current_price_is_override, so the two can never
    # disagree: the field is locked exactly when there is a default to lock
    # to and the admin hasn't already overridden it. Rendered server-side so
    # the field never paints as editable before refreshPricing() runs.
    current_price_is_locked = (
        info is not None
        and info["kind"] != KIND_STANDARD
        and not current_price_is_override
    )

    # The server re-adds the "N rooms: " prefix from the Rooms field on
    # submit, so showing it in the textarea too invites an admin to hand-edit
    # it there; that edit would be silently discarded.
    current_description = strip_rooms_prefix(detail.description)

    from app.routes.work.lines import build_spend_types_by_account
    return render_template(
        "admin_final/line_change_account.html",
        ctx=ctx,
        work_item=work_item,
        line=line,
        detail=detail,
        expense_accounts=expense_accounts,
        spend_types_by_account=build_spend_types_by_account(expense_accounts),
        account_pricing=account_pricing,
        approval_groups=_get_budget_approval_groups(ctx.work_type.id),
        format_currency=format_currency,
        current_rooms=current_rooms,
        current_nights=current_nights,
        current_price_is_override=current_price_is_override,
        current_price_is_locked=current_price_is_locked,
        current_description=current_description,
        current_spend_type_id=detail.spend_type_id or "",
    )


@admin_final_bp.post("/<event>/<dept>/<work_type_slug>/item/<public_id>/line/<int:line_num>/change-account")
@admin_final_bp.post("/<event>/<dept>/budget/item/<public_id>/line/<int:line_num>/change-account")
def line_change_account_submit(event: str, dept: str, public_id: str, line_num: int, work_type_slug: str = "budget"):
    user_ctx = get_user_ctx()
    require_budget_admin(user_ctx)
    work_item, line, ctx = _get_work_item_and_line(event, dept, public_id, line_num, work_type_slug)

    account, spend_type, group, errors = _validate_account_selection(ctx, request.form)
    note, note_errors = _parse_note(request.form)
    errors.extend(note_errors)

    pricing = None
    if account:
        # absent_as_none=True: a missing key means keep the line's current
        # value. The browser form always sends these fields now; this guards
        # hand-built or replayed POSTs that omit one, not normal submissions.
        pricing, pricing_errors = resolve_line_pricing(
            account, ctx.event_cycle.id, request.form, absent_as_none=True
        )
        errors.extend(pricing_errors)

    if not errors and pricing:
        success, error = change_line_expense_account(
            line=line, work_item=work_item,
            new_account=account, new_spend_type=spend_type,
            new_group=group, note=note, user_ctx=user_ctx,
            quantity=pricing["quantity"],
            unit_price_cents=pricing["unit_price_cents"],
            account_default_unit_price_cents=pricing["account_default_unit_price_cents"],
            description=pricing["description"],
        )
        if not success:
            errors.append(error)

    if errors:
        db.session.rollback()
        for e in errors:
            flash(e, "error")
        return redirect(url_for(
            "admin_final.line_change_account",
            event=event, dept=dept, public_id=public_id, line_num=line_num,
        ))

    _queue_group_notification(work_item, group.id)
    db.session.commit()
    _announce_group_notification(work_item)

    flash(
        f"Line {line_num} moved to {account.name} and sent back to {group.name} for review.",
        "success",
    )
    # Land on the unified /review page — admin_final.line_review (the old
    # /admin-review page) was removed in favor of this consolidated view.
    return redirect(url_for(
        "approvals.line_review",
        event=event, dept=dept, public_id=public_id, line_num=line_num,
    ))


# ============================================================
# Admin Add Line
# ============================================================

def _parse_line_numbers(form):
    """Validate confidence, frequency, priority, and the warehouse flag.

    Quantity, unit price, and description live in line_pricing.resolve_line_pricing.
    """
    errors = []
    values = {}

    for field, model, label in (
        ("confidence_level_id", ConfidenceLevel, "Confidence level"),
        ("frequency_id", FrequencyOption, "Frequency"),
        ("priority_id", PriorityLevel, "Priority"),
    ):
        obj = None
        id_str = (form.get(field) or "").strip()
        if not id_str:
            errors.append(f"{label} is required.")
        else:
            try:
                obj = model.query.get(int(id_str))
            except ValueError:
                obj = None
            if not obj or not obj.is_active:
                errors.append(f"Invalid {label.lower()}.")
                obj = None
        values[field.replace("_id", "")] = obj

    values["warehouse_flag"] = form.get("warehouse_flag") == "on"

    return values, errors


def _get_work_item_for_add(event, dept, public_id, work_type_slug):
    from app.routes.work.helpers import get_portfolio_context, require_budget_work_type
    from app.models import WorkItem
    ctx = get_portfolio_context(event, dept, work_type_slug)
    require_budget_work_type(ctx)
    work_item = WorkItem.query.filter_by(
        public_id=public_id, portfolio_id=ctx.portfolio.id, is_archived=False,
    ).first()
    if not work_item:
        abort(404, f"Work item not found: {public_id}")
    return work_item, ctx


@admin_final_bp.get("/<event>/<dept>/<work_type_slug>/item/<public_id>/add-line")
@admin_final_bp.get("/<event>/<dept>/budget/item/<public_id>/add-line")
def line_add(event: str, dept: str, public_id: str, work_type_slug: str = "budget"):
    """Form: admin adds a new line to an in-review request."""
    user_ctx = get_user_ctx()
    require_budget_admin(user_ctx)
    work_item, ctx = _get_work_item_for_add(event, dept, public_id, work_type_slug)

    expense_accounts = _get_admin_expense_accounts(ctx.event_cycle.id)
    from app.routes.work.lines import build_spend_types_by_account
    return render_template(
        "admin_final/line_add.html",
        ctx=ctx,
        work_item=work_item,
        expense_accounts=expense_accounts,
        spend_types_by_account=build_spend_types_by_account(expense_accounts),
        account_pricing=build_account_pricing_map(expense_accounts, ctx.event_cycle.id),
        approval_groups=_get_budget_approval_groups(ctx.work_type.id),
        confidence_levels=get_confidence_levels(),
        frequency_options=get_frequency_options(),
        priority_levels=get_priority_levels(),
    )


@admin_final_bp.post("/<event>/<dept>/<work_type_slug>/item/<public_id>/add-line")
@admin_final_bp.post("/<event>/<dept>/budget/item/<public_id>/add-line")
def line_add_submit(event: str, dept: str, public_id: str, work_type_slug: str = "budget"):
    user_ctx = get_user_ctx()
    require_budget_admin(user_ctx)
    work_item, ctx = _get_work_item_for_add(event, dept, public_id, work_type_slug)

    account, spend_type, group, errors = _validate_account_selection(ctx, request.form)
    values, value_errors = _parse_line_numbers(request.form)
    note, note_errors = _parse_note(request.form)
    errors.extend(value_errors)
    errors.extend(note_errors)

    pricing = None
    if account:
        pricing, pricing_errors = resolve_line_pricing(
            account, ctx.event_cycle.id, request.form
        )
        errors.extend(pricing_errors)

    line = None
    if not errors:
        line, error = admin_add_line(
            work_item=work_item, user_ctx=user_ctx,
            expense_account=account, spend_type=spend_type, approval_group=group,
            quantity=pricing["quantity"],
            unit_price_cents=pricing["unit_price_cents"],
            account_default_unit_price_cents=pricing["account_default_unit_price_cents"],
            confidence_level=values["confidence_level"], frequency=values["frequency"],
            priority=values["priority"], warehouse_flag=values["warehouse_flag"],
            description=pricing["description"], note=note,
        )
        if not line:
            errors.append(error)

    if errors:
        db.session.rollback()
        for e in errors:
            flash(e, "error")
        return redirect(url_for(
            "admin_final.line_add",
            event=event, dept=dept, public_id=public_id,
        ))

    _queue_group_notification(work_item, group.id)
    db.session.commit()
    _announce_group_notification(work_item)

    flash(
        f"Line {line.line_number} added and routed to {group.name} for review.",
        "success",
    )
    return redirect(url_for(
        "work.work_item_detail",
        event=event, dept=dept, public_id=public_id,
    ))
