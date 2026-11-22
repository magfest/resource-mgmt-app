"""Fixtures for inventory tests.

create_all() runs no migrations, so the container kinds that migration
iv4c7n2t9k1a seeds are seeded here instead.
"""
from types import SimpleNamespace

import pytest

from app import db
from app.models.constants import INV_JOB_SIGNIN_CARD
from app.models import (
    ApprovalGroup, Department, EventCycle, InvContainerKind, InvPrinter, InvPrintRoute,
    ROLE_INVENTORY_ADMIN, ROLE_INVENTORY_OPERATOR, ROLE_SUPER_ADMIN, ROLE_WORKTYPE_ADMIN,
    SupplyCategory, SupplyItem, TechOpsServiceType, User, UserRole, WorkType,
)

USERS = [
    ("test:admin", "admin@test.local", ROLE_SUPER_ADMIN),
    ("test:invadmin", "invadmin@test.local", ROLE_INVENTORY_ADMIN),
    ("test:operator", "operator@test.local", ROLE_INVENTORY_OPERATOR),
    ("test:nobody", "nobody@test.local", None),
]


@pytest.fixture
def inv(app):
    for user_id, email, _ in USERS:
        db.session.add(User(id=user_id, email=email,
                            display_name=user_id.split(":")[1].title(), is_active=True))
    db.session.flush()
    for user_id, _, role in USERS:
        if role:
            db.session.add(UserRole(user_id=user_id, role_code=role))

    kinds = {}
    for code, name, order in [("LOCATION", "Location", 10), ("PALLET", "Pallet", 50),
                              ("BIN", "Bin", 70)]:
        kinds[code] = InvContainerKind(code=code, name=name, sort_order=order, is_active=True,
                                       is_fixed=code == "LOCATION")
        db.session.add(kinds[code])

    category = SupplyCategory(code="GENERAL", name="General", is_active=True)
    db.session.add(category)
    db.session.flush()
    pens = SupplyItem(category_id=category.id, item_name="Blue Pen", unit="each")
    laptop = SupplyItem(category_id=category.id, item_name="Laptop", unit="each",
                        is_serialized=True, is_expendable=False)
    wap = SupplyItem(category_id=category.id, item_name="Wireless Access Point", unit="each",
                     is_serialized=True, is_orderable=False, is_expendable=False)
    db.session.add_all([pens, laptop, wap])
    db.session.commit()
    return SimpleNamespace(kinds=kinds, category=category, pens=pens, laptop=laptop, wap=wap)


@pytest.fixture
def supply_admin(inv):
    """A user holding WORKTYPE_ADMIN on the SUPPLY work type.

    Separate from `inv` so other tests run with no SUPPLY row at all.
    """
    supply = WorkType(code="SUPPLY", name="Supply Orders", is_active=True)
    db.session.add(supply)
    db.session.add(User(id="test:supplyadmin", email="supplyadmin@test.local",
                        display_name="Supplyadmin", is_active=True))
    db.session.flush()
    db.session.add(UserRole(user_id="test:supplyadmin", role_code=ROLE_WORKTYPE_ADMIN,
                            work_type_id=supply.id))
    db.session.commit()
    return supply


@pytest.fixture
def card_route(inv):
    """An active printer routed for sign-in cards."""
    printer = InvPrinter(name="Card printer", dpi=203, width_in=2, height_in=3,
                         device_name="Card printer")
    db.session.add(printer)
    db.session.flush()
    db.session.add(InvPrintRoute(job_type=INV_JOB_SIGNIN_CARD, printer_id=printer.id))
    db.session.commit()
    return printer


@pytest.fixture
def alloc(inv, supply_admin):
    """Two events, two departments, SUPPLY and TechOps work types, and two allocatable items.

    TECHOPS seeds inactive in production; it is activated here so its cards render.
    """
    techops = WorkType(code="TECHOPS", name="TechOps", is_active=True)
    db.session.add(techops)
    db.session.flush()
    group = ApprovalGroup(work_type_id=techops.id, code="TECHOPS_GEN", name="TechOps General")
    db.session.add(group)
    db.session.add(User(id="test:techopsadmin", email="techopsadmin@test.local",
                        display_name="Techopsadmin", is_active=True))
    db.session.flush()
    db.session.add(UserRole(user_id="test:techopsadmin", role_code=ROLE_WORKTYPE_ADMIN,
                            work_type_id=techops.id))
    printer_service = TechOpsServiceType(
        code="PRINTER", name="Printer", instance_noun="printer",
        default_approval_group_id=group.id)
    radio_service = TechOpsServiceType(
        code="RADIO_CHANNEL", name="Radio channel", instance_noun="channel",
        default_approval_group_id=group.id)
    event = EventCycle(code="TST27", name="Test 2027", is_default=True, sort_order=1)
    other_event = EventCycle(code="TST28", name="Test 2028", is_default=False, sort_order=2)
    alpha = Department(code="ALPHA", name="Alpha Team")
    beta = Department(code="BETA", name="Beta Team")
    db.session.add_all([printer_service, radio_service, event, other_event, alpha, beta])
    db.session.flush()
    tv = SupplyItem(category_id=inv.category.id, item_name="Test TV", unit="each",
                    is_serialized=True, is_expendable=False, needs_pre_allocation=True)
    printer = SupplyItem(category_id=inv.category.id, item_name="Test Printer", unit="each",
                         is_serialized=True, is_expendable=False, needs_pre_allocation=True,
                         techops_service_type_id=printer_service.id)
    db.session.add_all([tv, printer])
    db.session.commit()
    return SimpleNamespace(
        supply=supply_admin, techops=techops, techops_group=group,
        printer_service=printer_service, radio_service=radio_service,
        event=event, other_event=other_event, alpha=alpha, beta=beta, tv=tv, printer=printer,
    )
