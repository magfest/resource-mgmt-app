"""
Seed script for budget configuration data.

Parses Demo_Data/Budget App Mappings.xlsx and creates:
- Approval groups (TECH, HOTEL, OTHER)
- Spend types (DIVVY, BANK)
- Departments (including OFFICE for admin-restricted items)
- Event cycles
- Reference data (frequency, confidence, priority)
- Expense accounts with approval group assignments, spend types, and fixed costs
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from app import db
from app.models import (
    ApprovalGroup,
    ConfidenceLevel,
    Department,
    EventCycle,
    ExpenseAccount,
    FrequencyOption,
    PriorityLevel,
    SpendType,
    SPEND_TYPE_MODE_SINGLE_LOCKED,
    SPEND_TYPE_MODE_ALLOW_LIST,
    VISIBILITY_MODE_ALL,
    VISIBILITY_MODE_RESTRICTED,
    PROMPT_MODE_NONE,
    PROMPT_MODE_REQUIRE_EXPLICIT_NA,
    UI_GROUP_KNOWN_COSTS,
)


def seed_approval_groups() -> dict[str, ApprovalGroup]:
    """Seed TECH, HOTEL, OTHER approval groups."""
    print("Seeding approval groups...")

    groups_data = [
        ("TECH", "Tech Review", "Reviews technical equipment, rentals, and tech-related expenses", 10),
        ("HOTEL", "Hotel Review", "Reviews hotel rooms, venue fees, and hotel-related expenses", 20),
        ("OTHER", "Admin Review", "Reviews general expenses and admin items", 30),
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
            ("ONE_TIME", "One Time", "Single purchase for this event", 10),
            ("RECURRING", "Recurring", "Recurring expense across events", 20),
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
        ]
        for code, name, desc, sort in priorities:
            db.session.add(PriorityLevel(
                code=code, name=name, description=desc,
                is_active=True, sort_order=sort
            ))
        db.session.flush()
        print("  Created priority levels")


def seed_departments() -> dict[str, Department]:
    """Seed departments including OFFICE for admin-restricted items."""
    print("Seeding departments...")

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

    departments = {}
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
        ("SMF2026", "Super MAGFest 2026", True, True, 10),
        ("SMF2027", "Super MAGFest 2027", True, False, 20),
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


def parse_price(text: str) -> Optional[int]:
    """
    Extract price in cents from text like "$244/night" or "$19/night".
    Returns None if no price found.
    """
    if not text:
        return None

    # Look for dollar amounts like $244, $509.10, etc.
    match = re.search(r'\$(\d+(?:\.\d{2})?)', str(text))
    if match:
        price_str = match.group(1)
        price_float = float(price_str)
        return int(price_float * 100)  # Convert to cents
    return None


def parse_spend_types(spend_type_str: str) -> list[str]:
    """Parse spend type string like "Divvy, Bank" into list of codes."""
    if not spend_type_str or str(spend_type_str).lower() == 'nan':
        return []

    result = []
    spend_type_str = str(spend_type_str).strip()

    for part in spend_type_str.split(','):
        part = part.strip().upper()
        if part == 'DIVVY':
            result.append('DIVVY')
        elif part == 'BANK':
            result.append('BANK')

    return result


def slugify(name: str) -> str:
    """Convert expense account name to a code-friendly slug."""
    # Remove special characters, convert to uppercase with underscores
    slug = re.sub(r'[^a-zA-Z0-9\s]', '', name)
    slug = re.sub(r'\s+', '_', slug.strip())
    return slug.upper()


def determine_approval_group(row: dict) -> str:
    """
    Determine approval group code based on expense account characteristics.

    Mapping logic:
    - Hotel-related items → HOTEL
    - Tech/Equipment items → TECH
    - Everything else → OTHER
    """
    name = str(row.get('Expense Accounts', '')).lower()

    # Hotel-related
    hotel_keywords = ['hotel', 'venue', 'gaylord']
    if any(kw in name for kw in hotel_keywords):
        return 'HOTEL'

    # Tech-related (including "For Review By" = YES items that seem tech-related)
    tech_keywords = ['tech', 'equipment', 'a/v', 'rental', 'truck', 'content room']
    if any(kw in name for kw in tech_keywords):
        return 'TECH'

    # Default to OTHER (Admin)
    return 'OTHER'


def seed_expense_accounts_from_spreadsheet(
    approval_groups: dict[str, ApprovalGroup],
    spend_types: dict[str, SpendType],
    departments: dict[str, Department],
):
    """
    Parse Demo_Data/Budget App Mappings.xlsx and create expense accounts.
    """
    print("Seeding expense accounts from spreadsheet...")

    # Check if already seeded
    if db.session.query(ExpenseAccount).first():
        print("  Expense accounts already exist, skipping...")
        return

    try:
        import pandas as pd
    except ImportError:
        print("  ERROR: pandas not installed. Run: pip install pandas openpyxl")
        return

    # Find the spreadsheet
    project_root = Path(__file__).parent.parent.parent
    spreadsheet_path = project_root / "Demo_Data" / "Budget App Mappings.xlsx"

    if not spreadsheet_path.exists():
        print(f"  ERROR: Spreadsheet not found at {spreadsheet_path}")
        return

    df = pd.read_excel(spreadsheet_path)
    print(f"  Found {len(df)} rows in spreadsheet")

    sort_order = 10
    accounts_created = 0

    office_dept = departments.get('OFFICE')

    for _, row in df.iterrows():
        name = str(row.get('Expense Accounts', '')).strip()
        if not name or name.lower() == 'nan':
            continue

        # Parse basic fields
        spend_type_codes = parse_spend_types(row.get('Spend Type', ''))
        budget_availability = str(row.get('Budget Availability', '')).strip().upper()
        is_admin_only = budget_availability == 'ADMIN'
        is_contract_eligible = str(row.get('Contract/Approval Check', '')).strip().upper() == 'YES'
        fixed_cost_text = str(row.get('Fixed Cost', ''))
        has_fixed_cost = fixed_cost_text and fixed_cost_text.lower() != 'nan'

        # Determine approval group
        approval_group_code = determine_approval_group(row)
        approval_group = approval_groups.get(approval_group_code)

        # Check if this is Hotel Rooms (needs expansion into variants)
        if name == 'Hotel Rooms' and has_fixed_cost:
            # Create multiple accounts for hotel room variants
            hotel_variants = [
                ("HOTEL_ROOM_REGULAR", "Hotel Room - Regular", 24400, "Regular sleeping room (king/double-double)"),
                ("HOTEL_ROOM_ATRIUM", "Hotel Room - Atrium", 28900, "Atrium sleeping room (king/double-double)"),
                ("HOTEL_ROOM_EXEC_SUITE", "Hotel Room - Executive Suite", 50910, "Executive suite"),
                ("HOTEL_ROOM_HOSP_SUITE", "Hotel Room - Hospitality Suite", 69950, "Hospitality suite"),
            ]

            for code, variant_name, price_cents, desc in hotel_variants:
                account = create_expense_account(
                    code=code,
                    name=variant_name,
                    description=desc,
                    spend_type_codes=['BANK'],  # Hotel rooms are Bank only
                    spend_types=spend_types,
                    approval_group=approval_groups.get('HOTEL'),
                    is_admin_only=False,
                    is_contract_eligible=is_contract_eligible,
                    is_fixed_cost=True,
                    default_unit_price_cents=price_cents,
                    office_dept=office_dept,
                    sort_order=sort_order,
                )
                sort_order += 10
                accounts_created += 1
            continue

        # Check if this is Parking & Tolls (add Gaylord variant)
        if name == 'Parking & Tolls' and has_fixed_cost:
            # Create generic parking account
            code = slugify(name)
            account = create_expense_account(
                code=code,
                name=name,
                description="General parking and tolls",
                spend_type_codes=spend_type_codes,
                spend_types=spend_types,
                approval_group=approval_group,
                is_admin_only=is_admin_only,
                is_contract_eligible=is_contract_eligible,
                is_fixed_cost=False,
                default_unit_price_cents=None,
                office_dept=office_dept,
                sort_order=sort_order,
            )
            sort_order += 10
            accounts_created += 1

            # Create Gaylord parking variant
            account = create_expense_account(
                code="PARKING_GAYLORD",
                name="Parking - Gaylord",
                description="Gaylord hotel parking",
                spend_type_codes=['DIVVY'],
                spend_types=spend_types,
                approval_group=approval_groups.get('HOTEL'),
                is_admin_only=False,
                is_contract_eligible=is_contract_eligible,
                is_fixed_cost=True,
                default_unit_price_cents=1900,  # $19/night
                office_dept=office_dept,
                sort_order=sort_order,
            )
            sort_order += 10
            accounts_created += 1
            continue

        # Standard expense account
        code = slugify(name)

        # Parse price if fixed cost
        price_cents = None
        if has_fixed_cost:
            price_cents = parse_price(fixed_cost_text)

        # Appearance/Performance Fees is marked as fixed cost YES in "For Review By" column
        # but doesn't have specific pricing - it means price is confirmed per contract
        for_review = str(row.get('For Review By', '')).strip().upper()
        if for_review == 'YES' and not has_fixed_cost:
            # This indicates it needs special review, possibly tech
            # but we already determined approval group above
            pass

        account = create_expense_account(
            code=code,
            name=name,
            description=None,
            spend_type_codes=spend_type_codes,
            spend_types=spend_types,
            approval_group=approval_group,
            is_admin_only=is_admin_only,
            is_contract_eligible=is_contract_eligible,
            is_fixed_cost=has_fixed_cost and price_cents is not None,
            default_unit_price_cents=price_cents,
            office_dept=office_dept,
            sort_order=sort_order,
        )
        sort_order += 10
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
    office_dept: Optional[Department],
    sort_order: int,
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

    # Determine prompt mode for fixed-cost items
    prompt_mode = PROMPT_MODE_REQUIRE_EXPLICIT_NA if is_fixed_cost else PROMPT_MODE_NONE

    # Determine UI group
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

    # Add department restriction if admin-only
    if is_admin_only and office_dept:
        account.visible_to_departments.append(office_dept)

    return account


def run_all_seeds():
    """Master seeder - run all seed functions in order."""
    print("=" * 60)
    print("Running configuration seeds...")
    print("=" * 60)

    approval_groups = seed_approval_groups()
    spend_types = seed_spend_types()
    seed_reference_data()
    departments = seed_departments()
    event_cycles = seed_event_cycles()
    seed_expense_accounts_from_spreadsheet(approval_groups, spend_types, departments)

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
