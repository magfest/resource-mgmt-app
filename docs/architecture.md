# Architecture Overview

## The Big Picture

This system manages **work requests** for MAGFest events. A work request is something a department needs: budget items, tech services, warehouse supplies, AV equipment.

All request types share the same workflow engine:

```
DRAFT → [AWAITING_DISPATCH →] SUBMITTED (lines under review) → FINALIZED
```

The dispatch stage is per work type, controlled by `WorkTypeConfig.uses_dispatch`.
[Workflow: Stages by work type](./workflow.md#stages-by-work-type) lists the seeded
values.

The architecture is **shared chassis, per-type cabs**: the workflow engine (models,
lifecycle, routing, checkout, audit) is shared, and each work type has its own route
package (`app/routes/work/<type>/`) and template tree (`app/templates/<type>/`).
The original budget routes are BUDGET's cab; TECHOPS is the reference pattern for
new work types.

---

## Core Concepts

### Work Type

A **Work Type** defines a category of request. Each has its own:
- URL slug (`budget`, `contracts`, `supply`)
- Line detail model (what fields each line has)
- Routing strategy (how lines get assigned to approvers)
- Display labels ("Budget Lines" vs "Contract Items")

Current work types as of August 2026. [Work Types](./work-types.md) holds the
registry, the URL slugs, and the configuration flags.

| Work type | State | What exists |
| --- | --- | --- |
| BUDGET | Complete | The full pipeline: dispatch, admin final, reports, comments, supplementary requests. The reference implementation |
| SUPPLY | Partial | Ordering, catalog, and admin final. No warehouse, fulfillment, or returns |
| TECHOPS | Partial | Request entry and line review. No dispatch stage, no admin final. Deployed to production so the team can do test runs and give feedback, not for general use |
| CONTRACT | Model only | Data model and an admin configuration page. No route package, no templates |
| AV | Not built | Not present on this branch |

BUDGET is the only work type in general production use. The presence of a route
package does not mean a work type is in service.

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

Access comes from three layers: system roles on `UserRole`, department and division
memberships, and per-work-type access flags on each membership. A user with TechOps
budget access does not automatically see TechOps contracts.

[Permissions and Access Control](./permissions.md) is the authoritative copy of the
roles, the membership scoping, and the guard helpers.

---

## Request Lifecycle

A work item moves DRAFT, optionally AWAITING_DISPATCH, SUBMITTED, then FINALIZED,
with PAUSED for a supplementary blocked by a pending PRIMARY.

[Workflow: Work item transitions](./workflow.md#work-item-transitions) is the state
table, including who may make each move.
[Workflow: Statuses declared but never written](./workflow.md#statuses-declared-but-never-written)
covers `UNDER_REVIEW`, `UNAPPROVED`, and item-level `NEEDS_INFO`, none of which the
database holds today.

`FINALIZED` does not mean the department was told. BUDGET holds release until the
board approves the event topline; see [Workflow: Board release](./workflow.md#board-release).

---

## URL Structure

```
/                                    # Home dashboard
/<event>/<dept>/                     # Department landing (all work types)
/<event>/<dept>/budget/              # Budget portfolio
/<event>/<dept>/budget/item/<id>     # Budget work item detail
/<event>/<dept>/techops              # TechOps portfolio (app/routes/work/techops/portfolio.py:25)
/<event>/<dept>/supply               # Supply orders (app/routes/work/supply/portfolio.py:22)
/<event>/<dept>/contracts            # Contracts placeholder page (app/routes/work/portfolio.py:176)

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
| `app/cli.py` | Flask CLI commands (`flask seed`, `flask send-submission-reminders`, `flask drain-email-outbox`, `flask prune-email-audit`) |
