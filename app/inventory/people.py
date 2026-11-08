"""Names for the user ids and shifts that audit rows and ledger rows record."""
from __future__ import annotations

from typing import Iterable

from app import db
from app.models import InvShift, User

_SHIFT_PREFIX = "shift:"


def _shift_number(raw) -> int | None:
    if isinstance(raw, str) and raw.startswith(_SHIFT_PREFIX) and raw[len(_SHIFT_PREFIX):].isdigit():
        return int(raw[len(_SHIFT_PREFIX):])
    return None


def names_for(user_ids: Iterable[str | None]) -> dict[str, str]:
    """Map user ids and `shift:<n>` ids to display names; unknown ids are left out.

    Runs at most two queries however many ids it is given.
    """
    ids = {raw for raw in user_ids if raw}
    shift_numbers = {raw: _shift_number(raw) for raw in ids}
    shift_numbers = {raw: n for raw, n in shift_numbers.items() if n is not None}
    names: dict[str, str] = {}
    plain = ids - set(shift_numbers)
    if plain:
        for user in db.session.query(User).filter(User.id.in_(plain)):
            names[user.id] = user.display_name or user.id
    if shift_numbers:
        shifts = {s.id: s for s in db.session.query(InvShift)
                  .filter(InvShift.id.in_(set(shift_numbers.values())))}
        for raw, number in shift_numbers.items():
            if number in shifts:
                names[raw] = f"{shifts[number].person_name} (scanner)"
    return names


def actor_name(user_id: str | None, shift, names: dict[str, str]) -> str:
    """Name the person behind a row: the user, else the shift's person, else "Someone"."""
    if user_id:
        return names.get(user_id, user_id)
    if shift is not None:
        return f"{shift.person_name} (scanner)"
    return "Someone"
