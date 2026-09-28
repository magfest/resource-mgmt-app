"""The wired network service must stop describing itself as per-drop.

seed_techops_service_types only inserts, so the row written for the first
event keeps its wording and its instance_noun however often the seed runs.
instance_noun is what marks a service as a repeating group, and there is no
longer anything to repeat.
"""
from __future__ import annotations

import pytest

from app import db
from app.models import TechOpsServiceType
from app.seeds.bootstrap import seed_approval_groups, seed_work_types
from migrations.versions.nw6a3f92d15e_network_need_not_drop_plan import (
    NEW_DESCRIPTION,
    NEW_NAME,
    OLD_DESCRIPTION,
    OLD_NAME,
    restore_hardwired_ethernet,
    reword_wired_network,
)


@pytest.fixture
def old_row(app):
    groups = seed_approval_groups(seed_work_types())
    row = TechOpsServiceType(
        code="ETHERNET", name=OLD_NAME, description=OLD_DESCRIPTION,
        default_approval_group_id=next(iter(groups.values())).id,
        is_active=True, sort_order=20, instance_noun="drop",
    )
    db.session.add(row)
    db.session.commit()
    return row


def _row():
    return TechOpsServiceType.query.filter_by(code="ETHERNET").one()


def test_the_service_is_reworded_and_stops_being_a_repeating_group(app, old_row):
    reword_wired_network(db.session.connection())
    row = _row()
    assert row.name == NEW_NAME
    assert row.description == NEW_DESCRIPTION
    assert row.instance_noun is None


def test_wording_edited_by_hand_is_left_alone(app, old_row):
    old_row.description = "Ask Networking before requesting."
    db.session.commit()

    reword_wired_network(db.session.connection())
    assert _row().description == "Ask Networking before requesting."
    # The structural change still applies; it is not a matter of wording.
    assert _row().instance_noun is None


def test_the_change_is_reversible(app, old_row):
    reword_wired_network(db.session.connection())
    restore_hardwired_ethernet(db.session.connection())
    row = _row()
    assert (row.name, row.description, row.instance_noun) == (
        OLD_NAME, OLD_DESCRIPTION, "drop")


def test_the_migration_and_the_seed_agree(app):
    from app.seeds.bootstrap import seed_techops_service_types

    seed_techops_service_types(seed_approval_groups(seed_work_types()))
    db.session.commit()
    row = _row()
    assert row.name == NEW_NAME
    assert row.description == NEW_DESCRIPTION
    assert row.instance_noun is None
