"""Enqueue side of the email outbox.

Notification KIND is not template KEY. Kinds are fixed at seven; template keys
grow with every work type, so dedup rules and required-entity rules key on the
kind and stay at seven entries permanently.
"""
import json
from datetime import datetime

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app import db
from app.models import EmailOutbox, EmailTemplate
from app.models.constants import (
    ENQUEUE_OUTCOME_CREATED,
    ENQUEUE_OUTCOME_DUPLICATE,
    OUTBOX_STATUS_QUEUED,
)

# "Once ever" is per SUBMISSION and per RELEASE, not per work item for all
# time. Both of these events legitimately recur on the same row: recall then
# resubmit re-sets work_item.submitted_at (lifecycle.py:113 nulls it, :59
# re-sets it), and unfinalize clears board_released_at
# (admin_final/helpers.py:697) precisely so a re-finalize notifies again.
# Keying on item id alone silently drops the second one, because the first
# row still owns the key.
_ONCE_PER_EVENT_KINDS = ("submission_confirmation", "finalized")


def resolve_template_key(kind: str, work_type_code: str | None) -> str:
    """Prefer a work-type-specific template row, fall back to the generic one.

    A missing work-type template falls back silently on purpose. Failing hard
    would break every work type that has not been customised yet.
    """
    if work_type_code:
        specific = f"{work_type_code.lower()}_{kind}"
        exists = db.session.query(EmailTemplate.id).filter_by(template_key=specific).first()
        if exists:
            return specific
    return kind


def build_dedup_key(kind, *, work_item_id=None, event_cycle_id=None,
                    department_id=None, recipient_email, now=None,
                    event_stamp=None) -> str | None:
    now = now or datetime.utcnow()
    if kind == "submission_reminder":
        day = now.strftime("%Y-%m-%d")
        return f"{kind}:{event_cycle_id}:{department_id}:{recipient_email}:{day}"
    if kind in _ONCE_PER_EVENT_KINDS:
        # discriminator is the timestamp of THIS submission or THIS release.
        # A duplicate click inside one event still collides; a genuine second
        # event produces a new key.
        stamp = event_stamp.isoformat() if event_stamp else "none"
        return f"{kind}:{work_item_id}:{recipient_email}:{stamp}"
    hour = now.strftime("%Y-%m-%d-%H")
    return f"{kind}:{work_item_id}:{recipient_email}:{hour}"


def enqueue_email(template_key, recipient_email, *, recipient_user_id=None,
                  work_item=None, event_cycle=None, department=None,
                  work_type=None, context=None, dedup_key=None,
                  dispatch_at=None) -> str:
    """Insert one outbox row. The caller's transaction owns it.

    Returns an outcome string, not a bool. Sub-project 2 adds DEFERRED,
    BLOCKED_WINDOW, and BLOCKED_INACTIVE; adding string values later is
    non-breaking, changing a return type across every caller is not.

    Does NOT commit. The row must land in the same transaction as the workflow
    change that caused it, which is the whole point of the outbox.
    """
    values = {
        "template_key": template_key,
        "recipient_email": recipient_email,
        "recipient_user_id": recipient_user_id,
        "work_item_id": work_item.id if work_item else None,
        "event_cycle_id": event_cycle.id if event_cycle else None,
        "department_id": department.id if department else None,
        "work_type_id": work_type.id if work_type else None,
        "context_json": json.dumps(context) if context else None,
        "dedup_key": dedup_key,
        "dispatch_at": dispatch_at or datetime.utcnow(),
        "status": OUTBOX_STATUS_QUEUED,
        "created_at": datetime.utcnow(),
        "attempt_count": 0,
    }

    if dedup_key is None:
        db.session.add(EmailOutbox(**values))
        return ENQUEUE_OUTCOME_CREATED

    # ON CONFLICT DO NOTHING rather than catching IntegrityError: on Postgres a
    # caught integrity error aborts the surrounding transaction, so a duplicate
    # email would roll back the approval that triggered it.
    # Flask-SQLAlchemy 3.1's scoped session leaves .bind unset; it resolves the
    # engine lazily through get_bind(). db.session.bind is None here and would
    # raise AttributeError before ever reaching the dialect check.
    table = EmailOutbox.__table__
    if db.session.get_bind().dialect.name == "postgresql":
        stmt = pg_insert(table).values(**values).on_conflict_do_nothing(
            index_elements=[table.c.dedup_key]
        )
    else:
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        stmt = sqlite_insert(table).values(**values).on_conflict_do_nothing(
            index_elements=[table.c.dedup_key]
        )
    result = db.session.execute(stmt)
    return ENQUEUE_OUTCOME_CREATED if result.rowcount else ENQUEUE_OUTCOME_DUPLICATE
