"""Who changed a unit's codes, condition or item type at a laptop, and when.

Stock changes are already recorded as movements; these corrections move nothing,
so they are written to config_audit_events with entity type "inv_unit". The
changes JSON uses the {"field": {"old": ..., "new": ...}} shape the admin pages use.
"""
from __future__ import annotations

import json

from app import db
from app.models import ConfigAuditEvent

ENTITY_UNIT = "inv_unit"
ACTION_ADD_CODE = "ADD_CODE"
ACTION_RETIRE_CODE = "RETIRE_CODE"
ACTION_CONDITION = "CONDITION"
ACTION_ITEM = "ITEM"
ACTION_NAME = "NAME"
ACTION_NOTES = "NOTES"
ACTION_FIELDS = "FIELDS"
ACTION_TAG_LINK = "TAG_LINK"
ACTION_TAG_UNDO = "TAG_UNDO"
# Written by a scanner's Receive undo. Its tag stays free; any other retirement keeps the tag.
ACTION_RECEIPT_UNDO = "RECEIPT_UNDO"


def audit_who(actor) -> str:
    """The audit user id: the signed-in user, else the shift on a shared device."""
    return actor.user_id or f"shift:{actor.shift_id}"


def record_unit_change(unit_id: int, action: str, changes: dict, user_id: str) -> None:
    """Add one audit row in the caller's transaction; the caller commits."""
    db.session.add(ConfigAuditEvent(
        entity_type=ENTITY_UNIT, entity_id=str(unit_id), action=action,
        changes_json=json.dumps(changes), created_by_user_id=user_id))


def has_unit_change(unit_id: int, action: str, since=None) -> bool:
    """Report whether the unit has an audit row of this action, at or after `since` if given."""
    query = (db.session.query(ConfigAuditEvent.id)
             .filter(ConfigAuditEvent.entity_type == ENTITY_UNIT,
                     ConfigAuditEvent.entity_id == str(unit_id),
                     ConfigAuditEvent.action == action))
    if since is not None:
        query = query.filter(ConfigAuditEvent.created_at >= since)
    return query.first() is not None


def unit_changes(unit_id: int, limit: int | None = 50) -> list[tuple[ConfigAuditEvent, dict]]:
    """Newest first, each with its decoded changes. A limit of None returns every row."""
    query = (db.session.query(ConfigAuditEvent)
             .filter(ConfigAuditEvent.entity_type == ENTITY_UNIT,
                     ConfigAuditEvent.entity_id == str(unit_id))
             .order_by(ConfigAuditEvent.created_at.desc(), ConfigAuditEvent.id.desc()))
    rows = (query if limit is None else query.limit(limit)).all()
    return [(row, json.loads(row.changes_json or "{}")) for row in rows]
