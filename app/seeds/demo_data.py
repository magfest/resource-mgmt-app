"""
Demo organizational seed — operator-replaceable starter content.

Contains demo divisions, departments, event cycle, and parking expense
accounts. All names are prefixed with `[Demo] ` so operators can grep+delete
in admin UI before populating their real data.

What lives here vs bootstrap.py:
- bootstrap = referenced by code (worktypes, approval groups, hotel wizard
  expense accounts) — deleting breaks the app.
- demo_data = operator-replaceable (depts, event cycle, divisions, parking)
  — operators delete `[Demo]`-marked rows when they add real data.

Code-level reservations:
- `DEMO_GUESTS` and `DEMO_ARCADE` dept codes are referenced by
  `demo_users.py`'s membership plan. If you change those codes here, also
  update demo_users.py.

Idempotency contract:
    All seed functions INSERT missing rows only. They never UPDATE existing
    rows and never re-add deleted rows. Once an operator deletes a [Demo]
    row, it stays deleted across re-seeds.
"""
from __future__ import annotations

from app import db
from app.models import (
    ApprovalGroup,
    Department,
    Division,
    EventCycle,
    SpendType,
)
from app.seeds.bootstrap import create_expense_account


# Code constants — these are the public-ID fragments. Once a work item is
# created against any of these depts, the code is locked (per CLAUDE.md).
# Demo prefix is intentional: makes ugly public IDs like
# DEMO-DEMO_ADMIN-BUD-1, which motivates operators to delete demo rows
# before creating real work items.
DEMO_DIVISION_CODE = "DEMO_DIV"
DEMO_EVENT_CYCLE_CODE = "DEMO"
DEMO_DEPT_CODES = {
    "ADMIN":   ("DEMO_ADMIN",   "[Demo] Admin"),
    "FESTOPS": ("DEMO_FESTOPS", "[Demo] FestOps"),
    "TECH":    ("DEMO_TECH",    "[Demo] Tech"),
    "GUESTS":  ("DEMO_GUESTS",  "[Demo] Guests"),
    "ARCADE":  ("DEMO_ARCADE",  "[Demo] Arcade"),
}


def seed_demo_division() -> Division:
    """Seed a single demo division. Returns it (existing or new)."""
    print("Seeding demo division...")

    existing = db.session.query(Division).filter_by(code=DEMO_DIVISION_CODE).first()
    if existing:
        return existing

    division = Division(
        code=DEMO_DIVISION_CODE,
        name="[Demo] Division",
        description="Demo division — replace with real org structure.",
        is_active=True,
        sort_order=10,
    )
    db.session.add(division)
    db.session.flush()
    print(f"  Created demo division: {DEMO_DIVISION_CODE}")
    return division


def seed_demo_departments(division: Division) -> dict[str, Department]:
    """Seed 5 demo departments under the demo division.

    Returns dict keyed by short name (ADMIN/FESTOPS/TECH/GUESTS/ARCADE).
    """
    print("Seeding demo departments...")

    sort_order = 10
    departments = {}

    for short_name, (code, display_name) in DEMO_DEPT_CODES.items():
        existing = db.session.query(Department).filter_by(code=code).first()
        if existing:
            departments[short_name] = existing
            continue

        dept = Department(
            code=code,
            name=display_name,
            description=f"Demo department — replace with real {short_name.lower()} dept data.",
            division_id=division.id if division else None,
            is_active=True,
            sort_order=sort_order,
        )
        db.session.add(dept)
        departments[short_name] = dept
        sort_order += 10

    db.session.flush()
    print(f"  {len(departments)} demo departments present")
    return departments


def seed_demo_event_cycle() -> EventCycle:
    """Seed a single demo event cycle. Returns it (existing or new)."""
    print("Seeding demo event cycle...")

    existing = db.session.query(EventCycle).filter_by(code=DEMO_EVENT_CYCLE_CODE).first()
    if existing:
        return existing

    cycle = EventCycle(
        code=DEMO_EVENT_CYCLE_CODE,
        name="[Demo] Event Cycle",
        is_active=True,
        is_default=True,
        sort_order=10,
    )
    db.session.add(cycle)
    db.session.flush()
    print(f"  Created demo event cycle: {DEMO_EVENT_CYCLE_CODE}")
    return cycle


def seed_demo_parking_accounts(
    approval_groups: dict[str, ApprovalGroup],
    spend_types: dict[str, SpendType],
) -> None:
    """Seed PARKING_TOLLS and PARKING_GNH expense accounts.

    No code paths reference these by code (unlike HTL_*); they're useful
    examples for operators but safe to delete and replace. Sample CSV in
    routes/admin/data_upload.py mentions PARKING_GNH as an example, but
    that's documentation, not code coupling.
    """
    print("Seeding demo parking accounts...")

    from app.models import ExpenseAccount

    parking_accounts = [
        {
            "code": "PARKING_TOLLS",
            "name": "[Demo] Parking & Tolls",
            "description": "General parking and tolls",
            "spend_type_codes": ['DIVVY', 'BANK'],
            "approval_group_code": "GEN",
            "is_fixed_cost": False,
            "default_unit_price_cents": None,
            "ui_display_group": None,
        },
        {
            "code": "PARKING_GNH",
            "name": "[Demo] Parking - Gaylord",
            "description": "Gaylord hotel parking",
            "spend_type_codes": ['DIVVY'],
            "approval_group_code": "HOTEL",
            "is_fixed_cost": True,
            "default_unit_price_cents": 2500,  # placeholder
            "ui_display_group": "HOTEL_SERVICES",
        },
    ]

    sort_order = 1000  # high sort_order — keep below bootstrap accounts
    accounts_created = 0
    for spec in parking_accounts:
        existing = db.session.query(ExpenseAccount).filter_by(code=spec["code"]).first()
        if existing:
            continue

        approval_group = approval_groups.get(spec["approval_group_code"])
        create_expense_account(
            code=spec["code"],
            name=spec["name"],
            description=spec["description"],
            spend_type_codes=spec["spend_type_codes"],
            spend_types=spend_types,
            approval_group=approval_group,
            is_admin_only=False,
            is_contract_eligible=False,
            is_fixed_cost=spec["is_fixed_cost"],
            default_unit_price_cents=spec["default_unit_price_cents"],
            sort_order=sort_order,
            ui_display_group=spec["ui_display_group"],
        )
        sort_order += 10
        accounts_created += 1

    db.session.flush()
    print(f"  Created {accounts_created} demo parking accounts")


def run_demo_data() -> None:
    """Run the full demo seed.

    Depends on bootstrap having been run first (needs ApprovalGroups and
    SpendTypes for the parking accounts). Caller (run_all_seeds() in
    config_seed.py) ensures the order.
    """
    print("=" * 60)
    print("Demo seed (operator-replaceable starter content)...")
    print("=" * 60)

    division = seed_demo_division()
    seed_demo_departments(division)
    seed_demo_event_cycle()

    approval_groups = {g.code: g for g in db.session.query(ApprovalGroup).all()}
    spend_types = {s.code: s for s in db.session.query(SpendType).all()}
    seed_demo_parking_accounts(approval_groups, spend_types)

    db.session.commit()
    print("Demo seed complete.")
