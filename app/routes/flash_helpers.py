"""
Flash message helpers for consistent user feedback.

Categories:
- success: Operation completed successfully (green)
- error: Operation failed, validation error, or user mistake (red)
- warning: Non-critical issue or no action taken (yellow)
- info: Informational message, no action required (blue)

Usage:
    from app.routes.flash_helpers import flash_success, flash_error

    flash_success("Budget request submitted.")
    flash_error("Name is required.")
    flash_warning("No changes detected.")
    flash_info("Email is disabled in development mode.")
"""
from flask import flash


def flash_success(message: str) -> None:
    """Flash a success message (green). Use for completed operations."""
    flash(message, "success")


def flash_error(message: str) -> None:
    """Flash an error message (red). Use for failures and validation errors."""
    flash(message, "error")


def flash_warning(message: str) -> None:
    """Flash a warning message (yellow). Use for non-critical issues."""
    flash(message, "warning")


def flash_info(message: str) -> None:
    """Flash an info message (blue). Use for informational notices."""
    flash(message, "info")


# Common message patterns (for consistency)
def flash_created(item_type: str, name: str) -> None:
    """Flash a standard 'created' success message."""
    flash(f"{item_type} '{name}' created.", "success")


def flash_updated(item_type: str, name: str) -> None:
    """Flash a standard 'updated' success message."""
    flash(f"{item_type} '{name}' updated.", "success")


def flash_archived(item_type: str, name: str) -> None:
    """Flash a standard 'archived' success message."""
    flash(f"{item_type} '{name}' archived.", "success")


def flash_restored(item_type: str, name: str) -> None:
    """Flash a standard 'restored' success message."""
    flash(f"{item_type} '{name}' restored.", "success")


def flash_deleted(item_type: str, name: str) -> None:
    """Flash a standard 'deleted' success message."""
    flash(f"{item_type} '{name}' deleted.", "success")


def flash_required(field_name: str) -> None:
    """Flash a standard 'field required' error message."""
    flash(f"{field_name} is required.", "error")


def flash_already_exists(item_type: str, identifier: str) -> None:
    """Flash a standard 'already exists' error message."""
    flash(f"{item_type} '{identifier}' already exists.", "error")


def flash_not_found(item_type: str) -> None:
    """Flash a standard 'not found' error message."""
    flash(f"{item_type} not found.", "error")


def flash_no_permission() -> None:
    """Flash a standard 'no permission' error message."""
    flash("You do not have permission to perform this action.", "error")
