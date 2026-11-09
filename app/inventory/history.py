"""An asset's history as one list of sentences, newest first.

Merges movements, finished checks, audit rows, flags, allocation changes and typed notes. It reads
only; nothing here writes. Notes copied from the old system produce no line.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import selectinload

from app import db
from app.inventory.audit import ACTION_TAG_LINK, unit_changes
from app.inventory.flags import FLAG_WORDS
from app.inventory.notes import NOTE_TYPE_WORDS
from app.inventory.people import actor_name, names_for
from app.models import InvAllocationEvent, InvCheck, InvFlag, InvMovement, InvShift, InvUnit, InvUnitNote
from app.models.constants import (
    INV_ACTION_ADD, INV_ACTION_ADJUST, INV_ALLOCATION_ALLOCATE, INV_ALLOCATION_MOVE, INV_CHECK_FAILED, INV_CHECK_PASSED, INV_CHECK_SET_ASIDE,
    INV_NOTE_SOURCE_PERSON,
)

CONDITION_WORDS = {"IN_SERVICE": "In service", "REPAIR": "In repair", "LOST": "Lost",
                   "FOUND": "Found", "RETIRED": "Retired"}

_ALLOCATION_VERBS = {INV_ALLOCATION_ALLOCATE: "allocated it to",
                     INV_ALLOCATION_MOVE: "moved its allocation to"}

_CHECK_TEXT = {INV_CHECK_PASSED: "checked it: Works", INV_CHECK_FAILED: "checked it: Broken",
               INV_CHECK_SET_ASIDE: "set it aside during a check"}


@dataclass(frozen=True)
class HistoryLine:
    at: datetime
    who: str
    text: str
    note: str | None = None


def _place(container) -> str:
    return (container.name or container.label_code) if container else "top level"


def _movement_text(m: InvMovement) -> str:
    if m.action == INV_ACTION_ADD:
        return "added it, not placed yet" if m.to_container is None \
            else f"added it to {_place(m.to_container)}"
    if m.action == INV_ACTION_ADJUST:
        return f"found it in {_place(m.to_container)}"
    if m.to_container is not None:
        return f"moved it to {_place(m.to_container)}"
    return f"took it out of {_place(m.from_container)}"


def _shown(value) -> str:
    return str(value) if value not in (None, "", []) else "none"


def _audit_texts(action: str, changes: dict) -> list[str]:
    texts = []
    for key, change in changes.items():
        old, new = change.get("old"), change.get("new")
        if action == ACTION_TAG_LINK and key == "codes":
            texts += [f"linked asset tag {c}" for c in new or []]
        elif key == "codes":
            old_codes, new_codes = list(old or []), list(new or [])
            texts += [f"added {c}" for c in new_codes if c not in old_codes]
            texts += [f"retired {c}" for c in old_codes if c not in new_codes]
        elif key == "Old notes":
            texts.append("cleared the notes from the old system")
        elif key == "notes":
            texts.append("changed the notes")
        elif key == "condition":
            texts.append("changed the condition from "
                         f"{CONDITION_WORDS.get(old, _shown(old))} to "
                         f"{CONDITION_WORDS.get(new, _shown(new))}")
        else:
            texts.append(f"changed the {key.lower()} from {_shown(old)} to {_shown(new)}")
    return texts


def unit_history(unit: InvUnit) -> list[HistoryLine]:
    """Return every line of the asset's history, newest first, in a fixed number of queries."""
    movements = (db.session.query(InvMovement).filter(InvMovement.unit_id == unit.id)
                 .options(selectinload(InvMovement.to_container),
                          selectinload(InvMovement.from_container)).all())
    checks = (db.session.query(InvCheck)
              .filter(InvCheck.unit_id == unit.id, InvCheck.finished_at.isnot(None),
                      InvCheck.status.in_(tuple(_CHECK_TEXT))).all())
    flags = db.session.query(InvFlag).filter(InvFlag.unit_id == unit.id).all()
    notes = (db.session.query(InvUnitNote)
             .filter(InvUnitNote.unit_id == unit.id,
                     InvUnitNote.source == INV_NOTE_SOURCE_PERSON).all())
    # Sentences come from the event's own snapshot; the line may be gone or reworded since.
    allocations = (db.session.query(InvAllocationEvent)
                   .filter(InvAllocationEvent.unit_id == unit.id)
                   .options(selectinload(InvAllocationEvent.department),
                            selectinload(InvAllocationEvent.event_cycle)).all())
    audits = unit_changes(unit.id, limit=None)

    names = names_for(
        [m.performed_by_user_id for m in movements] + [c.performed_by_user_id for c in checks]
        + [f.performed_by_user_id for f in flags] + [f.resolved_by_user_id for f in flags]
        + [n.created_by_user_id for n in notes] + [a.performed_by_user_id for a in allocations] + [row.created_by_user_id for row, _ in audits])
    # Rows store the shift id; load the shifts once so no row triggers its own query.
    shift_ids = {r.shift_id for r in (*movements, *checks, *flags) if r.shift_id}
    shifts = {}
    if shift_ids:
        shifts = {s.id: s for s in db.session.query(InvShift).filter(InvShift.id.in_(shift_ids))}

    def who(row):
        return actor_name(row.performed_by_user_id, shifts.get(row.shift_id), names)

    lines = [HistoryLine(m.created_at, who(m), _movement_text(m)) for m in movements]
    lines += [HistoryLine(c.finished_at, who(c), _CHECK_TEXT[c.status], c.note) for c in checks]
    for f in flags:
        reason = FLAG_WORDS.get(f.reason, f.reason)
        lines.append(HistoryLine(f.created_at, who(f), f"flagged it: {reason}", f.note))
        if f.resolved_at is not None:
            lines.append(HistoryLine(f.resolved_at,
                                     actor_name(f.resolved_by_user_id, None, names),
                                     f"resolved a flag: {reason}", f.resolution_note))
    lines += [HistoryLine(n.created_at, actor_name(n.created_by_user_id, None, names),
                          f"added {'an' if n.note_type == 'ADMIN' else 'a'} "
                          f"{NOTE_TYPE_WORDS.get(n.note_type, n.note_type).lower()} note")
              for n in notes]
    for a in allocations:
        verb = _ALLOCATION_VERBS.get(a.action, "released it from")
        lines.append(HistoryLine(
            a.created_at, actor_name(a.performed_by_user_id, None, names),
            f"{verb} {a.department.name} for {a.event_cycle.code}: {a.line_label}"))
    for row, changes in audits:
        who_name = actor_name(row.created_by_user_id, None, names)
        lines += [HistoryLine(row.created_at, who_name, text)
                  for text in _audit_texts(row.action, changes)]
    lines.sort(key=lambda line: line.at, reverse=True)
    return lines
