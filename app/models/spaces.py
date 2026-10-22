"""
Venues, spaces, and per-event department assignments.

A space is a room, a named slice of a room, or a free-form pop-up. Rooms and
slices persist across events; a pop-up is event-scoped. Combining is not a
space; a slice's SpaceEventOverride points at the slice it folds into, for
one event only. Assignments, overrides, and combining reset each cycle.
Event Ops owns this data and holds the SPACE_ADMIN role.

Capacity counts from the venue sheet are excluded; departments misread them
and no TechOps work uses them.
"""
from __future__ import annotations

from datetime import datetime

from app import db


def compose_combined_name(names: list[str]) -> str:
    """Compose one name for a combined space from its members' names.

    Takes the members' shared leading words, then joins the differing tails
    with "/": ["Chesapeake 4", "Chesapeake 5"] becomes "Chesapeake 4/5"; three
    slices of one room compose to exactly what the venue calls that room,
    e.g. ["RiverView 1", "RiverView 2", "RiverView 3"] to "RiverView 1/2/3".
    Falls back to joining full names with " + " when no leading word is
    shared, which a fold always has except for a room whose slices carry no
    common prefix to begin with. A single name is returned unchanged.

    Lives in app/models, not app/routes/spaces/: `spaces_for_department()`
    must import nothing from app.routes, and `build_space_rows()` needs the
    same rule, so the one function both call has to sit somewhere neither
    import path runs through.
    """
    if not names:
        return ""
    if len(names) == 1:
        return names[0]

    split = _split_shared_prefix(names)
    if split is None:
        return " + ".join(names)
    prefix, tails = split
    return f"{prefix} {'/'.join(tails)}"


def compose_aliased_name(alias: str, names: list[str]) -> str:
    """Name an aliased space as its alias plus the rooms it occupies.

    The alias alone left departments unable to tell two same-named spaces
    apart, so the physical name follows in brackets: "LAN (Maryland 1-6, A,
    B, D)". A run of three or more consecutive numbers or single letters
    collapses to a range inside the brackets only; an unaliased combined
    space keeps compose_combined_name()'s venue form, "RiverView 1/2/3".
    """
    location = _compose_ranged_name(names)
    if not location or location == alias:
        return alias
    return f"{alias} ({location})"


def _split_shared_prefix(names: list[str]) -> tuple[str, list[str]] | None:
    """Return the shared leading words and each name's tail, or None."""
    word_lists = [name.split() for name in names]
    shortest = min(len(words) for words in word_lists)
    shared = 0
    while shared < shortest and len({words[shared] for words in word_lists}) == 1:
        shared += 1

    # A prefix that consumes an entire member's name leaves that member no
    # tail to join. Fall back rather than produce "Chesapeake /5".
    if shared == 0 or shared == shortest:
        return None
    prefix = " ".join(word_lists[0][:shared])
    return prefix, [" ".join(words[shared:]) for words in word_lists]


def _range_key(tail: str) -> tuple[str, int] | None:
    if tail.isdigit():
        return "number", int(tail)
    if len(tail) == 1 and tail.isalpha():
        return "letter", ord(tail.upper())
    return None


def _compose_ranged_name(names: list[str]) -> str:
    """compose_combined_name(), with runs of three or more shown as ranges.

    Members keep the order the caller passes, which is Event Ops'
    sort_order; sorting here would produce a range the venue map does not
    show. Any tail that is not a plain number or a single letter leaves the
    slash form unchanged.
    """
    split = _split_shared_prefix(names) if len(names) > 1 else None
    if split is None:
        return compose_combined_name(names)
    prefix, tails = split
    keys = [_range_key(tail) for tail in tails]
    if any(key is None for key in keys):
        return compose_combined_name(names)

    runs: list[list[str]] = []
    for index, tail in enumerate(tails):
        previous = keys[index - 1] if index else None
        kind, value = keys[index]
        if previous and previous == (kind, value - 1):
            runs[-1].append(tail)
        else:
            runs.append([tail])

    if all(len(run) < 3 for run in runs):
        return compose_combined_name(names)
    pieces = []
    for run in runs:
        if len(run) >= 3:
            pieces.append(f"{run[0]}-{run[-1]}")
        else:
            pieces.extend(run)
    return f"{prefix} {', '.join(pieces)}"


class Venue(db.Model):
    """A physical venue. MAGFest runs events at roughly five of these."""
    __tablename__ = "venues"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False, index=True)
    name = db.Column(db.String(128), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    notes = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by_user_id = db.Column(db.String(64), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow,
                           onupdate=datetime.utcnow)
    updated_by_user_id = db.Column(db.String(64), nullable=True)


class Space(db.Model):
    """One room, slice, or pop-up at a venue."""
    __tablename__ = "spaces"

    id = db.Column(db.Integer, primary_key=True)

    venue_id = db.Column(
        db.Integer,
        db.ForeignKey("venues.id", name="fk_spaces_venue_id"),
        nullable=False,
        index=True,
    )

    parent_id = db.Column(
        db.Integer,
        db.ForeignKey("spaces.id", name="fk_spaces_parent_id"),
        nullable=True,
        index=True,
    )

    # Not a scope column. It marks a pop-up created for one event, so that
    # one-off space stops appearing in this venue's list in later events.
    # A permanent room leaves it NULL.
    event_cycle_id = db.Column(
        db.Integer,
        db.ForeignKey("event_cycles.id", name="fk_spaces_event_cycle_id"),
        nullable=True,
        index=True,
    )

    name = db.Column(db.String(128), nullable=False)
    code = db.Column(db.String(32), nullable=False)
    kind = db.Column(db.String(16), nullable=False)

    # Verbatim from the venue sheet, "35x57x22". Not parsed.
    dimensions = db.Column(db.Text, nullable=True)
    area_sqft = db.Column(db.Integer, nullable=True)

    # Where a free-form space physically sits, since it has no parent room.
    location_note = db.Column(db.Text, nullable=True)

    sort_order = db.Column(db.Integer, nullable=True)

    # Retired at the venue, all events. Per-event unavailability is a
    # SpaceEventOverride row instead.
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by_user_id = db.Column(db.String(64), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow,
                           onupdate=datetime.utcnow)
    updated_by_user_id = db.Column(db.String(64), nullable=True)

    venue = db.relationship("Venue", backref="spaces")
    # No cascade here on purpose: deleting a room must not delete its
    # slices. create_space rejects a non-ROOM parent, so a slice is never
    # parented to anything else.
    parent = db.relationship("Space", remote_side=[id], backref="children")
    event_cycle = db.relationship("EventCycle")

    __table_args__ = (
        # A permanent row's code is unique at its venue. An event-scoped row
        # is unique within its event, because two years may both use the
        # code POP-1 for a pop-up they each built.
        db.Index("ix_spaces_code_permanent", "venue_id", "code",
                 unique=True,
                 sqlite_where=db.text("event_cycle_id IS NULL"),
                 postgresql_where=db.text("event_cycle_id IS NULL")),
        db.Index("ix_spaces_code_per_event", "venue_id", "code",
                 "event_cycle_id", unique=True,
                 sqlite_where=db.text("event_cycle_id IS NOT NULL"),
                 postgresql_where=db.text("event_cycle_id IS NOT NULL")),
    )


class SpaceEventOverride(db.Model):
    """Per-event name, availability, and combining for one space.

    This is not a status row for every space. A row exists only when a space
    is renamed, unavailable, or combined for one event; absence means the
    venue name applies, the space is available, and it stands alone.
    Resolution goes through get_effective_space_name(), mirroring
    ExpenseAccountEventOverride.
    """
    __tablename__ = "space_event_overrides"

    id = db.Column(db.Integer, primary_key=True)

    space_id = db.Column(
        db.Integer,
        db.ForeignKey("spaces.id", name="fk_space_event_overrides_space_id"),
        nullable=False,
        index=True,
    )
    event_cycle_id = db.Column(
        db.Integer,
        db.ForeignKey("event_cycles.id",
                      name="fk_space_event_overrides_event_cycle_id"),
        nullable=False,
        index=True,
    )

    # What the space is called this event. Expo Hall B is "Consoles" to every
    # department in it, so the name belongs here and not on the assignment.
    alias = db.Column(db.String(128), nullable=True)

    # A room out of service this event. It is excluded from the Unassigned
    # count, so Event Ops is not nagged to assign a closed room.
    is_available = db.Column(db.Boolean, nullable=False, default=True)
    unavailable_reason = db.Column(db.Text, nullable=True)

    # The slice this row's space folds into, for this event only. NULL means
    # the space stands alone. Set only on a SLICE; a room or a slice with
    # nothing pointing at it is a primary or standalone by definition, not a
    # separate flag. Same room only, and no chains: a slice already pointed
    # at cannot itself point elsewhere. Both are enforced by the caller, not
    # by this column, since SQLite checks neither.
    combined_into_space_id = db.Column(
        db.Integer,
        db.ForeignKey("spaces.id",
                      name="fk_space_event_overrides_combined_into_space_id"),
        nullable=True,
        index=True,
    )

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by_user_id = db.Column(db.String(64), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow,
                           onupdate=datetime.utcnow)
    updated_by_user_id = db.Column(db.String(64), nullable=True)

    # Cascades so a deleted space cannot leave an override row pointing at
    # nothing; space_id is NOT NULL. Two FKs to spaces, so each relationship
    # names its own column.
    space = db.relationship(
        "Space", foreign_keys=[space_id],
        backref=db.backref("event_overrides", cascade="all, delete-orphan"))
    combined_into = db.relationship(
        "Space", foreign_keys=[combined_into_space_id],
        backref="combined_members")
    event_cycle = db.relationship("EventCycle")

    __table_args__ = (
        db.UniqueConstraint("space_id", "event_cycle_id",
                            name="uq_space_event_override"),
    )


class SpaceAssignment(db.Model):
    """One department holding one space for one event. Resets each cycle."""
    __tablename__ = "space_assignments"

    id = db.Column(db.Integer, primary_key=True)

    space_id = db.Column(
        db.Integer,
        db.ForeignKey("spaces.id", name="fk_space_assignments_space_id"),
        nullable=False,
        index=True,
    )
    event_cycle_id = db.Column(
        db.Integer,
        db.ForeignKey("event_cycles.id",
                      name="fk_space_assignments_event_cycle_id"),
        nullable=False,
        index=True,
    )
    department_id = db.Column(
        db.Integer,
        db.ForeignKey("departments.id",
                      name="fk_space_assignments_department_id"),
        nullable=False,
        index=True,
    )

    notes = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by_user_id = db.Column(db.String(64), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow,
                           onupdate=datetime.utcnow)
    updated_by_user_id = db.Column(db.String(64), nullable=True)

    # Same cascade reasoning as SpaceEventOverride.space: a deleted space
    # must not leave an assignment row behind it.
    space = db.relationship(
        "Space", backref=db.backref("assignments",
                                    cascade="all, delete-orphan"))
    event_cycle = db.relationship("EventCycle")
    department = db.relationship("Department")

    __table_args__ = (
        db.UniqueConstraint("space_id", "event_cycle_id", "department_id",
                            name="uq_space_assignment_per_event"),
    )
