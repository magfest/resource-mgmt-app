"""
Admin line tools: change a line's expense account, or add a line to an
in-review request. Budget-admin-only. Both actions route the affected
line through the approval-group stage so nothing skips review.
"""
from decimal import Decimal, InvalidOperation

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
    get_effective_account_type,
    get_frequency_options,
    get_priority_levels,
)
from . import admin_final_bp
from .helpers import (
    admin_add_line,
    change_line_expense_account,
    require_budget_admin,
)
from .reviews import _get_work_item_and_line


def _get_admin_expense_accounts(event_cycle_id: int) -> list:
    """All active, non-fixed accounts — admins bypass department visibility."""
    accounts = ExpenseAccount.query.filter_by(is_active=True).order_by(
        ExpenseAccount.sort_order.asc(), ExpenseAccount.name.asc()
    ).all()
    return [
        a for a in accounts
        if not get_effective_account_type(a, event_cycle_id)[0]
    ]


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
        elif get_effective_account_type(account, ctx.event_cycle.id)[0]:
            errors.append("Fixed-cost expense accounts cannot be used here.")
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


def _notify_group_nonblocking(work_item, group_id):
    """Post-commit notification, mirroring dispatch_to_queue's pattern."""
    try:
        from app.services.notifications import notify_work_item_dispatched
        notify_work_item_dispatched(work_item, [group_id])
        db.session.commit()  # persist NotificationLog rows
    except Exception:
        db.session.rollback()
        import logging
        logging.getLogger(__name__).exception(
            "Failed to send admin-line-tool notification for %s", work_item.public_id
        )


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
    from app.routes.work.lines import build_spend_types_by_account
    return render_template(
        "admin_final/line_change_account.html",
        ctx=ctx,
        work_item=work_item,
        line=line,
        detail=detail,
        expense_accounts=expense_accounts,
        spend_types_by_account=build_spend_types_by_account(expense_accounts),
        approval_groups=_get_budget_approval_groups(ctx.work_type.id),
        format_currency=format_currency,
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

    if not errors:
        success, error = change_line_expense_account(
            line=line, work_item=work_item,
            new_account=account, new_spend_type=spend_type,
            new_group=group, note=note, user_ctx=user_ctx,
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

    db.session.commit()
    _notify_group_nonblocking(work_item, group.id)

    flash(
        f"Line {line_num} moved to {account.name} and sent back to {group.name} for review.",
        "success",
    )
    return redirect(url_for(
        "admin_final.line_review",
        event=event, dept=dept, public_id=public_id, line_num=line_num,
    ))


# ============================================================
# Admin Add Line
# ============================================================

def _parse_line_numbers(form):
    """
    Validate quantity / unit_price / confidence / frequency / priority /
    warehouse / description. Mirrors app/routes/work/lines.py:237-294.
    Returns (values_dict, errors).
    """
    errors = []
    values = {}

    quantity = Decimal("1")
    quantity_str = (form.get("quantity") or "1").strip()
    if quantity_str:
        try:
            quantity = Decimal(quantity_str)
            if quantity <= 0:
                errors.append("Quantity must be greater than 0.")
        except InvalidOperation:
            errors.append("Invalid quantity value.")
    values["quantity"] = quantity

    unit_price_cents = 0
    unit_price_str = (form.get("unit_price") or "0").strip()
    if unit_price_str:
        try:
            unit_price_dollars = Decimal(unit_price_str)
            if unit_price_dollars < 0:
                errors.append("Unit price cannot be negative.")
            else:
                unit_price_cents = int(unit_price_dollars * 100)
        except InvalidOperation:
            errors.append("Invalid unit price value.")
    values["unit_price_cents"] = unit_price_cents

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

    from app.routes.admin.helpers import MAX_FREEFORM_TEXT_LENGTH
    description_raw = (form.get("description") or "").replace("\r\n", "\n").replace("\r", "\n")
    if len(description_raw) > MAX_FREEFORM_TEXT_LENGTH:
        errors.append(f"Line description is too long (max {MAX_FREEFORM_TEXT_LENGTH:,} characters).")
    values["description"] = description_raw.strip()

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

    line = None
    if not errors:
        line, error = admin_add_line(
            work_item=work_item, user_ctx=user_ctx,
            expense_account=account, spend_type=spend_type, approval_group=group,
            quantity=values["quantity"], unit_price_cents=values["unit_price_cents"],
            confidence_level=values["confidence_level"], frequency=values["frequency"],
            priority=values["priority"], warehouse_flag=values["warehouse_flag"],
            description=values["description"], note=note,
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

    db.session.commit()
    _notify_group_nonblocking(work_item, group.id)

    flash(
        f"Line {line.line_number} added and routed to {group.name} for review.",
        "success",
    )
    return redirect(url_for(
        "work.work_item_detail",
        event=event, dept=dept, public_id=public_id,
    ))
