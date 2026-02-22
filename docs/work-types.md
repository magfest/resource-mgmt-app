# Work Types

The system supports multiple **work types** - different categories of requests that share the same workflow engine.

## Current Work Types

| Code | Name | URL Slug | Status | Contact |
|------|------|----------|--------|---------|
| BUDGET | Budget Requests | `/budget/` | Active | biz@magfest.org |
| CONTRACT | Contracts | `/contracts/` | Coming Soon | biz@magfest.org |
| SUPPLY | Supply Orders | `/supply/` | Coming Soon | festops@magfest.org |

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
    public_id_prefix="BUD",               # For IDs like BUD-A3F9K2
    line_detail_type="budget",            # Which detail model to use
    routing_strategy="expense_account",   # How to route to approvers
    supports_supplementary=True,          # Allow supplementary requests?
    item_singular="Budget",               # Display labels
    item_plural="Budgets",
    line_singular="Line",
    line_plural="Lines",
)
```

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

### ContractLineDetail (Coming Soon)

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

### SupplyOrderLineDetail (Coming Soon)

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

### category (Supply Orders)

```
Supply Line
    → SupplyOrderLineDetail.item
    → SupplyItem.category
    → SupplyCategory.approval_group
    → Approvers in that group review
```

## Adding a New Work Type

To add a new work type (e.g., "Travel Requests"):

### 1. Create the Line Detail Model

In `app/models.py`:

```python
class TravelLineDetail(db.Model):
    __tablename__ = "travel_line_details"

    work_line_id = db.Column(db.Integer, db.ForeignKey("work_lines.id"), primary_key=True)
    destination = db.Column(db.String(256), nullable=False)
    travel_date = db.Column(db.Date, nullable=False)
    estimated_cost_cents = db.Column(db.Integer, nullable=False)
    # ... other fields

    work_line = db.relationship("WorkLine", backref=db.backref("travel_detail", uselist=False))
```

### 2. Create a Routing Strategy (if needed)

In `app/routing/travel.py`:

```python
class TravelRoutingStrategy(RoutingStrategy):
    def get_approval_group(self, line):
        # Route all travel to a single approval group
        return ApprovalGroup.query.filter_by(code="TRAVEL").first()
```

### 3. Register in the Seed

In `app/seeds/config_seed.py`:

```python
WorkType(code="TRAVEL", name="Travel Requests", ...)
WorkTypeConfig(url_slug="travel", routing_strategy="travel", ...)
```

### 4. Create the Route Handler

Either add to existing `budget/portfolio.py` or create a new file.

### 5. Create Templates

Create form templates with the appropriate fields.

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
