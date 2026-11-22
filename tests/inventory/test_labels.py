from sqlalchemy import text

from app import db
from app.inventory.labels import allocate, parse, was_issued
from app.models import InvLabelSequence


def test_allocations_are_sequential_and_never_repeat(inv):
    first = allocate("C", 3)
    second = allocate("C", 2)
    db.session.commit()
    assert first == ["C-0001", "C-0002", "C-0003"]
    assert second == ["C-0004", "C-0005"]
    assert allocate("U", 1) == ["U-00001"]


def test_parse_accepts_label_format_only():
    assert parse("c-0042") == ("C", 42)
    assert parse("C-12345") == ("C", 12345)
    assert parse("X-0001") is None
    assert parse("C-") is None


def test_a_code_never_printed_was_not_issued(inv):
    allocate("C", 2)
    assert was_issued("C-0002") is True
    assert was_issued("C-0003") is False
    assert was_issued("C-9999") is False
    assert was_issued("not a label") is False


def test_allocation_rereads_a_counter_changed_underneath(inv):
    allocate("C", 2)
    db.session.commit()
    row = db.session.get(InvLabelSequence, "C")
    assert row.next_value == 3
    db.session.execute(text("UPDATE inv_label_sequences SET next_value = 10 WHERE prefix = 'C'"))
    assert row.next_value == 3  # stale on purpose; row must stay referenced
    assert allocate("C", 1) == ["C-0010"]


def test_was_issued_rereads_a_counter_changed_underneath(inv):
    allocate("C", 2)
    db.session.commit()
    row = db.session.get(InvLabelSequence, "C")
    assert was_issued("C-0005") is False
    db.session.execute(text("UPDATE inv_label_sequences SET next_value = 10 WHERE prefix = 'C'"))
    assert row.next_value == 3  # stale on purpose; row must stay referenced
    assert was_issued("C-0005") is True
