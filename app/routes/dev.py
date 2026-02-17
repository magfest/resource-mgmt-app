"""
Development routes for testing and debugging.
"""
from flask import Blueprint, render_template, redirect, url_for, request, session

from .. import db
from . import h

dev_bp = Blueprint('dev', __name__)


@dev_bp.get("/dev/login")
def dev_login():
    from ..models_old import User

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
    from ..models_old import User

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
    from ..models_old import Request

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



