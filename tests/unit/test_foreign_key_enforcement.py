"""The test harness enforces foreign keys, as PostgreSQL does.

Without these, a future change that drops the PRAGMA from conftest would
be invisible: every test would still pass, and referential defects would
go back to being found only in production.
"""
import pytest
from sqlalchemy.exc import IntegrityError

from app import db


def test_the_pragma_is_on(app):
    assert db.session.execute(db.text("PRAGMA foreign_keys")).scalar() == 1


def test_a_row_pointing_at_nothing_is_rejected(app):
    """The pragma being set is not the same as it being obeyed.

    Every NOT NULL column is filled, so a foreign key is the only thing
    left that can reject this row. An earlier version of this test omitted
    created_at and passed with enforcement off, satisfied by the wrong
    IntegrityError.
    """
    with pytest.raises(IntegrityError) as raised:
        db.session.execute(db.text(
            "INSERT INTO space_assignments "
            "(space_id, event_cycle_id, department_id, created_at, updated_at) "
            "VALUES (999999, 999999, 999999, '2026-09-22', '2026-09-22')"
        ))
        db.session.flush()
    assert "FOREIGN KEY" in str(raised.value)


def test_enforcement_survives_a_second_connection(app):
    """The listener fires on connect, so a connection opened later in the
    test must get the pragma too, not just the one the fixture touched.
    """
    with db.engine.connect() as conn:
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
