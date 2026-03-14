"""
Core organization models: Events, Divisions, Departments, Users, Memberships.

These are the foundational models that other models reference.
"""
from __future__ import annotations

from datetime import datetime

from app import db


class EventCycle(db.Model):
    __tablename__ = "event_cycles"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False, index=True)   # e.g. SMF2027
    name = db.Column(db.String(128), nullable=False)

    is_active = db.Column(db.Boolean, nullable=False, default=True)
    is_default = db.Column(db.Boolean, nullable=False, default=False)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    # Key dates for the budget cycle
    event_start_date = db.Column(db.Date, nullable=True)          # When the event starts
    event_end_date = db.Column(db.Date, nullable=True)            # When the event ends
    submission_deadline = db.Column(db.Date, nullable=True)       # Budget submission deadline
    approval_target_date = db.Column(db.Date, nullable=True)      # Target for completing approvals
    finalization_date = db.Column(db.Date, nullable=True)         # When budgets are locked/finalized

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by_user_id = db.Column(db.String(64), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_user_id = db.Column(db.String(64), nullable=True)


class Division(db.Model):
    __tablename__ = "divisions"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False, index=True)
    name = db.Column(db.String(128), nullable=False)
    description = db.Column(db.Text, nullable=True)

    is_active = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    departments = db.relationship("Department", backref="division", lazy=True)
    memberships = db.relationship("DivisionMembership", backref="division", lazy=True)


class Department(db.Model):
    __tablename__ = "departments"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False, index=True)  # TECHOPS, HOTELS, etc.
    name = db.Column(db.String(128), nullable=False)

    division_id = db.Column(
        db.Integer,
        db.ForeignKey("divisions.id", name="fk_departments_division_id"),
        nullable=True,  # Nullable for backwards compat
        index=True,
    )

    description = db.Column(db.Text, nullable=True)
    mailing_list = db.Column(db.String(256), nullable=True)
    slack_channel = db.Column(db.String(128), nullable=True)

    is_active = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by_user_id = db.Column(db.String(64), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_user_id = db.Column(db.String(64), nullable=True)


class User(db.Model):
    __tablename__ = "users"

    # Keep string PK to align with auth subject / internal IDs
    id = db.Column(db.String(64), primary_key=True)

    email = db.Column(db.String(256), nullable=False, unique=True, index=True)
    auth_subject = db.Column(db.String(255), nullable=True, unique=True, index=True)

    display_name = db.Column(db.String(128), nullable=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    roles = db.relationship("UserRole", backref="user", cascade="all, delete-orphan", lazy=True)


class DivisionMembership(db.Model):
    """
    Division-level membership grants permissions across ALL departments in a division.
    Division heads can view/edit/submit requests for any department in their division.
    Access is still scoped by work type via DivisionMembershipWorkTypeAccess.
    """
    __tablename__ = "division_memberships"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.String(64),
        db.ForeignKey("users.id", name="fk_division_memberships_user_id"),
        nullable=False,
        index=True,
    )

    division_id = db.Column(
        db.Integer,
        db.ForeignKey("divisions.id", name="fk_division_memberships_division_id"),
        nullable=False,
        index=True,
    )

    event_cycle_id = db.Column(
        db.Integer,
        db.ForeignKey("event_cycles.id", name="fk_division_memberships_event_cycle_id"),
        nullable=False,
        index=True,
    )

    is_division_head = db.Column(db.Boolean, nullable=False, default=False)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship("User", backref=db.backref("division_memberships", lazy=True))
    event_cycle = db.relationship("EventCycle")

    work_type_access = db.relationship(
        "DivisionMembershipWorkTypeAccess",
        backref="membership",
        cascade="all, delete-orphan",
        lazy=True,
    )

    __table_args__ = (
        db.UniqueConstraint(
            "user_id", "division_id", "event_cycle_id",
            name="uq_division_membership_user_div_cycle",
        ),
    )

    def get_work_type_access(self, work_type_id: int):
        """Get work type access for this membership, or None if no access."""
        for wta in self.work_type_access:
            if wta.work_type_id == work_type_id:
                return wta
        return None

    def can_view_work_type(self, work_type_id: int) -> bool:
        """Check if this membership grants view access to a work type."""
        wta = self.get_work_type_access(work_type_id)
        return wta is not None and wta.can_view

    def can_edit_work_type(self, work_type_id: int) -> bool:
        """Check if this membership grants edit access to a work type."""
        wta = self.get_work_type_access(work_type_id)
        return wta is not None and wta.can_edit


class DivisionMembershipWorkTypeAccess(db.Model):
    """
    Work type-specific access within a division membership.

    A user's division membership grants potential access to all departments
    in the division, but they only see/edit work types explicitly granted here.
    """
    __tablename__ = "division_membership_work_type_access"

    id = db.Column(db.Integer, primary_key=True)

    division_membership_id = db.Column(
        db.Integer,
        db.ForeignKey("division_memberships.id", name="fk_divmwta_membership_id"),
        nullable=False,
        index=True,
    )

    work_type_id = db.Column(
        db.Integer,
        db.ForeignKey("work_types.id", name="fk_divmwta_work_type_id"),
        nullable=False,
        index=True,
    )

    can_view = db.Column(db.Boolean, nullable=False, default=True)
    can_edit = db.Column(db.Boolean, nullable=False, default=False)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    work_type = db.relationship("WorkType")

    __table_args__ = (
        db.UniqueConstraint(
            "division_membership_id", "work_type_id",
            name="uq_divmwta_membership_work_type",
        ),
    )


class DepartmentMembership(db.Model):
    __tablename__ = "department_memberships"

    id = db.Column(db.Integer, primary_key=True)

    user_id = db.Column(
        db.String(64),
        db.ForeignKey("users.id", name="fk_dept_memberships_user_id"),
        nullable=False,
        index=True,
    )

    department_id = db.Column(
        db.Integer,
        db.ForeignKey("departments.id", name="fk_dept_memberships_department_id"),
        nullable=False,
        index=True,
    )

    event_cycle_id = db.Column(
        db.Integer,
        db.ForeignKey("event_cycles.id", name="fk_dept_memberships_event_cycle_id"),
        nullable=False,
        index=True,
    )

    is_department_head = db.Column(db.Boolean, nullable=False, default=False)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    user = db.relationship("User", backref=db.backref("department_memberships", lazy=True))
    department = db.relationship("Department")
    event_cycle = db.relationship("EventCycle")

    work_type_access = db.relationship(
        "DepartmentMembershipWorkTypeAccess",
        backref="membership",
        cascade="all, delete-orphan",
        lazy=True,
    )

    __table_args__ = (
        db.UniqueConstraint(
            "user_id", "department_id", "event_cycle_id",
            name="uq_dept_membership_user_dept_cycle",
        ),
    )

    def get_work_type_access(self, work_type_id: int):
        """Get work type access for this membership, or None if no access."""
        for wta in self.work_type_access:
            if wta.work_type_id == work_type_id:
                return wta
        return None

    def can_view_work_type(self, work_type_id: int) -> bool:
        """Check if this membership grants view access to a work type."""
        wta = self.get_work_type_access(work_type_id)
        return wta is not None and wta.can_view

    def can_edit_work_type(self, work_type_id: int) -> bool:
        """Check if this membership grants edit access to a work type."""
        wta = self.get_work_type_access(work_type_id)
        return wta is not None and wta.can_edit


class DepartmentMembershipWorkTypeAccess(db.Model):
    """
    Work type-specific access within a department membership.

    A user's department membership grants potential access, but they only
    see/edit work types explicitly granted here.
    """
    __tablename__ = "department_membership_work_type_access"

    id = db.Column(db.Integer, primary_key=True)

    department_membership_id = db.Column(
        db.Integer,
        db.ForeignKey("department_memberships.id", name="fk_dmwta_membership_id"),
        nullable=False,
        index=True,
    )

    work_type_id = db.Column(
        db.Integer,
        db.ForeignKey("work_types.id", name="fk_dmwta_work_type_id"),
        nullable=False,
        index=True,
    )

    can_view = db.Column(db.Boolean, nullable=False, default=True)
    can_edit = db.Column(db.Boolean, nullable=False, default=False)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    work_type = db.relationship("WorkType")

    __table_args__ = (
        db.UniqueConstraint(
            "department_membership_id", "work_type_id",
            name="uq_dmwta_membership_work_type",
        ),
    )


class EventCycleDivision(db.Model):
    """
    Controls which divisions participate in which events.

    Key behavior:
    - No record = ENABLED (backward compatible)
    - Division disabled = ALL departments in that division are disabled
    - Only divisions explicitly disabled via is_enabled=False are excluded
    """
    __tablename__ = "event_cycle_divisions"

    id = db.Column(db.Integer, primary_key=True)

    event_cycle_id = db.Column(
        db.Integer,
        db.ForeignKey("event_cycles.id", name="fk_ecd_event_cycle_id"),
        nullable=False,
        index=True,
    )

    division_id = db.Column(
        db.Integer,
        db.ForeignKey("divisions.id", name="fk_ecd_division_id"),
        nullable=False,
        index=True,
    )

    is_enabled = db.Column(db.Boolean, nullable=False, default=True)
    note = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by_user_id = db.Column(db.String(64), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_user_id = db.Column(db.String(64), nullable=True)

    event_cycle = db.relationship("EventCycle", backref=db.backref("division_enablements", lazy=True))
    division = db.relationship("Division", backref=db.backref("event_enablements", lazy=True))

    __table_args__ = (
        db.UniqueConstraint(
            "event_cycle_id", "division_id",
            name="uq_ecdiv_event_div",
        ),
    )


class EventCycleDepartment(db.Model):
    """
    Controls which departments participate in which events.

    Key behavior:
    - No record = ENABLED (backward compatible)
    - Department can only be enabled if its division is also enabled
    - If division is disabled, all its departments are disabled regardless of this record
    """
    __tablename__ = "event_cycle_departments"

    id = db.Column(db.Integer, primary_key=True)

    event_cycle_id = db.Column(
        db.Integer,
        db.ForeignKey("event_cycles.id", name="fk_ecdept_event_cycle_id"),
        nullable=False,
        index=True,
    )

    department_id = db.Column(
        db.Integer,
        db.ForeignKey("departments.id", name="fk_ecdept_department_id"),
        nullable=False,
        index=True,
    )

    is_enabled = db.Column(db.Boolean, nullable=False, default=True)
    note = db.Column(db.Text, nullable=True)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by_user_id = db.Column(db.String(64), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_user_id = db.Column(db.String(64), nullable=True)

    event_cycle = db.relationship("EventCycle", backref=db.backref("department_enablements", lazy=True))
    department = db.relationship("Department", backref=db.backref("event_enablements", lazy=True))

    __table_args__ = (
        db.UniqueConstraint(
            "event_cycle_id", "department_id",
            name="uq_ecd_event_dept",
        ),
    )
