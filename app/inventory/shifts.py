"""Shifts: which RMS user is working a shared scanner.

A shift belongs to the user who signed in with a card or PIN on one device.
Shifts written before sign-in carry only a typed person_name. The caller commits.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app import db
from app.inventory.ledger import InventoryError
from app.inventory.sign_in import can_sign_in
from app.models import InvDevice, InvShift
from app.models.constants import (
    INV_SHIFT_END_IDLE, INV_SHIFT_END_REPLACED, INV_SHIFT_END_REVOKED,
)

IDLE_LIMIT = timedelta(minutes=15)


class NameNeeded(InventoryError):
    """The device has no open shift. The scanner page sends the volunteer to sign in."""

    need_name = True


def _open_shift(device: InvDevice) -> InvShift | None:
    return (db.session.query(InvShift).filter_by(device_id=device.id, ended_at=None)
            .order_by(InvShift.id.desc()).first())


def current_shift(device: InvDevice, now: datetime | None = None) -> InvShift | None:
    """Return the open shift, or close it and return None.

    A shift idle past IDLE_LIMIT closes as IDLE. A shift whose user can no
    longer use scanners closes as REVOKED, so a removed role ends the shift on
    the next request. A typed-name shift has no user to check.
    """
    now = now or datetime.utcnow()
    shift = _open_shift(device)
    if shift is None:
        return None
    if now - shift.last_activity_at > IDLE_LIMIT:
        reason = INV_SHIFT_END_IDLE
    elif shift.user_id is not None and not can_sign_in(shift.user):
        reason = INV_SHIFT_END_REVOKED
    else:
        return shift
    shift.ended_at = now
    shift.end_reason = reason
    db.session.flush()
    return None


def lock_device(device: InvDevice) -> None:
    """Serialize shift changes and keypad failure counting per device.

    The lock does not refresh the loaded row; a caller that reads device columns
    refreshes after locking. SQLite ignores the row lock.
    """
    db.session.query(InvDevice).filter_by(id=device.id).with_for_update().one()


def start_shift(device: InvDevice, user_id: str, now: datetime | None = None) -> InvShift:
    """Open a shift for the user, closing every open one on the device as REPLACED.

    Shifts on other devices stay open; one person may work two scanners.
    """
    now = now or datetime.utcnow()
    lock_device(device)
    for old in db.session.query(InvShift).filter_by(device_id=device.id, ended_at=None):
        old.ended_at = now
        old.end_reason = INV_SHIFT_END_REPLACED
    shift = InvShift(device_id=device.id, user_id=user_id,
                     started_at=now, last_activity_at=now)
    db.session.add(shift)
    db.session.flush()
    return shift


def end_shift(device: InvDevice, reason: str, now: datetime | None = None) -> None:
    now = now or datetime.utcnow()
    lock_device(device)
    for shift in db.session.query(InvShift).filter_by(device_id=device.id, ended_at=None):
        shift.ended_at = now
        shift.end_reason = reason
    db.session.flush()


def end_user_shifts(user_id: str, reason: str = INV_SHIFT_END_REVOKED,
                    now: datetime | None = None) -> int:
    """Close the user's open shifts on every device and return how many closed."""
    now = now or datetime.utcnow()
    shifts = db.session.query(InvShift).filter_by(user_id=user_id, ended_at=None).all()
    for shift in shifts:
        shift.ended_at = now
        shift.end_reason = reason
    db.session.flush()
    return len(shifts)


def touch(shift: InvShift, now: datetime | None = None) -> None:
    """Record activity so an active volunteer is not timed out."""
    shift.last_activity_at = now or datetime.utcnow()
