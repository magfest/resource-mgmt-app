"""Card and PIN credentials that sign a person in on a shared scanner.

Boundary: this module holds sign-in secrets. Plain values leave only as return
values and are never stored or logged; the database keeps HMAC-SHA256 digests
keyed by SECRET_KEY. Functions flush and never commit; the caller commits.
"""
import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta
from typing import Iterable

from flask import current_app
from sqlalchemy.exc import IntegrityError

from app import db
from app.inventory.codes import normalize_code
from app.inventory.ledger import Actor, InventoryError, savepoint
from app.inventory.printing import local_today, printer_for, request_label
from app.models.constants import INV_JOB_SIGNIN_CARD, INV_JOB_SOURCE_SCAN
from app.models import (
    InvCredential, InvDevice, InvPrintJob, ROLE_INVENTORY_ADMIN, ROLE_INVENTORY_OPERATOR,
    ROLE_SUPER_ADMIN, User, UserRole,
)

PIN_LENGTH = 6
MAX_PIN_FAILURES = 5
KEYPAD_LOCK = timedelta(minutes=5)
CARD_PREFIX = "OP-"
# Crockford base32 drops I, L, O and U so a printed value is hard to misread.
_CARD_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_CARD_BODY = 12
_CARD_PATTERN = re.compile(
    rf"{re.escape(CARD_PREFIX)}[{_CARD_ALPHABET}]{{{_CARD_BODY}}}")
_PIN_DRAWS = 20


def _digest(kind: str, value: str) -> str:
    """Return the keyed digest of a PIN or card value.

    A salted password hash cannot work here: sign-in by PIN alone needs a
    lookup by digest, and uniqueness needs a unique index on it.
    """
    key = current_app.config["SECRET_KEY"]
    if isinstance(key, str):
        key = key.encode()
    return hmac.new(key, f"{kind}:{value}".encode(), hashlib.sha256).hexdigest()


def _draw_pin() -> str:
    return f"{secrets.randbelow(10 ** PIN_LENGTH):0{PIN_LENGTH}d}"


def _draw_card() -> str:
    return CARD_PREFIX + "".join(secrets.choice(_CARD_ALPHABET) for _ in range(_CARD_BODY))


def _credential(user_id: str) -> InvCredential:
    row = db.session.get(InvCredential, user_id)
    if row is None:
        row = InvCredential(user_id=user_id)
        db.session.add(row)
    return row


def _digest_taken(column, digest: str, user_id: str) -> bool:
    return db.session.query(InvCredential).filter(
        column == digest, InvCredential.user_id != user_id).first() is not None


def is_card_code(raw: str | None) -> bool:
    """Return True when the scanned value has the shape of a sign-in card."""
    return _CARD_PATTERN.fullmatch(normalize_code(raw)) is not None


def can_sign_in(user: User | None) -> bool:
    """Return True for an active super admin, inventory admin or operator.

    Mirrors is_inventory_operator in app/routes/inventory/helpers.py, from a
    User row, because a scanner request has no user_ctx.
    """
    if user is None or not user.is_active:
        return False
    roles = {code for (code,) in db.session.query(UserRole.role_code).filter_by(user_id=user.id)}
    return bool(roles & {ROLE_SUPER_ADMIN, ROLE_INVENTORY_ADMIN, ROLE_INVENTORY_OPERATOR})


def _issue(user: User, kind: str, draw, digest_attr: str, stamp_attr: str,
           now: datetime | None) -> str:
    """Store a fresh unique value for the user and return it in plain text."""
    now = now or datetime.utcnow()
    column = getattr(InvCredential, digest_attr)
    for _ in range(_PIN_DRAWS):
        value = draw()
        digest = _digest(kind, value)
        if _digest_taken(column, digest, user.id):
            continue
        row = _credential(user.id)
        try:
            with savepoint():
                setattr(row, digest_attr, digest)
                setattr(row, stamp_attr, now)
                db.session.flush()
        except IntegrityError:
            # A concurrent request took the same value; draw again.
            continue
        return value
    raise RuntimeError(f"Could not find an unused {kind} value")


def new_pin(user: User, now: datetime | None = None) -> str:
    """Return a fresh PIN unique across all people, replacing any old one."""
    return _issue(user, "pin", _draw_pin, "pin_digest", "pin_set_at", now)


def new_card(user: User, now: datetime | None = None) -> str:
    """Return a fresh card value, replacing any old one."""
    return _issue(user, "card", _draw_card, "card_digest", "card_printed_at", now)


def clear_pin(user_id: str) -> None:
    """Void the user's PIN. A user with no credential row is left alone."""
    row = db.session.get(InvCredential, user_id)
    if row is not None:
        row.pin_digest = None
        row.pin_set_at = None
        db.session.flush()


def user_for_pin(pin: str | None) -> User | None:
    """Return the owner of a PIN, with no eligibility check."""
    pin = (pin or "").strip()
    if len(pin) != PIN_LENGTH or not pin.isdigit() or not pin.isascii():
        return None
    row = db.session.query(InvCredential).filter_by(pin_digest=_digest("pin", pin)).first()
    return row.user if row else None


def user_for_card(raw: str | None) -> User | None:
    """Return the owner of a card value, with no eligibility check."""
    if not is_card_code(raw):
        return None
    digest = _digest("card", normalize_code(raw))
    row = db.session.query(InvCredential).filter_by(card_digest=digest).first()
    return row.user if row else None


def keypad_locked(device: InvDevice, now: datetime | None = None) -> bool:
    """Return True while this scanner refuses PINs. Cards still work."""
    now = now or datetime.utcnow()
    return device.pin_locked_until is not None and now < device.pin_locked_until


def record_pin_failure(device: InvDevice, now: datetime | None = None) -> None:
    """Count a wrong PIN; the fifth in a row locks the keypad and resets the count."""
    now = now or datetime.utcnow()
    device.pin_failures = (device.pin_failures or 0) + 1
    if device.pin_failures >= MAX_PIN_FAILURES:
        device.pin_locked_until = now + KEYPAD_LOCK
        device.pin_failures = 0
    db.session.flush()


def clear_pin_failures(device: InvDevice) -> None:
    """Reset the wrong-PIN count after a successful sign-in."""
    device.pin_failures = 0
    db.session.flush()


def credentials_for(user_ids: Iterable[str]) -> dict[str, InvCredential]:
    """Return credential rows keyed by user id, in one query."""
    ids = list(user_ids)
    if not ids:
        return {}
    rows = db.session.query(InvCredential).filter(InvCredential.user_id.in_(ids)).all()
    return {row.user_id: row for row in rows}


def issue_card(user: User, actor: Actor, now: datetime | None = None) -> InvPrintJob:
    """Queue a new sign-in card for the user and end their open shifts.

    The printer is checked before the credential changes, so a refusal leaves
    the old card working. The caller commits.
    """
    # shifts.py imports can_sign_in from this module, so this import stays local.
    from app.inventory.shifts import end_user_shifts

    name = (user.display_name or "").strip()
    if not name:
        raise InventoryError(
            "This account has no name to print. A super admin can set one in Users admin.")
    printer_for(INV_JOB_SIGNIN_CARD)
    code = new_card(user, now)
    job = request_label(INV_JOB_SIGNIN_CARD, code, {"name": name}, actor,
                        INV_JOB_SOURCE_SCAN, printed_date=local_today())
    end_user_shifts(user.id, now=now)
    return job
