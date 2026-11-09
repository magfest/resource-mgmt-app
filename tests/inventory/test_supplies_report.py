"""Supplies on hand: totals, places, last count, access and query count."""
from datetime import datetime

import pytest
from sqlalchemy import event

from app import db
from app.inventory.stock_report import supplies_on_hand
from app.models import InvCount, InvCountLine, InvStock, SupplyItem
from app.models.constants import INV_COUNT_LINE_APPLIED, INV_COUNT_LINE_UNDONE
from tests.inventory.helpers import login, make_container

URL = "/inv/admin/supplies"


def _stock(item, container, quantity, approximate=False):
    db.session.add(InvStock(item_id=item.id, container_id=container.id, quantity=quantity,
                            is_approximate=approximate))
    db.session.flush()


def _count(item, container, submitted_at, outcome=INV_COUNT_LINE_APPLIED):
    count = InvCount(container_id=container.id, started_at=submitted_at,
                     submitted_at=submitted_at)
    db.session.add(count)
    db.session.flush()
    db.session.add(InvCountLine(count_id=count.id, item_id=item.id, expected_quantity=0,
                                counted_quantity=1, outcome=outcome))
    db.session.flush()


@pytest.fixture
def pens(inv):
    pallet = make_container(inv, "C-P4", name="Pallet 4")
    tote = make_container(inv, "C-T2", kind="BIN", name="Tote 2")
    _stock(inv.pens, pallet, 20)
    _stock(inv.pens, tote, 5, approximate=True)
    db.session.commit()
    return inv.pens


def test_totals_across_containers_with_about(pens):
    row = supplies_on_hand()[0]
    assert row["total"] == 25 and row["approximate"] is True


def test_item_without_stock_above_zero_is_not_listed(inv):
    box = make_container(inv, "C-B1", name="Box")
    _stock(inv.pens, box, 0)
    db.session.commit()
    assert supplies_on_hand() == []


def test_places_largest_first(inv):
    small = make_container(inv, "C-S1", name="Small")
    big = make_container(inv, "C-B1", name="Big")
    _stock(inv.pens, small, 3)
    _stock(inv.pens, big, 30)
    db.session.commit()
    assert [p[1] for p in supplies_on_hand()[0]["places"]] == [30, 3]


def test_undone_count_is_not_the_last_count(pens):
    box = make_container_for(pens)
    first, later = datetime(2026, 12, 1, 15, 0), datetime(2026, 12, 2, 15, 0)
    _count(pens, box, first)
    _count(pens, box, later, outcome=INV_COUNT_LINE_UNDONE)
    db.session.commit()
    assert supplies_on_hand()[0]["last_counted"] == first


def make_container_for(item):
    return db.session.query(InvStock).filter_by(item_id=item.id).first().container


def test_csv_where_cell_and_columns(client, pens):
    login(client, "test:operator")
    response = client.get(URL, query_string={"format": "csv"})
    lines = response.get_data(as_text=True).splitlines()
    assert lines[0] == "Item,Unit of measure,Total,Exact,Last counted,Where"
    assert lines[1] == 'Blue Pen,each,25,no,,C-P4 Pallet 4: 20; C-T2 Tote 2: about 5'
    assert "supplies-" in response.headers["Content-Disposition"]


def test_operator_can_open_and_nobody_cannot(client, pens):
    login(client, "test:operator")
    page = client.get(URL)
    assert page.status_code == 200 and "about 25 each" in page.get_data(as_text=True)
    login(client, "test:nobody")
    assert client.get(URL).status_code == 403


def _selects(fn):
    seen = [0]

    def count(conn, cursor, statement, *args):
        if statement.lstrip().upper().startswith("SELECT"):
            seen[0] += 1

    event.listen(db.engine, "before_cursor_execute", count)
    try:
        fn()
    finally:
        event.remove(db.engine, "before_cursor_execute", count)
    return seen[0]


def _items_each_in_own_container(inv, n, start):
    for i in range(start, start + n):
        item = SupplyItem(category_id=inv.category.id, item_name=f"Item {i:03d}", unit="each")
        db.session.add(item)
        db.session.flush()
        _stock(item, make_container(inv, f"C-{8000 + i}", kind="BIN"), 4)
    db.session.commit()


def test_page_does_not_query_per_item(inv):
    _items_each_in_own_container(inv, 3, 0)
    db.session.expire_all()
    few = _selects(supplies_on_hand)
    _items_each_in_own_container(inv, 12, 100)
    db.session.expire_all()
    assert _selects(supplies_on_hand) == few
