"""
Request routes - create, view, edit, submit, approve requests.
"""
import re

from flask import Blueprint, render_template, redirect, url_for, request, abort
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from .. import db
from . import (
    h,
    PRIORITY_OPTIONS,
    _get_or_create_line_review_for_line,
    _ensure_line_reviews_for_revision, require_can_view, get_editable_departments, get_user_ctx,
    can_user_create_main_request, require_can_edit, require_can_submit, build_request_perms,
)

requests_bp = Blueprint('requests', __name__)

@requests_bp.get("/")
def home():
    from ..models_old import Request, DepartmentMembership
    from sqlalchemy import and_, or_

    uid = h.get_active_user_id()
    if not uid:
        return redirect(url_for("dev.dev_login"))

    is_admin_or_finance = h.is_admin() or h.is_finance()

    q = db.session.query(Request)

    if not is_admin_or_finance:
        q = (
            q.outerjoin(
                DepartmentMembership,
                and_(
                    DepartmentMembership.department_id == Request.department_id,
                    DepartmentMembership.event_cycle_id == Request.event_cycle_id,
                    DepartmentMembership.user_id == uid,
                    DepartmentMembership.can_view.is_(True),
                ),
            )
            .filter(
                or_(
                    Request.created_by_user_id == uid,
                    DepartmentMembership.id.isnot(None),
                )
            )
        )

    requests_list = (
        q.order_by(Request.id.desc())
        .distinct()
        .limit(200)
        .all()
    )

    # Build per-request perms so templates never “promise” an action that will 403
    user_ctx = get_user_ctx()
    perms_by_request_id = {r.id: build_request_perms(r, user_ctx=user_ctx) for r in requests_list}

    return render_template(
        "requests/home.html",
        my_requests=requests_list,
        perms_by_request_id=perms_by_request_id,
        is_admin=is_admin_or_finance,
    )

@requests_bp.get("/requests/new")
def new_request():
    from ..models_old import EventCycle

    user_ctx = get_user_ctx()

    cycles = (
        db.session.query(EventCycle)
        .filter(EventCycle.is_active.is_(True))
        .order_by(EventCycle.sort_order.asc(), EventCycle.name.asc())
        .all()
    )
    if not cycles:
        abort(500)

    # Admin: keep "default" semantics
    if user_ctx.is_admin:
        default_cycle = next((c for c in cycles if c.is_default), cycles[0])
        departments = get_editable_departments(user_ctx=user_ctx, event_cycle_id=default_cycle.id)
        return render_template(
            "requests/request_new.html",
            cycles=cycles,
            default_cycle_id=default_cycle.id,
            departments=departments,
        )

    # Non-admin: pick the first cycle where the user has at least one editable department
    default_cycle = None
    departments = []
    for c in cycles:
        depts = get_editable_departments(user_ctx=user_ctx, event_cycle_id=c.id)
        if depts:
            default_cycle = c
            departments = depts
            break

    # If they have no editable departments in any cycle, fall back to the default cycle (shows empty depts)
    if not default_cycle:
        default_cycle = next((c for c in cycles if c.is_default), cycles[0])
        departments = get_editable_departments(user_ctx=user_ctx, event_cycle_id=default_cycle.id)

    return render_template(
        "requests/request_new.html",
        cycles=cycles,
        default_cycle_id=default_cycle.id,
        departments=departments,
    )

@requests_bp.post("/requests/new")
def new_request_post():
    from ..models_old import Request, Department, EventCycle

    user_ctx = get_user_ctx()

    event_cycle_id = int(request.form.get("event_cycle_id") or 0)
    department_id = int(request.form.get("department_id") or 0)

    cycle = db.session.get(EventCycle, event_cycle_id)
    dept = db.session.get(Department, department_id)
    if not cycle or not cycle.is_active:
        abort(400)
    if not dept or not dept.is_active:
        abort(400)

    if not can_user_create_main_request(user_ctx=user_ctx, department_id=department_id, event_cycle_id=event_cycle_id):
        abort(403)

    existing = (
        db.session.query(Request)
        .filter(Request.event_cycle_id == event_cycle_id)
        .filter(Request.department_id == department_id)
        .order_by(Request.id.desc())
        .first()
    )
    if existing:
        return redirect(url_for("requests.request_detail", request_id=existing.id))

    req = Request(
        event_cycle_id=event_cycle_id,
        department_id=department_id,
        created_by_user_id=user_ctx.user_id,
        current_status="DRAFT",

        # legacy fields (still NOT NULL in DB)
        event_cycle=cycle.code,
        requesting_department=dept.code,
    )
    db.session.add(req)
    db.session.commit()

    # choose where you want them to land:
    return redirect(url_for("requests.edit_request_draft", request_id=req.id))

@requests_bp.get("/requests/<int:request_id>")
def request_detail(request_id: int):
    from ..models_old import (
        Request,
        RequestRevision,
        RequestLine,
        LineReview,
    )

    h.ensure_demo_budget_data()

    req = db.session.get(Request, request_id)
    if not req:
        abort(404)

    require_can_view(req)

    revisions = (
        db.session.query(RequestRevision)
        .filter(RequestRevision.request_id == req.id)
        .order_by(RequestRevision.revision_number.desc())
        .all()
    )

    totals_by_revision_id = {}
    if revisions:
        rev_ids = [r.id for r in revisions]
        lines_for_all_revs = (
            db.session.query(RequestLine.revision_id, RequestLine.requested_amount)
            .filter(RequestLine.revision_id.in_(rev_ids))
            .all()
        )
        for rev_id, amt in lines_for_all_revs:
            totals_by_revision_id[rev_id] = totals_by_revision_id.get(rev_id, 0) + (amt or 0)

    current_revision = None
    current_lines = []
    current_total = 0

    if req.current_revision_id:
        current_revision = db.session.get(RequestRevision, req.current_revision_id)

        current_lines = (
            db.session.query(RequestLine)
            .options(joinedload(RequestLine.budget_item_type))
            .filter(RequestLine.revision_id == req.current_revision_id)
            .order_by(RequestLine.public_line_number.asc().nullslast(), RequestLine.id.asc())
            .all()
        )
        current_total = sum((l.requested_amount or 0) for l in current_lines)

        if current_lines:
            created = _ensure_line_reviews_for_revision(current_revision.id)
            if created:
                db.session.flush()

    line_reviews_by_line_id = {}
    if current_lines:
        line_ids = [l.id for l in current_lines]
        reviews = (
            db.session.query(LineReview)
            .options(joinedload(LineReview.approval_group))
            .filter(LineReview.request_line_id.in_(line_ids))
            .order_by(LineReview.approval_group_id.asc(), LineReview.updated_at.desc())
            .all()
        )
        for lr in reviews:
            line_reviews_by_line_id.setdefault(lr.request_line_id, []).append(lr)

    review_status_counts = {"PENDING": 0, "NEEDS_INFO": 0, "APPROVED": 0, "REJECTED": 0}
    lines_without_any_reviews = 0
    lines_with_any_blockers = 0
    blocking_lines = []

    if current_lines:
        for line in current_lines:
            lrs = line_reviews_by_line_id.get(line.id, [])
            if not lrs:
                lines_without_any_reviews += 1
                lines_with_any_blockers += 1
                blocking_lines.append({
                    "line_id": line.id,
                    "reason": "No reviews yet",
                    "statuses": [],
                })
                continue

            all_approved_for_line = True
            blocking_statuses = []
            for lr in lrs:
                st = (lr.status or "PENDING").upper()
                if st not in review_status_counts:
                    st = "PENDING"
                review_status_counts[st] += 1

                if st != "APPROVED":
                    all_approved_for_line = False
                    group_code = lr.approval_group.code if lr.approval_group else "GROUP"
                    blocking_statuses.append(f"{group_code}:{st}")

            if not all_approved_for_line:
                lines_with_any_blockers += 1
                blocking_lines.append({
                    "line_id": line.id,
                    "reason": "Not fully approved",
                    "statuses": blocking_statuses,
                })

    ready_to_finalize = (
            bool(current_lines)
            and lines_without_any_reviews == 0
            and review_status_counts["PENDING"] == 0
            and review_status_counts["NEEDS_INFO"] == 0
    )

    review_summary = {
        "pending": review_status_counts["PENDING"],
        "needs_info": review_status_counts["NEEDS_INFO"],
        "approved": review_status_counts["APPROVED"],
        "rejected": review_status_counts["REJECTED"],
        "no_review": lines_without_any_reviews,
        "ready_to_finalize": ready_to_finalize,
    }
    user_ctx = get_user_ctx()
    perms = build_request_perms(req, user_ctx=user_ctx, review_summary=review_summary)

    kickback_reason = getattr(req, "kickback_reason", None)

    return render_template(
        "requests/request_detail.html",
        req=req,
        revisions=revisions,
        totals_by_revision_id=totals_by_revision_id,
        current_revision=current_revision,
        current_lines=current_lines,
        current_total=current_total,
        line_reviews_by_line_id=line_reviews_by_line_id,
        kickback_reason=kickback_reason,
        review_summary=review_summary,
        blocking_lines=blocking_lines,
        perms=perms,
        lines_without_any_reviews=lines_without_any_reviews,
    )


@requests_bp.get("/requests/<int:request_id>/edit")
def edit_request_draft(request_id: int):
    """
    Edit the working draft for a budget request.

    This route is the primary entry point for building or revising a request.
    It is membership-aware and allows:
      - admins
      - request owners
      - department editors / DHs (via DepartmentMembership.can_edit)

    Editing is blocked for finalized requests and enforced centrally via
    require_can_edit().
    """
    from ..models_old import Request, RequestDraft, DraftLine, RequestLine, BudgetItemType

    h.ensure_demo_budget_data()
    req = db.session.get(Request, request_id)

    # Check if request exists
    if not req:
        abort(404)

    # Permission gates
    require_can_view(req)
    perms = require_can_edit(req)  # returns perms, aborts 403 if not allowed

    #Load budget item types for line editor dropdowns
    item_types = (
        db.session.query(BudgetItemType)
        .filter(BudgetItemType.is_active == True)  # noqa: E712
        .order_by(BudgetItemType.item_name.asc())
        .all()
    )

    # Load or create the working draft
    draft = (
        db.session.query(RequestDraft)
        .filter(RequestDraft.request_id == req.id)
        .one_or_none()
    )

    if draft is None:
        draft = RequestDraft(request_id=req.id)
        db.session.add(draft)
        db.session.flush()

        # Seed draft lines from the currently approved / submitted revision
        if req.current_revision_id:
            snapshot_lines = (
                db.session.query(RequestLine)
                .filter(RequestLine.revision_id == req.current_revision_id)
                .order_by(RequestLine.public_line_number.asc().nullslast(), RequestLine.id.asc())
                .all()
            )
            for i, l in enumerate(snapshot_lines, start=1):
                priority = (getattr(l, "priority", "") or (l.justification or "") or "").strip()
                reason = (getattr(l, "reason", "") or (l.description or "") or "").strip()

                bit_id = getattr(l, "budget_item_type_id", None)
                bit = db.session.get(BudgetItemType, bit_id) if bit_id else None

                category = (bit.spend_type if bit and bit.spend_type else (
                        l.category or "Other")).strip() or "Other"
                description = reason or (l.description or "").strip() or ""
                justification = priority or (l.justification or "").strip() or ""

                db.session.add(DraftLine(
                    draft_id=draft.id,
                    budget_item_type_id=bit_id,
                    requested_amount=l.requested_amount or 0,
                    priority=priority,
                    reason=reason,
                    category=category,
                    description=description,
                    justification=justification,
                    sort_order=i,
                ))
        db.session.commit()

    # Load draft lines for display/editing
    lines = (
        db.session.query(DraftLine)
        .filter(DraftLine.draft_id == draft.id)
        .order_by(DraftLine.sort_order.asc(), DraftLine.id.asc())
        .all()
    )

    requested_total = sum((l.requested_amount or 0) for l in lines)

    return render_template(
        "requests/request_edit.html",
        req=req,
        draft=draft,
        lines=lines,
        requested_total=requested_total,
        item_types=item_types,
        priority_options=PRIORITY_OPTIONS,
        perms=perms,  # used by template to hide/show actions
    )

@requests_bp.post("/requests/<int:request_id>/edit")
def save_request_draft(request_id: int):
    """
    Persist edits to a request's working draft lines.

    This route is membership-aware:
      - admins
      - request owners
      - department editors / DHs (DepartmentMembership.can_edit)

    It supports:
      - updating existing draft lines
      - deleting draft lines (checkbox)
      - adding new draft lines (blank row handling)
      - normalizing BudgetItemType -> category/spend_type for display consistency

    IMPORTANT:
    - Do not add ad-hoc permission checks here.
    - Centralized enforcement is via require_can_edit().
    """

    from ..models_old import Request, RequestDraft, DraftLine, RequestLine, BudgetItemType


    # Load request
    req = db.session.get(Request, request_id)
    if not req:
        abort(404)


    # Permission gates
    # require_can_edit() enforces finalized locks and status rules.
    require_can_view(req)
    require_can_edit(req)

    # Load or create draft (POST must be safe even if GET wasn't visited)
    draft = (
        db.session.query(RequestDraft)
        .filter(RequestDraft.request_id == req.id)
        .one_or_none()
    )

    if draft is None:
        draft = RequestDraft(request_id=req.id)
        db.session.add(draft)
        db.session.flush()  # ensure draft.id exists

        # Seed from current revision if present (supports kickback/revision workflow)
        if req.current_revision_id:
            snapshot_lines = (
                db.session.query(RequestLine)
                .filter(RequestLine.revision_id == req.current_revision_id)
                .order_by(RequestLine.public_line_number.asc().nullslast(), RequestLine.id.asc())
                .all()
            )

            for i, l in enumerate(snapshot_lines, start=1):
                priority = (getattr(l, "priority", "") or (l.justification or "") or "").strip()
                reason = (getattr(l, "reason", "") or (l.description or "") or "").strip()

                bit_id = getattr(l, "budget_item_type_id", None)
                bit = db.session.get(BudgetItemType, bit_id) if bit_id else None

                category = (bit.spend_type if bit and bit.spend_type else (l.category or "Other")).strip() or "Other"
                description = reason or (l.description or "").strip() or ""
                justification = priority or (l.justification or "").strip() or ""

                db.session.add(
                    DraftLine(
                        draft_id=draft.id,
                        budget_item_type_id=bit_id,
                        requested_amount=int(l.requested_amount or 0),
                        priority=priority,
                        reason=reason,
                        category=category,
                        description=description,
                        justification=justification,
                        item_name=getattr(l, "item_name", "") or "",
                        sort_order=i,
                    )
                )

        db.session.commit()

    # Parse dynamic line indices from the submitted form
    # Identify rows by scanning keys like: line-<idx>-budget_item_type_id
    index_re = re.compile(r"^line-(\d+)-budget_item_type_id$")
    indices = sorted({
        int(m.group(1))
        for k in request.form.keys()
        for m in [index_re.match(k)]
        if m
    })

    # Sort_order sequentially on every save
    next_sort = 1

    # Apply each submitted row to DraftLine records
    for idx in indices:
        line_id = (request.form.get(f"line-{idx}-id") or "").strip()
        delete_checked = request.form.get(f"line-{idx}-delete") == "on"

        bit_raw = (request.form.get(f"line-{idx}-budget_item_type_id") or "").strip()
        priority = (request.form.get(f"line-{idx}-priority") or "").strip()
        reason = (request.form.get(f"line-{idx}-reason") or "").strip()
        amount_raw = (request.form.get(f"line-{idx}-amount") or "").strip()

        # Ignore completely blank new rows (common in "add another row" UIs)
        is_blank_new = (not line_id) and (not bit_raw) and (not priority) and (not reason) and (not amount_raw)
        if is_blank_new:
            continue

        # Coerce budget_item_type_id / amount safely
        try:
            budget_item_type_id = int(bit_raw) if bit_raw else None
        except ValueError:
            budget_item_type_id = None

        try:
            amount = int(amount_raw) if amount_raw else 0
        except ValueError:
            amount = 0

        # Normalize category based on BudgetItemType (spend_type)
        bit = db.session.get(BudgetItemType, budget_item_type_id) if budget_item_type_id else None
        category = (bit.spend_type if bit and bit.spend_type else "Other").strip() or "Other"

        description = reason
        justification = priority

        # Existing row update/delete
        if line_id:
            try:
                line_pk = int(line_id)
            except ValueError:
                continue

            line = db.session.get(DraftLine, line_pk)

            # Safety: ensure the line belongs to this draft (prevents tampering)
            if not line or line.draft_id != draft.id:
                continue

            if delete_checked:
                db.session.delete(line)
                continue

            line.budget_item_type_id = budget_item_type_id
            line.priority = priority
            line.reason = reason
            line.requested_amount = amount
            line.category = category
            line.description = description
            line.justification = justification
            line.sort_order = next_sort
            next_sort += 1
            continue

        # New row insert
        if delete_checked:
            # Ignore "delete" checked on a row that doesn't exist yet
            continue

        db.session.add(
            DraftLine(
                draft_id=draft.id,
                budget_item_type_id=budget_item_type_id,
                priority=priority,
                reason=reason,
                requested_amount=amount,
                category=category,
                description=description,
                justification=justification,
                sort_order=next_sort,
            )
        )
        next_sort += 1

    db.session.commit()
    return redirect(url_for("requests.edit_request_draft", request_id=req.id))

@requests_bp.post("/requests/<int:request_id>/submit")
def submit_request_draft(request_id: int):
    """
    Submit a request draft as a new revision.

    This route:
      - validates the draft lines
      - creates a RequestRevision and RequestLine rows
      - initializes LineReview rows for the new revision lines
      - transitions Request.current_status to SUBMITTED
      - logs an audit event

    Permissions are enforced centrally (membership-aware) via require_can_submit().
    """

    from ..models_old import (
        Request, RequestDraft, DraftLine,
        RequestRevision, RequestLine,
        BudgetItemType,
        RequestAuditEvent,
    )

    # Load request
    req = db.session.get(Request, request_id)
    if not req:
        abort(404)

    # Permission gates (single source of truth)
    # require_can_submit() enforces:
    #   - not finalized
    #   - correct statuses for submit
    #   - admin OR owner OR dept membership can_edit (for submit)
    require_can_view(req)
    perms = require_can_submit(req)

    uid = perms.user_id if hasattr(perms, "user_id") else h.get_active_user_id()  # safe fallback
    is_admin = perms.is_admin

    # Status constraints (kept explicit because admin has a special case)
    old_status = (req.current_status or "").upper()

    # Admin can resubmit from SUBMITTED (to create a new revision)
    allowed = {"DRAFT", "NEEDS_REVISION", "SUBMITTED"} if is_admin else {"DRAFT", "NEEDS_REVISION"}
    if old_status not in allowed:
        return f"Cannot submit from status {old_status}.", 400

    # Load draft + draft lines
    draft = (
        db.session.query(RequestDraft)
        .filter(RequestDraft.request_id == req.id)
        .one_or_none()
    )
    if not draft:
        return "No draft exists for this request.", 400

    draft_lines = (
        db.session.query(DraftLine)
        .filter(DraftLine.draft_id == draft.id)
        .order_by(DraftLine.sort_order.asc(), DraftLine.id.asc())
        .all()
    )
    if not draft_lines:
        return "Cannot submit: draft has no lines.", 400

    # Determine next revision number
    max_rev = (
        db.session.query(func.max(RequestRevision.revision_number))
        .filter(RequestRevision.request_id == req.id)
        .scalar()
    )
    next_rev_num = int(max_rev or 0) + 1

    # Validate / clean draft lines
    clean_lines: list[DraftLine] = []
    errors: list[str] = []

    for dl in draft_lines:
        bit_id = getattr(dl, "budget_item_type_id", None)
        priority = (getattr(dl, "priority", "") or "").strip()
        reason = (getattr(dl, "reason", "") or "").strip()
        amt = int(dl.requested_amount or 0)

        # Ignore fully blank lines (common when users add an empty row)
        is_fully_blank = (bit_id is None) and (not priority) and (not reason) and (amt == 0)
        if is_fully_blank:
            continue

        missing = []
        if bit_id is None:
            missing.append("Item type")
        if not priority:
            missing.append("Priority")
        if not reason:
            missing.append("Reason")
        if amt <= 0:
            missing.append("Amount (> 0)")

        if missing:
            errors.append(f"Draft line {dl.sort_order or dl.id}: missing {', '.join(missing)}.")
            continue

        clean_lines.append(dl)

    if errors:
        return "Cannot submit:\n" + "\n".join(errors), 400
    if not clean_lines:
        return "Cannot submit: draft has no valid lines.", 400

    # Create a new revision
    new_rev = RequestRevision(
        request_id=req.id,
        revision_number=next_rev_num,
        submitted_by_user_id=h.get_active_user_id(),  # keep consistent with existing auth helper
        status_at_submission="SUBMITTED",
    )
    db.session.add(new_rev)
    db.session.flush()  # new_rev.id

    # Create RequestLine rows for this revision
    created_lines: list[RequestLine] = []
    public_n = 1

    for dl in clean_lines:
        bit = db.session.get(BudgetItemType, dl.budget_item_type_id)
        if not bit:
            return f"Invalid BudgetItemType id={dl.budget_item_type_id}.", 400

        rl = RequestLine(
            revision_id=new_rev.id,
            public_line_number=public_n,
            budget_item_type_id=dl.budget_item_type_id,
            category=(bit.spend_type or "Other"),
            item_name="",
            description=(dl.reason or ""),
            justification=(dl.priority or ""),
            priority=(dl.priority or ""),
            reason=(dl.reason or ""),
            requested_amount=int(dl.requested_amount or 0),
            requester_comment=None,
        )
        db.session.add(rl)
        created_lines.append(rl)
        public_n += 1

    db.session.flush()

    # Initialize line review rows for the new lines
    for rl in created_lines:
        _get_or_create_line_review_for_line(rl)


    # Update request pointers + status
    req.current_revision_id = new_rev.id
    req.current_status = "SUBMITTED"
    if old_status == "NEEDS_REVISION":
        req.kickback_reason = None

    # Audit log
    event_type = "SUBMITTED_REVISION"
    if is_admin and old_status == "SUBMITTED":
        event_type = "ADMIN_RESUBMITTED_REVISION"

    db.session.add(RequestAuditEvent(
        request_id=req.id,
        event_type=event_type,
        old_value=old_status,
        new_value=f"Rev {next_rev_num} submitted (draft {draft.id})",
        created_by_user_id=h.get_active_user_id(),
    ))

    db.session.commit()
    return redirect(url_for("lines.revision_snapshot", revision_id=new_rev.id))

@requests_bp.post("/requests/<int:request_id>/kickback")
def kickback_request(request_id: int):
    from ..models_old import Request, RequestAuditEvent

    req = db.session.get(Request, request_id)
    if not req:
        abort(404)

    if not (h.is_admin() or h.is_finance()):
        abort(403)
    if (req.current_status or "").upper() == "APPROVED":
        abort(400, "Request is finalized and cannot be modified.")

    old_status = (req.current_status or "").upper()
    if old_status != "SUBMITTED":
        return f"Invalid transition: {old_status} -> NEEDS_REVISION", 400

    reason = (request.form.get("kickback_reason") or "").strip()
    if not reason:
        return "Kickback reason is required.", 400

    req.current_status = "NEEDS_REVISION"
    req.kickback_reason = reason

    db.session.add(RequestAuditEvent(
        request_id=req.id,
        event_type="STATUS_CHANGE",
        old_value=old_status,
        new_value=f"NEEDS_REVISION :: {reason}",
        created_by_user_id=h.get_active_user_id(),
    ))

    db.session.commit()
    return redirect(url_for("requests.request_detail", request_id=req.id))

@requests_bp.post("/requests/<int:request_id>/approve")
def approve_request(request_id: int):
    from datetime import datetime
    from ..models_old import Request, RequestLine, LineReview

    req = db.session.get(Request, request_id)
    if not req:
        abort(404)

    final_note = (request.form.get("final_approval_note") or "").strip()
    if not final_note:
        abort(400, "Final approval note is required.")

    req.final_approval_note = final_note

    if (req.current_status or "").upper() == "APPROVED":
        abort(400, "Request is finalized and cannot be modified.")

    if (req.current_status or "").upper() != "SUBMITTED":
        abort(400, "Cannot approve request unless it is SUBMITTED.")

    if not req.current_revision_id:
        abort(400, "Cannot approve request without a current revision.")

    if not (h.is_admin() or h.is_finance()):
        abort(403)

    lines = (
        db.session.query(RequestLine)
        .filter(RequestLine.revision_id == req.current_revision_id)
        .all()
    )
    if not lines:
        abort(400, "Cannot approve request with no lines.")

    line_ids = [l.id for l in lines]

    reviews = (
        db.session.query(LineReview)
        .filter(LineReview.request_line_id.in_(line_ids))
        .all()
    )

    reviews_by_line = {}
    for lr in reviews:
        reviews_by_line.setdefault(lr.request_line_id, []).append(lr)

    for line in lines:
        lrs = reviews_by_line.get(line.id)
        if not lrs:
            abort(400, "Cannot approve request: some lines have no reviews.")
        for lr in lrs:
            st = (lr.status or "PENDING").upper()

            if st in ("PENDING", "NEEDS_INFO"):
                abort(400, "Cannot finalize request: some line reviews are still pending or need info.")

            if st not in ("APPROVED", "REJECTED"):
                abort(400, "Cannot finalize request: invalid line review status.")

    req.current_status = "APPROVED"
    req.approved_revision_id = req.current_revision_id
    req.approved_at = datetime.utcnow()
    req.approved_by_user_id = h.get_active_user_id()

    db.session.commit()
    return redirect(url_for("requests.request_detail", request_id=req.id))
