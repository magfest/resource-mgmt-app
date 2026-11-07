"""Shared scanner devices.

A device is not a user and holds no roles. Its cookie carries a random
token; the database stores only the SHA-256, so a database read cannot
impersonate a scanner. The token is checked on every request, so a
revocation takes effect immediately.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta

from app import db
from app.inventory.ledger import InventoryError
from app.models import InvContainer, InvDevice, InvShift
from app.models.constants import (
    INV_SHIFT_END_REVOKED, INV_STATION_LOOKUP, INV_STATIONS,
)

COOKIE_NAME = "inv_device"
COOKIE_MAX_AGE = 60 * 60 * 24 * 180
_SEEN_WRITE_INTERVAL = timedelta(minutes=1)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _check_station(station_code: str) -> None:
    if station_code not in INV_STATIONS:
        raise InventoryError(f"{station_code or 'That'} is not a station.")


def enroll_device(name: str, user_id: str, station_code: str = INV_STATION_LOOKUP,
                  home_container: InvContainer | None = None) -> tuple[InvDevice, str]:
    """Create a device and return it with its raw cookie token."""
    name = (name or "").strip()
    if not name or len(name) > 64:
        raise InventoryError("Name this scanner, such as Scanner 3.")
    _check_station(station_code)
    raw = secrets.token_urlsafe(32)
    device = InvDevice(
        name=name, token_hash=hash_token(raw), enrolled_by_user_id=user_id,
        station_code=station_code,
        home_container_id=home_container.id if home_container else None,
    )
    db.session.add(device)
    db.session.flush()
    return device, raw


def set_station(device: InvDevice, station_code: str,
                home_container: InvContainer | None = None) -> None:
    """Change a device's station; the home container is replaced, not kept."""
    _check_station(station_code)
    device.station_code = station_code
    device.home_container_id = home_container.id if home_container else None


def device_for_token(raw: str | None) -> InvDevice | None:
    """Return the active device for a cookie token, or None."""
    if not raw:
        return None
    device = db.session.query(InvDevice).filter_by(token_hash=hash_token(raw)).first()
    if device is None or device.revoked_at is not None:
        return None
    now = datetime.utcnow()
    # Throttled so a busy scanner does not write this row on every request.
    if device.last_seen_at is None or now - device.last_seen_at > _SEEN_WRITE_INTERVAL:
        device.last_seen_at = now
    return device


def revoke_device(device: InvDevice, user_id: str) -> None:
    """Revoke a device and close its open shift."""
    now = datetime.utcnow()
    device.revoked_at = now
    device.revoked_by_user_id = user_id
    for shift in db.session.query(InvShift).filter_by(device_id=device.id, ended_at=None):
        shift.ended_at = now
        shift.end_reason = INV_SHIFT_END_REVOKED
