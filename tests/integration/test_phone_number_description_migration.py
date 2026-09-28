"""The correction must move the rows the room-first migration wrote, and
leave alone any description somebody has already edited by hand.

seed_techops_service_types is insert-only, so an existing row keeps whatever
description it has however often the seed runs. This migration is the only
thing that moves a database already out there, which is why it is guarded on
the exact old text rather than on the code alone: an operator who corrected
the wording through the admin page outranks it.
"""
from __future__ import annotations

import pytest

from app import db
from app.models import TechOpsServiceType
from app.seeds.bootstrap import seed_approval_groups, seed_work_types
from migrations.versions.pd5c19e4a7b3_correct_phone_number_description import (
    NEW_DESCRIPTION,
    OLD_DESCRIPTION,
    correct_phone_number_description,
    restore_phone_number_description,
)


@pytest.fixture
def phone_number_row(app):
    """One PHONE_NUMBER row carrying the wording tr7742b1c8e5 inserted.

    conftest's db.create_all() skips Alembic data migrations, so nothing
    creates this row for us.
    """
    groups = seed_approval_groups(seed_work_types())
    row = TechOpsServiceType(
        code="PHONE_NUMBER", name="Phone number",
        description=OLD_DESCRIPTION,
        default_approval_group_id=next(iter(groups.values())).id,
        is_active=True, sort_order=41,
    )
    db.session.add(row)
    db.session.commit()
    return row


def _current():
    return TechOpsServiceType.query.filter_by(code="PHONE_NUMBER").one().description


def test_the_old_wording_is_replaced(app, phone_number_row):
    correct_phone_number_description(db.session.connection())
    assert _current() == NEW_DESCRIPTION


def test_a_hand_edited_description_is_left_alone(app, phone_number_row):
    """An operator who fixed this through the admin page has said something
    the migration has no business overwriting."""
    phone_number_row.description = "Ask TechOps before filling this in."
    db.session.commit()

    correct_phone_number_description(db.session.connection())
    assert _current() == "Ask TechOps before filling this in."


def test_the_correction_is_reversible(app, phone_number_row):
    correct_phone_number_description(db.session.connection())
    restore_phone_number_description(db.session.connection())
    assert _current() == OLD_DESCRIPTION


def test_running_it_twice_changes_nothing_the_second_time(app, phone_number_row):
    correct_phone_number_description(db.session.connection())
    correct_phone_number_description(db.session.connection())
    assert _current() == NEW_DESCRIPTION


def test_the_migration_and_the_seed_agree_on_the_wording(app):
    """A fresh database is built by the seed and an existing one by this
    migration. If the two strings drift, the same service reads differently
    depending on how the database was made."""
    from app.seeds.bootstrap import seed_techops_service_types

    seed_techops_service_types(seed_approval_groups(seed_work_types()))
    db.session.commit()
    assert _current() == NEW_DESCRIPTION
