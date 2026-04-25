"""
Seed script for budget configuration data.

Creates core configuration needed for a fresh instance:
- Approval groups (LOGISTICS, OFFICE, GEN, GUEST, TECH, HOTEL, OTHER_SPECIAL)
- Work types (BUDGET, CONTRACT, SUPPLY) with configs
- Contract types and supply categories
- Spend types (DIVVY, BANK)
- Reference data (frequency, confidence, priority)
- Divisions and departments
- Event cycle
- Core expense accounts (hotel rooms, parking)
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from app import db
from app.models import (
    ApprovalGroup,
    ConfidenceLevel,
    ContractType,
    Department,
    Division,
    EventCycle,
    ExpenseAccount,
    FrequencyOption,
    PriorityLevel,
    SpendType,
    SupplyCategory,
    WorkType,
    WorkTypeConfig,
    SPEND_TYPE_MODE_SINGLE_LOCKED,
    SPEND_TYPE_MODE_ALLOW_LIST,
    VISIBILITY_MODE_ALL,
    VISIBILITY_MODE_RESTRICTED,
    PROMPT_MODE_NONE,
    PROMPT_MODE_REQUIRE_EXPLICIT_NA,
    UI_GROUP_KNOWN_COSTS,
    UI_GROUP_HOTEL_SERVICES,
    ROUTING_STRATEGY_EXPENSE_ACCOUNT,
    ROUTING_STRATEGY_CONTRACT_TYPE,
    ROUTING_STRATEGY_CATEGORY,
    ROUTING_STRATEGY_DIRECT,
)


def seed_approval_groups() -> dict[str, ApprovalGroup]:
    """Seed TECH, HOTEL, OTHER approval groups."""
    print("Seeding approval groups...")

    groups_data = [
        ("LOGISTICS", "Logistics / Warehouse", "Items that should be reviewed by the logistics or warehouse teams", 10),
        ("OFFICE", "Org Ops", "Items that need to be reviewed by the office or other organization leadership", 20),
        ("GEN", "General", "General items that normally do not need custom reviews or approval", 30),
        ("GUEST", "Guest / Booking", "Guest and booking related items", 40),
        ("TECH", "FestOps - Tech", "Reviews technical equipment, rentals, and tech-related expenses", 50),
        ("HOTEL", "Hotels Team", "Reviews hotel rooms, venue fees, and hotel-related expenses", 60),
        ("OTHER_SPECIAL", "Other Specialty", "Reviews general expenses and admin items", 70),
    ]

    groups = {}
    for code, name, description, sort_order in groups_data:
        existing = db.session.query(ApprovalGroup).filter_by(code=code).first()
        if existing:
            groups[code] = existing
            continue

        group = ApprovalGroup(
            code=code,
            name=name,
            description=description,
            is_active=True,
            sort_order=sort_order,
        )
        db.session.add(group)
        groups[code] = group

    db.session.flush()
    print(f"  Created {len(groups)} approval groups")
    return groups


def seed_work_types() -> dict[str, WorkType]:
    """Seed work types (BUDGET, CONTRACT, SUPPLY, TECHOPS, AV)."""
    print("Seeding work types...")

    # is_active=False for work types whose UI isn't built yet — keeps them
    # out of user-facing pickers but lets URL routing resolve their slugs.
    work_types_data = [
        ("BUDGET", "Budget Requests", 10, True),
        ("CONTRACT", "Contracts", 20, True),
        ("SUPPLY", "Supply Orders", 30, True),
        ("TECHOPS", "TechOps Services", 40, False),
        ("AV", "AV Requests", 50, False),
    ]

    work_types = {}
    for code, name, sort_order, is_active in work_types_data:
        existing = db.session.query(WorkType).filter_by(code=code).first()
        if existing:
            work_types[code] = existing
            continue

        wt = WorkType(
            code=code,
            name=name,
            is_active=is_active,
            sort_order=sort_order,
        )
        db.session.add(wt)
        work_types[code] = wt

    db.session.flush()
    print(f"  Created {len(work_types)} work types")
    return work_types


def seed_work_type_configs(work_types: dict[str, WorkType]) -> None:
    """Seed work type configurations."""
    print("Seeding work type configs...")

    configs_created = 0

    # BUDGET config
    budget_wt = work_types.get("BUDGET")
    if budget_wt and not budget_wt.config:
        config = WorkTypeConfig(
            work_type_id=budget_wt.id,
            url_slug="budget",
            public_id_prefix="BUD",
            line_detail_type="budget",
            routing_strategy=ROUTING_STRATEGY_EXPENSE_ACCOUNT,
            supports_supplementary=True,
            supports_fixed_costs=True,
            item_singular="Budget Request",
            item_plural="Budget Requests",
            line_singular="Line Item",
            line_plural="Line Items",
        )
        db.session.add(config)
        configs_created += 1

    # CONTRACT config
    contract_wt = work_types.get("CONTRACT")
    if contract_wt and not contract_wt.config:
        config = WorkTypeConfig(
            work_type_id=contract_wt.id,
            url_slug="contracts",
            public_id_prefix="CON",
            line_detail_type="contract",
            routing_strategy=ROUTING_STRATEGY_CONTRACT_TYPE,
            supports_supplementary=False,
            supports_fixed_costs=False,
            item_singular="Contract",
            item_plural="Contracts",
            line_singular="Contract",
            line_plural="Contracts",
        )
        db.session.add(config)
        configs_created += 1

    # SUPPLY config
    supply_wt = work_types.get("SUPPLY")
    if supply_wt and not supply_wt.config:
        config = WorkTypeConfig(
            work_type_id=supply_wt.id,
            url_slug="supply",
            public_id_prefix="SUP",
            line_detail_type="supply",
            routing_strategy=ROUTING_STRATEGY_CATEGORY,
            supports_supplementary=False,
            supports_fixed_costs=False,
            item_singular="Supply Order",
            item_plural="Supply Orders",
            line_singular="Item",
            line_plural="Items",
        )
        db.session.add(config)
        configs_created += 1

    # TECHOPS config (inactive — UI not yet built)
    techops_wt = work_types.get("TECHOPS")
    if techops_wt and not techops_wt.config:
        config = WorkTypeConfig(
            work_type_id=techops_wt.id,
            url_slug="techops",
            public_id_prefix="TEC",
            line_detail_type="techops",
            routing_strategy=ROUTING_STRATEGY_DIRECT,
            supports_supplementary=False,
            supports_fixed_costs=False,
            item_singular="TechOps Request",
            item_plural="TechOps Requests",
            line_singular="Item",
            line_plural="Items",
        )
        db.session.add(config)
        configs_created += 1

    # AV config (inactive — UI not yet built)
    av_wt = work_types.get("AV")
    if av_wt and not av_wt.config:
        config = WorkTypeConfig(
            work_type_id=av_wt.id,
            url_slug="av",
            public_id_prefix="AV",
            line_detail_type="av",
            routing_strategy=ROUTING_STRATEGY_DIRECT,
            supports_supplementary=False,
            supports_fixed_costs=False,
            item_singular="AV Request",
            item_plural="AV Requests",
            line_singular="Item",
            line_plural="Items",
        )
        db.session.add(config)
        configs_created += 1

    db.session.flush()
    print(f"  Created {configs_created} work type configs")


def seed_contract_types(approval_groups: dict[str, ApprovalGroup]) -> dict[str, ContractType]:
    """Seed contract types for routing."""
    print("Seeding contract types...")

    contract_types_data = [
        ("PERFORMER", "Performer/Artist", "Contracts for performers and artists", "GUEST", 10),
        ("VENDOR", "Vendor Service", "Service provider contracts", "GEN", 20),
        ("VENUE", "Venue/Space", "Venue and space rental contracts", "HOTEL", 30),
        ("EQUIPMENT", "Equipment Rental", "Equipment rental contracts", "TECH", 40),
        ("SPONSOR", "Sponsorship", "Sponsorship agreements", "OFFICE", 50),
    ]

    contract_types = {}
    for code, name, description, approval_group_code, sort_order in contract_types_data:
        existing = db.session.query(ContractType).filter_by(code=code).first()
        if existing:
            contract_types[code] = existing
            continue

        ct = ContractType(
            code=code,
            name=name,
            description=description,
            approval_group_id=approval_groups.get(approval_group_code).id if approval_group_code in approval_groups else None,
            is_active=True,
            sort_order=sort_order,
        )
        db.session.add(ct)
        contract_types[code] = ct

    db.session.flush()
    print(f"  Created {len(contract_types)} contract types")
    return contract_types


def seed_supply_categories(approval_groups: dict[str, ApprovalGroup]) -> dict[str, SupplyCategory]:
    """Seed supply categories for routing."""
    print("Seeding supply categories...")

    categories_data = [
        ("OFFICE", "Office Supplies", "General office supplies", "GEN", 10),
        ("TECH", "Tech Equipment", "Technical equipment and supplies", "TECH", 20),
        ("EVENT", "Event Supplies", "Event-specific supplies", "GEN", 30),
        ("SAFETY", "Safety/Medical", "Safety and medical supplies", "LOGISTICS", 40),
        ("SIGNAGE", "Signage/Printing", "Signs, banners, and printed materials", "GEN", 50),
    ]

    categories = {}
    for code, name, description, approval_group_code, sort_order in categories_data:
        existing = db.session.query(SupplyCategory).filter_by(code=code).first()
        if existing:
            categories[code] = existing
            continue

        sc = SupplyCategory(
            code=code,
            name=name,
            description=description,
            approval_group_id=approval_groups.get(approval_group_code).id if approval_group_code in approval_groups else None,
            is_active=True,
            sort_order=sort_order,
        )
        db.session.add(sc)
        categories[code] = sc

    db.session.flush()
    print(f"  Created {len(categories)} supply categories")
    return categories


def seed_spend_types() -> dict[str, SpendType]:
    """Seed DIVVY, BANK spend types."""
    print("Seeding spend types...")

    types_data = [
        ("DIVVY", "Divvy", "Corporate card purchases via Divvy", 10),
        ("BANK", "Bank", "Direct bank transfers, checks, or wire payments", 20),
    ]

    spend_types = {}
    for code, name, description, sort_order in types_data:
        existing = db.session.query(SpendType).filter_by(code=code).first()
        if existing:
            spend_types[code] = existing
            continue

        st = SpendType(
            code=code,
            name=name,
            description=description,
            is_active=True,
            sort_order=sort_order,
        )
        db.session.add(st)
        spend_types[code] = st

    db.session.flush()
    print(f"  Created {len(spend_types)} spend types")
    return spend_types


def seed_reference_data():
    """Seed frequency options, confidence levels, priority levels."""
    print("Seeding reference data...")

    # Frequency options
    if not db.session.query(FrequencyOption).first():
        frequencies = [
            ("ONE_TIME", "One Time / Infrastructure Purchase", "Single purchase for this event", 10),
            ("RECURRING", "Yearly Operating Cost", "Recurring that have to happen every event", 20),
        ]
        for code, name, desc, sort in frequencies:
            db.session.add(FrequencyOption(
                code=code, name=name, description=desc,
                is_active=True, sort_order=sort
            ))
        db.session.flush()
        print("  Created frequency options")

    # Confidence levels
    if not db.session.query(ConfidenceLevel).first():
        levels = [
            ("CONFIRMED", "Confirmed", "Price is confirmed/quoted", 10),
            ("ESTIMATED", "Estimated", "Price is estimated", 20),
            ("PLACEHOLDER", "Placeholder", "Rough placeholder amount", 30),
        ]
        for code, name, desc, sort in levels:
            db.session.add(ConfidenceLevel(
                code=code, name=name, description=desc,
                is_active=True, sort_order=sort
            ))
        db.session.flush()
        print("  Created confidence levels")

    # Priority levels
    if not db.session.query(PriorityLevel).first():
        priorities = [
            ("CRITICAL", "Critical", "Essential for event operations", 10),
            ("HIGH", "High", "Important but event can proceed without", 20),
            ("MEDIUM", "Medium", "Nice to have", 30),
            ("LOW", "Low", "Optional / stretch goal", 40),
            ("EXPERIMENTAL", "Experimental / Stretch Goal", "Non-operational item; exploratory or aspirational expense that would be nice to pursue if budget allows", 50),
        ]
        for code, name, desc, sort in priorities:
            db.session.add(PriorityLevel(
                code=code, name=name, description=desc,
                is_active=True, sort_order=sort
            ))
        db.session.flush()
        print("  Created priority levels")


def seed_divisions() -> dict[str, Division]:
    """Seed divisions from CSV data."""
    print("Seeding divisions...")

    divisions_data = [
        ("ADMIN", "Admin/Support", "Administrative and support departments", 10),
        ("COMMS", "Communications Division", "Communications, media, and promotional departments", 20),
        ("GAMING", "Gaming Division", "Gaming departments including arcade, console, tabletop, and more", 30),
        ("MUSIC", "Music Division", "Music performance and venue departments", 40),
        ("FESTOPS", "Fest Ops Division", "Festival operations and logistics departments", 50),
        ("PROGRAMMING", "Programming Division", "Programming and event content departments", 60),
        ("STAFF_SERVICES", "Staff Services Division", "Staff support and services departments", 70),
    ]

    divisions = {}
    for code, name, description, sort_order in divisions_data:
        existing = db.session.query(Division).filter_by(code=code).first()
        if existing:
            divisions[code] = existing
            continue

        division = Division(
            code=code,
            name=name,
            description=description,
            is_active=True,
            sort_order=sort_order,
        )
        db.session.add(division)
        divisions[code] = division

    db.session.flush()
    print(f"  Created {len(divisions)} divisions")
    return divisions


def seed_departments(divisions: dict[str, Division] = None) -> dict[str, Department]:
    """Seed departments from CSV, linking to divisions."""
    print("Seeding departments...")

    import csv

    # Map division names from CSV to division codes
    division_name_to_code = {
        "Admin/Support": "ADMIN",
        "Communications Division": "COMMS",
        "Gaming Division": "GAMING",
        "Music Division": "MUSIC",
        "Fest Ops Division": "FESTOPS",
        "Programming Division": "PROGRAMMING",
        "Staff Services Division": "STAFF_SERVICES",
    }

    # Find the CSV file
    project_root = Path(__file__).parent.parent.parent
    csv_path = project_root / "Demo_Data" / "Super_MAGFest_Department_Contacts_Clean_with_Division.csv"

    departments = {}
    sort_order = 10

    if csv_path.exists() and divisions:
        print(f"  Reading departments from {csv_path.name}...")
        with open(csv_path, newline='', encoding='utf-8') as csvfile:
            reader = csv.DictReader(csvfile)
            for row in reader:
                division_name = row.get('Division', '').strip()
                dept_name = row.get('Department/Team', '').strip()
                description = row.get('Description', '').strip() or None
                mailing_list = row.get('Department Mailing List', '').strip() or None
                slack_channel = row.get('Public Slack', '').strip() or None

                # Skip rows without department name
                if not dept_name:
                    continue

                # Generate code from department name
                code = slugify(dept_name)

                # Skip if already processed (duplicate rows)
                if code in departments:
                    continue

                # Look up division
                division_code = division_name_to_code.get(division_name)
                division = divisions.get(division_code) if division_code else None

                existing = db.session.query(Department).filter_by(code=code).first()
                if existing:
                    # Update existing department with division if not set
                    if division and not existing.division_id:
                        existing.division_id = division.id
                    departments[code] = existing
                    continue

                dept = Department(
                    code=code,
                    name=dept_name,
                    description=description,
                    mailing_list=mailing_list,
                    slack_channel=slack_channel,
                    division_id=division.id if division else None,
                    is_active=True,
                    sort_order=sort_order,
                )
                db.session.add(dept)
                departments[code] = dept
                sort_order += 10
    else:
        # Fallback to hardcoded data if CSV not found or no divisions
        print("  CSV not found, using fallback department data...")
        depts_data = [
            ("OFFICE", "Office/Admin", "Administrative and office operations", 5),
            ("TECHOPS", "TechOps", "Technical operations", 10),
            ("HOTELS", "Hotels", "Hotel and venue coordination", 20),
            ("BROADCAST", "BroadcastOps", "Broadcasting and streaming", 30),
            ("FESTOPS", "FestOps", "Festival operations", 40),
            ("SUPPLY", "SupplyOps", "Supply chain and logistics", 50),
            ("REG", "Registration", "Registration and check-in", 60),
            ("PANEL", "Panels", "Panel programming", 70),
            ("GUEST", "Guests", "Guest relations", 80),
            ("ARCADE", "Arcades", "Arcade gaming", 90),
            ("CONSOLE", "Console", "Console gaming", 100),
            ("TABLETOP", "Tabletop", "Tabletop gaming", 110),
            ("MUSIC", "Music", "Music programming", 120),
            ("SECURITY", "Security", "Security operations", 130),
            ("MERCH", "Merchandise", "Merchandise sales", 140),
        ]

        for code, name, description, sort_order in depts_data:
            existing = db.session.query(Department).filter_by(code=code).first()
            if existing:
                departments[code] = existing
                continue

            dept = Department(
                code=code,
                name=name,
                description=description,
                is_active=True,
                sort_order=sort_order,
            )
            db.session.add(dept)
            departments[code] = dept

    db.session.flush()
    print(f"  Created {len(departments)} departments")
    return departments


def seed_event_cycles() -> dict[str, EventCycle]:
    """Seed event cycles."""
    print("Seeding event cycles...")

    cycles_data = [
        ("SMF2027", "Super MAGFest 2027", True, True, 10),
    ]

    cycles = {}
    for code, name, is_active, is_default, sort_order in cycles_data:
        existing = db.session.query(EventCycle).filter_by(code=code).first()
        if existing:
            cycles[code] = existing
            continue

        cycle = EventCycle(
            code=code,
            name=name,
            is_active=is_active,
            is_default=is_default,
            sort_order=sort_order,
        )
        db.session.add(cycle)
        cycles[code] = cycle

    db.session.flush()
    print(f"  Created {len(cycles)} event cycles")
    return cycles


def slugify(name: str) -> str:
    """Convert a name to a code-friendly slug."""
    # Remove special characters, convert to uppercase with underscores
    slug = re.sub(r'[^a-zA-Z0-9\s]', '', name)
    slug = re.sub(r'\s+', '_', slug.strip())
    return slug.upper()


def seed_expense_accounts(
    approval_groups: dict[str, ApprovalGroup],
    spend_types: dict[str, SpendType],
):
    """
    Seed core expense accounts that are known at standup time.

    Creates hotel room variants (by room type and payment scenario)
    and venue parking accounts. Additional expense accounts can be
    added later via the admin UI.
    """
    print("Seeding core expense accounts...")

    # Check if already seeded
    if db.session.query(ExpenseAccount).first():
        print("  Expense accounts already exist, skipping...")
        return

    sort_order = 10
    accounts_created = 0

    hotel_group = approval_groups.get('HOTEL')

    # --- Hotel Rooms ---
    # Three room types: Standard, Executive Suite, Hospitality Suite
    # Three payment scenarios: MAGFest Paid, Third Party (Held), Staff Crash
    # Placeholder prices — replace with real negotiated rates per event
    # via admin UI (Expense Accounts > edit unit price)

    # MAGFest Paid variants (hits department budget)
    magfest_paid_variants = [
        ("HTL_STD_MAGPAID", "Standard Room (MAGFest Paid)", 15000, "Standard hotel room - MAGFest covers cost"),
        ("HTL_EXEC_MAGPAID", "Executive Suite (MAGFest Paid)", 30000, "Executive suite - MAGFest covers cost"),
        ("HTL_HOSP_MAGPAID", "Hospitality Suite (MAGFest Paid)", 60000, "Hospitality suite with attached bedrooms - MAGFest covers cost"),
    ]

    for code, name, price_cents, desc in magfest_paid_variants:
        create_expense_account(
            code=code,
            name=name,
            description=desc,
            spend_type_codes=['BANK'],
            spend_types=spend_types,
            approval_group=hotel_group,
            is_admin_only=False,
            is_contract_eligible=False,
            is_fixed_cost=True,
            default_unit_price_cents=price_cents,
            sort_order=sort_order,
            ui_display_group=UI_GROUP_HOTEL_SERVICES,
        )
        sort_order += 10
        accounts_created += 1

    # Third Party Held variants ($0 cost - partner books and pays, we just reserve it)
    held_variants = [
        ("HTL_STD_HELD", "Standard Room (Third Party Pays)", "Standard room held for partner to book - no budget impact"),
        ("HTL_EXEC_HELD", "Executive Suite (Third Party Pays)", "Executive suite held for partner to book - no budget impact"),
        ("HTL_HOSP_HELD", "Hospitality Suite (Third Party Pays)", "Hospitality suite held for partner to book - no budget impact"),
    ]

    for code, name, desc in held_variants:
        create_expense_account(
            code=code,
            name=name,
            description=desc,
            spend_type_codes=['BANK'],
            spend_types=spend_types,
            approval_group=hotel_group,
            is_admin_only=False,
            is_contract_eligible=False,
            is_fixed_cost=True,
            default_unit_price_cents=0,
            sort_order=sort_order,
            prompt_mode_override=PROMPT_MODE_NONE,
            ui_display_group=UI_GROUP_HOTEL_SERVICES,
        )
        sort_order += 10
        accounts_created += 1

    # Staff Crash variants ($0 cost - informational, staffers pay out of pocket)
    # No standard room for crash - only suites per policy
    crash_variants = [
        ("HTL_EXEC_CRASH", "Executive Suite (Staff Crash)", "Executive suite for staff crash space - paid out of pocket by staffers, not department budget"),
        ("HTL_HOSP_CRASH", "Hospitality Suite (Staff Crash)", "Hospitality suite for staff crash space - paid out of pocket by staffers, not department budget"),
    ]

    for code, name, desc in crash_variants:
        create_expense_account(
            code=code,
            name=name,
            description=desc,
            spend_type_codes=['BANK'],
            spend_types=spend_types,
            approval_group=hotel_group,
            is_admin_only=False,
            is_contract_eligible=False,
            is_fixed_cost=True,
            default_unit_price_cents=0,
            sort_order=sort_order,
            prompt_mode_override=PROMPT_MODE_NONE,
            ui_display_group=UI_GROUP_HOTEL_SERVICES,
        )
        sort_order += 10
        accounts_created += 1

    # --- Parking ---
    # General parking (flexible cost, user enters amount)
    create_expense_account(
        code="PARKING_TOLLS",
        name="Parking & Tolls",
        description="General parking and tolls",
        spend_type_codes=['DIVVY', 'BANK'],
        spend_types=spend_types,
        approval_group=approval_groups.get('GEN'),
        is_admin_only=False,
        is_contract_eligible=False,
        is_fixed_cost=False,
        default_unit_price_cents=None,
        sort_order=sort_order,
    )
    sort_order += 10
    accounts_created += 1

    # Gaylord parking variant (GNH = Gaylord National Harbor, fixed rate)
    create_expense_account(
        code="PARKING_GNH",
        name="Parking - Gaylord",
        description="Gaylord hotel parking",
        spend_type_codes=['DIVVY'],
        spend_types=spend_types,
        approval_group=hotel_group,
        is_admin_only=False,
        is_contract_eligible=False,
        is_fixed_cost=True,
        default_unit_price_cents=2500,  # Placeholder — replace with real rate
        sort_order=sort_order,
        ui_display_group=UI_GROUP_HOTEL_SERVICES,
    )
    accounts_created += 1

    db.session.flush()
    print(f"  Created {accounts_created} expense accounts")


def create_expense_account(
    code: str,
    name: str,
    description: Optional[str],
    spend_type_codes: list[str],
    spend_types: dict[str, SpendType],
    approval_group: Optional[ApprovalGroup],
    is_admin_only: bool,
    is_contract_eligible: bool,
    is_fixed_cost: bool,
    default_unit_price_cents: Optional[int],
    sort_order: int,
    prompt_mode_override: Optional[str] = None,
    ui_display_group: Optional[str] = None,
) -> ExpenseAccount:
    """Create an expense account with proper settings."""

    # Determine spend type mode
    if len(spend_type_codes) == 1:
        spend_type_mode = SPEND_TYPE_MODE_SINGLE_LOCKED
        default_spend_type = spend_types.get(spend_type_codes[0])
    else:
        spend_type_mode = SPEND_TYPE_MODE_ALLOW_LIST
        default_spend_type = spend_types.get(spend_type_codes[0]) if spend_type_codes else None

    # Determine visibility mode
    visibility_mode = VISIBILITY_MODE_RESTRICTED if is_admin_only else VISIBILITY_MODE_ALL

    # Determine prompt mode for fixed-cost items (use override if provided)
    if prompt_mode_override is not None:
        prompt_mode = prompt_mode_override
    else:
        prompt_mode = PROMPT_MODE_REQUIRE_EXPLICIT_NA if is_fixed_cost else PROMPT_MODE_NONE

    # Determine UI group (use override if provided, otherwise derive from is_fixed_cost)
    if ui_display_group is None:
        ui_display_group = UI_GROUP_KNOWN_COSTS if is_fixed_cost else None

    account = ExpenseAccount(
        code=code,
        name=name,
        description=description,
        is_active=True,
        is_contract_eligible=is_contract_eligible,
        spend_type_mode=spend_type_mode,
        default_spend_type_id=default_spend_type.id if default_spend_type else None,
        visibility_mode=visibility_mode,
        approval_group_id=approval_group.id if approval_group else None,
        is_fixed_cost=is_fixed_cost,
        default_unit_price_cents=default_unit_price_cents,
        unit_price_locked=is_fixed_cost,
        warehouse_default=False,
        ui_display_group=ui_display_group,
        prompt_mode=prompt_mode,
        sort_order=sort_order,
    )
    db.session.add(account)

    # Add allowed spend types
    for st_code in spend_type_codes:
        st = spend_types.get(st_code)
        if st:
            account.allowed_spend_types.append(st)

    return account


def run_all_seeds():
    """Master seeder - run all seed functions in order."""
    print("=" * 60)
    print("Running configuration seeds...")
    print("=" * 60)

    approval_groups = seed_approval_groups()
    work_types = seed_work_types()
    seed_work_type_configs(work_types)
    seed_contract_types(approval_groups)
    seed_supply_categories(approval_groups)
    spend_types = seed_spend_types()
    seed_reference_data()
    divisions = seed_divisions()
    departments = seed_departments(divisions)
    seed_event_cycles()
    seed_expense_accounts(approval_groups, spend_types)

    db.session.commit()

    print("=" * 60)
    print("Configuration seeding complete!")
    print("=" * 60)


if __name__ == "__main__":
    # Allow running directly for testing
    from app import create_app
    app = create_app()
    with app.app_context():
        run_all_seeds()
