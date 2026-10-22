"""The migration that gives a seedless database the TechOps catalog.

Production gained an active TECHOPS work type without ever running the seed,
so it had no TechOps approval groups and no service types. Nothing else in
the suite runs migrations: conftest builds the schema with db.create_all().
"""
from __future__ import annotations

import importlib

import pytest

from app import db
from app.models import ApprovalGroup, TechOpsServiceType, WorkType
from app.seeds.bootstrap import (
    seed_approval_groups,
    seed_techops_service_types,
    seed_work_types,
)

migration = importlib.import_module(
    "migrations.versions.cb3k7r9t2m5x_backfill_techops_catalog")


@pytest.fixture
def bind_migration(app, monkeypatch):
    # Fetched per call: each test commits seed rows before upgrading, and a
    # commit closes the connection the session held at fixture time.
    monkeypatch.setattr(migration.op, "get_bind",
                        lambda: db.session.connection(), raising=False)


def _techops_groups():
    techops = WorkType.query.filter_by(code="TECHOPS").one()
    return ApprovalGroup.query.filter_by(work_type_id=techops.id).all()


def _catalog():
    """Every TechOps group and service type, as comparable tuples."""
    groups = sorted(
        (g.code, g.name, g.description, g.is_active, g.sort_order)
        for g in _techops_groups())
    services = sorted(
        (s.code, s.name, s.description, s.default_approval_group.code,
         s.sort_order, s.instance_noun, s.is_active)
        for s in TechOpsServiceType.query.all())
    return groups, services


def _clear_catalog():
    TechOpsServiceType.query.delete()
    for group in _techops_groups():
        db.session.delete(group)
    db.session.commit()


def test_a_seedless_database_gets_exactly_what_the_seed_creates(
        app, bind_migration):
    seed_work_types()
    db.session.commit()
    assert TechOpsServiceType.query.count() == 0

    migration.upgrade()
    migration.upgrade()
    db.session.expire_all()
    backfilled = _catalog()

    # The migration copies the seed's values. Rebuilding the same rows from
    # the seed and comparing catches the two drifting apart.
    _clear_catalog()
    seed_techops_service_types(seed_approval_groups(seed_work_types()))
    db.session.commit()
    db.session.expire_all()
    assert backfilled == _catalog()

    active = {s.code for s in TechOpsServiceType.query.filter_by(is_active=True)}
    assert {"RADIO_CHANNEL", "OTHER", "WIFI", "ETHERNET", "PAYMENTS"} <= active


def test_a_seeded_database_is_left_exactly_as_it_was(app, bind_migration):
    seed_techops_service_types(seed_approval_groups(seed_work_types()))
    db.session.commit()
    # An operator edit the backfill must not overwrite.
    radio = TechOpsServiceType.query.filter_by(code="RADIO_CHANNEL").one()
    radio.description = "Edited by an admin"
    db.session.commit()
    before = _catalog()

    migration.upgrade()
    db.session.expire_all()

    assert _catalog() == before


def test_a_database_without_techops_is_untouched(app, bind_migration):
    migration.upgrade()
    assert ApprovalGroup.query.count() == 0
    assert TechOpsServiceType.query.count() == 0
