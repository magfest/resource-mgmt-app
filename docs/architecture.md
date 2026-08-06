# Architecture Overview

## The Big Picture

This system manages **work requests** for MAGFest events. A work request is something a department needs: budget items, tech services, warehouse supplies, AV equipment.

All request types share the same workflow engine:

```
DRAFT → [AWAITING_DISPATCH →] SUBMITTED (lines under review) → FINALIZED
```

(The dispatch stage is per-work-type: BUDGET uses it, TECHOPS doesn't — controlled
by `WorkTypeConfig.uses_dispatch`.)

The architecture is **shared chassis, per-type cabs**: the workflow engine (models,
lifecycle, routing, checkout, audit) is shared, and each work type has its own route
package (`app/routes/work/<type>/`) and template tree (`app/templates/<type>/`).
The original budget routes are BUDGET's cab; TECHOPS is the reference pattern for
new work types. See `docs/adding-a-work-type.md`.

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
| TECHOPS | TechOps Requests | **Live** |
| SUPPLY | Supply Orders | In development (models + admin pages in master) |
| AV | AV Requests | In development (feature branch) |
| CONTRACT | Contracts | Future (data model exists, no UI) |

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

Work item statuses (actual constants in `app/models/constants.py`):

```
           [submit]              [dispatch]                [finalize]
┌─────────┐      ┌──────────────────┐      ┌───────────┐      ┌───────────┐
│  DRAFT  │─────▶│ AWAITING_DISPATCH│─────▶│ SUBMITTED │─────▶│ FINALIZED │
└─────────┘      └──────────────────┘      └───────────┘      └───────────┘
                  (only if the work         (lines under
                   type uses_dispatch;       approval group /
                   otherwise submit          admin final review)
                   goes straight to
                   SUBMITTED)
```

- **DRAFT**: Requester is building the request
- **AWAITING_DISPATCH**: Submitted; admin assigns approval groups (BUDGET only)
- **SUBMITTED**: Lines under review by approval groups (and admin final, if the
  work type has that stage)
- **FINALIZED**: Locked; amounts confirmed. Work types without an admin-final
  stage auto-finalize when the last line is decided.
- **NEEDS_INFO**: Awaiting requester response (item-level; see below)
- **PAUSED**: Supplementary blocked by a pending PRIMARY

`FINALIZED` does not mean the department was told. BUDGET holds release until
the board approves the event topline; see [Workflow: Statuses](./workflow.md#work-item-statuses)
for `board_released_at` and the derived `PENDING_BOARD_APPROVAL` status.

Declared in `app/models/constants.py:18-25` but never persisted:

- **UNDER_REVIEW**: display-only, derived in `compute_line_status_summary()`
  (`app/routes/work/helpers/computations.py`)
- **UNAPPROVED**: vestigial — nothing reads or writes it. Unfinalize resets to
  SUBMITTED (`app/routes/admin_final/helpers.py:634`), not UNAPPROVED.

`NEEDS_ADJUSTMENT` is **line-level only**. `NEEDS_INFO` exists at **both** levels —
`app/routes/work/work_items/actions.py:321` sets it on the work item — see
`docs/workflow.md`.

---

## URL Structure

```
/                                    # Home dashboard
/<event>/<dept>/                     # Department landing (all work types)
/<event>/<dept>/budget/              # Budget portfolio
/<event>/<dept>/budget/item/<id>     # Budget work item detail
/<event>/<dept>/techops/             # TechOps portfolio (live)
/<event>/<dept>/supply               # Supply orders (live — app/routes/work/supply/portfolio.py:22)
/<event>/<dept>/contracts            # Contracts (still a placeholder — app/routes/work/portfolio.py:176)

/approvals/                          # Approver dashboard
/approvals/<group_code>              # Approval group queue (no trailing slash)

/admin/dispatch/                     # Dispatch queue (BUDGET)
/admin/                              # Admin dashboard
/admin/config/departments/           # Department management
/admin/config/expense-accounts/      # Expense account management
/admin/final-review/                 # Final review dashboard + reports
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
| `app/routes/work/techops/` | TECHOPS work type (reference pattern for new types) |
| `app/routes/home.py` | Main dashboard |
| `app/seeds/bootstrap.py` | Database seeding (`config_seed.py` is a wrapper) |
| `app/cli.py` | Flask CLI commands (`flask seed`, `flask send-submission-reminders`, `flask send-board-release-emails`) |
