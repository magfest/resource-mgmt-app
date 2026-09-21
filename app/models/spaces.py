"""
Venues, spaces, and per-event department assignments.

A space is a room, a named slice of a room, a free-form pop-up, or a
combination. Rooms and slices persist across events; a combination is
event-scoped, grouping member spaces for one event only. Assignments and
per-event names reset each cycle. Event Ops owns this data and holds the
SPACE_ADMIN role.

Capacity counts from the venue sheet are excluded; departments misread them
and no TechOps work uses them.
"""
from __future__ import annotations

from datetime import datetime

from app import db


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
    """One room, slice, combo, or pop-up at a venue.

    A combo is event-scoped: it groups member spaces for one event through
    SpaceCombinationMember and is not a parent of slices.
    """
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
    # slices. create_space rejects a non-ROOM parent so a slice can never
    # be parented to a combo, the one space kind this app hard-deletes.
    parent = db.relationship("Space", remote_side=[id], backref="children")
    event_cycle = db.relationship("EventCycle")

    __table_args__ = (
        # A permanent row's code is unique at its venue. An event-scoped row
        # is unique within its event, because two years may both use the
        # code CHES-JK for a combination they each built.
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
    """Per-event name and availability for one space.

    This is not a status row for every space. A row exists only when a space
    is renamed or unavailable for one event; absence means the venue name
    applies and the space is available. Resolution goes through
    get_effective_space_name(), mirroring ExpenseAccountEventOverride.
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

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by_user_id = db.Column(db.String(64), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow,
                           onupdate=datetime.utcnow)
    updated_by_user_id = db.Column(db.String(64), nullable=True)

    # Cascades so a deleted space, a dissolved combo, cannot leave an
    # override row pointing at nothing; space_id is NOT NULL.
    space = db.relationship(
        "Space", backref=db.backref("event_overrides",
                                    cascade="all, delete-orphan"))
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


class SpaceCombinationMember(db.Model):
    """One space inside an event's combination.

    Nothing here validates adjacency, shared parentage, or exclusive
    membership. Maryland A and C are a valid pair although B sits between
    them, and the admin owns correctness.
    """
    __tablename__ = "space_combination_members"

    id = db.Column(db.Integer, primary_key=True)

    combination_space_id = db.Column(
        db.Integer,
        db.ForeignKey("spaces.id", name="fk_scm_combination_space_id"),
        nullable=False,
        index=True,
    )
    member_space_id = db.Column(
        db.Integer,
        db.ForeignKey("spaces.id", name="fk_scm_member_space_id"),
        nullable=False,
        index=True,
    )

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by_user_id = db.Column(db.String(64), nullable=True)

    # Two foreign keys to one table, so each relationship names its own.
    combination = db.relationship(
        "Space", foreign_keys=[combination_space_id],
        backref=db.backref("combination_members",
                           cascade="all, delete-orphan"),
    )
    member = db.relationship(
        "Space", foreign_keys=[member_space_id],
        backref="member_of",
    )

    __table_args__ = (
        db.UniqueConstraint("combination_space_id", "member_space_id",
                            name="uq_space_combination_member"),
    )
