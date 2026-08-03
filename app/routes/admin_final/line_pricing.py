"""Quantity and price rules for the admin line tools.

Budget admins may book a line against any active expense account, including the
fixed-cost, hotel, and badge accounts the requester forms handle through
dedicated flows. This module owns the per-kind rules so add-line and
change-account cannot drift apart.

Hotel lines carry their room count implicitly. Quantity is room-nights, and the
room count lives in a description prefix that
app/routes/admin_final/hotel_rooms_report.py:60 (`parse_room_count`) parses
back out.
"""
import re
from decimal import Decimal, InvalidOperation

from app.models import UI_GROUP_BADGES, UI_GROUP_HOTEL_SERVICES
from app.routes.work.helpers import (
    get_effective_account_type,
    get_effective_fixed_cost_settings,
)

KIND_STANDARD = "STANDARD"
KIND_FIXED = "FIXED"
KIND_HOTEL = "HOTEL"
KIND_BADGE = "BADGE"

# app/routes/admin_final/hotel_rooms_report.py:47 recovers the room count from
# the description with this same pattern. Changing one without the other
# silently breaks the room totals the Hotels team books against.
_ROOMS_PREFIX_RE = re.compile(r"^\s*(\d+)\s+rooms:\s*", re.IGNORECASE)


def classify_account(account, event_cycle_id):
    """Return the pricing kind for an account, honoring event overrides."""
    is_fixed, ui_group = get_effective_account_type(account, event_cycle_id)
    if not is_fixed:
        return KIND_STANDARD
    if ui_group == UI_GROUP_HOTEL_SERVICES:
        return KIND_HOTEL
    if ui_group == UI_GROUP_BADGES:
        return KIND_BADGE
    return KIND_FIXED


def build_account_pricing_map(accounts, event_cycle_id):
    """Per-account pricing metadata keyed by account id, for the form script.

    Mirrors build_spend_types_by_account (app/routes/work/lines.py:72) so both
    maps are consumed the same way in the template.
    """
    result = {}
    for account in accounts:
        settings = get_effective_fixed_cost_settings(account, event_cycle_id)
        result[account.id] = {
            "kind": classify_account(account, event_cycle_id),
            "default_unit_price_cents": settings["unit_price_cents"],
            "default_frequency_id": settings["frequency_id"],
            "warehouse_default": settings["warehouse_default"],
        }
    return result


def strip_rooms_prefix(description):
    """Remove a leading "N rooms: " prefix. Safe to call on unprefixed text.

    Called for every account kind, not only hotel. Re-booking a hotel line onto
    a standard or fixed-cost account must drop the stale room count instead of
    carrying it into the new description.
    """
    return _ROOMS_PREFIX_RE.sub("", description or "").strip()


def _parse_positive_int(form, field, label, errors):
    """Parse a required integer of at least 1, appending any error."""
    raw = (form.get(field) or "").strip()
    if not raw:
        errors.append(f"{label} is required.")
        return 1
    try:
        value = int(raw)
    except ValueError:
        errors.append(f"{label} must be a whole number.")
        return 1
    if value < 1:
        errors.append(f"{label} must be at least 1.")
        return 1
    return value


def _parse_quantity(form, errors):
    """Parse quantity as a positive Decimal. Mirrors line_admin.py:230.

    Decimal("nan") and Decimal("Infinity") parse without raising InvalidOperation,
    but comparing or doing arithmetic on them raises later. Reject non-finite
    values here, before any comparison, so the check stays inside error handling
    rather than becoming an uncaught exception.
    """
    raw = (form.get("quantity") or "1").strip()
    if not raw:
        return Decimal("1")
    try:
        quantity = Decimal(raw)
        if not quantity.is_finite():
            errors.append("Invalid quantity value.")
            return Decimal("1")
        if quantity <= 0:
            errors.append("Quantity must be greater than 0.")
            return Decimal("1")
    except InvalidOperation:
        errors.append("Invalid quantity value.")
        return Decimal("1")
    return quantity


def _parse_unit_price_cents(form, errors):
    """Parse unit price in dollars to cents. Mirrors line_admin.py:241.

    Rejects non-finite Decimal values (nan, Infinity) before comparison or the
    cents conversion; both parse successfully but raise on `< 0` or `int()`.
    """
    raw = (form.get("unit_price") or "0").strip()
    if not raw:
        return 0
    try:
        dollars = Decimal(raw)
        if not dollars.is_finite():
            errors.append("Invalid unit price value.")
            return 0
        if dollars < 0:
            errors.append("Unit price cannot be negative.")
            return 0
    except InvalidOperation:
        errors.append("Invalid unit price value.")
        return 0
    return int(dollars * 100)


def resolve_line_pricing(account, event_cycle_id, form):
    """Decide quantity, unit price, and description for an admin-written line.

    Hotel accounts take rooms and nights instead of a raw quantity; the stored
    quantity is room-nights. Fixed-cost, hotel, and badge accounts take the
    account default price unless the form carries an explicit override.

    Returns:
        (values, errors). values carries quantity, unit_price_cents,
        account_default_unit_price_cents, description, and kind.
    """
    errors = []
    kind = classify_account(account, event_cycle_id)

    raw_description = (form.get("description") or "")
    raw_description = raw_description.replace("\r\n", "\n").replace("\r", "\n")
    description = strip_rooms_prefix(raw_description)

    if kind == KIND_HOTEL:
        rooms = _parse_positive_int(form, "rooms", "Rooms", errors)
        nights = _parse_positive_int(form, "nights", "Nights", errors)
        quantity = Decimal(rooms * nights)
        if rooms > 1:
            description = f"{rooms} rooms: {description}".strip()
    else:
        quantity = _parse_quantity(form, errors)

    if kind == KIND_STANDARD:
        account_default = None
        unit_price_cents = _parse_unit_price_cents(form, errors)
    else:
        settings = get_effective_fixed_cost_settings(account, event_cycle_id)
        account_default = settings["unit_price_cents"]
        if form.get("price_override") == "on":
            unit_price_cents = _parse_unit_price_cents(form, errors)
        else:
            unit_price_cents = account_default

    return {
        "quantity": quantity,
        "unit_price_cents": unit_price_cents,
        "account_default_unit_price_cents": account_default,
        "description": description,
        "kind": kind,
    }, errors
