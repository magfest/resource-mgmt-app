"""
Expense account query helpers.

Functions for retrieving expense accounts with visibility filtering.
"""
from __future__ import annotations

from sqlalchemy import or_

from app.models import (
    ExpenseAccount,
    SpendType,
    ConfidenceLevel,
    FrequencyOption,
    PriorityLevel,
    UI_GROUP_HOTEL_SERVICES,
    VISIBILITY_MODE_ALL,
    SPEND_TYPE_MODE_SINGLE_LOCKED,
)


# ============================================================
# Expense Account Queries
# ============================================================

def get_visible_expense_accounts(
    department_id: int,
    event_cycle_id: int | None = None,
    exclude_fixed: bool = True,
) -> list[ExpenseAccount]:
    """
    Get expense accounts visible to a department.

    Args:
        department_id: Department to filter for
        event_cycle_id: Optional event cycle for overrides (not used in Chunk A)
        exclude_fixed: If True, excludes fixed-cost accounts (Chunk A only)
    """
    query = ExpenseAccount.query.filter(ExpenseAccount.is_active == True)

    if exclude_fixed:
        query = query.filter(ExpenseAccount.is_fixed_cost == False)

    # Filter by visibility mode
    query = query.filter(
        or_(
            ExpenseAccount.visibility_mode == VISIBILITY_MODE_ALL,
            ExpenseAccount.visible_to_departments.any(id=department_id)
        )
    )

    accounts = query.order_by(
        ExpenseAccount.sort_order.asc(),
        ExpenseAccount.name.asc()
    ).all()

    return accounts


def get_fixed_cost_expense_accounts(
    department_id: int,
    event_cycle_id: int | None = None,
) -> list[ExpenseAccount]:
    """
    Get fixed-cost expense accounts visible to a department.

    These are accounts where is_fixed_cost=True, meaning the unit price
    is predetermined and users only specify quantity.

    Args:
        department_id: Department to filter for
        event_cycle_id: Optional event cycle for overrides
    """
    query = ExpenseAccount.query.filter(
        ExpenseAccount.is_active == True,
        ExpenseAccount.is_fixed_cost == True,
    )

    # Filter by visibility mode
    query = query.filter(
        or_(
            ExpenseAccount.visibility_mode == VISIBILITY_MODE_ALL,
            ExpenseAccount.visible_to_departments.any(id=department_id)
        )
    )

    accounts = query.order_by(
        ExpenseAccount.sort_order.asc(),
        ExpenseAccount.name.asc()
    ).all()

    return accounts


def get_hotel_service_expense_accounts(
    department_id: int,
    event_cycle_id: int | None = None,
) -> list[ExpenseAccount]:
    """
    Get hotel/Gaylord expense accounts (UI_GROUP_HOTEL_SERVICES).

    These are per-day/per-night costs where the calculator is useful.

    Args:
        department_id: Department to filter for
        event_cycle_id: Optional event cycle for overrides
    """
    query = ExpenseAccount.query.filter(
        ExpenseAccount.is_active == True,
        ExpenseAccount.is_fixed_cost == True,
        ExpenseAccount.ui_display_group == UI_GROUP_HOTEL_SERVICES,
    )

    # Filter by visibility mode
    query = query.filter(
        or_(
            ExpenseAccount.visibility_mode == VISIBILITY_MODE_ALL,
            ExpenseAccount.visible_to_departments.any(id=department_id)
        )
    )

    accounts = query.order_by(
        ExpenseAccount.sort_order.asc(),
        ExpenseAccount.name.asc()
    ).all()

    return accounts


def get_non_hotel_fixed_cost_accounts(
    department_id: int,
    event_cycle_id: int | None = None,
) -> list[ExpenseAccount]:
    """
    Get fixed-cost accounts that are NOT hotel services.

    These stay in the Fixed Costs tab (one-time costs like Ethernet Drops).

    Args:
        department_id: Department to filter for
        event_cycle_id: Optional event cycle for overrides
    """
    query = ExpenseAccount.query.filter(
        ExpenseAccount.is_active == True,
        ExpenseAccount.is_fixed_cost == True,
        or_(
            ExpenseAccount.ui_display_group.is_(None),
            ExpenseAccount.ui_display_group != UI_GROUP_HOTEL_SERVICES,
        )
    )

    # Filter by visibility mode
    query = query.filter(
        or_(
            ExpenseAccount.visibility_mode == VISIBILITY_MODE_ALL,
            ExpenseAccount.visible_to_departments.any(id=department_id)
        )
    )

    accounts = query.order_by(
        ExpenseAccount.sort_order.asc(),
        ExpenseAccount.name.asc()
    ).all()

    return accounts


def get_effective_fixed_cost_settings(
    expense_account: ExpenseAccount,
    event_cycle_id: int | None = None,
) -> dict:
    """
    Get effective fixed-cost settings for an expense account,
    considering event-specific overrides.

    Returns dict with:
        - unit_price_cents: The locked unit price
        - frequency_id: Default frequency (if any)
        - warehouse_default: Default warehouse flag
    """
    # Start with base account settings
    unit_price_cents = expense_account.default_unit_price_cents or 0
    frequency_id = expense_account.default_frequency_id
    warehouse_default = expense_account.warehouse_default

    # Check for event-specific override
    if event_cycle_id:
        # Find override matching this event cycle
        override = None
        for o in expense_account.event_overrides:
            if o.event_cycle_id == event_cycle_id:
                override = o
                break

        if override:
            if override.default_unit_price_cents is not None:
                unit_price_cents = override.default_unit_price_cents
            if override.default_frequency_id is not None:
                frequency_id = override.default_frequency_id
            if override.warehouse_default is not None:
                warehouse_default = override.warehouse_default

    return {
        "unit_price_cents": unit_price_cents,
        "frequency_id": frequency_id,
        "warehouse_default": warehouse_default,
    }


def get_effective_description(
    expense_account: ExpenseAccount,
    event_cycle_id: int | None = None,
) -> str | None:
    """
    Get effective description for an expense account,
    considering event-specific overrides.

    If an override exists for the event cycle and has a description set,
    that description is returned. Otherwise, the base account description
    is returned.

    Args:
        expense_account: The expense account
        event_cycle_id: Optional event cycle to check for overrides

    Returns:
        The effective description, or None if not set.
    """
    if event_cycle_id:
        # Find override matching this event cycle
        for o in expense_account.event_overrides:
            if o.event_cycle_id == event_cycle_id:
                if o.description:
                    return o.description
                break

    return expense_account.description


def get_allowed_spend_types(expense_account: ExpenseAccount) -> list[SpendType]:
    """
    Get valid spend types for an expense account.

    For SINGLE_LOCKED mode, returns only the default spend type.
    For ALLOW_LIST mode, returns the allowed_spend_types list.
    """
    if expense_account.spend_type_mode == SPEND_TYPE_MODE_SINGLE_LOCKED:
        if expense_account.default_spend_type:
            return [expense_account.default_spend_type]
        return []

    # ALLOW_LIST mode
    return list(expense_account.allowed_spend_types)


# ============================================================
# Dropdown Data Helpers
# ============================================================

def get_confidence_levels() -> list[ConfidenceLevel]:
    """Get active confidence levels for dropdown."""
    return ConfidenceLevel.query.filter_by(is_active=True).order_by(
        ConfidenceLevel.sort_order.asc(),
        ConfidenceLevel.name.asc()
    ).all()


def get_frequency_options() -> list[FrequencyOption]:
    """Get active frequency options for dropdown."""
    return FrequencyOption.query.filter_by(is_active=True).order_by(
        FrequencyOption.sort_order.asc(),
        FrequencyOption.name.asc()
    ).all()


def get_priority_levels() -> list[PriorityLevel]:
    """Get active priority levels for dropdown."""
    return PriorityLevel.query.filter_by(is_active=True).order_by(
        PriorityLevel.sort_order.asc(),
        PriorityLevel.name.asc()
    ).all()


def get_spend_types() -> list[SpendType]:
    """Get active spend types for dropdown."""
    return SpendType.query.filter_by(is_active=True).order_by(
        SpendType.sort_order.asc(),
        SpendType.name.asc()
    ).all()
