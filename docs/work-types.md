# Work Types

The system supports multiple **work types** - different categories of requests that share the same workflow engine.

## Current Work Types

| Code | Name | URL Slug | Status | Contact |
|------|------|----------|--------|---------|
| BUDGET | Budget Requests | `/budget/` | **Live** | biz@magfest.org |
| TECHOPS | TechOps Requests | `/techops/` | **Live** | techops@magfest.org |
| SUPPLY | Supply Orders | `/supply/` | In development (models + admin pages exist; requester UI next) | festops@magfest.org |
| AV | AV Requests | `/av/` | In development (on `feature/AV-Request` branch) | av@magfest.org |
| CONTRACT | Contracts | `/contracts/` | Future (data model exists, no UI) | biz@magfest.org |

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
    uses_dispatch=True,                   # Lifecycle: admin dispatch stage? (BUDGET yes, TECHOPS no)
    has_admin_final=True,                 # Lifecycle: admin final review stage?
    item_singular="Budget",               # Display labels
    item_plural="Budgets",
    line_singular="Line",
    line_plural="Lines",
)
```

The lifecycle flags default to `False` — new work types opt into stages explicitly.
A work type with both flags off (like TECHOPS) skips dispatch and auto-finalizes
when the last line is decided (`app/routes/work/helpers/lifecycle.py`).

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

### ContractLineDetail (Future Release)

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

### TechOpsLineDetail (Live)

```python
service_type_id         # TechOps service type (ethernet, phone, radio, ...)
location / usage        # Per-instance fields (one WorkLine per instance)
description             # Single-line services (wifi, other)
config                  # Service-specific extras (JSON)
routed_approval_group_id  # Snapshot from the service type's routing
```

(See `app/models/techops.py` — also has `TechOpsServiceType` catalog and
`TechOpsRequestDetail` for request-level fields.)

### SupplyOrderLineDetail (In Development)

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

### category (TechOps — live; Supply Orders will use it too)

```
TechOps Line                          Supply Line
    → TechOpsLineDetail.service_type      → SupplyOrderLineDetail.item
    → service type's approval group       → SupplyItem.category
    → Approvers review                    → SupplyCategory.approval_group
```

Implemented per-type in `app/routing/category.py`.

## Adding a New Work Type

See **[`docs/adding-a-work-type.md`](adding-a-work-type.md)** — the full 10-step
recipe distilled from how TECHOPS shipped. Short version: detail model → migrations →
seeds (`app/seeds/bootstrap.py`) → routing → line-detail dispatch → **own route
package** `app/routes/work/<type>/` → **own template tree** `app/templates/<type>/` →
activation. Do NOT extend the budget routes; TECHOPS (`app/routes/work/techops/`)
is the reference implementation.

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

This is why `line_details.py` lives at the app root - it's not specific to any one work type.
