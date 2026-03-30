"""
Work routes helpers package.

All helpers are re-exported here for backwards compatibility.
Existing imports like `from app.routes.work.helpers import get_portfolio_context` continue to work.

Module organization:
- context.py: Dataclasses + context/permission building
- checkout.py: Checkout/checkin functionality
- expense_accounts.py: Expense account queries + dropdowns
- computations.py: Totals + line status computation
- formatting.py: Status labels, currency, misc utilities
"""

# Re-export context/permission classes and functions
from .context import (
    # Dataclasses
    PortfolioContext,
    PortfolioPerms,
    WorkItemPerms,
    # Context building
    get_budget_work_type,
    get_work_type_by_slug,
    get_work_type_by_code,
    get_active_work_types,
    get_portfolio_context,
    # Permission building
    is_worktype_admin,
    is_budget_admin,
    build_portfolio_perms,
    # Permission enforcement
    require_portfolio_view,
    require_portfolio_edit,
    require_work_item_view,
    require_work_item_edit,
)

# Re-export checkout functions
from .checkout import (
    # Configuration
    DEFAULT_CHECKOUT_TIMEOUTS,
    get_checkout_timeouts,
    get_checkout_timeout_minutes,
    # Status functions
    is_checked_out,
    get_checkout_info,
    # Operations
    can_checkout,
    checkout_work_item,
    checkin_work_item,
    release_expired_checkouts,
    # Work item permissions
    build_work_item_perms,
    _is_approver_for_work_item,  # Used by work_items.py
)

# Re-export expense account functions
from .expense_accounts import (
    # Expense account queries
    get_visible_expense_accounts,
    get_fixed_cost_expense_accounts,
    get_hotel_service_expense_accounts,
    get_badge_expense_accounts,
    get_non_hotel_fixed_cost_accounts,
    get_effective_fixed_cost_settings,
    get_effective_description,
    get_allowed_spend_types,
    # Dropdown data
    get_confidence_levels,
    get_frequency_options,
    get_priority_levels,
    get_spend_types,
)

# Re-export computation functions
from .computations import (
    # Totals
    compute_portfolio_totals,
    compute_work_item_totals,
    # Line status
    LineStatusSummary,
    compute_line_status_summary,
    compute_portfolio_status_summary,
    compute_portfolio_status_from_loaded,
)

# Re-export formatting/utility functions
from .formatting import (
    # Public ID
    generate_public_id,
    generate_public_id_for_work_type,
    generate_public_id_for_portfolio,
    # Formatting
    format_currency,
    STATUS_LABELS,
    friendly_status,
    # Comment visibility
    get_comment_visibility,
    get_next_line_number,
    # Line filtering
    filter_lines_for_user,
    # Work item detail helpers
    get_kicked_back_lines_summary,
    get_unified_audit_events,
)

# Re-export event enablement functions
from .event_enablement import (
    # Division functions
    is_division_enabled_for_event,
    get_enabled_division_ids_for_event,
    get_division_enablement_record,
    set_division_enablement,
    get_all_division_enablement_records,
    # Department functions
    is_department_enabled_for_event,
    get_enabled_department_ids_for_event,
    get_enabled_departments_for_event,
    get_department_enablement_record,
    set_department_enablement,
    get_all_department_enablement_records,
    get_all_department_enabled_status,
    # Bulk operations
    copy_event_enablement,
    bulk_set_all_enabled,
)

# Define __all__ for explicit exports
__all__ = [
    # Dataclasses
    "PortfolioContext",
    "PortfolioPerms",
    "WorkItemPerms",
    "LineStatusSummary",
    # Context building
    "get_budget_work_type",
    "get_work_type_by_slug",
    "get_work_type_by_code",
    "get_active_work_types",
    "get_portfolio_context",
    # Permission building
    "is_worktype_admin",
    "is_budget_admin",
    "build_portfolio_perms",
    "build_work_item_perms",
    # Permission enforcement
    "require_portfolio_view",
    "require_portfolio_edit",
    "require_work_item_view",
    "require_work_item_edit",
    # Checkout configuration
    "DEFAULT_CHECKOUT_TIMEOUTS",
    "get_checkout_timeouts",
    "get_checkout_timeout_minutes",
    # Checkout status
    "is_checked_out",
    "get_checkout_info",
    # Checkout operations
    "can_checkout",
    "checkout_work_item",
    "checkin_work_item",
    "release_expired_checkouts",
    "_is_approver_for_work_item",
    # Expense accounts
    "get_visible_expense_accounts",
    "get_fixed_cost_expense_accounts",
    "get_hotel_service_expense_accounts",
    "get_badge_expense_accounts",
    "get_non_hotel_fixed_cost_accounts",
    "get_effective_fixed_cost_settings",
    "get_effective_description",
    "get_allowed_spend_types",
    # Dropdown data
    "get_confidence_levels",
    "get_frequency_options",
    "get_priority_levels",
    "get_spend_types",
    # Totals
    "compute_portfolio_totals",
    "compute_work_item_totals",
    # Line status
    "compute_line_status_summary",
    "compute_portfolio_status_summary",
    "compute_portfolio_status_from_loaded",
    # Public ID
    "generate_public_id",
    "generate_public_id_for_work_type",
    "generate_public_id_for_portfolio",
    # Formatting
    "format_currency",
    "STATUS_LABELS",
    "friendly_status",
    # Comment visibility
    "get_comment_visibility",
    "get_next_line_number",
    # Line filtering
    "filter_lines_for_user",
    # Work item detail helpers
    "get_kicked_back_lines_summary",
    "get_unified_audit_events",
    # Event enablement - divisions
    "is_division_enabled_for_event",
    "get_enabled_division_ids_for_event",
    "get_division_enablement_record",
    "set_division_enablement",
    "get_all_division_enablement_records",
    # Event enablement - departments
    "is_department_enabled_for_event",
    "get_enabled_department_ids_for_event",
    "get_enabled_departments_for_event",
    "get_department_enablement_record",
    "set_department_enablement",
    "get_all_department_enablement_records",
    "get_all_department_enabled_status",
    # Event enablement - bulk
    "copy_event_enablement",
    "bulk_set_all_enabled",
]
