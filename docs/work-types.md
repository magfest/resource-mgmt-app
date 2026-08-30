# Work Types

The system supports multiple **work types**, different categories of requests that
share the same workflow engine.

## Current Work Types

State as of August 2026. Slugs are the seeded `WorkTypeConfig.url_slug` values
(`app/seeds/bootstrap.py:170-255`).

| Work type | URL slug | State | What exists | Contact |
| --- | --- | --- | --- | --- |
| BUDGET | `budget` | Complete | The full pipeline: dispatch, admin final, reports, comments, supplementary requests. The reference implementation | biz@magfest.org |
| SUPPLY | `supply` | Partial | Ordering, catalog, and admin final. No warehouse, fulfillment, or returns | festops@magfest.org |
| TECHOPS | `techops` | Partial | Request entry and line review. No dispatch stage, no admin final. Deployed to production so the team can do test runs and give feedback, not for general use | techops@magfest.org |
| CONTRACT | `contracts` | Model only | Data model and an admin configuration page. No route package, no templates | biz@magfest.org |
| AV | `av` | Not built | Not present on this branch | av@magfest.org |

BUDGET is the only work type in general production use. A route package on this
branch is not evidence that a work type is in service.

`bootstrap.py:67-71` seeds BUDGET and SUPPLY with `is_active=True`; CONTRACT,
TECHOPS, and AV seed inactive. TECHOPS runs in production despite seeding inactive,
so a fresh development database will not show it in the pickers until someone
enables it under Admin, Work Types. That flip is a manual step.

## How It Works

### WorkType Model

Each work type has a database record:

```python
WorkType(
    code="BUDGET",
    name="Budget Requests",
    is_active=True,
    sort_order=10,
)
```

### WorkTypeConfig Model

Configuration is stored separately for flexibility:

```python
WorkTypeConfig(
    work_type_id=1,
    url_slug="budget",                    # URL path segment
    public_id_prefix="BUD",               # For IDs like SMF27-TECHOPS-BUD-1
    line_detail_type="budget",            # Which detail model to use
    routing_strategy="expense_account",   # How to route to approvers
    supports_supplementary=True,          # Allow supplementary requests?
    uses_dispatch=True,                   # Lifecycle: admin dispatch stage?
    has_admin_final=True,                 # Lifecycle: admin final review stage?
    uses_board_release=True,              # Hold the finished item for board sign-off?
    item_singular="Budget",               # Display labels
    item_plural="Budgets",
    line_singular="Line",
    line_plural="Lines",
)
```

### Lifecycle flags

Three flags on `WorkTypeConfig` decide which stages a request passes through.

| Flag | Controls |
| --- | --- |
| `uses_dispatch` | Whether submit lands in AWAITING_DISPATCH, where an admin assigns approval groups per line |
| `has_admin_final` | Whether an admin sets the authoritative amounts and finalizes. Without it the item auto-finalizes when the last line is decided (`app/routes/work/helpers/lifecycle.py`) |
| `uses_board_release` | Whether a finished item waits for board sign-off before its department is told. See [Workflow: Board release](./workflow.md#board-release) |

All three default to `False` (`app/models/workflow.py:102`, `:103`, `:108`). A new
work type opts into every stage explicitly. TECHOPS has neither dispatch nor admin
final because nobody turned them on.

[Workflow: Stages by work type](./workflow.md#stages-by-work-type) carries the
seeded value of each flag per work type.

## Line Detail Models

Each work type has its own line detail model with type-specific fields:

### BudgetLineDetail

```python
expense_account_id      # Which expense account
spend_type_id           # How it's spent (purchase, rental, etc.)
quantity                # Number of units
unit_price_cents        # Price per unit
routed_approval_group_id  # Computed from expense_account
```

### ContractLineDetail (model only)

```python
contract_type_id        # Type of contract
vendor_name             # Vendor/contractor name
vendor_contact          # Contact info
contract_amount_cents   # Total contract value
start_date              # Contract start
end_date                # Contract end
terms_summary           # Key terms
routed_approval_group_id  # Computed from contract_type
```

### TechOpsLineDetail

```python
service_type_id         # TechOps service type (ethernet, phone, radio, ...)
location / usage        # Per-instance fields (one WorkLine per instance)
description             # Single-line services (wifi, other)
config                  # Service-specific extras (JSON)
routed_approval_group_id  # Snapshot from the service type's routing
```

(See `app/models/techops.py`; it also has `TechOpsServiceType` catalog and
`TechOpsRequestDetail` for request-level fields.)

### SupplyOrderLineDetail

```python
item_id                 # Warehouse item from catalog
quantity_requested      # How many needed
quantity_approved       # How many approved (may be less)
needed_by_date          # When needed
delivery_location       # Where to deliver
routed_approval_group_id  # Computed from item.category
```

## Routing Strategies

Different work types route to approvers differently:

### expense_account (Budget)

```
Budget Line
    → BudgetLineDetail.expense_account
    → ExpenseAccount.approval_group
    → Approvers in that group review
```

### contract_type (Contracts)

```
Contract Line
    → ContractLineDetail.contract_type
    → ContractType.approval_group
    → Approvers in that group review
```

### category (TechOps and Supply Orders)

```
TechOps Line                          Supply Line
    → TechOpsLineDetail.service_type      → SupplyOrderLineDetail.item
    → service type's approval group       → SupplyItem.category
    → Approvers review                    → SupplyCategory.approval_group
```

Implemented per-type in `app/routing/category.py`.

## Work Type Access Control

Access to work types is controlled per-membership:

```
User
    └── DepartmentMembership (TechOps, SMF2027)
        └── DepartmentMembershipWorkTypeAccess
            ├── BUDGET: can_view=True, can_edit=True
            ├── CONTRACT: can_view=False, can_edit=False  ← No access
            └── SUPPLY: can_view=True, can_edit=False    ← View only
```

This allows:
- Budget access without seeing contracts
- Restricting contracts to specific people
- View-only access for oversight

Configure via Admin → Departments → Members → Edit.

## Generic Helpers

The `app/line_details.py` module provides helpers that work across all work types:

```python
from app.line_details import get_line_detail, get_line_amount_cents

# Works for any line type
detail = get_line_detail(line)  # Returns BudgetLineDetail, ContractLineDetail, etc.
amount = get_line_amount_cents(line)  # Returns amount regardless of calculation method
```

This is why `line_details.py` lives at the app root; it is not specific to any one work type.
