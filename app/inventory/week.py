"""This week's check status for units, read from prep rows in bulk.

"Seen" follows `live_prep_row` in cards.py: the newest prep row for the event
that is not VOIDED and whose check is not VOIDED. This module does the same
lookup for many units in one grouped subquery, so a list never costs a query per unit.
"""
from __future__ import annotations

from sqlalchemy import false, func, or_, true

from app import db
from app.inventory.ledger import NUMBER_KINDS
from app.models import InvCheck, InvPrepStatus, InvUnit, InvUnitIdentifier
from app.models.constants import (
    INV_CHECK_VOIDED, INV_CONDITION_RETIRED, INV_OWNERSHIP_OWNED, INV_PREP_FAILED_CHECK,
    INV_PREP_VOIDED,
)

WEEK_WORKS = "WORKS"
WEEK_BROKEN = "BROKEN"
WEEK_NOT_SEEN = "NOT_SEEN"
WEEK_WORDS = {WEEK_WORKS: "Works", WEEK_BROKEN: "Broken", WEEK_NOT_SEEN: "Not seen"}

SHOW_FILTERS = ("seen", "not_seen", "broken", "no_number")


def _live_prep_ids(event):
    """Subquery of the id of each unit's newest live prep row for the event."""
    return (db.session.query(func.max(InvPrepStatus.id).label("prep_id"))
            .outerjoin(InvCheck, InvCheck.id == InvPrepStatus.check_id)
            .filter(InvPrepStatus.event_cycle_id == event.id,
                    InvPrepStatus.status != INV_PREP_VOIDED,
                    or_(InvPrepStatus.check_id.is_(None),
                        InvCheck.status != INV_CHECK_VOIDED))
            .group_by(InvPrepStatus.unit_id).subquery())


def live_prep_rows(event, unit_ids: list[int]) -> dict[int, InvPrepStatus]:
    """Each listed unit's live prep row; units with none are absent."""
    if event is None or not unit_ids:
        return {}
    live = _live_prep_ids(event)
    rows = (db.session.query(InvPrepStatus)
            .filter(InvPrepStatus.unit_id.in_(unit_ids),
                    InvPrepStatus.id.in_(db.session.query(live.c.prep_id))).all())
    return {row.unit_id: row for row in rows}


def week_status(prep: InvPrepStatus | None) -> str:
    """The week status for one live prep row, or None for a unit nobody has seen."""
    if prep is None:
        return WEEK_NOT_SEEN
    return WEEK_BROKEN if prep.status == INV_PREP_FAILED_CHECK else WEEK_WORKS


def week_statuses(event, unit_ids: list[int]) -> dict[int, str]:
    """Map each unit id to WEEK_WORKS, WEEK_BROKEN or WEEK_NOT_SEEN."""
    rows = live_prep_rows(event, unit_ids)
    return {uid: week_status(rows.get(uid)) for uid in unit_ids}


def _has_number():
    return (db.session.query(InvUnitIdentifier.id)
            .filter(InvUnitIdentifier.unit_id == InvUnit.id,
                    InvUnitIdentifier.kind.in_(NUMBER_KINDS),
                    InvUnitIdentifier.retired_at.is_(None)).exists())


def show_condition(event, show: str):
    """SQL clause on InvUnit for one of SHOW_FILTERS; anything else matches all."""
    if show == "no_number":
        return ~_has_number()
    if show not in SHOW_FILTERS:
        return true()
    if event is None:
        return true() if show == "not_seen" else false()
    live = _live_prep_ids(event)
    seen = (db.session.query(InvPrepStatus.id)
            .filter(InvPrepStatus.unit_id == InvUnit.id,
                    InvPrepStatus.id.in_(db.session.query(live.c.prep_id))))
    if show == "seen":
        return seen.exists()
    if show == "not_seen":
        return ~seen.exists()
    return seen.filter(InvPrepStatus.status == INV_PREP_FAILED_CHECK).exists()


def week_totals(event) -> dict:
    """Counts over owned, unretired units: owned, seen, not_seen, broken, no_number."""
    owned = db.session.query(InvUnit).filter(InvUnit.condition != INV_CONDITION_RETIRED,
                                             InvUnit.ownership == INV_OWNERSHIP_OWNED)

    def count(show):
        return owned.filter(show_condition(event, show)).count()

    total = owned.count()
    seen = count("seen")
    return {"owned": total, "seen": seen, "not_seen": total - seen,
            "broken": count("broken"), "no_number": count("no_number")}
