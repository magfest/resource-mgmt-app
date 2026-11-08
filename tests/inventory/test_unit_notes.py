"""Unit notes: the append-only table, its service, and the role helper."""
from types import SimpleNamespace

import pytest

from app import db
from app.inventory.ledger import Actor, InventoryError, add_unit
from app.inventory.notes import add_note, latest_note, unit_notes
from app.routes.inventory.helpers import note_types_for


@pytest.fixture
def unit(inv):
    unit = add_unit(inv.laptop, None, Actor(user_id="test:invadmin"),
                    [("FESTOPS_LABEL", "U-0001")])
    db.session.commit()
    return unit


def test_add_note_keeps_type_author_and_text(inv, unit):
    note = add_note(unit, "PICKER", "Invented: spare remote in tote", "test:operator")
    assert (note.note_type, note.source, note.created_by_user_id) == ("PICKER", "PERSON", "test:operator")
    assert note.body == "Invented: spare remote in tote"


def test_blank_note_is_refused(inv, unit):
    with pytest.raises(InventoryError):
        add_note(unit, "ADMIN", " \r\n  ", "test:operator")


def test_crlf_counts_as_one_character(inv, unit):
    body = "a" * 999 + "\r\n" + "b" * 1000      # 2000 after normalizing
    assert add_note(unit, "ADMIN", body, "test:operator").body == "a" * 999 + "\n" + "b" * 1000
    with pytest.raises(InventoryError):
        add_note(unit, "ADMIN", "c" * 2001, "test:operator")


def test_unknown_type_is_refused(inv, unit):
    with pytest.raises(InventoryError):
        add_note(unit, "SECRET", "x", "test:operator")


def test_unknown_source_is_refused(inv, unit):
    with pytest.raises(InventoryError):
        add_note(unit, "ADMIN", "x", "test:operator", source="ROBOT")


def test_unit_notes_newest_first(inv, unit):
    first = add_note(unit, "ADMIN", "one", "test:operator")
    second = add_note(unit, "ADMIN", "two", "test:operator")
    assert [n.id for n in unit_notes(unit.id)] == [second.id, first.id]
    assert latest_note(unit.id).id == second.id


def test_note_types_by_role():
    # is_inventory_operator reads only these two attributes.
    ctx = lambda roles, sa=False: SimpleNamespace(is_super_admin=sa, roles=set(roles))
    assert note_types_for(ctx({"INVENTORY_OPERATOR"})) == ("ADMIN", "PUBLIC", "PICKER")
    assert note_types_for(ctx({"INVENTORY_ADMIN"})) == ("ADMIN", "PUBLIC", "PICKER")
    assert note_types_for(ctx(set(), sa=True)) == ("ADMIN", "PUBLIC", "PICKER")
    assert note_types_for(ctx(set())) == ()
