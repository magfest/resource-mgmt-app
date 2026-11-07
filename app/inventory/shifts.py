"""Shifts: which volunteer is using a shared scanner.

A shift is a typed name on one device, never a badge scan. The caller commits.
"""
from __future__ import annotations

import unicodedata
from datetime import datetime, timedelta

from app import db
from app.inventory.ledger import InventoryError
from app.models import InvDevice, InvShift
from app.models.constants import (
    INV_SHIFT_END_IDLE, INV_SHIFT_END_REPLACED,
)

IDLE_LIMIT = timedelta(minutes=15)
RECENT_NAMES_WINDOW = timedelta(days=7)
MAX_RECENT_NAMES = 12
MAX_NAME_LENGTH = 64
NAME_REQUIRED = "Type a name, 1 to 64 characters."
_VISIBLE_CATEGORIES = "LNPSM"


class NameNeeded(InventoryError):
    """The device has no open shift. The scanner page sends the volunteer to the name screen."""

    need_name = True


def _open_shift(device: InvDevice) -> InvShift | None:
    return (db.session.query(InvShift).filter_by(device_id=device.id, ended_at=None)
            .order_by(InvShift.id.desc()).first())


def current_shift(device: InvDevice, now: datetime | None = None) -> InvShift | None:
    """Return the open shift, or close it as IDLE and return None."""
    now = now or datetime.utcnow()
    shift = _open_shift(device)
    if shift is None:
        return None
    if now - shift.last_activity_at > IDLE_LIMIT:
        shift.ended_at = now
        shift.end_reason = INV_SHIFT_END_IDLE
        db.session.flush()
        return None
    return shift


def _lock_device(device: InvDevice) -> None:
    """Serialize shift changes per device; SQLite ignores the row lock."""
    db.session.query(InvDevice).filter_by(id=device.id).with_for_update().one()


def _clean_name(person_name: str | None) -> str:
    """Return the name with spaces collapsed, or raise when it is not visible text."""
    raw = person_name or ""
    # Require a visible shift name.
    if any(unicodedata.category(c) in ("Cc", "Cs") for c in raw):
        raise InventoryError(NAME_REQUIRED)
    name = " ".join(raw.split())
    if len(name) > MAX_NAME_LENGTH \
            or not any(unicodedata.category(c)[0] in _VISIBLE_CATEGORIES for c in name):
        raise InventoryError(NAME_REQUIRED)
    return name


def start_shift(device: InvDevice, person_name: str, now: datetime | None = None) -> InvShift:
    """Open a shift, closing every open one as REPLACED."""
    now = now or datetime.utcnow()
    name = _clean_name(person_name)
    _lock_device(device)
    for old in db.session.query(InvShift).filter_by(device_id=device.id, ended_at=None):
        old.ended_at = now
        old.end_reason = INV_SHIFT_END_REPLACED
    shift = InvShift(device_id=device.id, person_name=name,
                     started_at=now, last_activity_at=now)
    db.session.add(shift)
    db.session.flush()
    return shift


def end_shift(device: InvDevice, reason: str, now: datetime | None = None) -> None:
    now = now or datetime.utcnow()
    _lock_device(device)
    for shift in db.session.query(InvShift).filter_by(device_id=device.id, ended_at=None):
        shift.ended_at = now
        shift.end_reason = reason
    db.session.flush()


def touch(shift: InvShift, now: datetime | None = None) -> None:
    """Record activity so an active volunteer is not timed out."""
    shift.last_activity_at = now or datetime.utcnow()


def recent_names(device: InvDevice, now: datetime | None = None) -> list[str]:
    """Names used on this device in the last week, newest first.

    Names that differ only by case count once, under the newest spelling.
    """
    now = now or datetime.utcnow()
    rows = (db.session.query(InvShift.person_name)
            .filter(InvShift.device_id == device.id,
                    InvShift.started_at >= now - RECENT_NAMES_WINDOW)
            .order_by(InvShift.started_at.desc(), InvShift.id.desc()).all())
    newest = {}
    for (name,) in rows:
        newest.setdefault(name.casefold(), name)
    return list(newest.values())[:MAX_RECENT_NAMES]
