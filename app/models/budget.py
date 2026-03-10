"""
Budget-specific models: spend types, expense accounts, budget line details.

These models support the budget request workflow.
"""
from __future__ import annotations

from datetime import datetime
from sqlalchemy import Numeric

from app import db
from .constants import (
    SPEND_TYPE_MODE_ALLOW_LIST,
    VISIBILITY_MODE_ALL,
    PROMPT_MODE_NONE,
)


class SpendType(db.Model):
    __tablename__ = "spend_types"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False, index=True)   # DIVVY, BANK
    name = db.Column(db.String(64), nullable=False)                            # Divvy, Bank
    description = db.Column(db.Text, nullable=True)

    is_active = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by_user_id = db.Column(db.String(64), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_user_id = db.Column(db.String(64), nullable=True)


class FrequencyOption(db.Model):
    __tablename__ = "frequency_options"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False, index=True)   # ONE_TIME, RECURRING
    name = db.Column(db.String(64), nullable=False)
    description = db.Column(db.Text, nullable=True)

    is_active = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by_user_id = db.Column(db.String(64), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_user_id = db.Column(db.String(64), nullable=True)


class ConfidenceLevel(db.Model):
    __tablename__ = "confidence_levels"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False, index=True)
    name = db.Column(db.String(64), nullable=False)
    description = db.Column(db.Text, nullable=True)

    is_active = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by_user_id = db.Column(db.String(64), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_user_id = db.Column(db.String(64), nullable=True)


class PriorityLevel(db.Model):
    __tablename__ = "priority_levels"

    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(32), unique=True, nullable=False, index=True)
    name = db.Column(db.String(64), nullable=False)
    description = db.Column(db.Text, nullable=True)

    is_active = db.Column(db.Boolean, nullable=False, default=True)
    sort_order = db.Column(db.Integer, nullable=False, default=0)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by_user_id = db.Column(db.String(64), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_user_id = db.Column(db.String(64), nullable=True)


class ExpenseAccount(db.Model):
    __tablename__ = "expense_accounts"

    id = db.Column(db.Integer, primary_key=True)

    code = db.Column(db.String(64), unique=True, nullable=False, index=True)
    name = db.Column(db.String(128), nullable=False)
    quickbooks_account_name = db.Column(db.String(128), nullable=True)
    description = db.Column(db.Text, nullable=True)

    is_active = db.Column(db.Boolean, nullable=False, default=True)

    # Cross-module (future): approved budget in this account counts toward contract-eligible budget
    is_contract_eligible = db.Column(db.Boolean, nullable=False, default=False, index=True)

    # Spend type behavior (base defaults)
    spend_type_mode = db.Column(db.String(32), nullable=False, default=SPEND_TYPE_MODE_ALLOW_LIST, index=True)

    default_spend_type_id = db.Column(
        db.Integer,
        db.ForeignKey("spend_types.id", name="fk_expense_accounts_default_spend_type_id"),
        nullable=True,
        index=True,
    )

    # Department visibility behavior (base defaults)
    visibility_mode = db.Column(db.String(32), nullable=False, default=VISIBILITY_MODE_ALL, index=True)

    # Budget approval routing (L1 approvals)
    approval_group_id = db.Column(
        db.Integer,
        db.ForeignKey("approval_groups.id", name="fk_expense_accounts_approval_group_id"),
        nullable=True,
        index=True,
    )

    # Fixed-cost behavior (base defaults; per-event overrides are preferred when pricing changes)
    is_fixed_cost = db.Column(db.Boolean, nullable=False, default=False, index=True)

    default_unit_price_cents = db.Column(db.Integer, nullable=True)
    unit_price_locked = db.Column(db.Boolean, nullable=False, default=False)

    default_frequency_id = db.Column(
        db.Integer,
        db.ForeignKey("frequency_options.id", name="fk_expense_accounts_default_frequency_id"),
        nullable=True,
        index=True,
    )
    frequency_locked = db.Column(db.Boolean, nullable=False, default=False)

    warehouse_default = db.Column(db.Boolean, nullable=False, default=False)

    # UI grouping + prompting
    ui_display_group = db.Column(db.String(32), nullable=True, index=True)
    prompt_mode = db.Column(db.String(32), nullable=False, default=PROMPT_MODE_NONE, index=True)

    sort_order = db.Column(db.Integer, nullable=False, default=0)

    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    created_by_user_id = db.Column(db.String(64), nullable=True)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
    updated_by_user_id = db.Column(db.String(64), nullable=True)

    default_spend_type = db.relationship("SpendType", foreign_keys=[default_spend_type_id])
    default_frequency = db.relationship("FrequencyOption", foreign_keys=[default_frequency_id])
    approval_group = db.relationship("ApprovalGroup", foreign_keys=[approval_group_id])

    allowed_spend_types = db.relationship(
        "SpendType",
        secondary="expense_account_spend_types",
        backref=db.backref("expense_accounts", lazy=True),
        lazy=True,
    )

    visible_to_departments = db.relationship(
        "Department",
        secondary="expense_account_departments",
        backref=db.backref("restricted_expense_accounts", lazy=True),
        lazy=True,
    )

    event_overrides = db.relationship(
        "ExpenseAccountEventOverride",
        backref="expense_account",
        cascade="all, delete-orphan",
        lazy=True,
    )


class ExpenseAccountSpendType(db.Model):
    __tablename__ = "expense_account_spend_types"

    expense_account_id = db.Column(
        db.Integer,
        db.ForeignKey("expense_accounts.id", name="fk_east_expense_account_id"),
        primary_key=True,
    )
    spend_type_id = db.Column(
        db.Integer,
        db.ForeignKey("spend_types.id", name="fk_east_spend_type_id"),
        primary_key=True,
    )


class ExpenseAccountDepartment(db.Model):
    __tablename__ = "expense_account_departments"

    expense_account_id = db.Column(
        db.Integer,
        db.ForeignKey("expense_accounts.id", name="fk_ead_expense_account_id"),
        primary_key=True,
    )
    department_id = db.Column(
        db.Integer,
        db.ForeignKey("departments.id", name="fk_ead_department_id"),
        primary_key=True,
    )


class ExpenseAccountEventOverride(db.Model):
    __tablename__ = "expense_account_event_overrides"

    id = db.Column(db.Integer, primary_key=True)

    expense_account_id = db.Column(
        db.Integer,
        db.ForeignKey("expense_accounts.id", name="fk_eaeo_expense_account_id"),
        nullable=False,
        index=True,
    )

    event_cycle_id = db.Column(
        db.Integer,
        db.ForeignKey("event_cycles.id", name="fk_eaeo_event_cycle_id"),
        nullable=False,
        index=True,
    )

    # Override fields (nullable = inherit from base ExpenseAccount)
    is_fixed_cost = db.Column(db.Boolean, nullable=True)

    default_unit_price_cents = db.Column(db.Integer, nullable=True)
    unit_price_locked = db.Column(db.Boolean, nullable=True)

    default_frequency_id = db.Column(
        db.Integer,
        db.ForeignKey("frequency_options.id", name="fk_eaeo_default_frequency_id"),
        nullable=True,
        index=True,
    )
    frequency_locked = db.Column(db.Boolean, nullable=True)

    warehouse_default = db.Column(db.Boolean, nullable=True)

    default_spend_type_id = db.Column(
        db.Integer,
        db.ForeignKey("spend_types.id", name="fk_eaeo_default_spend_type_id"),
        nullable=True,
        index=True,
    )

    ui_display_group = db.Column(db.String(32), nullable=True, index=True)
    prompt_mode = db.Column(db.String(32), nullable=True, index=True)

    # Per-event description override (replaces base account description if set)
    description = db.Column(db.Text, nullable=True)

    __table_args__ = (
        db.UniqueConstraint("expense_account_id", "event_cycle_id", name="uq_eaeo_account_event"),
    )

    event_cycle = db.relationship("EventCycle")
    default_frequency = db.relationship("FrequencyOption", foreign_keys=[default_frequency_id])
    default_spend_type = db.relationship("SpendType", foreign_keys=[default_spend_type_id])


class BudgetLineDetail(db.Model):
    __tablename__ = "budget_line_details"

    work_line_id = db.Column(
        db.Integer,
        db.ForeignKey("work_lines.id", name="fk_budget_line_details_work_line_id"),
        primary_key=True,
    )

    expense_account_id = db.Column(
        db.Integer,
        db.ForeignKey("expense_accounts.id", name="fk_budget_line_details_expense_account_id"),
        nullable=False,
        index=True,
    )

    # Snapshot of routing at submission/review time (prevents history drift if mappings change)
    routed_approval_group_id = db.Column(
        db.Integer,
        db.ForeignKey("approval_groups.id", name="fk_budget_line_details_routed_approval_group_id"),
        nullable=True,
        index=True,
    )

    spend_type_id = db.Column(
        db.Integer,
        db.ForeignKey("spend_types.id", name="fk_budget_line_details_spend_type_id"),
        nullable=False,
        index=True,
    )

    unit_price_cents = db.Column(db.Integer, nullable=False)
    quantity = db.Column(Numeric(12, 3), nullable=False, default=1)

    confidence_level_id = db.Column(
        db.Integer,
        db.ForeignKey("confidence_levels.id", name="fk_budget_line_details_confidence_level_id"),
        nullable=True,
        index=True,
    )

    frequency_id = db.Column(
        db.Integer,
        db.ForeignKey("frequency_options.id", name="fk_budget_line_details_frequency_id"),
        nullable=True,
        index=True,
    )

    warehouse_flag = db.Column(db.Boolean, nullable=False, default=False, index=True)

    priority_id = db.Column(
        db.Integer,
        db.ForeignKey("priority_levels.id", name="fk_budget_line_details_priority_id"),
        nullable=True,
        index=True,
    )

    description = db.Column(db.Text, nullable=True)

    work_line = db.relationship("WorkLine", backref=db.backref("budget_detail", uselist=False))
    expense_account = db.relationship("ExpenseAccount")
    routed_approval_group = db.relationship("ApprovalGroup", foreign_keys=[routed_approval_group_id])
    spend_type = db.relationship("SpendType")
    confidence_level = db.relationship("ConfidenceLevel")
    frequency = db.relationship("FrequencyOption")
    priority = db.relationship("PriorityLevel")

    __table_args__ = (
        # Composite index for approval group workload queries
        db.Index("ix_budget_line_details_approval_routing", "routed_approval_group_id", "expense_account_id"),
    )
