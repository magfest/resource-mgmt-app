"""Names for the user ids and shifts that audit rows and ledger rows record."""
from __future__ import annotations

from typing import Iterable

from sqlalchemy.orm import joinedload

from app import db
from app.models import InvShift, User

_SHIFT_PREFIX = "shift:"


def shift_person(shift: InvShift) -> str:
    """Name the person on a shift: the user, else the typed name, else "Someone".

    Shifts written before sign-in have only a typed person_name and no user.
    """
    if shift.user is not None:
        return shift.user.display_name or shift.user.id
    return shift.person_name or "Someone"


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
                  .options(joinedload(InvShift.user))
                  .filter(InvShift.id.in_(set(shift_numbers.values())))}
        for raw, number in shift_numbers.items():
            if number in shifts:
                names[raw] = f"{shift_person(shifts[number])} (scanner)"
    return names


def actor_name(user_id: str | None, shift, names: dict[str, str]) -> str:
    """Name the person behind a row: the user, else the shift's person, else "Someone"."""
    if user_id:
        return names.get(user_id, user_id)
    if shift is not None:
        return f"{shift_person(shift)} (scanner)"
    return "Someone"
