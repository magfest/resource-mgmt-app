"""AV team review actions on an AV request.

Currently implements:
- publish_plan     (Task 31): AV team member publishes a Plan (rev 1).
  Transitions the single line's WorkLineReview from PENDING → LOGGED
  and creates an AVRequestPlan row at revision N.
- kickback         (Task 32): NEEDS_INFO / NEEDS_ADJUSTMENT
  Sets WorkLineReview.status to NEEDS_INFO or NEEDS_ADJUSTMENT with a note.
  No AVRequestPlan is created — this is a kickback, not a Plan publication.
- reject           (Task 33): REJECTED (terminal)
- revise           (Task 34): publish Plan rev N+1 (LOGGED → LOGGED)
  Relaxed eligibility check in av_publish_plan to also accept LOGGED state.

Design note — URL namespace:
These endpoints live under the dept's AV namespace
(/<event>/<dept>/av/item/<public_id>/...) rather than /approvals/...
because AV requests are authored at the dept level and the detail page
that hosts the AV-team action forms is also in this namespace.
"""
from __future__ import annotations

from datetime import datetime

from flask import flash, redirect, request, url_for

from app import db
from app.models import (
    ActivityEvent,
    Department,
    EventCycle,
    WorkItem,
    WorkLine,
    WorkLineAuditEvent,
    WorkPortfolio,
    WorkType,
    AUDIT_EVENT_REVIEW_DECISION,
    REVIEW_STATUS_PENDING,
)
from app.models.av import AVRequestPlan
from app.models.constants import (
    REVIEW_STATUS_LOGGED,
    REVIEW_STATUS_NEEDS_INFO,
    REVIEW_STATUS_NEEDS_ADJUSTMENT,
    REVIEW_STATUS_REJECTED,
    ACTIVITY_AV_PLAN_PUBLISHED,
    ACTIVITY_AV_KICKBACK,
    ACTIVITY_AV_REQUEST_REJECTED,
)
from app.routes import get_user_ctx
from app.routes.work.av.permissions import require_av_team_member
from .. import work_bp


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _next_plan_revision(work_item: WorkItem) -> int:
    """Return the next AVRequestPlan revision number for *work_item*.

    Extension point for Task 34 (revise plan):
    - Rev 1 (first publish, Task 31): no plans exist yet → returns 1.
    - Rev N+1 (revision, Task 34): max existing revision + 1.

    Task 34 only needs to relax the eligibility check; this helper already
    computes the correct next revision number.
    """
    existing_revisions = [p.revision for p in work_item.av_plans]
    return (max(existing_revisions) + 1) if existing_revisions else 1


def _get_av_work_item(event_code: str, dept_code: str, public_id: str) -> WorkItem:
    """Resolve an AV WorkItem from URL segments; 404 on any miss."""
    event = EventCycle.query.filter_by(code=event_code.upper()).first_or_404()
    dept = Department.query.filter_by(code=dept_code.upper()).first_or_404()
    av_wt = WorkType.query.filter_by(code="AV").first_or_404()

    portfolio = WorkPortfolio.query.filter_by(
        work_type_id=av_wt.id,
        event_cycle_id=event.id,
        department_id=dept.id,
        is_archived=False,
    ).first_or_404()

    work_item = (
        WorkItem.query
        .filter_by(
            public_id=public_id,
            portfolio_id=portfolio.id,
            is_archived=False,
        )
        .options(
            db.selectinload(WorkItem.lines)
                .selectinload(WorkLine.reviews),
            db.selectinload(WorkItem.av_plans),
        )
        .first_or_404()
    )
    return work_item


def _detail_url(event_code: str, dept_code: str, public_id: str) -> str:
    return url_for(
        "work.av_request_view",
        event=event_code,
        dept=dept_code,
        public_id=public_id,
    )


# ---------------------------------------------------------------------------
# Publish Plan (Task 31)
# ---------------------------------------------------------------------------

@work_bp.post("/<event>/<dept>/av/item/<public_id>/publish-plan")
def av_publish_plan(event: str, dept: str, public_id: str):
    """AV team member publishes a Plan for an AV request.

    Rev 1 (Task 31): requires WorkLineReview.status == PENDING.
    Rev N+1 (Task 34): also accepts LOGGED (revision case) — LOGGED → LOGGED.

    Transaction summary:
    1. Create AVRequestPlan at the next revision number.
    2. Mutate the existing WorkLineReview: PENDING/LOGGED → LOGGED.
    3. Sync WorkLine.status → "LOGGED".
    4. Write a WorkLineAuditEvent (REVIEW_DECISION).
    5. Write an ActivityEvent (AV_PLAN_PUBLISHED).
    6. Commit.
    """
    user_ctx = get_user_ctx()
    require_av_team_member(user_ctx)

    work_item = _get_av_work_item(event, dept, public_id)
    detail_url = _detail_url(event, dept, public_id)

    # ── Eligibility: WorkItem must be SUBMITTED ───────────────────────────
    if work_item.status != "SUBMITTED":
        flash(
            f"{public_id} is not in SUBMITTED status; cannot publish a Plan.",
            "error",
        )
        return redirect(detail_url)

    # ── Resolve single line + latest review ──────────────────────────────
    if not work_item.lines:
        flash(f"{public_id} has no lines; cannot publish a Plan.", "error")
        return redirect(detail_url)

    line = work_item.lines[0]
    latest_review = max(line.reviews, key=lambda r: r.id) if line.reviews else None

    # ── Eligibility: review must be PENDING (rev 1) or LOGGED (revision) ───
    if latest_review is None or latest_review.status not in (
        REVIEW_STATUS_PENDING, REVIEW_STATUS_LOGGED
    ):
        flash(
            f"{public_id} is not in a state where a Plan can be published or revised.",
            "error",
        )
        return redirect(detail_url)

    # ── Validate form input ───────────────────────────────────────────────
    gear_spec = (request.form.get("gear_spec") or "").strip()
    if not gear_spec:
        flash("Gear spec is required.", "error")
        return redirect(detail_url)

    planning_notes = (request.form.get("planning_notes") or "").strip() or None

    # ── Compute next revision (extension point for Task 34) ───────────────
    next_revision = _next_plan_revision(work_item)

    # ── 1. Create AVRequestPlan ───────────────────────────────────────────
    plan = AVRequestPlan(
        work_item_id=work_item.id,
        revision=next_revision,
        gear_spec=gear_spec,
        planning_notes=planning_notes,
        authored_by_user_id=user_ctx.user_id,
    )
    db.session.add(plan)

    # ── 2+3. Transition WorkLineReview + WorkLine to LOGGED ───────────────
    # Pattern: mutate the existing review row (same as budget engine).
    old_status = latest_review.status
    latest_review.status = REVIEW_STATUS_LOGGED
    latest_review.decided_at = datetime.utcnow()
    latest_review.decided_by_user_id = user_ctx.user_id
    # note: no free-text note for plan publishing — the plan itself is the record.

    line.status = REVIEW_STATUS_LOGGED  # "LOGGED" (AV-specific line status)
    line.status_changed_at = datetime.utcnow()
    line.status_changed_by_user_id = user_ctx.user_id

    # ── 4. WorkLine audit event ───────────────────────────────────────────
    audit_event = WorkLineAuditEvent(
        work_line_id=line.id,
        event_type=AUDIT_EVENT_REVIEW_DECISION,
        field_name="status",
        old_value=old_status,
        new_value=REVIEW_STATUS_LOGGED,
        note=f"Plan rev {next_revision} published",
        created_by_user_id=user_ctx.user_id,
    )
    db.session.add(audit_event)

    # ── 5. ActivityEvent (AV_PLAN_PUBLISHED) ──────────────────────────────
    activity = ActivityEvent(
        event_type=ACTIVITY_AV_PLAN_PUBLISHED,
        work_item_id=work_item.id,
        space_id=work_item.av_request_detail.space_id,
        actor_user_id=user_ctx.user_id,
    )
    db.session.add(activity)

    # ── 6. Commit ─────────────────────────────────────────────────────────
    db.session.commit()

    flash(
        f"Plan rev {next_revision} published for {public_id}.",
        "success",
    )
    return redirect(detail_url)


# ---------------------------------------------------------------------------
# Kickback actions (Task 32): NEEDS_INFO / NEEDS_ADJUSTMENT
# ---------------------------------------------------------------------------

@work_bp.post("/<event>/<dept>/av/item/<public_id>/needs-info")
def av_kickback_needs_info(event: str, dept: str, public_id: str):
    """AV team member sends a NEEDS_INFO kickback to the requesting dept."""
    return _do_av_kickback(event, dept, public_id, REVIEW_STATUS_NEEDS_INFO)


@work_bp.post("/<event>/<dept>/av/item/<public_id>/needs-adjustment")
def av_kickback_needs_adjustment(event: str, dept: str, public_id: str):
    """AV team member sends a NEEDS_ADJUSTMENT kickback to the requesting dept."""
    return _do_av_kickback(event, dept, public_id, REVIEW_STATUS_NEEDS_ADJUSTMENT)


def _do_av_kickback(event: str, dept: str, public_id: str, target_status: str):
    """Shared handler for NEEDS_INFO and NEEDS_ADJUSTMENT kickbacks.

    Transaction summary:
    1. Validate eligibility (SUBMITTED + PENDING review).
    2. Validate that a note was provided.
    3. Mutate the existing PENDING WorkLineReview → target_status + note.
    4. Sync WorkLine.status and set needs_requester_action=True.
    5. Write a WorkLineAuditEvent (REVIEW_DECISION).
    6. Write an ActivityEvent (AV_KICKBACK).
    7. Commit.
    """
    user_ctx = get_user_ctx()
    require_av_team_member(user_ctx)

    work_item = _get_av_work_item(event, dept, public_id)
    detail_url = _detail_url(event, dept, public_id)

    # ── Eligibility: WorkItem must be SUBMITTED ───────────────────────────
    if work_item.status != "SUBMITTED":
        flash(
            f"{public_id} is not in SUBMITTED status; cannot send a kickback.",
            "error",
        )
        return redirect(detail_url)

    # ── Resolve single line + latest review ──────────────────────────────
    if not work_item.lines:
        flash(f"{public_id} has no lines; cannot send a kickback.", "error")
        return redirect(detail_url)

    line = work_item.lines[0]
    latest_review = max(line.reviews, key=lambda r: r.id) if line.reviews else None

    # ── Eligibility: review must be PENDING ──────────────────────────────
    if latest_review is None or latest_review.status != REVIEW_STATUS_PENDING:
        flash(
            f"{public_id} is not awaiting AV team action (review must be PENDING).",
            "error",
        )
        return redirect(detail_url)

    # ── Validate form input ───────────────────────────────────────────────
    note = (request.form.get("note") or "").strip()
    if not note:
        flash("A note is required when sending a kickback.", "error")
        return redirect(detail_url)

    # ── 3. Mutate the existing PENDING review ────────────────────────────
    old_status = latest_review.status
    latest_review.status = target_status
    latest_review.note = note
    latest_review.decided_at = datetime.utcnow()
    latest_review.decided_by_user_id = user_ctx.user_id

    # ── 4. Sync WorkLine status + flag requester action needed ───────────
    line.status = target_status
    line.status_changed_at = datetime.utcnow()
    line.status_changed_by_user_id = user_ctx.user_id
    line.needs_requester_action = True

    # ── 5. WorkLine audit event ───────────────────────────────────────────
    label = "needs more info" if target_status == REVIEW_STATUS_NEEDS_INFO else "needs adjustment"
    audit_event = WorkLineAuditEvent(
        work_line_id=line.id,
        event_type=AUDIT_EVENT_REVIEW_DECISION,
        field_name="status",
        old_value=old_status,
        new_value=target_status,
        note=note,
        created_by_user_id=user_ctx.user_id,
    )
    db.session.add(audit_event)

    # ── 6. ActivityEvent (AV_KICKBACK) ────────────────────────────────────
    activity = ActivityEvent(
        event_type=ACTIVITY_AV_KICKBACK,
        work_item_id=work_item.id,
        space_id=work_item.av_request_detail.space_id,
        actor_user_id=user_ctx.user_id,
    )
    db.session.add(activity)

    # ── 7. Commit ─────────────────────────────────────────────────────────
    db.session.commit()

    flash(
        f"{public_id} marked as {label}. The requesting department has been notified.",
        "success",
    )
    return redirect(detail_url)


# ---------------------------------------------------------------------------
# Reject action (Task 33): REJECTED (terminal)
# ---------------------------------------------------------------------------

@work_bp.post("/<event>/<dept>/av/item/<public_id>/reject")
def av_reject_request(event: str, dept: str, public_id: str):
    """AV team member rejects an AV request (terminal decision).

    REJECTED is terminal — the request can never be incorporated into a Space
    scope. A note is required so the AV team explains why.

    Transaction summary:
    1. Validate eligibility (SUBMITTED + PENDING review).
    2. Validate that a note was provided.
    3. Mutate the existing PENDING WorkLineReview → REJECTED + note.
    4. Sync WorkLine.status (no needs_requester_action — terminal, no response).
    5. Write a WorkLineAuditEvent (REVIEW_DECISION).
    6. Write an ActivityEvent (AV_REQUEST_REJECTED).
    7. Commit.
    """
    user_ctx = get_user_ctx()
    require_av_team_member(user_ctx)

    work_item = _get_av_work_item(event, dept, public_id)
    detail_url = _detail_url(event, dept, public_id)

    # ── Eligibility: WorkItem must be SUBMITTED ───────────────────────────
    if work_item.status != "SUBMITTED":
        flash(
            f"{public_id} is not in SUBMITTED status; cannot reject.",
            "error",
        )
        return redirect(detail_url)

    # ── Resolve single line + latest review ──────────────────────────────
    if not work_item.lines:
        flash(f"{public_id} has no lines; cannot reject.", "error")
        return redirect(detail_url)

    line = work_item.lines[0]
    latest_review = max(line.reviews, key=lambda r: r.id) if line.reviews else None

    # ── Eligibility: review must be PENDING ──────────────────────────────
    if latest_review is None or latest_review.status != REVIEW_STATUS_PENDING:
        flash(
            f"{public_id} is not awaiting AV team action (review must be PENDING).",
            "error",
        )
        return redirect(detail_url)

    # ── Validate form input ───────────────────────────────────────────────
    note = (request.form.get("note") or "").strip()
    if not note:
        flash("A note is required when rejecting a request.", "error")
        return redirect(detail_url)

    # ── 3. Mutate the existing PENDING review → REJECTED ─────────────────
    old_status = latest_review.status
    latest_review.status = REVIEW_STATUS_REJECTED
    latest_review.note = note
    latest_review.decided_at = datetime.utcnow()
    latest_review.decided_by_user_id = user_ctx.user_id
    # Reject is terminal — do NOT set needs_requester_action (no response expected)

    # ── 4. Sync WorkLine status ───────────────────────────────────────────
    line.status = REVIEW_STATUS_REJECTED
    line.status_changed_at = datetime.utcnow()
    line.status_changed_by_user_id = user_ctx.user_id
    # needs_requester_action intentionally left unchanged (terminal action)

    # ── 5. WorkLine audit event ───────────────────────────────────────────
    audit_event = WorkLineAuditEvent(
        work_line_id=line.id,
        event_type=AUDIT_EVENT_REVIEW_DECISION,
        field_name="status",
        old_value=old_status,
        new_value=REVIEW_STATUS_REJECTED,
        note=note,
        created_by_user_id=user_ctx.user_id,
    )
    db.session.add(audit_event)

    # ── 6. ActivityEvent (AV_REQUEST_REJECTED) ────────────────────────────
    activity = ActivityEvent(
        event_type=ACTIVITY_AV_REQUEST_REJECTED,
        work_item_id=work_item.id,
        space_id=work_item.av_request_detail.space_id,
        actor_user_id=user_ctx.user_id,
    )
    db.session.add(activity)

    # ── 7. Commit ─────────────────────────────────────────────────────────
    db.session.commit()

    flash(f"{public_id} rejected.", "success")
    return redirect(detail_url)
