# Architecture Overview

## The Big Picture

This system manages **work requests** for MAGFest events. A work request is something a department needs: budget items, vendor contracts, or warehouse supplies.

All request types share the same workflow:

```
DRAFT → SUBMITTED → UNDER REVIEW → APPROVED/REJECTED → FINALIZED
```

Instead of building separate systems for budgets, contracts, and supply orders, we built a **generic work type system** that handles all of them with the same code.

---

## Core Concepts

### Work Type

A **Work Type** defines a category of request. Each has its own:
- URL slug (`/budget/`, `/contracts/`, `/supply/`)
- Line detail model (what fields each line has)
- Routing strategy (how lines get assigned to approvers)
- Display labels ("Budget Lines" vs "Contract Items")

Current work types:

| Code | Name | Status |
|------|------|--------|
| BUDGET | Budget Requests | **Live** |
| CONTRACT | Contracts | Future release (data model exists, no UI yet) |
| SUPPLY | Supply Orders | Future release (data model exists, no UI yet) |

### Portfolio

A **Portfolio** is a container for one department's requests of one work type in one event cycle.

```
Portfolio = Department + Work Type + Event Cycle
```

Example: "TechOps Budget Portfolio for Super MAGFest 2027"

### Work Item

A **Work Item** is a single request within a portfolio. There are two kinds:

- **PRIMARY**: The main budget/request (one per portfolio)
- **SUPPLEMENTARY**: Additional requests added later (zero or more)

### Work Line

A **Work Line** is an individual line item. It has:
- A line number
- A description
- A status (PENDING, NEEDS_INFO, APPROVED, etc.)
- A **Line Detail** with type-specific data

### Line Detail

A **Line Detail** holds the type-specific fields for a line:

| Work Type | Line Detail Model | Key Fields |
|-----------|------------------|------------|
| Budget | BudgetLineDetail | expense_account, spend_type, quantity, unit_price |
| Contract | ContractLineDetail | vendor_name, contract_amount, start_date, end_date |
| Supply | SupplyOrderLineDetail | item, quantity_requested, needed_by_date |

The relationship is 1-to-1: every WorkLine has exactly one line detail.

---

## How Approval Routing Works

Different work types route to approvers differently:

```
Budget Line → Expense Account → Approval Group
Contract Line → Contract Type → Approval Group
Supply Line → Supply Category → Approval Group
```

This is implemented via the **routing strategy pattern**:

```python
# Each work type has a routing strategy
class ExpenseAccountRoutingStrategy:
    def get_approval_group(self, line):
        return line.budget_detail.expense_account.approval_group

class ContractTypeRoutingStrategy:
    def get_approval_group(self, line):
        return line.contract_detail.contract_type.approval_group
```

The `WorkTypeConfig.routing_strategy` field determines which strategy to use.

---

## Database Schema (Simplified)

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  WorkType   │────▶│WorkTypeConfig│     │   Division  │
└─────────────┘     └─────────────┘     └──────┬──────┘
       │                                       │
       ▼                                       ▼
┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│WorkPortfolio│◀────│  EventCycle │     │ Department  │
└──────┬──────┘     └─────────────┘     └──────┬──────┘
       │                                       │
       ▼                                       │
┌─────────────┐                                │
│  WorkItem   │◀───────────────────────────────┘
└──────┬──────┘
       │
       ▼
┌─────────────┐     ┌─────────────────┐
│  WorkLine   │────▶│ BudgetLineDetail │ (or ContractLineDetail, etc.)
└──────┬──────┘     └─────────────────┘
       │
       ▼
┌─────────────────┐
│ WorkLineReview  │────▶ ApprovalGroup
└─────────────────┘
```

---

## Permission Model

Access is controlled at multiple levels:

### 1. System Roles (UserRole)

| Role | Scope | Access |
|------|-------|--------|
| SUPER_ADMIN | Global | Everything |
| WORKTYPE_ADMIN | Per work type | Admin for one work type (e.g., Budget Admin) |
| APPROVER | Per approval group | Can review lines routed to their group |

### 2. Memberships

| Membership | Grants Access To |
|------------|------------------|
| DepartmentMembership | One department |
| DivisionMembership | All departments in a division |

### 3. Work Type Access

Memberships are scoped by work type. A user with "TechOps Budget access" doesn't automatically see "TechOps Contracts."

```
DepartmentMembership
    └── DepartmentMembershipWorkTypeAccess (per work type: can_view, can_edit)
```

---

## Request Lifecycle

```
┌─────────┐     ┌───────────┐     ┌──────────────┐     ┌───────────┐
│  DRAFT  │────▶│ SUBMITTED │────▶│ UNDER_REVIEW │────▶│ FINALIZED │
└─────────┘     └───────────┘     └──────────────┘     └───────────┘
     │                                   │
     │                                   ▼
     │                          ┌──────────────┐
     │                          │  NEEDS_INFO  │──────┐
     │                          └──────────────┘      │
     │                                   │            │
     │                                   ▼            │
     │                          ┌──────────────────┐  │
     └──────────────────────────│ NEEDS_ADJUSTMENT │◀─┘
                                └──────────────────┘
```

1. **DRAFT**: Requester is building the request
2. **SUBMITTED**: Requester submitted for review
3. **UNDER_REVIEW**: Approvers are reviewing lines
4. **NEEDS_INFO**: Approver asked a question, waiting for requester
5. **NEEDS_ADJUSTMENT**: Approver requested changes
6. **FINALIZED**: Admin locked the request

---

## URL Structure

```
/                                    # Home dashboard
/<event>/<dept>/                     # Department landing (all work types)
/<event>/<dept>/budget/              # Budget portfolio
/<event>/<dept>/budget/item/<id>     # Budget work item detail
/<event>/<dept>/contracts/           # Contracts (future release)
/<event>/<dept>/supply/              # Supply orders (future release)

/approvals/                          # Approver dashboard
/approvals/<group>/                  # Approval group queue

/admin/                              # Admin dashboard
/admin/config/departments/           # Department management
/admin/config/expense-accounts/      # Expense account management
/admin/final/                        # Final review dashboard
```

---

## Key Files

| File/Package | Purpose |
|--------------|---------|
| `app/models/` | Database models (package with domain-organized modules) |
| `app/services/` | Business logic (email, notifications) |
| `app/line_details.py` | Generic line detail helpers |
| `app/routing/registry.py` | Approval routing lookup |
| `app/routes/work/helpers/` | Context builders, permission checks, computations |
| `app/routes/work/work_items/` | Work item routes (create, view, edit, actions) |
| `app/routes/home.py` | Main dashboard |
| `app/seeds/config_seed.py` | Database seeding |
