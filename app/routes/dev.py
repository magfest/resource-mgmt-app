"""
Development routes for testing and debugging.
"""
from datetime import datetime, timedelta

from flask import Blueprint, render_template, redirect, url_for, request, session, flash

from .. import db
from . import h

dev_bp = Blueprint('dev', __name__)


@dev_bp.get("/dev/login")
def dev_login():
    from ..models import User

    h.ensure_demo_users()

    users = (
        db.session.query(User)
        .filter(User.is_active == True)  # noqa: E712
        .order_by(User.display_name.asc())
        .all()
    )

    return render_template(
        "requests/dev_login.html",
        users=users,
        current_user_id=h.get_active_user_id(),
    )


@dev_bp.post("/dev/login")
def dev_login_post():
    from ..models import User

    h.ensure_demo_users()

    chosen = (request.form.get("user_id") or "").strip()
    if not chosen:
        return redirect(url_for("dev.dev_login"))

    u = db.session.get(User, chosen)
    if not u or not u.is_active:
        return "Unknown or inactive user", 400

    session["active_user_id"] = u.id
    return redirect(url_for("dev.dev_login"))


@dev_bp.post("/dev/create-sample-request")
def dev_create_sample_request():
    from ..models import Request

    r = Request(
        event_cycle="Super MAGFest 2026",
        requesting_department="TechOps",
        created_by_user_id=h.get_active_user_id(),
    )
    db.session.add(r)
    db.session.commit()
    return {"ok": True, "id": r.id}


@dev_bp.post("/dev/create-sample-request-with-revision")
def dev_create_sample_request_with_revision():
    from ..models_old import Request, RequestRevision

    r = Request(
        event_cycle="Super MAGFest 2026",
        requesting_department="TechOps",
        created_by_user_id=h.get_active_user_id(),
        current_status="SUBMITTED",
    )
    db.session.add(r)
    db.session.flush()

    rev = RequestRevision(
        request_id=r.id,
        revision_number=1,
        submitted_by_user_id=h.get_active_user_id(),
        revision_note="Initial submission",
        status_at_submission="SUBMITTED",
    )
    db.session.add(rev)
    db.session.flush()

    r.current_revision_id = rev.id
    db.session.commit()

    return {
        "ok": True,
        "request_id": r.id,
        "revision_id": rev.id,
        "current_revision_id": r.current_revision_id,
    }


@dev_bp.post("/dev/create-techops-sample-v1")
def dev_create_techops_sample_v1():
    from ..models_old import Request, RequestRevision, RequestLine

    r = Request(
        event_cycle="Super MAGFest 2026",
        requesting_department="TechOps",
        created_by_user_id=h.get_active_user_id(),
        current_status="SUBMITTED",
    )
    db.session.add(r)
    db.session.flush()

    rev = RequestRevision(
        request_id=r.id,
        revision_number=1,
        submitted_by_user_id=h.get_active_user_id(),
        revision_note="TechOps sample based on FY25 lines",
        status_at_submission="SUBMITTED",
    )
    db.session.add(rev)
    db.session.flush()

    lines = [
        ("Tech Equipment", 38000, "Critical (we cannot operate without this)",
         "We are requesting apx 30% increase over last year's budgeted amount to account for the current economic conditions and other uncertainty related to supply chain changes."),
        ("Equipment Rental", 8000, "Critical (we cannot operate without this)", "Radios"),
        ("Equipment Rental", 15000, "Critical (we cannot operate without this)",
         "iPads, Laptops, and other Hartford equipment"),
        ("Supplies", 5000, "Critical (we cannot operate without this)",
         "Office Supplies (assuming this comes under FestOps now?)"),
        ("Food Stuffs", 4000, "Critical (we cannot operate without this)",
         "Water/Gatorade (assuming this comes under FestOps now?)"),
        ("Printing & Copying", 2500, "Critical (we cannot operate without this)",
         "Bulk Printing (assuming this comes under FestOps now?)"),
        ("Truck Rental", 1000, "Medium",
         "Week long panel van rental for twice daily supply runs (assuming this comes under FestOps now?)"),
        ("Venue Rental Fees/Tips", 35000, "Critical (we cannot operate without this)",
         "Misc. Hotel Costs (Ethernet Drops, Power costs, etc.) - NOTE: BOPS now should have a line for all digital signage, thus not included here"),
    ]

    for category, amount, priority_text, details in lines:
        db.session.add(RequestLine(
            revision_id=rev.id,
            category=category,
            description=details,
            requested_amount=amount,
            justification=priority_text,
        ))

    r.current_revision_id = rev.id
    db.session.commit()

    return {
        "ok": True,
        "request_id": r.id,
        "revision_id": rev.id,
        "lines_created": len(lines),
    }


@dev_bp.get("/dev/requests/<int:request_id>/debug")
def dev_request_debug(request_id: int):
    h.ensure_demo_users()

    def iso(dt):
        try:
            return dt.isoformat() if dt else None
        except Exception:
            return None

    def safe_int(x, default=None):
        try:
            return int(x)
        except Exception:
            return default

    from ..models_old import (
        Request,
        RequestDraft,
        DraftLine,
        RequestRevision,
        RequestLine,
        LineReview,
    )

    req = db.session.get(Request, request_id)
    if not req:
        return {"error": "Request not found"}, 404

    Department = None
    EventCycle = None
    try:
        from ..models_old import Department, EventCycle  # type: ignore
    except Exception:
        pass

    department_id = getattr(req, "department_id", None)
    event_cycle_id = getattr(req, "event_cycle_id", None)

    dept = db.session.get(Department, department_id) if Department and department_id else None
    cycle = db.session.get(EventCycle, event_cycle_id) if EventCycle and event_cycle_id else None

    draft = (
        db.session.query(RequestDraft)
        .filter(RequestDraft.request_id == req.id)
        .one_or_none()
    )

    draft_lines = []
    if draft:
        draft_lines = (
            db.session.query(DraftLine)
            .filter(DraftLine.draft_id == draft.id)
            .all()
        )

    def draft_line_amount(l):
        return safe_int(getattr(l, "requested_amount", 0), 0) or 0

    revisions = (
        db.session.query(RequestRevision)
        .filter(RequestRevision.request_id == req.id)
        .order_by(RequestRevision.revision_number.asc())
        .all()
    )

    revision_summaries = []
    for rev in revisions:
        lines = (
            db.session.query(RequestLine)
            .filter(RequestLine.revision_id == rev.id)
            .all()
        )
        reviews = (
            db.session.query(LineReview)
            .join(RequestLine, LineReview.request_line_id == RequestLine.id)
            .filter(RequestLine.revision_id == rev.id)
            .all()
        )

        ts = getattr(rev, "submitted_at", None) or getattr(rev, "created_at", None)

        revision_summaries.append(
            {
                "revision_id": rev.id,
                "revision_number": rev.revision_number,
                "submitted_at": iso(ts),
                "submitted_by_user_id": getattr(rev, "submitted_by_user_id", None),
                "line_count": len(lines),
                "review_count": len(reviews),
            }
        )

    status = getattr(req, "current_status", None)

    invariants = {
        "has_department_fk": department_id is not None,
        "has_event_cycle_fk": event_cycle_id is not None,
        "legacy_department_matches_fk": (
                dept is not None and getattr(req, "requesting_department", None) == getattr(dept, "name", None)
        ),
        "legacy_event_cycle_matches_fk": (
                cycle is not None and getattr(req, "event_cycle", None) == getattr(cycle, "name", None)
        ),
        "draft_exists_when_editable": (status in ("DRAFT", "NEEDS_REVISION") and draft is not None),
    }

    health = "OK" if all(v is True for v in invariants.values()) else "WARN"

    return {
        "health": health,
        "request": {
            "id": req.id,
            "status": status,
            "created_at": iso(getattr(req, "created_at", None)),
            "created_by_user_id": getattr(req, "created_by_user_id", None),

            "department_id": department_id,
            "department_name_fk": getattr(dept, "name", None) if dept else None,
            "event_cycle_id": event_cycle_id,
            "event_cycle_name_fk": getattr(cycle, "name", None) if cycle else None,

            "department_name_legacy": getattr(req, "requesting_department", None),
            "event_cycle_name_legacy": getattr(req, "event_cycle", None),

            "current_revision_id": getattr(req, "current_revision_id", None),
            "approved_revision_id": getattr(req, "approved_revision_id", None),
            "kickback_reason": getattr(req, "kickback_reason", None),
        },
        "draft": {
            "exists": bool(draft),
            "draft_id": draft.id if draft else None,
            "updated_at": iso(getattr(draft, "updated_at", None)) if draft else None,
            "draft_line_count": len(draft_lines),
            "draft_total": sum(draft_line_amount(l) for l in draft_lines),
        },
        "revisions": revision_summaries,
        "invariants": invariants,
    }

@dev_bp.get("/dev/users")
def dev_users():
    from ..models_old import User

    users = (
        db.session.query(User)
        .order_by(User.display_name.asc(), User.id.asc())
        .all()
    )

    return render_template(
        "requests/dev_users.html",
        users=users,
    )


# ============================================================
# Chunk C Test Routes - Supplementary, Checkout, NEEDS_INFO
# ============================================================

@dev_bp.get("/dev/test-chunk-c")
def test_chunk_c():
    """Test dashboard for Chunk C features."""
    from ..models import (
        WorkItem, WorkPortfolio, Department, EventCycle, User,
        WORK_ITEM_STATUS_DRAFT, WORK_ITEM_STATUS_SUBMITTED, WORK_ITEM_STATUS_FINALIZED,
        WORK_ITEM_STATUS_NEEDS_INFO, REQUEST_KIND_PRIMARY, REQUEST_KIND_SUPPLEMENTARY,
    )

    h.ensure_demo_users()

    # Get test data
    event = db.session.query(EventCycle).filter_by(code="SMF2026").first()
    dept = db.session.query(Department).filter_by(code="ARCADE").first()

    portfolio = None
    primary_item = None
    supplementary_items = []
    checked_out_items = []
    needs_info_items = []

    if event and dept:
        from ..models import WorkType
        work_type = db.session.query(WorkType).filter_by(code="BUDGET").first()
        if work_type:
            portfolio = db.session.query(WorkPortfolio).filter_by(
                work_type_id=work_type.id,
                event_cycle_id=event.id,
                department_id=dept.id,
                is_archived=False,
            ).first()

            if portfolio:
                primary_item = db.session.query(WorkItem).filter_by(
                    portfolio_id=portfolio.id,
                    request_kind=REQUEST_KIND_PRIMARY,
                    is_archived=False,
                ).first()

                supplementary_items = db.session.query(WorkItem).filter_by(
                    portfolio_id=portfolio.id,
                    request_kind=REQUEST_KIND_SUPPLEMENTARY,
                    is_archived=False,
                ).all()

        # Get all checked out items
        now = datetime.utcnow()
        checked_out_items = db.session.query(WorkItem).filter(
            WorkItem.checked_out_by_user_id.isnot(None),
            WorkItem.checked_out_expires_at > now,
        ).all()

        # Get all NEEDS_INFO items
        needs_info_items = db.session.query(WorkItem).filter_by(
            status=WORK_ITEM_STATUS_NEEDS_INFO,
        ).all()

    # Get users for quick login
    users = db.session.query(User).filter(
        User.is_active == True,
        User.id.in_(['dev:alex', 'dev:admin', 'dev:tech_approver'])
    ).all()

    return render_template(
        "dev/test_chunk_c.html",
        event=event,
        dept=dept,
        portfolio=portfolio,
        primary_item=primary_item,
        supplementary_items=supplementary_items,
        checked_out_items=checked_out_items,
        needs_info_items=needs_info_items,
        users=users,
        current_user_id=h.get_active_user_id(),
    )


@dev_bp.post("/dev/test-chunk-c/create-primary-with-lines")
def test_create_primary_with_lines():
    """Create a PRIMARY work item with budget lines for testing."""
    from ..models import (
        WorkItem, WorkLine, WorkPortfolio, Department, EventCycle, WorkType,
        BudgetLineDetail, ExpenseAccount, SpendType,
        WORK_ITEM_STATUS_DRAFT, REQUEST_KIND_PRIMARY, WORK_LINE_STATUS_PENDING,
    )

    h.ensure_demo_users()
    h.ensure_demo_budget_data()

    event = db.session.query(EventCycle).filter_by(code="SMF2026").first()
    dept = db.session.query(Department).filter_by(code="ARCADE").first()
    work_type = db.session.query(WorkType).filter_by(code="BUDGET").first()

    if not event or not dept or not work_type:
        flash("Missing demo data (event, dept, or work type)", "error")
        return redirect(url_for("dev.test_chunk_c"))

    # Get or create portfolio
    portfolio = db.session.query(WorkPortfolio).filter_by(
        work_type_id=work_type.id,
        event_cycle_id=event.id,
        department_id=dept.id,
        is_archived=False,
    ).first()

    if not portfolio:
        portfolio = WorkPortfolio(
            work_type_id=work_type.id,
            event_cycle_id=event.id,
            department_id=dept.id,
            created_by_user_id=h.get_active_user_id(),
        )
        db.session.add(portfolio)
        db.session.flush()

    # Check for existing PRIMARY
    existing = db.session.query(WorkItem).filter_by(
        portfolio_id=portfolio.id,
        request_kind=REQUEST_KIND_PRIMARY,
        is_archived=False,
    ).first()

    if existing:
        flash(f"PRIMARY already exists: {existing.public_id}", "warning")
        return redirect(url_for("dev.test_chunk_c"))

    # Generate public ID
    import secrets
    public_id = f"BUD-{''.join(secrets.token_urlsafe(4).upper().replace('-', '').replace('_', '')[:6])}"

    # Create work item
    work_item = WorkItem(
        portfolio_id=portfolio.id,
        request_kind=REQUEST_KIND_PRIMARY,
        status=WORK_ITEM_STATUS_DRAFT,
        public_id=public_id,
        created_by_user_id=h.get_active_user_id(),
    )
    db.session.add(work_item)
    db.session.flush()

    # Get expense accounts and spend types
    expense_accounts = db.session.query(ExpenseAccount).filter_by(is_active=True).limit(3).all()
    default_spend_type = db.session.query(SpendType).filter_by(code="DIVVY").first()

    if not expense_accounts:
        flash("No expense accounts found. Run demo data seed.", "error")
        return redirect(url_for("dev.test_chunk_c"))

    # Create sample lines
    for i, acc in enumerate(expense_accounts, start=1):
        line = WorkLine(
            work_item_id=work_item.id,
            line_number=i,
            status=WORK_LINE_STATUS_PENDING,
            updated_by_user_id=h.get_active_user_id(),
        )
        db.session.add(line)
        db.session.flush()

        spend_type = acc.default_spend_type or default_spend_type
        detail = BudgetLineDetail(
            work_line_id=line.id,
            expense_account_id=acc.id,
            spend_type_id=spend_type.id if spend_type else None,
            unit_price_cents=acc.default_unit_price_cents or (1000 * (i + 1)),
            quantity=i * 2,
            description=f"Test line {i} - {acc.name}",
        )
        db.session.add(detail)

    db.session.commit()
    flash(f"Created PRIMARY {public_id} with {len(expense_accounts)} lines", "success")
    return redirect(url_for("dev.test_chunk_c"))


@dev_bp.post("/dev/test-chunk-c/submit-primary")
def test_submit_primary():
    """Submit the PRIMARY work item."""
    from ..models import (
        WorkItem, WorkPortfolio, Department, EventCycle, WorkType,
        WORK_ITEM_STATUS_DRAFT, WORK_ITEM_STATUS_SUBMITTED, REQUEST_KIND_PRIMARY,
        REVIEW_STAGE_APPROVAL_GROUP,
    )

    event = db.session.query(EventCycle).filter_by(code="SMF2026").first()
    dept = db.session.query(Department).filter_by(code="ARCADE").first()
    work_type = db.session.query(WorkType).filter_by(code="BUDGET").first()

    if not event or not dept or not work_type:
        flash("Missing demo data", "error")
        return redirect(url_for("dev.test_chunk_c"))

    portfolio = db.session.query(WorkPortfolio).filter_by(
        work_type_id=work_type.id,
        event_cycle_id=event.id,
        department_id=dept.id,
        is_archived=False,
    ).first()

    if not portfolio:
        flash("No portfolio found", "error")
        return redirect(url_for("dev.test_chunk_c"))

    primary = db.session.query(WorkItem).filter_by(
        portfolio_id=portfolio.id,
        request_kind=REQUEST_KIND_PRIMARY,
        is_archived=False,
    ).first()

    if not primary:
        flash("No PRIMARY found", "error")
        return redirect(url_for("dev.test_chunk_c"))

    if primary.status != WORK_ITEM_STATUS_DRAFT:
        flash(f"PRIMARY is not DRAFT (status: {primary.status})", "warning")
        return redirect(url_for("dev.test_chunk_c"))

    # Submit it
    primary.status = WORK_ITEM_STATUS_SUBMITTED
    primary.submitted_at = datetime.utcnow()
    primary.submitted_by_user_id = h.get_active_user_id()

    # Snapshot routing
    for line in primary.lines:
        if line.budget_detail:
            detail = line.budget_detail
            if detail.expense_account and detail.expense_account.approval_group_id:
                detail.routed_approval_group_id = detail.expense_account.approval_group_id
            line.current_review_stage = REVIEW_STAGE_APPROVAL_GROUP

    db.session.commit()
    flash(f"Submitted PRIMARY {primary.public_id}", "success")
    return redirect(url_for("dev.test_chunk_c"))


@dev_bp.post("/dev/test-chunk-c/finalize-primary")
def test_finalize_primary():
    """Finalize the PRIMARY work item (skip review for testing)."""
    from ..models import (
        WorkItem, WorkPortfolio, Department, EventCycle, WorkType,
        WORK_ITEM_STATUS_SUBMITTED, WORK_ITEM_STATUS_FINALIZED, REQUEST_KIND_PRIMARY,
    )

    event = db.session.query(EventCycle).filter_by(code="SMF2026").first()
    dept = db.session.query(Department).filter_by(code="ARCADE").first()
    work_type = db.session.query(WorkType).filter_by(code="BUDGET").first()

    if not event or not dept or not work_type:
        flash("Missing demo data", "error")
        return redirect(url_for("dev.test_chunk_c"))

    portfolio = db.session.query(WorkPortfolio).filter_by(
        work_type_id=work_type.id,
        event_cycle_id=event.id,
        department_id=dept.id,
        is_archived=False,
    ).first()

    if not portfolio:
        flash("No portfolio found", "error")
        return redirect(url_for("dev.test_chunk_c"))

    primary = db.session.query(WorkItem).filter_by(
        portfolio_id=portfolio.id,
        request_kind=REQUEST_KIND_PRIMARY,
        is_archived=False,
    ).first()

    if not primary:
        flash("No PRIMARY found", "error")
        return redirect(url_for("dev.test_chunk_c"))

    if primary.status == WORK_ITEM_STATUS_FINALIZED:
        flash("PRIMARY is already FINALIZED", "warning")
        return redirect(url_for("dev.test_chunk_c"))

    # Finalize it (skip review for testing)
    primary.status = WORK_ITEM_STATUS_FINALIZED
    primary.finalized_at = datetime.utcnow()
    primary.finalized_by_user_id = h.get_active_user_id()

    db.session.commit()
    flash(f"Finalized PRIMARY {primary.public_id} (test mode - skipped review)", "success")
    return redirect(url_for("dev.test_chunk_c"))


@dev_bp.post("/dev/test-chunk-c/checkout-item/<public_id>")
def test_checkout_item(public_id: str):
    """Checkout a work item for testing."""
    from ..models import WorkItem, WORK_ITEM_STATUS_SUBMITTED
    from .budget.helpers import get_checkout_timeout_minutes
    from . import get_user_ctx

    work_item = db.session.query(WorkItem).filter_by(public_id=public_id).first()
    if not work_item:
        flash(f"Work item not found: {public_id}", "error")
        return redirect(url_for("dev.test_chunk_c"))

    if work_item.status != WORK_ITEM_STATUS_SUBMITTED:
        flash(f"Work item must be SUBMITTED (status: {work_item.status})", "warning")
        return redirect(url_for("dev.test_chunk_c"))

    if work_item.checked_out_by_user_id:
        flash(f"Already checked out by {work_item.checked_out_by_user_id}", "warning")
        return redirect(url_for("dev.test_chunk_c"))

    user_ctx = get_user_ctx()
    timeout_minutes = get_checkout_timeout_minutes(user_ctx)
    now = datetime.utcnow()

    work_item.checked_out_by_user_id = user_ctx.user_id
    work_item.checked_out_at = now
    work_item.checked_out_expires_at = now + timedelta(minutes=timeout_minutes)

    db.session.commit()
    flash(f"Checked out {public_id} (expires in {timeout_minutes} minutes)", "success")
    return redirect(url_for("dev.test_chunk_c"))


@dev_bp.post("/dev/test-chunk-c/set-needs-info/<public_id>")
def test_set_needs_info(public_id: str):
    """Set a work item to NEEDS_INFO for testing."""
    from ..models import WorkItem, WorkLineComment, WORK_ITEM_STATUS_NEEDS_INFO, COMMENT_VISIBILITY_PUBLIC

    work_item = db.session.query(WorkItem).filter_by(public_id=public_id).first()
    if not work_item:
        flash(f"Work item not found: {public_id}", "error")
        return redirect(url_for("dev.test_chunk_c"))

    # Add a test comment
    if work_item.lines:
        comment = WorkLineComment(
            work_line_id=work_item.lines[0].id,
            visibility=COMMENT_VISIBILITY_PUBLIC,
            body="[INFO REQUESTED] Test info request - please provide vendor quotes.",
            created_by_user_id=h.get_active_user_id(),
        )
        db.session.add(comment)

    work_item.status = WORK_ITEM_STATUS_NEEDS_INFO
    work_item.needs_info_requested_at = datetime.utcnow()
    work_item.needs_info_requested_by_user_id = h.get_active_user_id()

    # Clear checkout
    work_item.checked_out_by_user_id = None
    work_item.checked_out_at = None
    work_item.checked_out_expires_at = None

    db.session.commit()
    flash(f"Set {public_id} to NEEDS_INFO", "success")
    return redirect(url_for("dev.test_chunk_c"))


@dev_bp.post("/dev/test-chunk-c/reset-all")
def test_reset_all():
    """Reset all test data for Chunk C."""
    from ..models import WorkItem, WorkLine, BudgetLineDetail, WorkPortfolio, Department, EventCycle, WorkType

    event = db.session.query(EventCycle).filter_by(code="SMF2026").first()
    dept = db.session.query(Department).filter_by(code="ARCADE").first()
    work_type = db.session.query(WorkType).filter_by(code="BUDGET").first()

    if event and dept and work_type:
        portfolio = db.session.query(WorkPortfolio).filter_by(
            work_type_id=work_type.id,
            event_cycle_id=event.id,
            department_id=dept.id,
        ).first()

        if portfolio:
            # Delete all work items in this portfolio
            items = db.session.query(WorkItem).filter_by(portfolio_id=portfolio.id).all()
            for item in items:
                for line in item.lines:
                    if line.budget_detail:
                        db.session.delete(line.budget_detail)
                    for comment in line.comments:
                        db.session.delete(comment)
                    db.session.delete(line)
                db.session.delete(item)
            db.session.commit()
            flash(f"Deleted {len(items)} work item(s) from test portfolio", "success")
        else:
            flash("No portfolio found to reset", "info")
    else:
        flash("Missing demo data", "error")

    return redirect(url_for("dev.test_chunk_c"))



