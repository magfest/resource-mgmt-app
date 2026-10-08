"""Human-readable text for inventory movements, used by the movement log."""
from __future__ import annotations

from app import db
from app.models import InvMovement, User
from app.models.constants import INV_ACTION_ADD, INV_ACTION_ADJUST


def mask_badge(code: str | None) -> str:
    """Show a badge code as `~z…9Q`. Full codes are never displayed."""
    if not code:
        return ""
    return f"{code[:2]}…{code[-2:]}"


def _where(container) -> str:
    return container.label_code if container else "top level"


def describe_movement(m: InvMovement) -> str:
    if m.subject_container is not None:
        label = m.subject_container.label_code
        if m.action == INV_ACTION_ADD:
            return f"Created {label} in {_where(m.to_container)}"
        return f"{label} moved {_where(m.from_container)} to {_where(m.to_container)}"
    if m.unit is not None:
        name = f"{m.item.item_name} {m.unit.identifiers[0].value if m.unit.identifiers else ''}".strip()
        if m.action == INV_ACTION_ADD:
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
    if m.badge_session is not None:
        parts.append(mask_badge(m.badge_session.badge_code))
    if m.device is not None:
        parts.append(f"on {m.device.name}")
    return " ".join(parts) or "unknown"
