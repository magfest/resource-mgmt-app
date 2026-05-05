"""
Bootstrap seed — schema-required rows the app cannot function without.

Contains rows that code paths or templates reference by code/name. Operators
must NOT delete these. To customize, edit the row via admin UI or write a
migration. The seed itself is insert-only and will not overwrite admin edits.

What lives here vs demo_data.py:
- bootstrap = referenced by code (worktypes, approval groups, hotel wizard
  expense accounts) — deleting breaks the app.
- demo_data = operator-replaceable starter content (depts, event cycle,
  divisions) — operators delete and replace with real data.

Idempotency contract:
    All seed functions INSERT missing rows only. They never UPDATE existing
    rows and never re-add deleted rows. Safe to re-run on populated DBs.
    To update seed values during development, drop the row and re-seed, or
    write a migration.
"""
from __future__ import annotations

from typing import Optional

from app import db
from app.models import (
    ApprovalGroup,
    ConfidenceLevel,
    ContractType,
    ExpenseAccount,
    FrequencyOption,
    PriorityLevel,
    SpendType,
    SupplyCategory,
    TechOpsServiceType,
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


def seed_work_types() -> dict[str, WorkType]:
    """Seed work types (BUDGET, CONTRACT, SUPPLY, TECHOPS, AV).

    Insert-only. Existing rows returned unchanged; admin edits to
    name / sort_order / is_active are preserved.
    """
    print("Seeding work types...")

    # is_active=False for work types whose UI isn't built yet (CONTRACT,
    # SUPPLY) or that should opt-in per environment (TECHOPS is enabled
    # in staging only via the admin Work Types page after seeding).
    # Inactive worktypes still let URL routing resolve their slugs.
    work_types_data = [
        ("BUDGET", "Budget Requests", 10, True),
        ("CONTRACT", "Contracts", 20, False),
        ("SUPPLY", "Supply Orders", 30, False),
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
    active_count = sum(1 for w in work_types.values() if w.is_active)
    print(f"  {len(work_types)} work types present ({active_count} active)")
    return work_types


def seed_approval_groups(work_types: dict[str, WorkType]) -> dict[str, ApprovalGroup]:
    """Seed approval groups for all worktypes.

    Approval groups are scoped per-worktype (composite unique on
    (work_type_id, code)). Returned dict is keyed by group code; seed
    data uses distinct codes across worktypes to keep that flat lookup
    unambiguous.
    """
    print("Seeding approval groups...")

    groups_by_worktype = {
        "BUDGET": [
            ("LOGISTICS", "Logistics / Warehouse", "Items that should be reviewed by the logistics or warehouse teams", 10),
            ("OFFICE", "Org Ops", "Items that need to be reviewed by the office or other organization leadership", 20),
            ("GEN", "General", "General items that normally do not need custom reviews or approval", 30),
            ("GUEST", "Guest / Booking", "Guest and booking related items", 40),
            ("TECH", "FestOps - Tech", "Reviews technical equipment, rentals, and tech-related expenses", 50),
            ("HOTEL", "Hotels Team", "Reviews hotel rooms, venue fees, and hotel-related expenses", 60),
            ("OTHER_SPECIAL", "Other Specialty", "Reviews general expenses and admin items", 70),
        ],
        "TECHOPS": [
            ("TECHOPS_NET", "TechOps Networking", "Reviews network and phone services (WiFi, ethernet, bandwidth, phone lines)", 10),
            ("TECHOPS_GEN", "TechOps Generic", "Reviews dedicated radio channels, consultations, and any other TechOps requests", 20),
        ],
    }

    groups: dict[str, ApprovalGroup] = {}
    for wt_code, group_rows in groups_by_worktype.items():
        wt = work_types.get(wt_code)
        if wt is None:
            raise RuntimeError(f"{wt_code} work type must be seeded before approval groups")

        for code, name, description, sort_order in group_rows:
            existing = (
                db.session.query(ApprovalGroup)
                .filter_by(code=code, work_type_id=wt.id)
                .first()
            )
            if existing:
                groups[code] = existing
                continue

            group = ApprovalGroup(
                work_type_id=wt.id,
                code=code,
                name=name,
                description=description,
                is_active=True,
                sort_order=sort_order,
            )
            db.session.add(group)
            groups[code] = group

    db.session.flush()
    print(f"  {len(groups)} approval groups present")
    return groups


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
            uses_dispatch=True,
            has_admin_final=True,
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
            uses_dispatch=True,
            has_admin_final=True,
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
            uses_dispatch=True,
            has_admin_final=True,
            item_singular="Supply Order",
            item_plural="Supply Orders",
            line_singular="Item",
            line_plural="Items",
        )
        db.session.add(config)
        configs_created += 1

    # TECHOPS config
    techops_wt = work_types.get("TECHOPS")
    if techops_wt and not techops_wt.config:
        config = WorkTypeConfig(
            work_type_id=techops_wt.id,
            url_slug="techops",
            public_id_prefix="TEC",
            line_detail_type="techops",
            routing_strategy=ROUTING_STRATEGY_CATEGORY,
            supports_supplementary=False,
            supports_fixed_costs=False,
            uses_dispatch=False,
            has_admin_final=False,
            item_singular="TechOps Request",
            item_plural="TechOps Requests",
            line_singular="Service",
            line_plural="Services",
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
            uses_dispatch=False,
            has_admin_final=False,
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
    print(f"  {len(contract_types)} contract types present")
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
    print(f"  {len(categories)} supply categories present")
    return categories


def seed_techops_service_types(approval_groups: dict[str, ApprovalGroup]) -> dict[str, TechOpsServiceType]:
    """Seed TechOps service types.

    Each row carries the default approval group used by category routing
    at submit time. instance_noun controls form rendering: NULL → single
    description box for the whole service; non-NULL → repeating-group
    section where each instance (drop, phone line, channel) is its own
    WorkLine with location + usage.

    BANDWIDTH was deactivated when its concerns were merged into WIFI
    and ETHERNET descriptions; the row stays for rollback / historical
    line preservation.

    Insert-only per the module idempotency contract. To update an
    existing row's metadata (description, sort_order, etc.), edit it via
    admin UI or write a migration — the seed will not silently overwrite.
    """
    print("Seeding TechOps service types...")

    # (code, name, description, approval_group_code, sort_order,
    #  instance_noun, is_active)
    service_types_data = [
        (
            "WIFI",
            "WiFi access/coverage",
            (
                "WiFi coverage for staff or attendees in a specific area "
                "or for a use case. Call out heavy bandwidth needs "
                "(streaming, large transfers, attendees on network) in "
                "the description."
            ),
            "TECHOPS_NET", 10, None, True,
        ),
        (
            "ETHERNET",
            "Hardwired ethernet",
            (
                "Wired network drop at a specific location for a specific "
                "use. Call out heavy bandwidth needs (streaming, large "
                "transfers) in the per-drop usage notes."
            ),
            "TECHOPS_NET", 20, "drop", True,
        ),
        (
            # Deactivated: bandwidth concerns moved into WIFI / ETHERNET
            # descriptions. Row kept for rollback only.
            "BANDWIDTH",
            "Special bandwidth usage",
            "Streaming, large file transfers, attendees-on-network, or other heavy bandwidth needs",
            "TECHOPS_NET", 30, None, False,
        ),
        (
            "PHONE",
            "Hardwired phone line",
            "Dedicated phone line at a location, internal-only or external-callable",
            "TECHOPS_NET", 40, "phone line", True,
        ),
        (
            "RADIO_CHANNEL",
            "Dedicated radio channel",
            "Reserved channel on the event radio system",
            "TECHOPS_GEN", 50, "channel", True,
        ),
        (
            "OTHER",
            "Other / consultation",
            "Anything not covered above, including general consultation requests",
            "TECHOPS_GEN", 60, None, True,
        ),
    ]

    service_types = {}
    for (
        code, name, description, approval_group_code, sort_order,
        instance_noun, is_active,
    ) in service_types_data:
        existing = db.session.query(TechOpsServiceType).filter_by(code=code).first()
        if existing:
            service_types[code] = existing
            continue

        approval_group = approval_groups.get(approval_group_code)
        if approval_group is None:
            raise RuntimeError(
                f"Approval group {approval_group_code} must be seeded before TechOps service type {code}"
            )

        st = TechOpsServiceType(
            code=code,
            name=name,
            description=description,
            default_approval_group_id=approval_group.id,
            is_active=is_active,
            sort_order=sort_order,
            instance_noun=instance_noun,
            # quantity_label intentionally left None — per-instance services
            # don't use it, single-line services don't surface a qty field.
            quantity_label=None,
        )
        db.session.add(st)
        service_types[code] = st

    db.session.flush()
    active_count = sum(1 for s in service_types.values() if s.is_active)
    print(f"  {len(service_types)} TechOps service types present ({active_count} active)")
    return service_types


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
    print(f"  {len(spend_types)} spend types present")
    return spend_types


def seed_reference_data():
    """Seed frequency options, confidence levels, priority levels.

    Form dropdowns crash without these — required for budget line entry.
    """
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


def seed_hotel_expense_accounts(
    approval_groups: dict[str, ApprovalGroup],
    spend_types: dict[str, SpendType],
):
    """Seed the 8 HTL_* hotel expense accounts.

    These codes are HARDCODED in the budget-draft hotel wizard
    (app/routes/work/work_items/edit.py:800-805 builds account codes via
    f-string interpolation: HTL_{room}_{scenario}). Deleting them breaks
    the wizard. Operators must NOT remove these.

    Insert-only per module contract. The early-exit covers the case where
    seed_expense_accounts has been run before (any ExpenseAccount existing
    means we don't re-create).
    """
    print("Seeding hotel expense accounts...")

    # Skip entirely if any expense account exists. Matches the original
    # behavior — operators can prune the HTL_ subset if they really want
    # to (and accept the wizard breakage), and re-running won't add them
    # back.
    if db.session.query(ExpenseAccount).first():
        print("  Expense accounts already exist, skipping...")
        return

    sort_order = 10
    accounts_created = 0
    hotel_group = approval_groups.get('HOTEL')

    # MAGFest Paid variants (hits department budget)
    magfest_paid_variants = [
        ("HTL_STD_MAGPAID", "Standard Room (MAGFest Paid)", 15000, "Standard hotel room - MAGFest covers cost"),
        ("HTL_EXEC_MAGPAID", "Executive Suite (MAGFest Paid)", 30000, "Executive suite - MAGFest covers cost"),
        ("HTL_HOSP_MAGPAID", "Hospitality Suite (MAGFest Paid)", 60000, "Hospitality suite with attached bedrooms - MAGFest covers cost"),
    ]

    for code, name, price_cents, desc in magfest_paid_variants:
        create_expense_account(
            code=code, name=name, description=desc,
            spend_type_codes=['BANK'], spend_types=spend_types,
            approval_group=hotel_group,
            is_admin_only=False, is_contract_eligible=False,
            is_fixed_cost=True, default_unit_price_cents=price_cents,
            sort_order=sort_order, ui_display_group=UI_GROUP_HOTEL_SERVICES,
        )
        sort_order += 10
        accounts_created += 1

    # Third Party Held variants ($0 — partner books and pays, we just reserve it)
    held_variants = [
        ("HTL_STD_HELD", "Standard Room (Third Party Pays)", "Standard room held for partner to book - no budget impact"),
        ("HTL_EXEC_HELD", "Executive Suite (Third Party Pays)", "Executive suite held for partner to book - no budget impact"),
        ("HTL_HOSP_HELD", "Hospitality Suite (Third Party Pays)", "Hospitality suite held for partner to book - no budget impact"),
    ]

    for code, name, desc in held_variants:
        create_expense_account(
            code=code, name=name, description=desc,
            spend_type_codes=['BANK'], spend_types=spend_types,
            approval_group=hotel_group,
            is_admin_only=False, is_contract_eligible=False,
            is_fixed_cost=True, default_unit_price_cents=0,
            sort_order=sort_order,
            prompt_mode_override=PROMPT_MODE_NONE,
            ui_display_group=UI_GROUP_HOTEL_SERVICES,
        )
        sort_order += 10
        accounts_created += 1

    # Staff Crash variants ($0 — staffers pay out of pocket, informational only)
    crash_variants = [
        ("HTL_EXEC_CRASH", "Executive Suite (Staff Crash)", "Executive suite for staff crash space - paid out of pocket by staffers, not department budget"),
        ("HTL_HOSP_CRASH", "Hospitality Suite (Staff Crash)", "Hospitality suite for staff crash space - paid out of pocket by staffers, not department budget"),
    ]

    for code, name, desc in crash_variants:
        create_expense_account(
            code=code, name=name, description=desc,
            spend_type_codes=['BANK'], spend_types=spend_types,
            approval_group=hotel_group,
            is_admin_only=False, is_contract_eligible=False,
            is_fixed_cost=True, default_unit_price_cents=0,
            sort_order=sort_order,
            prompt_mode_override=PROMPT_MODE_NONE,
            ui_display_group=UI_GROUP_HOTEL_SERVICES,
        )
        sort_order += 10
        accounts_created += 1

    db.session.flush()
    print(f"  Created {accounts_created} hotel expense accounts")


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
    """Create an expense account with proper settings.

    Shared helper used by seed_hotel_expense_accounts (here) and
    seed_demo_parking_accounts (in demo_data.py).
    """
    if len(spend_type_codes) == 1:
        spend_type_mode = SPEND_TYPE_MODE_SINGLE_LOCKED
        default_spend_type = spend_types.get(spend_type_codes[0])
    else:
        spend_type_mode = SPEND_TYPE_MODE_ALLOW_LIST
        default_spend_type = spend_types.get(spend_type_codes[0]) if spend_type_codes else None

    visibility_mode = VISIBILITY_MODE_RESTRICTED if is_admin_only else VISIBILITY_MODE_ALL

    if prompt_mode_override is not None:
        prompt_mode = prompt_mode_override
    else:
        prompt_mode = PROMPT_MODE_REQUIRE_EXPLICIT_NA if is_fixed_cost else PROMPT_MODE_NONE

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

    for st_code in spend_type_codes:
        st = spend_types.get(st_code)
        if st:
            account.allowed_spend_types.append(st)

    return account


def run_bootstrap() -> None:
    """Run the full bootstrap seed.

    Order matters: worktypes → approval groups → configs → typed children
    (contracts, supply, techops) → spend types → reference data → hotel
    accounts. Each function commits via db.session.flush(); the final
    commit happens here.
    """
    print("=" * 60)
    print("Bootstrap seed (required structural data)...")
    print("=" * 60)

    work_types = seed_work_types()
    approval_groups = seed_approval_groups(work_types)
    seed_work_type_configs(work_types)
    seed_contract_types(approval_groups)
    seed_supply_categories(approval_groups)
    seed_techops_service_types(approval_groups)
    spend_types = seed_spend_types()
    seed_reference_data()
    seed_hotel_expense_accounts(approval_groups, spend_types)

    db.session.commit()
    print("Bootstrap seed complete.")
