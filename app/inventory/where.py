"""One tree of every container and what sits in it, built in a fixed number of queries.

The builder walks parents through an id map. The lazy `parent` relationship would
cost a query per container.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import joinedload, selectinload

from app import db
from app.inventory.describe import unit_tag
from app.inventory.tree import place_name, unplaced_text
from app.inventory.unit_fields import details_line
from app.inventory.week import WEEK_BROKEN, WEEK_WORKS, live_prep_rows, week_status
from app.models import InvContainer, InvGearKind, InvStock, InvUnit, InvUnitValue, SupplyItem
from app.models.constants import (
    INV_CONDITION_LOST, INV_CONDITION_REPAIR, INV_CONDITION_RETIRED,
)

NO_NUMBER_LABEL = "No number yet"
_WEEK_WORDS = {WEEK_WORKS: "Works", WEEK_BROKEN: "Broken"}


@dataclass
class UnitRow:
    unit_id: int
    label: str
    item_name: str
    details: str
    word: str | None
    search: str


@dataclass
class SupplyRow:
    item_name: str
    quantity: int
    approximate: bool


@dataclass
class Node:
    container_id: int
    name: str
    code: str
    children: list[Node] = field(default_factory=list)
    units: list[UnitRow] = field(default_factory=list)
    unplaced_units: list[UnitRow] = field(default_factory=list)
    unplaced_text: str | None = None
    supplies: list[SupplyRow] = field(default_factory=list)
    unit_total: int = 0
    supply_total: int = 0
    search: str = ""


@dataclass
class WhereTree:
    roots: list[Node]
    not_placed: list[UnitRow]
    lost: list[UnitRow]


def _effective_parents(containers: dict[int, InvContainer]) -> dict[int, int | None]:
    """Map each id to its parent id, or None for a top-level container.

    A missing parent makes a container top-level. In a cycle, the lowest id is
    cut loose so every container appears once.
    """
    parent = {cid: (c.parent_id if c.parent_id in containers else None)
              for cid, c in containers.items()}
    done: set[int] = set()
    for start in sorted(containers):
        path: list[int] = []
        on_path: set[int] = set()
        cid = start
        while cid is not None and cid not in done:
            if cid in on_path:
                cycle = path[path.index(cid):]
                parent[min(cycle)] = None
                break
            path.append(cid)
            on_path.add(cid)
            cid = parent[cid]
        done.update(path)
    return parent


def _unit_row(unit: InvUnit, word: str | None) -> UnitRow:
    label = unit.name or unit_tag(unit) or NO_NUMBER_LABEL
    item_name = unit.item.item_name
    parts = [unit.name or ""]
    parts += [i.value for i in unit.identifiers if i.retired_at is None]
    parts.append(item_name)
    return UnitRow(unit_id=unit.id, label=label, item_name=item_name,
                   details=details_line(unit), word=word,
                   search=" ".join(parts).lower())


def _word(unit: InvUnit, event, prep_rows) -> str | None:
    if unit.condition == INV_CONDITION_REPAIR:
        return "Repair"
    if event is None:
        return None
    return _WEEK_WORDS.get(week_status(prep_rows.get(unit.id)), "Not checked")


def _sort_units(rows: list[UnitRow]) -> None:
    rows.sort(key=lambda r: (r.label.lower(), r.unit_id))


def build_where(event) -> WhereTree:
    containers = {c.id: c for c in db.session.query(InvContainer).all()}
    units = (db.session.query(InvUnit)
             .filter(InvUnit.condition != INV_CONDITION_RETIRED)
             .options(selectinload(InvUnit.item).selectinload(SupplyItem.gear_kind)
                      .selectinload(InvGearKind.fields),
                      selectinload(InvUnit.identifiers),
                      selectinload(InvUnit.values).selectinload(InvUnitValue.field),
                      selectinload(InvUnit.values).selectinload(InvUnitValue.choice))
             .all())
    stock = (db.session.query(InvStock).options(joinedload(InvStock.item))
             .filter(InvStock.quantity > 0).all())
    prep_rows = live_prep_rows(event, [u.id for u in units])

    parent = _effective_parents(containers)
    nodes = {cid: Node(container_id=cid, name=place_name(c), code=c.label_code,
                       search=f"{place_name(c)} {c.label_code}".lower())
             for cid, c in containers.items()}
    for node in nodes.values():
        if parent[node.container_id] is None:
            node.unplaced_text = unplaced_text(containers[node.container_id])

    not_placed: list[UnitRow] = []
    lost: list[UnitRow] = []
    for unit in units:
        row = _unit_row(unit, _word(unit, event, prep_rows))
        if unit.condition == INV_CONDITION_LOST:
            lost.append(row)
        elif unit.current_container_id not in nodes:
            not_placed.append(row)
        else:
            node = nodes[unit.current_container_id]
            (node.unplaced_units if parent[node.container_id] is None
             else node.units).append(row)

    for line in stock:
        if line.container_id in nodes:
            nodes[line.container_id].supplies.append(
                SupplyRow(line.item.item_name, line.quantity, bool(line.is_approximate)))

    for cid in sorted(nodes, key=lambda i: containers[i].label_code):
        if parent[cid] is not None:
            nodes[parent[cid]].children.append(nodes[cid])

    for lst in (not_placed, lost):
        _sort_units(lst)
    roots = [n for cid, n in nodes.items() if parent[cid] is None]
    roots = [r for r in (_finish(n, containers) for n in roots) if r is not None]
    roots.sort(key=lambda n: (n.code, n.container_id))
    return WhereTree(roots=roots, not_placed=not_placed, lost=lost)


def _finish(node: Node, containers) -> Node | None:
    """Sort, total and prune one subtree; None when an inactive subtree is empty."""
    kept = []
    for child in node.children:
        done = _finish(child, containers)
        if done is not None:
            kept.append(done)
    node.children = kept
    _sort_units(node.units)
    _sort_units(node.unplaced_units)
    node.supplies.sort(key=lambda s: s.item_name)
    node.unit_total = (len(node.units) + len(node.unplaced_units)
                       + sum(c.unit_total for c in kept))
    node.supply_total = len(node.supplies) + sum(c.supply_total for c in kept)
    if not containers[node.container_id].is_active and not (node.unit_total or node.supply_total):
        return None
    return node
