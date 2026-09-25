"""
Tests for TechOps-specific models (TechOpsServiceType, TechOpsLineDetail,
TechOpsRequestDetail).

Verifies basic CRUD, relationships, and cascade-delete behavior. Setup is
local to this module so the TechOps tests don't depend on (or contaminate)
the BUDGET-shaped seed_workflow_data fixture used elsewhere.
"""
from __future__ import annotations

import pytest

from app import db
from app.models import (
    ApprovalGroup,
    Department,
    EventCycle,
    TechOpsLineDetail,
    TechOpsRequestDetail,
    TechOpsRequestSpace,
    TechOpsServiceType,
    User,
    WorkItem,
    WorkLine,
    WorkPortfolio,
    WorkType,
    WorkTypeConfig,
    REQUEST_KIND_PRIMARY,
    ROUTING_STRATEGY_CATEGORY,
    WORK_ITEM_STATUS_DRAFT,
    WORK_LINE_STATUS_PENDING,
)


@pytest.fixture(scope="function")
def techops_seed(app):
    """Minimal TechOps fixture: WorkType + config, two approval groups, one
    service type, one DRAFT work item with one work line, ready for tests
    to attach line/request details to."""
    user = User(
        id="test:techops_user", email="techops@test.local",
        display_name="TechOps Tester", is_active=True,
    )
    db.session.add(user)

    cycle = EventCycle(
        code="TST2026", name="Test Event 2026",
        is_active=True, is_default=True, sort_order=1,
    )
    dept = Department(
        code="TESTDEPT", name="Test Department", is_active=True,
    )
    db.session.add_all([cycle, dept])

    wt = WorkType(code="TECHOPS", name="TechOps Services", is_active=True)
    db.session.add(wt)
    db.session.flush()

    wtc = WorkTypeConfig(
        work_type_id=wt.id, url_slug="techops",
        public_id_prefix="TEC", line_detail_type="techops",
        routing_strategy=ROUTING_STRATEGY_CATEGORY,
        uses_dispatch=False, has_admin_final=False,
    )
    db.session.add(wtc)

    net_group = ApprovalGroup(
        work_type_id=wt.id, code="TECHOPS_NET",
        name="TechOps Networking", is_active=True,
    )
    gen_group = ApprovalGroup(
        work_type_id=wt.id, code="TECHOPS_GEN",
        name="TechOps Generic", is_active=True,
    )
    db.session.add_all([net_group, gen_group])
    db.session.flush()

    wifi_service = TechOpsServiceType(
        code="WIFI", name="WiFi access/coverage",
        default_approval_group_id=net_group.id,
        is_active=True, sort_order=10,
    )
    db.session.add(wifi_service)
    db.session.flush()

    portfolio = WorkPortfolio(
        work_type_id=wt.id, event_cycle_id=cycle.id,
        department_id=dept.id, created_by_user_id=user.id,
    )
    db.session.add(portfolio)
    db.session.flush()

    work_item = WorkItem(
        portfolio_id=portfolio.id,
        request_kind=REQUEST_KIND_PRIMARY,
        status=WORK_ITEM_STATUS_DRAFT,
        public_id="TST2026-TESTDEPT-TEC-1",
        created_by_user_id=user.id,
    )
    db.session.add(work_item)
    db.session.flush()

    line = WorkLine(
        work_item_id=work_item.id, line_number=1,
        status=WORK_LINE_STATUS_PENDING,
    )
    db.session.add(line)
    db.session.commit()

    return {
        "user": user,
        "work_type": wt,
        "net_group": net_group,
        "gen_group": gen_group,
        "wifi_service": wifi_service,
        "work_item": work_item,
        "line": line,
    }


@pytest.fixture(scope="function")
def techops_draft(techops_seed):
    """Alias onto techops_seed's DRAFT work item, for the room-first tests.

    Reuses the org setup above rather than the BUDGET-shaped
    seed_workflow_data fixture, per this module's own local-setup policy.
    """
    return techops_seed


def _add_techops_line(techops_draft, line_number=2):
    """Attach a WorkLine + TechOpsLineDetail to the draft's work item."""
    line = WorkLine(
        work_item_id=techops_draft["work_item"].id, line_number=line_number,
        status=WORK_LINE_STATUS_PENDING,
    )
    db.session.add(line)
    db.session.flush()

    detail = TechOpsLineDetail(
        work_line_id=line.id,
        service_type_id=techops_draft["wifi_service"].id,
    )
    db.session.add(detail)
    db.session.flush()
    return line


def test_service_type_persists_with_default_group(techops_seed):
    """A TechOpsServiceType round-trips and resolves its approval group."""
    service = (
        db.session.query(TechOpsServiceType)
        .filter_by(code="WIFI").one()
    )
    assert service.name == "WiFi access/coverage"
    assert service.default_approval_group.code == "TECHOPS_NET"


def test_service_type_code_is_unique(techops_seed):
    """The code column rejects duplicates."""
    duplicate = TechOpsServiceType(
        code="WIFI", name="Different name",
        default_approval_group_id=techops_seed["net_group"].id,
        is_active=True,
    )
    db.session.add(duplicate)
    with pytest.raises(Exception):
        db.session.commit()
    db.session.rollback()


def test_line_detail_persists_with_relationships(techops_seed):
    """A TechOpsLineDetail attaches to a WorkLine and exposes service-type
    and routed-approval-group relationships."""
    detail = TechOpsLineDetail(
        work_line_id=techops_seed["line"].id,
        service_type_id=techops_seed["wifi_service"].id,
        description="Press box WiFi for media broadcast crew",
        quantity=1,
        config={"coverage_priority": "high"},
        routed_approval_group_id=techops_seed["net_group"].id,
    )
    db.session.add(detail)
    db.session.commit()

    fetched = db.session.query(TechOpsLineDetail).one()
    assert fetched.service_type.code == "WIFI"
    assert fetched.routed_approval_group.code == "TECHOPS_NET"
    assert fetched.config == {"coverage_priority": "high"}
    assert fetched.quantity == 1


def test_line_detail_back_ref_from_work_line(techops_seed):
    """WorkLine.techops_detail back-ref returns the attached detail row."""
    detail = TechOpsLineDetail(
        work_line_id=techops_seed["line"].id,
        service_type_id=techops_seed["wifi_service"].id,
    )
    db.session.add(detail)
    db.session.commit()

    line = db.session.query(WorkLine).filter_by(id=techops_seed["line"].id).one()
    assert line.techops_detail is not None
    assert line.techops_detail.service_type.code == "WIFI"


def test_line_detail_cascade_on_work_line_delete(techops_seed):
    """Deleting a WorkLine cascades to its TechOpsLineDetail."""
    detail = TechOpsLineDetail(
        work_line_id=techops_seed["line"].id,
        service_type_id=techops_seed["wifi_service"].id,
    )
    db.session.add(detail)
    db.session.commit()

    db.session.delete(techops_seed["line"])
    db.session.commit()

    assert db.session.query(TechOpsLineDetail).count() == 0


def test_request_detail_persists_with_back_ref(techops_seed):
    """TechOpsRequestDetail attaches to a WorkItem and back-refs from it."""
    detail = TechOpsRequestDetail(
        work_item_id=techops_seed["work_item"].id,
        primary_contact_name="Heather Selbe",
        primary_contact_email="heather.selbe@magfest.org",
        additional_notes="Please coordinate with broadcast team for shared bandwidth",
    )
    db.session.add(detail)
    db.session.commit()

    item = db.session.query(WorkItem).filter_by(id=techops_seed["work_item"].id).one()
    assert item.techops_detail is not None
    assert item.techops_detail.primary_contact_email == "heather.selbe@magfest.org"


def test_request_detail_cascade_on_work_item_delete(techops_seed):
    """Deleting a WorkItem cascades to its TechOpsRequestDetail."""
    detail = TechOpsRequestDetail(
        work_item_id=techops_seed["work_item"].id,
        primary_contact_name="Test Contact",
        primary_contact_email="test@magfest.org",
    )
    db.session.add(detail)
    db.session.commit()

    db.session.delete(techops_seed["work_item"])
    db.session.commit()

    assert db.session.query(TechOpsRequestDetail).count() == 0


def test_line_detail_carries_the_room_first_columns():
    """hasattr on a declared Column passes whatever the column's type or
    nullability is, so assert against the table instead."""
    cols = TechOpsLineDetail.__table__.columns
    assert {"space_id", "parent_line_id", "purpose",
            "internal_only"} <= set(cols.keys())
    assert cols["internal_only"].nullable is False
    assert cols["purpose"].nullable is True
    assert cols["purpose"].type.length == 8
    # storage_only was removed (owner feedback round 2, item 3): redundant
    # with the free-text no-services reason. auto_added was removed once
    # WiFi required an explicit answer (round-3 fix item 3): no line is
    # ever created without one, so the flag protected against nothing.
    # Locks down that a reader who re-adds either for "consistency" is
    # reintroducing dropped scope.
    assert "storage_only" not in cols.keys()
    assert "auto_added" not in cols.keys()


def test_a_written_line_defaults_internal_only_to_false(app, techops_draft):
    """SQLite does not apply a Python-side default on a raw INSERT, so
    prove the default through the ORM path the app actually uses."""
    line = _add_techops_line(techops_draft)
    db.session.flush()
    assert line.techops_detail.internal_only is False


def test_request_space_declares_a_unique_constraint_on_item_and_space():
    cols = {c.name for c in TechOpsRequestSpace.__table__.columns}
    assert cols == {
        "id", "work_item_id", "space_id", "answer",
        "no_services_reason", "wifi_requested", "wifi_declined_reason",
        "notes",
    }
    uniques = [c for c in TechOpsRequestSpace.__table__.constraints
               if c.__class__.__name__ == "UniqueConstraint"]
    assert any({col.name for col in u.columns} == {"work_item_id", "space_id"}
               for u in uniques)
