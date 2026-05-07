"""
Space and SpaceDepartmentAssignment models.

A Space is a per-event physical area (panel room, main stage, etc.).
SpaceDepartmentAssignment is the M2M link between departments and spaces;
each assignment grants a department visibility into the space and the
right to file AV requests for it.

Spaces are managed centrally by WORKTYPE_ADMIN(AV) holders (event
leadership). Soft-delete only on assignments — set unassigned_at instead
of deleting rows, so audit trail is preserved.
"""
from __future__ import annotations

from datetime import datetime

from app import db


class Space(db.Model):
    """A physical event space, scoped to one EventCycle."""
    __tablename__ = "spaces"

    id = db.Column(db.Integer, primary_key=True)
    event_cycle_id = db.Column(
        db.Integer,
        db.ForeignKey("event_cycles.id", name="fk_spaces_event_cycle_id"),
        nullable=False,
        index=True,
    )
    code = db.Column(db.String(64), nullable=False)
    name = db.Column(db.String(128), nullable=False)
    location = db.Column(db.String(256), nullable=True)
    square_feet = db.Column(db.Integer, nullable=True)
    push_in_at = db.Column(db.DateTime, nullable=True)
    push_out_at = db.Column(db.DateTime, nullable=True)
    notes = db.Column(db.Text, nullable=True)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by_user_id = db.Column(db.String(64), nullable=False)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_user_id = db.Column(db.String(64), nullable=True)

    event_cycle = db.relationship("EventCycle")
    assignments = db.relationship(
        "SpaceDepartmentAssignment",
        back_populates="space",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        db.UniqueConstraint("event_cycle_id", "code", name="uq_spaces_event_cycle_id_code"),
    )


class SpaceDepartmentAssignment(db.Model):
    """M2M: which departments are assigned to which spaces.

    Soft-deletable: set unassigned_at + unassigned_by_user_id rather than
    deleting the row, so audit trail is preserved. Active queries should
    filter `unassigned_at IS NULL`.
    """
    __tablename__ = "space_department_assignments"

    id = db.Column(db.Integer, primary_key=True)
    space_id = db.Column(
        db.Integer,
        db.ForeignKey("spaces.id", name="fk_space_department_assignments_space_id"),
        nullable=False,
        index=True,
    )
    department_id = db.Column(
        db.Integer,
        db.ForeignKey("departments.id", name="fk_space_department_assignments_department_id"),
        nullable=False,
        index=True,
    )
    assigned_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    assigned_by_user_id = db.Column(db.String(64), nullable=False)
    unassigned_at = db.Column(db.DateTime, nullable=True)
    unassigned_by_user_id = db.Column(db.String(64), nullable=True)
    notes = db.Column(db.Text, nullable=True)

    space = db.relationship("Space", back_populates="assignments")
    department = db.relationship("Department")

    __table_args__ = (
        db.Index("ix_space_department_assignments_space_dept", "space_id", "department_id"),
        db.Index("ix_space_department_assignments_active", "department_id", "unassigned_at"),
    )
