"""Walks of the container tree.

Depth is a handful of levels (location, truck, pallet, bin), so a Python walk
up parent links is cheaper to read than a recursive CTE and fast enough.
"""
from __future__ import annotations

from app.models import InvContainer


def ancestors(container: InvContainer) -> list[InvContainer]:
    """Return the container's parents, nearest first."""
    out, seen = [], {container.id}
    node = container.parent
    while node is not None and node.id not in seen:
        out.append(node)
        seen.add(node.id)
        node = node.parent
    return out


def display_name(container: InvContainer) -> str:
    if container.name:
        return f"{container.label_code} {container.name}"
    return f"{container.label_code} ({container.kind.name})"


def path_text(container: InvContainer) -> str:
    """Return the location path from the top, such as `C-0001 Warehouse > C-0007 (Pallet)`."""
    chain = list(reversed(ancestors(container))) + [container]
    return " > ".join(display_name(c) for c in chain)


def would_cycle(container: InvContainer, new_parent: InvContainer | None) -> bool:
    if new_parent is None:
        return False
    if new_parent.id == container.id:
        return True
    return any(a.id == container.id for a in ancestors(new_parent))
