"""
Line and revision routes - view/edit lines, comments, transitions, and revisions.
"""
from datetime import datetime

from flask import Blueprint, render_template, redirect, url_for, request, abort
from sqlalchemy.orm import joinedload

from . import (
    h,
    get_user_ctx,
    get_request_or_404,
    require_can_view,
    FINAL_REQUEST_STATUSES,
    user_can_edit_line_review,  # <-- missing today, but used below
    _apply_line_review_transition,
    _get_or_create_line_review_for_line,
    _norm,
    _line_match_key, build_request_perms,
)

from .. import db

lines_bp = Blueprint('lines', __name__)

@lines_bp.get("/requests/<int:request_id>/lines/<int:line_id>")
def line_detail(request_id: int, line_id: int):
    from ..models import (
        RequestLine,
        LineReview,
        LineComment,
        LineAuditEvent,
        User,
        BudgetItemType,
    )

    # ------------------------------------------------------------
    # Load request + basic view permission gate
    # ------------------------------------------------------------
    req = get_request_or_404(request_id)
    require_can_view(req)

    # ------------------------------------------------------------
    # Load line and ensure it belongs to this request
    # ------------------------------------------------------------
    line = db.session.get(RequestLine, line_id)
    if not line or not line.revision or line.revision.request_id != req.id:
        abort(404)

    # ------------------------------------------------------------
    # Determine approval_group_id for the line (canonical source)
    # ------------------------------------------------------------
    approval_group_id = None
    if line.budget_item_type_id:
        bit = db.session.get(BudgetItemType, line.budget_item_type_id)
        approval_group_id = bit.approval_group_id if bit else None

    # Fallback if budget item type is missing but review exists
    # (useful for legacy/demo data)
    if approval_group_id is None:
        any_review = (
            db.session.query(LineReview)
            .filter(LineReview.request_line_id == line.id)
            .order_by(LineReview.id.asc())
            .first()
        )
        if any_review:
            approval_group_id = any_review.approval_group_id

    # ------------------------------------------------------------
    # Compute user context + reviewer permission
    # ------------------------------------------------------------
    user_ctx = get_user_ctx()
    is_requester = "REQUESTER" in set(user_ctx.roles)

    # Reviewer = admin OR can review the approval group for this line
    is_reviewer = bool(user_ctx.is_admin or (approval_group_id and h.can_review_group(approval_group_id)))

    # Admin-only internal visibility: allow admins and reviewers to see internal notes + audit
    can_view_admin_thread = is_reviewer

    # ------------------------------------------------------------
    # Select the LineReview for THIS line + owning group (if possible)
    # ------------------------------------------------------------
    line_review = None
    if approval_group_id:
        line_review = (
            db.session.query(LineReview)
            .filter(LineReview.request_line_id == line.id)
            .filter(LineReview.approval_group_id == approval_group_id)
            .one_or_none()
        )
    if line_review is None:
        # Final fallback: show the earliest review if we still can't match
        line_review = (
            db.session.query(LineReview)
            .filter(LineReview.request_line_id == line.id)
            .order_by(LineReview.id.asc())
            .first()
        )

    owning_group_code = None
    if line_review:
        try:
            owning_group_code = line_review.approval_group.code
        except Exception:
            owning_group_code = str(line_review.approval_group_id)

    # Keep this for template compatibility (if you use it)
    can_review_this_line = is_reviewer

    # ------------------------------------------------------------
    # Load comments and audit events
    # ------------------------------------------------------------
    public_comments = (
        db.session.query(LineComment)
        .filter(LineComment.request_line_id == line.id)
        .filter(LineComment.visibility == "PUBLIC")
        .order_by(LineComment.created_at.asc())
        .all()
    )

    admin_comments = []
    audit_events = []
    if can_view_admin_thread:
        admin_comments = (
            db.session.query(LineComment)
            .filter(LineComment.request_line_id == line.id)
            .filter(LineComment.visibility == "ADMIN")
            .order_by(LineComment.created_at.asc())
            .all()
        )

        audit_events = (
            db.session.query(LineAuditEvent)
            .filter(LineAuditEvent.request_line_id == line.id)
            .order_by(LineAuditEvent.created_at.desc())
            .all()
        )

    # ------------------------------------------------------------
    # Resolve user display names for comment/audit authors
    # ------------------------------------------------------------
    user_ids = {c.created_by_user_id for c in public_comments} | {c.created_by_user_id for c in admin_comments}
    user_ids |= {e.created_by_user_id for e in audit_events}

    users = (
        db.session.query(User)
        .filter(User.id.in_(list(user_ids)) if user_ids else False)
        .all()
    )
    user_by_id = {u.id: u for u in users}

    is_finalized = (req.current_status or "").upper() in FINAL_REQUEST_STATUSES

    return render_template(
        "requests/line_detail.html",
        req=req,
        line=line,
        line_review=line_review,
        approval_group_id=approval_group_id,
        is_requester=is_requester,
        is_reviewer=is_reviewer,
        can_review_this_line=can_review_this_line,
        can_view_admin_thread=can_view_admin_thread,
        public_comments=public_comments,
        admin_comments=admin_comments,
        audit_events=audit_events,
        user_by_id=user_by_id,
        owning_group_code=owning_group_code,
        is_finalized=is_finalized,
    )

@lines_bp.post("/requests/<int:request_id>/lines/<int:line_id>/comment")
def add_line_comment(request_id: int, line_id: int):
    from ..models import Request, RequestLine, LineComment, LineAuditEvent

    req = get_request_or_404(request_id)
    require_can_view(req)

    # Lock after finalization (admin-only exceptions can be added later if desired)
    if (req.current_status or "").upper() in FINAL_REQUEST_STATUSES:
        abort(403)

    line = db.session.get(RequestLine, line_id)
    if not line or line.revision.request_id != req.id:
        abort(404)

    body = (request.form.get("body") or "").strip()
    visibility = (request.form.get("visibility") or "PUBLIC").strip().upper()

    if not body:
        return redirect(url_for("lines.line_detail", request_id=req.id, line_id=line.id))

    user_ctx = get_user_ctx()

    # Determine reviewer capability for this line (admin OR can review owning group)
    approval_group_id = None
    if line.budget_item_type_id:
        from ..models import BudgetItemType
        bit = db.session.get(BudgetItemType, line.budget_item_type_id)
        approval_group_id = bit.approval_group_id if bit else None

    is_reviewer = bool(user_ctx.is_admin or (approval_group_id and h.can_review_group(approval_group_id)))

    # Normalize visibility based on capability
    if visibility not in ("PUBLIC", "ADMIN"):
        visibility = "PUBLIC"

    if visibility == "ADMIN" and not is_reviewer:
        visibility = "PUBLIC"

    uid = h.get_active_user_id()

    c = LineComment(
        request_line_id=line.id,
        visibility=visibility,
        body=body,
        created_by_user_id=uid,
    )
    db.session.add(c)

    db.session.add(LineAuditEvent(
        request_line_id=line.id,
        event_type="COMMENT_ADDED",
        old_value=None,
        new_value=f"{visibility} comment added",
        created_by_user_id=uid,
    ))

    db.session.commit()
    return redirect(url_for("lines.line_detail", request_id=req.id, line_id=line.id))

@lines_bp.post("/requests/<int:request_id>/lines/<int:line_id>/transition")
def transition_line_review(request_id: int, line_id: int):
    """
    Canonical line-review transition endpoint.
    """
    from ..models import Request, RequestLine, LineReview, LineAuditEvent

    req = get_request_or_404(request_id)
    require_can_view(req)

    # Lock after finalization (admin-only exceptions can be added later if desired)
    if (req.current_status or "").upper() in FINAL_REQUEST_STATUSES:
        abort(403)

    line = db.session.get(RequestLine, line_id)
    if not line:
        abort(404)

    if not line.revision or line.revision.request_id != req.id:
        abort(404)

    # Identify the owning approval group for this line (canonical)
    approval_group_id = None
    if line.budget_item_type_id:
        from ..models import BudgetItemType
        bit = db.session.get(BudgetItemType, line.budget_item_type_id)
        approval_group_id = bit.approval_group_id if bit else None

    # Fallback: if we can't resolve via BIT (legacy/demo), allow the first review
    if approval_group_id:
        lr = (
            db.session.query(LineReview)
            .filter(LineReview.request_line_id == line.id)
            .filter(LineReview.approval_group_id == approval_group_id)
            .one_or_none()
        )
    else:
        lr = (
            db.session.query(LineReview)
            .filter(LineReview.request_line_id == line.id)
            .order_by(LineReview.id.asc())
            .first()
        )

    if not lr:
        abort(404)

    user_ctx = get_user_ctx()
    if not (user_ctx.is_admin or user_can_edit_line_review(lr, user_ctx=user_ctx)):
        abort(403)

    action = (request.form.get("action") or "").strip().upper()
    note = (request.form.get("note") or "").strip()
    approved_amount_raw = (request.form.get("approved_amount") or "").strip()

    def _parse_amount(raw_value: str | None):
        if raw_value is None:
            return None
        if raw_value == "":
            return None
        try:
            amount = int(raw_value)
        except ValueError:
            return None
        return amount if amount >= 0 else None

    approved_amount = _parse_amount(approved_amount_raw)
    if approved_amount_raw and approved_amount is None:
        return "Invalid approved amount.", 400

    allowed_actions = {"APPROVE", "REJECT", "REQUEST_INFO", "MARK_PENDING", "UPDATE_DECISION_NOTE", "UPDATE_APPROVED_AMOUNT"}
    if action not in allowed_actions:
        abort(400)

    internal_note = (request.form.get("internal_note") or "").strip() or None

    if action == "UPDATE_DECISION_NOTE":
        if lr.status not in ("APPROVED", "REJECTED"):
            return "Decision note updates are only allowed after approval/rejection.", 400
        if not note:
            return "Decision note is required.", 400
        lr.final_decision_note = note
        lr.final_decision_at = datetime.utcnow()
        lr.final_decision_by_user_id = h.get_active_user_id()
        db.session.add(LineAuditEvent(
            request_line_id=lr.request_line_id,
            event_type="FINAL_NOTE_UPDATED",
            old_value="",
            new_value=note,
            created_by_user_id=h.get_active_user_id(),
        ))
        db.session.commit()
        rev_id = lr.request_line.revision_id
        return redirect(url_for("lines.revision_snapshot", revision_id=rev_id))

    if action == "UPDATE_APPROVED_AMOUNT":
        if lr.status != "APPROVED":
            return "Approved amount can only be updated for approved lines.", 400
        if approved_amount is None:
            return "Approved amount is required.", 400
        requested_amount = lr.request_line.requested_amount or 0
        if approved_amount != requested_amount and not note:
            return "Approval note is required when approved amount differs from requested.", 400
        old_amount = lr.approved_amount
        if old_amount != approved_amount:
            lr.approved_amount = approved_amount
            db.session.add(LineAuditEvent(
                request_line_id=lr.request_line_id,
                event_type="APPROVED_AMOUNT_CHANGE",
                old_value=str(old_amount) if old_amount is not None else "",
                new_value=f"{approved_amount} :: {note}" if note else str(approved_amount),
                created_by_user_id=h.get_active_user_id(),
            ))
        if note and note != (lr.final_decision_note or ""):
            lr.final_decision_note = note
            lr.final_decision_at = datetime.utcnow()
            lr.final_decision_by_user_id = h.get_active_user_id()
            db.session.add(LineAuditEvent(
                request_line_id=lr.request_line_id,
                event_type="FINAL_NOTE_UPDATED",
                old_value="",
                new_value=note,
                created_by_user_id=h.get_active_user_id(),
            ))
        db.session.commit()
        rev_id = lr.request_line.revision_id
        return redirect(url_for("lines.revision_snapshot", revision_id=rev_id))

    if action == "APPROVE":
        if approved_amount is None:
            approved_amount = lr.request_line.requested_amount or 0
        if approved_amount != (lr.request_line.requested_amount or 0) and not note:
            return "Approval note is required when approved amount differs from requested.", 400

    _apply_line_review_transition(lr=lr, action=action, note=note, internal_note=internal_note)

    if action == "APPROVE":
        old_amount = lr.approved_amount
        lr.approved_amount = approved_amount
        db.session.add(LineAuditEvent(
            request_line_id=lr.request_line_id,
            event_type="APPROVED_AMOUNT_CHANGE",
            old_value=str(old_amount) if old_amount is not None else "",
            new_value=str(approved_amount),
            created_by_user_id=h.get_active_user_id(),
        ))

    # Keep request status in sync
    if lr.request_line and lr.request_line.revision:
        h.recalc_request_status_from_lines(lr.request_line.revision)

    db.session.commit()

    rev_id = lr.request_line.revision_id
    return redirect(url_for("lines.revision_snapshot", revision_id=rev_id))

@lines_bp.post("/requests/<int:request_id>/lines/<int:line_id>/requester-respond")
def requester_respond_to_needs_info(request_id: int, line_id: int):
    from ..models import RequestLine, LineReview, LineComment, LineAuditEvent

    req = get_request_or_404(request_id)
    require_can_view(req)

    # Lock after finalization (admin-only exceptions can be added later if desired)
    if (req.current_status or "").upper() in FINAL_REQUEST_STATUSES:
        abort(403)

    line = db.session.get(RequestLine, line_id)
    if not line or line.revision.request_id != req.id:
        abort(404)

    user_ctx = get_user_ctx()
    perms = build_request_perms(req, user_ctx=user_ctx)
    if not (perms.can_edit or perms.is_admin):
        abort(403)

    lr = (
        db.session.query(LineReview)
        .filter(LineReview.request_line_id == line.id)
        .order_by(LineReview.id.asc())
        .first()
    )
    if not lr:
        return "No review record for this line.", 400

    if lr.status != "NEEDS_INFO":
        return "This line is not currently in NEEDS_INFO.", 400

    body = (request.form.get("body") or "").strip()
    if not body:
        return "Response is required.", 400

    uid = h.get_active_user_id()

    c = LineComment(
        request_line_id=line.id,
        visibility="PUBLIC",
        body=body,
        created_by_user_id=uid,
    )
    db.session.add(c)

    db.session.add(LineAuditEvent(
        request_line_id=line.id,
        event_type="REQUESTER_RESPONSE",
        old_value="",
        new_value=body,
        created_by_user_id=uid,
    ))

    old_status = lr.status
    lr.status = "PENDING"
    lr.updated_by_user_id = uid

    db.session.add(LineAuditEvent(
        request_line_id=line.id,
        event_type="STATUS_CHANGE",
        old_value=old_status,
        new_value="PENDING :: requester responded",
        created_by_user_id=uid,
    ))

    db.session.commit()
    return redirect(url_for("lines.line_detail", request_id=req.id, line_id=line.id))

@lines_bp.post("/line-reviews/<int:line_review_id>/approve")
def approve_line_review(line_review_id: int):
    from ..models import LineReview, LineAuditEvent

    lr = db.session.get(LineReview, line_review_id)
    if not lr:
        abort(404)

    # Enforce request visibility first
    req = lr.request_line.revision.request
    if not lr.request_line or not lr.request_line.revision or not lr.request_line.revision.request:
        abort(404)
    require_can_view(req)

    # Lock after finalization
    if (req.current_status or "").upper() in FINAL_REQUEST_STATUSES:
        abort(403)

    # Enforce reviewer permissions (admin == finance)
    user_ctx = get_user_ctx()
    if user_ctx.is_admin or user_ctx.is_finance:
        group_ids = None
    else:
        group_ids = list(user_ctx.approval_group_ids or [])
        if not group_ids:
            abort(403)

    note = (request.form.get("note") or "").strip()
    approved_amount_raw = (request.form.get("approved_amount") or "").strip()
    try:
        approved_amount = int(approved_amount_raw) if approved_amount_raw != "" else None
    except ValueError:
        return "Invalid approved amount.", 400
    if approved_amount is not None and approved_amount < 0:
        return "Invalid approved amount.", 400

    _apply_line_review_transition(lr=lr, action="APPROVE", note=note)

    if approved_amount is None:
        approved_amount = lr.request_line.requested_amount or 0

    if approved_amount != (lr.request_line.requested_amount or 0) and not note:
        return "Approval note is required when approved amount differs from requested.", 400

    old_amount = lr.approved_amount
    lr.approved_amount = approved_amount
    db.session.add(LineAuditEvent(
        request_line_id=lr.request_line_id,
        event_type="APPROVED_AMOUNT_CHANGE",
        old_value=str(old_amount) if old_amount is not None else "",
        new_value=str(approved_amount),
        created_by_user_id=h.get_active_user_id(),
    ))

    # Keep request status in sync
    if lr.request_line and lr.request_line.revision:
        h.recalc_request_status_from_lines(lr.request_line.revision)

    db.session.commit()

    rev_id = lr.request_line.revision_id
    return redirect(url_for("lines.revision_snapshot", revision_id=rev_id))

@lines_bp.post("/line-reviews/<int:line_review_id>/kickback")
def kickback_line_review(line_review_id: int):
    from ..models import LineReview

    lr = db.session.get(LineReview, line_review_id)
    if not lr:
        abort(404)

    # Enforce request visibility first
    req = lr.request_line.revision.request
    if not lr.request_line or not lr.request_line.revision or not lr.request_line.revision.request:
        abort(404)
    require_can_view(req)

    # Lock after finalization
    if (req.current_status or "").upper() in FINAL_REQUEST_STATUSES:
        abort(403)

    # Enforce reviewer permissions (admin == finance)
    user_ctx = get_user_ctx()
    if not (user_ctx.is_admin or user_can_edit_line_review(lr, user_ctx=user_ctx)):
        abort(403)

    external_note = (request.form.get("external_admin_note") or "").strip()
    internal_note = (request.form.get("internal_admin_note") or "").strip()

    _apply_line_review_transition(
        lr=lr,
        action="REQUEST_INFO",
        note=external_note,
        internal_note=internal_note,
    )

    # Keep request status in sync
    if lr.request_line and lr.request_line.revision:
        h.recalc_request_status_from_lines(lr.request_line.revision)

    db.session.commit()

    rev_id = lr.request_line.revision_id
    return redirect(url_for("lines.revision_snapshot", revision_id=rev_id))

@lines_bp.get("/revisions/<int:revision_id>")
def revision_snapshot(revision_id: int):
    from ..models import RequestRevision, Request, RequestLine, LineReview, ApprovalGroup

    revision = db.session.get(RequestRevision, revision_id)
    if not revision:
        abort(404)

    req = revision.request
    require_can_view(req)

    lines = (
        db.session.query(RequestLine)
        .options(joinedload(RequestLine.budget_item_type))
        .filter(RequestLine.revision_id == revision.id)
        .order_by(RequestLine.id.asc())
        .all()
    )

    requested_total = sum(l.requested_amount for l in lines)

    line_ids = [l.id for l in lines]
    reviews = []
    if line_ids:
        reviews = (
            db.session.query(LineReview)
            .filter(LineReview.request_line_id.in_(line_ids))
            .all()
        )

    reviews_by_line_id = {}
    for r in reviews:
        reviews_by_line_id.setdefault(r.request_line_id, []).append(r)

    groups_by_id = {g.id: g for g in db.session.query(ApprovalGroup).all()}

    prev_revision = None
    prev_lines = []

    if revision.revision_number and revision.revision_number > 1:
        prev_revision = (
            db.session.query(RequestRevision)
            .filter(RequestRevision.request_id == req.id)
            .filter(RequestRevision.revision_number == (revision.revision_number - 1))
            .one_or_none()
        )

    if prev_revision:
        prev_lines = (
            db.session.query(RequestLine)
            .options(joinedload(RequestLine.budget_item_type))
            .filter(RequestLine.revision_id == prev_revision.id)
            .order_by(RequestLine.id.asc())
            .all()
        )

    curr_by_key = {}
    for l in lines:
        curr_by_key[_line_match_key(l)] = l

    prev_by_key = {}
    for l in prev_lines:
        prev_by_key[_line_match_key(l)] = l

    added_keys = [k for k in curr_by_key.keys() if k not in prev_by_key]
    removed_keys = [k for k in prev_by_key.keys() if k not in curr_by_key]
    common_keys = [k for k in curr_by_key.keys() if k in prev_by_key]

    added_lines = [curr_by_key[k] for k in added_keys]
    removed_lines = [prev_by_key[k] for k in removed_keys]

    changed_lines = []
    for k in common_keys:
        a = prev_by_key[k]
        b = curr_by_key[k]

        diffs = []

        if (a.requested_amount or 0) != (b.requested_amount or 0):
            diffs.append({
                "field": "Amount",
                "before": a.requested_amount or 0,
                "after": b.requested_amount or 0,
            })

        if (a.budget_item_type_id or None) != (b.budget_item_type_id or None):
            diffs.append({
                "field": "Item type",
                "before": a.budget_item_type.item_name if a.budget_item_type else "",
                "after": b.budget_item_type.item_name if b.budget_item_type else "",
            })

        if _norm(a.item_name) != _norm(b.item_name):
            diffs.append({"field": "Item name", "before": a.item_name or "", "after": b.item_name or ""})

        if _norm(a.description) != _norm(b.description):
            diffs.append({"field": "Description", "before": a.description or "", "after": b.description or ""})

        if _norm(a.justification) != _norm(b.justification):
            diffs.append(
                {"field": "Justification", "before": a.justification or "", "after": b.justification or ""})

        if diffs:
            changed_lines.append({
                "prev": a,
                "curr": b,
                "diffs": diffs,
            })

    prev_total = sum((l.requested_amount or 0) for l in prev_lines) if prev_lines else None

    user_ctx = get_user_ctx()
    perms = build_request_perms(req, user_ctx=user_ctx)

    return render_template(
        "requests/revision_snapshot.html",
        revision=revision,
        request=req,
        lines=lines,
        requested_total=requested_total,
        reviews_by_line_id=reviews_by_line_id,
        groups_by_id=groups_by_id,
        user_ctx=user_ctx,  # <-- new canonical context
        prev_revision=prev_revision,
        prev_total=prev_total,
        added_lines=added_lines,
        removed_lines=removed_lines,
        changed_lines=changed_lines,
        perms=perms,
    )

@lines_bp.post("/revisions/<int:revision_id>/approve-my-lines")
def approve_my_lines_for_revision(revision_id: int):
    from ..models import LineReview, RequestRevision, LineAuditEvent

    revision = db.session.get(RequestRevision, revision_id)
    if not revision:
        abort(404)

    req = revision.request
    require_can_view(req)

    # Lock after finalization
    if (req.current_status or "").upper() in FINAL_REQUEST_STATUSES:
        abort(403)

    # if not (h.is_admin() or h.is_finance()):
    #     group_ids = list(h.active_user_approval_group_ids() or [])
    #     if not group_ids:
    #         abort(403)
    # else:
    #     group_ids = None

    q = (
        db.session.query(LineReview)
        .join(LineReview.request_line)
        .filter(LineReview.status == "PENDING")
        .filter(LineReview.request_line.has(revision_id=revision_id))
    )
    # if group_ids is not None:
    #     q = q.filter(LineReview.approval_group_id.in_(group_ids))
    # if group_ids is not None:
    #     q = q.filter(LineReview.approval_group_id.in_(group_ids))

    reviews = q.all()

    if not reviews:
        return redirect(url_for("lines.revision_snapshot", revision_id=revision_id))

    bulk_note = "Approved (bulk)"

    for r in reviews:
        if (r.status or "PENDING").upper() != "PENDING":
            continue
        _apply_line_review_transition(lr=r, action="APPROVE", note=bulk_note)
        old_amount = r.approved_amount
        r.approved_amount = r.request_line.requested_amount or 0
        db.session.add(LineAuditEvent(
            request_line_id=r.request_line_id,
            event_type="APPROVED_AMOUNT_CHANGE",
            old_value=str(old_amount) if old_amount is not None else "",
            new_value=str(r.approved_amount),
            created_by_user_id=h.get_active_user_id(),
        ))

    db.session.commit()

    if hasattr(h, "recalc_request_status_from_lines"):
        h.recalc_request_status_from_lines(revision)
        db.session.commit()

    return redirect(url_for("lines.revision_snapshot", revision_id=revision_id))
