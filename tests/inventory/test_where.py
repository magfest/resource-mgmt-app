"""The where-things-are tree."""
import pytest
from sqlalchemy import event as sa_event

from app import db
from app.inventory.ledger import Actor, add_unit
from app.inventory.where import build_where
from app.models import EventCycle, InvStock
from app.models.constants import (
    INV_CONDITION_LOST, INV_CONDITION_REPAIR, INV_CONDITION_RETIRED,
)
from app.inventory.unit_fields import set_unit_value
from tests.inventory.helpers import make_container, seed_gear_kind

ACTOR = Actor(user_id="test:invadmin")


@pytest.fixture
def tree_parts(inv):
    warehouse = make_container(inv, "C-9001", "LOCATION", name="Warehouse")
    pallet = make_container(inv, "C-9012", "PALLET", parent=warehouse, name="Pallet 3")
    tote = make_container(inv, "C-9040", "BIN", parent=pallet, name="Tote 2")
    return warehouse, pallet, tote


@pytest.fixture
def event(inv):
    cycle = EventCycle(code="SMF2027", name="SMF 2027", is_active=True, is_default=True)
    db.session.add(cycle)
    db.session.commit()
    return cycle


_counter = [0]


def _unit(inv, container=None, condition=None, name=None):
    _counter[0] += 1
    ident = [("FESTOPS_LABEL", f"U-{_counter[0]:04d}")]
    unit = add_unit(inv.laptop, None, ACTOR, ident, name=name)
    if container is not None:
        unit.current_container_id = container.id
    if condition:
        unit.condition = condition
    db.session.commit()
    return unit


def _find(nodes, cid):
    for node in nodes:
        if node.container_id == cid:
            return node
        found = _find(node.children, cid)
        if found:
            return found
    return None


def test_unit_on_pallet_is_placed(inv, tree_parts):
    warehouse, pallet, _ = tree_parts
    unit = _unit(inv, pallet)
    tree = build_where()
    assert [u.unit_id for u in _find(tree.roots, pallet.id).units] == [unit.id]
    assert _find(tree.roots, warehouse.id).unit_total == 1


def test_unit_directly_in_warehouse_is_unplaced_there(inv, tree_parts):
    warehouse = tree_parts[0]
    unit = _unit(inv, warehouse)
    root = _find(build_where().roots, warehouse.id)
    assert root.unplaced_text == "In the Warehouse, exact location not assigned"
    assert [u.unit_id for u in root.unplaced_units] == [unit.id]
    assert root.units == []


def test_unit_with_no_container_is_not_placed(inv, tree_parts):
    unit = _unit(inv)
    assert [u.unit_id for u in build_where().not_placed] == [unit.id]


def test_repair_unit_with_no_container(inv, tree_parts):
    _unit(inv, condition=INV_CONDITION_REPAIR)
    rows = build_where().not_placed
    assert len(rows) == 1 and rows[0].word == "In repair"


def test_lost_unit_only_in_lost(inv, tree_parts):
    warehouse, pallet, _ = tree_parts
    unit = _unit(inv, pallet, condition=INV_CONDITION_LOST)
    tree = build_where()
    assert [u.unit_id for u in tree.lost] == [unit.id]
    assert _find(tree.roots, pallet.id).unit_total == 0
    assert _find(tree.roots, pallet.id).units == []
    assert tree.not_placed == []


def test_retired_unit_left_out(inv, tree_parts):
    warehouse, pallet, _ = tree_parts
    _unit(inv, pallet, condition=INV_CONDITION_RETIRED)
    _unit(inv, condition=INV_CONDITION_RETIRED)
    tree = build_where()
    assert tree.not_placed == [] and tree.lost == []
    assert _find(tree.roots, pallet.id).units == []
    assert _find(tree.roots, warehouse.id).unit_total == 0


def test_counts_include_subtree_supplies(inv, tree_parts):
    warehouse, _, tote = tree_parts
    db.session.add(InvStock(item_id=inv.pens.id, container_id=tote.id, quantity=5))
    db.session.add(InvStock(item_id=inv.pens.id, container_id=warehouse.id, quantity=0))
    db.session.commit()
    tree = build_where()
    assert _find(tree.roots, warehouse.id).supply_total == 1
    assert _find(tree.roots, tote.id).supplies[0].quantity == 5


def test_cycle_renders_each_container_once(inv):
    a = make_container(inv, "C-9101")
    b = make_container(inv, "C-9102", parent=a)
    a.parent_id = b.id
    db.session.commit()
    tree = build_where()
    ids = []

    def walk(nodes):
        for node in nodes:
            ids.append(node.container_id)
            walk(node.children)
    walk(tree.roots)
    assert sorted(ids) == sorted([a.id, b.id])
    assert [n.container_id for n in tree.roots] == [min(a.id, b.id)]


def test_unit_without_a_check_says_not_checked(inv, tree_parts):
    _unit(inv, tree_parts[1])
    assert _find(build_where().roots, tree_parts[1].id).units[0].word == "Not checked"


def test_tree_word_reads_a_check_under_another_event(inv, tree_parts):
    from app.models import InvPrepStatus
    other = EventCycle(code="OLD2026", name="Old 2026", is_active=True, is_default=False)
    db.session.add(other)
    db.session.flush()
    unit = _unit(inv, tree_parts[1])
    db.session.add(InvPrepStatus(unit_id=unit.id, event_cycle_id=other.id, status="CHECKED"))
    db.session.commit()
    assert _find(build_where().roots, tree_parts[1].id).units[0].word == "Works"


def test_repair_unit_says_repair(inv, tree_parts):
    _unit(inv, tree_parts[1], condition=INV_CONDITION_REPAIR)
    assert _find(build_where().roots, tree_parts[1].id).units[0].word == "In repair"


def _selects(fn):
    seen = [0]

    def count(conn, cursor, statement, *args):
        if statement.lstrip().upper().startswith("SELECT"):
            seen[0] += 1

    sa_event.listen(db.engine, "before_cursor_execute", count)
    try:
        fn()
    finally:
        sa_event.remove(db.engine, "before_cursor_execute", count)
    return seen[0]


def _fill(unit, kind):
    set_unit_value(unit, kind.fields[0], "Windows")
    set_unit_value(unit, kind.fields[1], "16")
    db.session.commit()


def _measure():
    db.session.expire_all()
    return _selects(lambda: build_where())


def test_query_count_is_fixed(inv):
    kind = seed_gear_kind("LAPTOPK", "Laptop kind", [
        ("os", "OS", "CHOICE", ["Windows", "Mac"]), ("ram", "Memory", "NUMBER", "GB")])
    inv.laptop.gear_kind_id = kind.id
    db.session.commit()
    top = make_container(inv, "C-9201", "LOCATION", name="Hall")
    sub = make_container(inv, "C-9202", parent=top)
    _fill(_unit(inv, sub, name="Alpha"), kind)
    _unit(inv, top, name="Beta")
    small = _measure()
    for i in range(10):
        c = make_container(inv, f"C-93{i:02d}", parent=sub)
        _fill(_unit(inv, c, name=f"Gamma {i}"), kind)
        _unit(inv, sub, name=f"Delta {i}")
    db.session.add(InvStock(item_id=inv.pens.id, container_id=sub.id, quantity=3))
    db.session.commit()
    big = _measure()
    assert big == small


def test_a_found_asset_is_placed_not_lost(inv, tree_parts):
    warehouse, pallet, _ = tree_parts
    unit = _unit(inv, pallet, condition="FOUND")
    tree = build_where()
    assert tree.lost == []
    assert [u.unit_id for u in _find(tree.roots, pallet.id).units] == [unit.id]
