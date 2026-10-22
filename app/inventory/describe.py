"""Human-readable text for inventory movements, used by the movement log."""
from __future__ import annotations

from app import db
from app.models import InvMovement, User
from app.models.constants import (
    INV_ACTION_ADD, INV_ACTION_ADJUST, INV_IDENT_FESTOPS_LABEL, INV_IDENT_MAC,
    INV_IDENT_MAGFEST_TAG, INV_IDENT_SERIAL, INV_IDENT_VENDOR_TAG,
)

# The most recognizable code first: a MAGFest tag is on the gear itself.
_TAG_ORDER = (INV_IDENT_MAGFEST_TAG, INV_IDENT_FESTOPS_LABEL, INV_IDENT_VENDOR_TAG,
              INV_IDENT_SERIAL, INV_IDENT_MAC)


def _where(container) -> str:
    return container.label_code if container else "top level"


def unit_tag(unit) -> str:
    """Return the best active identifier for naming a unit on screen."""
    active = [i for i in unit.identifiers if i.retired_at is None]
    for kind in _TAG_ORDER:
        for identifier in active:
            if identifier.kind == kind:
                # The tag is shown in canonical form, whatever was typed.
                if kind == INV_IDENT_MAGFEST_TAG:
                    return identifier.normalized_value
                return identifier.value
    return f"unit {unit.id}"


def describe_movement(m: InvMovement) -> str:
    if m.subject_container is not None:
        label = m.subject_container.label_code
        if m.action == INV_ACTION_ADD:
            return f"Created {label} in {_where(m.to_container)}"
        return f"{label} moved {_where(m.from_container)} to {_where(m.to_container)}"
    if m.unit is not None:
        name = f"{m.item.item_name} {unit_tag(m.unit)}"
        if m.action == INV_ACTION_ADD:
            if m.to_container is None:
                return f"Added {name}, not placed yet"
            return f"Added {name} to {_where(m.to_container)}"
        verb = "found in" if m.action == INV_ACTION_ADJUST else "moved to"
        return f"{name} {verb} {_where(m.to_container)} from {_where(m.from_container)}"
    what = f"{m.quantity} {m.item.item_name}"
    if m.action == INV_ACTION_ADD:
        return f"Added {what} to {_where(m.to_container)}"
    if m.action == INV_ACTION_ADJUST:
        # A count writes the gain as to-only and the loss as from-only.
        if m.to_container is not None:
            return f"Count added {what} in {m.to_container.label_code}"
        return f"Count removed {what} from {m.from_container.label_code}"
    return f"{what} moved {_where(m.from_container)} to {_where(m.to_container)}"


def describe_actor(m: InvMovement) -> str:
    if m.performed_by_user_id:
        user = db.session.get(User, m.performed_by_user_id)
        return user.display_name if user else m.performed_by_user_id
    parts = []
    if m.shift is not None:
        parts.append(m.shift.person_name)
    if m.device is not None:
        parts.append(f"on {m.device.name}")
    return " ".join(parts) or "unknown"
