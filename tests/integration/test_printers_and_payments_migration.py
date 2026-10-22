"""The migration path that adds Treasury, PRINTER and PAYMENTS to a live database.

A fresh database gets these rows from the seed. A database that already
exists gets them only from this migration, and nothing else in the suite
runs it: conftest builds the schema with db.create_all().
"""
from __future__ import annotations

import importlib

import pytest

from app import db
from app.models import ApprovalGroup, TechOpsServiceType
from app.seeds.bootstrap import (
    seed_approval_groups,
    seed_techops_service_types,
    seed_work_types,
)

migration = importlib.import_module(
    "migrations.versions.pp4t7m2k9q1a_techops_printers_and_payments")


@pytest.fixture
def existing_database(app, monkeypatch):
    """A seeded database from before this feature: no Treasury, no new services."""
    seed_techops_service_types(seed_approval_groups(seed_work_types()))
    db.session.commit()
    TechOpsServiceType.query.filter(
        TechOpsServiceType.code.in_(["PRINTER", "PAYMENTS"])
    ).delete(synchronize_session=False)
    ApprovalGroup.query.filter_by(code="TREASURY").delete()
    db.session.commit()
    connection = db.session.connection()
    monkeypatch.setattr(migration.op, "get_bind", lambda: connection,
                        raising=False)
    return connection


def _new_services():
    return {row.code: row for row in TechOpsServiceType.query.filter(
        TechOpsServiceType.code.in_(["PRINTER", "PAYMENTS"]))}


def test_upgrade_adds_treasury_and_both_services_once(app, existing_database):
    assert _new_services() == {}
    migration.upgrade()
    migration.upgrade()
    db.session.expire_all()

    treasury = ApprovalGroup.query.filter_by(code="TREASURY").all()
    assert len(treasury) == 1
    assert treasury[0].work_type.code == "TECHOPS"

    services = _new_services()
    assert set(services) == {"PRINTER", "PAYMENTS"}
    printer, payments = services["PRINTER"], services["PAYMENTS"]
    assert printer.default_approval_group.code == "TECHOPS_GEN"
    assert payments.default_approval_group.code == "TREASURY"
    assert (printer.sort_order, payments.sort_order) == (45, 55)
    assert printer.instance_noun == "printer"
    assert payments.instance_noun is None


def test_downgrade_removes_what_upgrade_added(app, existing_database):
    migration.upgrade()
    migration.downgrade()
    db.session.expire_all()

    assert ApprovalGroup.query.filter_by(code="TREASURY").count() == 0
    assert _new_services() == {}
