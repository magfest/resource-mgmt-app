"""Shared scanner devices.

A device is not a user and holds no roles. Its cookie carries a random
token; the database stores only the SHA-256, so a database read cannot
impersonate a scanner. The token is checked on every request, so a
revocation takes effect immediately.

A device enrolls with a one-time code that an admin creates on their own
computer, so no admin ever signs in on a shared device.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta

from app import db
from app.inventory.ledger import InventoryError
from app.models import InvContainer, InvDevice, InvEnrollCode, InvShift
from app.models.constants import (
    INV_SHIFT_END_REVOKED, INV_STATION_LOOKUP, INV_STATION_UNIT, INV_STATIONS,
)
from app.security_audit import CATEGORY_ADMIN, EVENT_CONFIG_MODIFY, log_security_event

COOKIE_NAME = "inv_device"
COOKIE_MAX_AGE = 60 * 60 * 24 * 180
_SEEN_WRITE_INTERVAL = timedelta(minutes=1)
ENROLL_CODE_LIFETIME = timedelta(minutes=10)
# No 0, 1, I, L, O or U: the code is read aloud and typed on a handheld keyboard.
_CODE_ALPHABET = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
_CODE_LENGTH = 8
UNIT_NEEDS_HOME = "A Check station needs a home location, such as the warehouse."
CODE_REFUSED = "That code did not work. Ask an inventory admin for a new one."


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _check_station(station_code: str) -> None:
    if station_code not in INV_STATIONS:
        raise InventoryError(f"{station_code or 'That'} is not a station.")


def _check_unit_home(station_code: str, home_container_id: int | None) -> None:
    if station_code == INV_STATION_UNIT and home_container_id is None:
        raise InventoryError(UNIT_NEEDS_HOME)


def enroll_device(name: str, user_id: str, station_code: str = INV_STATION_LOOKUP,
                  home_container: InvContainer | None = None) -> tuple[InvDevice, str]:
    """Create a device and return it with its raw cookie token."""
    name = (name or "").strip()
    if not name or len(name) > 64:
        raise InventoryError("Name this scanner, such as Scanner 3.")
    _check_station(station_code)
    _check_unit_home(station_code, home_container.id if home_container else None)
    raw = secrets.token_urlsafe(32)
    device = InvDevice(
        name=name, token_hash=hash_token(raw), enrolled_by_user_id=user_id,
        station_code=station_code,
        home_container_id=home_container.id if home_container else None,
    )
    db.session.add(device)
    db.session.flush()
    return device, raw


def _normalize_enroll_code(raw: str | None) -> str:
    return "".join((raw or "").upper().split()).replace("-", "")


def create_enroll_code(name: str, user_id: str, station_code: str = INV_STATION_LOOKUP,
                       home_container: InvContainer | None = None,
                       now: datetime | None = None) -> str:
    """Create a one-time enrollment code and return it as ABCD-EFGH.

    Only the hash is stored; the raw code cannot be recovered afterward.
    """
    name = (name or "").strip()
    if not name or len(name) > 64:
        raise InventoryError("Name this scanner, such as Scanner 3.")
    _check_station(station_code)
    _check_unit_home(station_code, home_container.id if home_container else None)
    now = now or datetime.utcnow()
    plain = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(_CODE_LENGTH))
    db.session.add(InvEnrollCode(
        code_hash=hash_token(plain), name=name, station_code=station_code,
        home_container_id=home_container.id if home_container else None,
        created_by_user_id=user_id, created_at=now, expires_at=now + ENROLL_CODE_LIFETIME,
    ))
    db.session.flush()
    log_security_event(EVENT_CONFIG_MODIFY, category=CATEGORY_ADMIN, user_id=user_id,
                       details={"source": "inventory_enroll_code_create",
                                "device_name": name, "station_code": station_code})
    return f"{plain[:4]}-{plain[4:]}"


def redeem_enroll_code(raw: str | None, now: datetime | None = None) -> tuple[InvDevice, str]:
    """Spend a code and return the new device with its raw cookie token.

    A wrong, expired and used code raise the same error, so the message
    does not reveal which codes exist. The caller commits.
    """
    now = now or datetime.utcnow()
    plain = _normalize_enroll_code(raw)
    # populate_existing: the locked read must see a concurrent redeemer's used_at.
    code = (db.session.query(InvEnrollCode).filter_by(code_hash=hash_token(plain))
            .with_for_update().populate_existing().first()) if plain else None
    if code is None or code.used_at is not None or code.expires_at <= now:
        raise InventoryError(CODE_REFUSED)
    device, token = enroll_device(
        code.name, code.created_by_user_id, station_code=code.station_code,
        home_container=db.session.get(InvContainer, code.home_container_id)
        if code.home_container_id else None)
    code.used_at = now
    code.device_id = device.id
    log_security_event(EVENT_CONFIG_MODIFY, category=CATEGORY_ADMIN,
                       user_id=code.created_by_user_id,
                       details={"source": "inventory_device_enroll", "device_id": device.id,
                                "device_name": device.name, "station_code": device.station_code})
    return device, token


def set_station(device: InvDevice, station_code: str,
                home_container: InvContainer | None = None, keep_home: bool = False,
                user_id: str | None = None) -> None:
    """Change a device's station and write the security event; the caller commits.

    The home container is replaced by `home_container` unless `keep_home` is set.
    `user_id` is the admin who changed it; a scanner switching itself passes None.
    """
    _check_station(station_code)
    new_home_id = (device.home_container_id if keep_home
                   else home_container.id if home_container else None)
    _check_unit_home(station_code, new_home_id)
    device.station_code = station_code
    device.home_container_id = new_home_id
    log_security_event(EVENT_CONFIG_MODIFY, category=CATEGORY_ADMIN, user_id=user_id,
                       details={"source": "inventory_device_station", "device_id": device.id,
                                "station_code": device.station_code})


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
