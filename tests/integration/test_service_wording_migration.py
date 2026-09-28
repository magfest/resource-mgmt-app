"""The reworded services must reach databases that already exist.

seed_techops_service_types only inserts, so a row written for the first
event keeps its original wording however often the seed runs. These three
rows are read by a department head filling the form once a year, which is
the audience the original wording was not written for.
"""
from __future__ import annotations

import pytest

from app import db
from app.models import TechOpsServiceType
from app.seeds.bootstrap import seed_approval_groups, seed_work_types
from migrations.versions.sv2e7c41b9d8_service_wording_for_requesters import (
    REWORDINGS,
    restore_service_wording,
    reword_services_for_requesters,
)


@pytest.fixture
def old_rows(app):
    """The three rows carrying the wording that shipped for the first event."""
    groups = seed_approval_groups(seed_work_types())
    group_id = next(iter(groups.values())).id
    seen = {}
    for code, column, old, _new in REWORDINGS:
        row = seen.get(code)
        if row is None:
            row = TechOpsServiceType(
                code=code, name=code.title(), description="",
                default_approval_group_id=group_id, is_active=True,
            )
            db.session.add(row)
            seen[code] = row
        setattr(row, column, old)
    db.session.commit()
    return seen


def _value(code, column):
    row = TechOpsServiceType.query.filter_by(code=code).one()
    return getattr(row, column)


def test_every_rewording_is_applied(app, old_rows):
    reword_services_for_requesters(db.session.connection())
    for code, column, _old, new in REWORDINGS:
        assert _value(code, column) == new, f"{code}.{column}"


def test_wording_edited_by_hand_is_left_alone(app, old_rows):
    """An operator who fixed this through the admin page has said something
    the migration has no business overwriting."""
    old_rows["WIFI"].name = "Event WiFi (ask Networking first)"
    db.session.commit()

    reword_services_for_requesters(db.session.connection())
    assert _value("WIFI", "name") == "Event WiFi (ask Networking first)"
    # The rows nobody touched still move.
    assert _value("OTHER", "name") == "Something else, or ask TechOps for advice"


def test_the_rewording_is_reversible(app, old_rows):
    reword_services_for_requesters(db.session.connection())
    restore_service_wording(db.session.connection())
    for code, column, old, _new in REWORDINGS:
        assert _value(code, column) == old, f"{code}.{column}"


def test_running_it_twice_changes_nothing_the_second_time(app, old_rows):
    reword_services_for_requesters(db.session.connection())
    reword_services_for_requesters(db.session.connection())
    assert _value("OTHER", "name") == "Something else, or ask TechOps for advice"


def test_the_migration_and_the_seed_agree(app):
    """A fresh database is built by the seed and an existing one by this
    migration. Drift means the same service reads differently depending on
    how the database was made."""
    from app.seeds.bootstrap import seed_techops_service_types

    seed_techops_service_types(seed_approval_groups(seed_work_types()))
    db.session.commit()
    for code, column, _old, new in REWORDINGS:
        assert _value(code, column) == new, f"{code}.{column}"
