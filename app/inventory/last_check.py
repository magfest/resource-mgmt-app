"""A unit's latest check and last-seen time, read across every event.

A check is a fact about the unit, not about an event. The readers here take
each unit's newest live record whichever event wrote it, and load many units in
a fixed number of queries.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, true

from app import db
from app.inventory.ledger import NUMBER_KINDS
from app.inventory.printing import INV_LOCAL_TZ, local_today
from app.models import InvCheck, InvMovement, InvPrepStatus, InvUnit, InvUnitIdentifier
from app.models.constants import (
    INV_CHECK_OPEN, INV_CHECK_VOIDED, INV_CONDITION_IN_SERVICE, INV_CONDITION_REPAIR,
    INV_CONDITION_RETIRED, INV_OWNERSHIP_OWNED, INV_PREP_CHECKED, INV_PREP_FAILED_CHECK,
    INV_PREP_VOIDED,
)

CHECK_WORKS = "WORKS"
CHECK_BROKEN = "BROKEN"
CHECK_WORDS = {CHECK_WORKS: "Works", CHECK_BROKEN: "Broken"}

CHECK_FILTERS = ("checked", "not_checked", "broken", "no_number")


def _latest_ids():
    """Subquery of the id of each unit's newest live prep row, any event."""
    return (db.session.query(func.max(InvPrepStatus.id).label("prep_id"))
            .outerjoin(InvCheck, InvCheck.id == InvPrepStatus.check_id)
            .filter(InvPrepStatus.status != INV_PREP_VOIDED,
                    or_(InvPrepStatus.check_id.is_(None),
                        InvCheck.status != INV_CHECK_VOIDED))
            .group_by(InvPrepStatus.unit_id).subquery())


def latest_check_rows(unit_ids: list[int]) -> dict[int, InvPrepStatus]:
    """Each listed unit's newest live prep row; units with none are absent."""
    if not unit_ids:
        return {}
    latest = _latest_ids()
    rows = (db.session.query(InvPrepStatus)
            .filter(InvPrepStatus.unit_id.in_(unit_ids),
                    InvPrepStatus.id.in_(db.session.query(latest.c.prep_id))).all())
    return {row.unit_id: row for row in rows}


def check_word(prep: InvPrepStatus | None) -> str | None:
    """Return CHECK_BROKEN, CHECK_WORKS, or None when there is no live row."""
    if prep is None:
        return None
    if prep.status == INV_PREP_FAILED_CHECK:
        return CHECK_BROKEN
    if prep.status == INV_PREP_CHECKED:
        return CHECK_WORKS
    return None


def last_seen_dates(unit_ids: list[int]) -> dict[int, datetime]:
    """Map each unit to the newer of its last movement and last finished check."""
    if not unit_ids:
        return {}
    moved = dict(db.session.query(InvMovement.unit_id, func.max(InvMovement.created_at))
                 .filter(InvMovement.unit_id.in_(unit_ids))
                 .group_by(InvMovement.unit_id).all())
    checked = dict(db.session.query(InvCheck.unit_id, func.max(InvCheck.finished_at))
                   .filter(InvCheck.unit_id.in_(unit_ids),
                           InvCheck.finished_at.isnot(None),
                           InvCheck.status.notin_([INV_CHECK_VOIDED, INV_CHECK_OPEN]))
                   .group_by(InvCheck.unit_id).all())
    seen = {}
    for uid in unit_ids:
        times = [t for t in (moved.get(uid), checked.get(uid)) if t is not None]
        if times:
            seen[uid] = max(times)
    return seen


def default_since() -> date:
    return local_today() - timedelta(days=30)


def parse_since(raw: str | None) -> tuple[date, bool]:
    """Parse `YYYY-MM-DD`; the bool is True only when a bad value fell back."""
    if not raw:
        return default_since(), False
    try:
        day = date.fromisoformat(raw)
    except ValueError:
        return default_since(), True
    if day > local_today():
        return default_since(), True
    return day, False


def since_start(day: date) -> datetime:
    """Local midnight of `day` as naive UTC, the form the database stores."""
    local = datetime.combine(day, time.min, tzinfo=ZoneInfo(INV_LOCAL_TZ))
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def counted_units():
    """Clause for owned units in service or repair, the ones a period count covers."""
    return and_(InvUnit.ownership == INV_OWNERSHIP_OWNED,
                InvUnit.condition.in_([INV_CONDITION_IN_SERVICE, INV_CONDITION_REPAIR]))


def _has_number():
    return (db.session.query(InvUnitIdentifier.id)
            .filter(InvUnitIdentifier.unit_id == InvUnit.id,
                    InvUnitIdentifier.kind.in_(NUMBER_KINDS),
                    InvUnitIdentifier.retired_at.is_(None)).exists())


def _latest_row_of_unit():
    latest = _latest_ids()
    return (db.session.query(InvPrepStatus.id)
            .filter(InvPrepStatus.unit_id == InvUnit.id,
                    InvPrepStatus.id.in_(db.session.query(latest.c.prep_id))))


def check_filter(show: str, since: datetime):
    """SQL clause on InvUnit for one of CHECK_FILTERS; anything else matches all."""
    if show not in CHECK_FILTERS:
        return true()
    if show == "no_number":
        return and_(InvUnit.ownership == INV_OWNERSHIP_OWNED, ~_has_number())
    row = _latest_row_of_unit()
    if show == "checked":
        clause = row.filter(InvPrepStatus.created_at >= since).exists()
    elif show == "not_checked":
        clause = ~row.filter(InvPrepStatus.created_at >= since).exists()
    else:
        clause = row.filter(InvPrepStatus.status == INV_PREP_FAILED_CHECK).exists()
    return and_(counted_units(), clause)


def check_totals(since: datetime) -> dict:
    """Counts: counted, checked, not_checked, broken, no_number."""
    def count(clause):
        return db.session.query(InvUnit).filter(clause).count()

    owned_unretired = and_(InvUnit.ownership == INV_OWNERSHIP_OWNED,
                           InvUnit.condition != INV_CONDITION_RETIRED)
    return {
        "counted": count(counted_units()),
        "checked": count(check_filter("checked", since)),
        "not_checked": count(check_filter("not_checked", since)),
        "broken": count(check_filter("broken", since)),
        "no_number": count(and_(owned_unretired, check_filter("no_number", since))),
    }
