# Directory Structure

This document explains where files live and the reasoning behind the organization.

## Top-Level Structure

```
magfest-budget/
├── app/                    # Main application code
│   ├── models/             # Database models (package)
│   ├── services/           # Business logic services (email, notifications)
│   ├── line_details.py     # Generic line detail helpers (see note below)
│   ├── routing/            # Approval routing strategies
│   ├── routes/             # Flask blueprints and route handlers
│   ├── seeds/              # Database seeding scripts
│   └── templates/          # Jinja2 HTML templates
├── docs/                   # Documentation (you are here)
├── migrations/             # Alembic database migrations
├── requirements.txt        # Python dependencies
└── run.py                  # Flask app entry point
```

## Detailed Breakdown

### `app/models/`

SQLAlchemy models organized as a package for maintainability:

```
models/
├── __init__.py      # Re-exports everything for backwards compatibility
├── constants.py     # Status codes, role codes, visibility modes
├── org.py           # EventCycle, Division, Department, User, Memberships
├── workflow.py      # WorkType, ApprovalGroup, WorkItem, WorkLine, Reviews
├── budget.py        # SpendType, ExpenseAccount, BudgetLineDetail
├── contract.py      # ContractType, ContractLineDetail
├── supply.py        # SupplyCategory, SupplyItem, SupplyOrderLineDetail
└── telemetry.py     # ActivityEvent, NotificationLog, SecurityAuditLog
```

**Import pattern**: Use `from app.models import WorkItem` - the `__init__.py` re-exports all models.

| Module | Models | Purpose |
|--------|--------|---------|
| **constants** | (constants only) | All status codes, role codes, visibility modes |
| **org** | User, EventCycle, Division, Department, Memberships | Users and org structure |
| **workflow** | WorkType, WorkPortfolio, WorkItem, WorkLine, Reviews | The workflow engine |
| **budget** | SpendType, ExpenseAccount, BudgetLineDetail | Budget-specific data |
| **contract** | ContractType, ContractLineDetail | Contract-specific data |
| **supply** | SupplyCategory, SupplyItem, SupplyOrderLineDetail | Supply-specific data |
| **telemetry** | ActivityEvent, NotificationLog, AuditLogs | Logging and audit |

### `app/routing/`

Pluggable approval routing strategies:

```
routing/
├── __init__.py          # Base RoutingStrategy interface
├── budget.py            # Routes via ExpenseAccount
├── contracts.py         # Routes via ContractType
├── supply_orders.py     # Routes via SupplyCategory
└── registry.py          # Strategy lookup
```

Each work type can route to approval groups differently. Budget routes based on expense account, contracts route based on contract type, etc.

### `app/routes/`

Flask blueprints organized by functional area:

```
routes/
├── __init__.py          # Route helpers, render_page(), get_user_ctx()
├── home.py              # Main dashboard
├── auth.py              # Login/logout
├── dev.py               # Dev-only routes (impersonation, etc.)
├── admin/               # Admin config pages (departments, users, etc.)
├── admin_final/         # Admin final review workflow
├── approvals/           # Approver workflow
└── work/                # Requester workflow (all work types)
    ├── __init__.py      # Blueprint setup
    ├── department.py    # Department landing page
    ├── division.py      # Division landing page (all departments in a division)
    ├── portfolio.py     # Portfolio landing, placeholder routes
    ├── lines.py         # Line item CRUD
    ├── helpers/         # Helper functions (package)
    │   ├── __init__.py  # Re-exports everything
    │   ├── context.py   # PortfolioContext, PortfolioPerms, WorkItemPerms
    │   ├── checkout.py  # Checkout/checkin functionality
    │   ├── expense_accounts.py  # Expense account queries
    │   ├── computations.py      # Totals, line status summaries
    │   └── formatting.py        # Status labels, currency, utilities
    └── work_items/      # Work item routes (package)
        ├── __init__.py  # Registers all routes
        ├── common.py    # Shared helpers
        ├── create.py    # PRIMARY/SUPPLEMENTARY creation
        ├── view.py      # Detail view, comments, quick review
        ├── edit.py      # Edit form, fixed costs, hotel wizard
        └── actions.py   # Submit, checkout, checkin, needs_info
```

The `work/` folder handles ALL work types via the generic system. The URL structure is:
- `/<event>/<dept>/budget/` → Budget requests
- `/<event>/<dept>/contracts/` → Contract requests (future release)
- `/<event>/<dept>/supply/` → Supply requests (future release)

The blueprint is registered as `work` so URL generation uses `url_for('work.<route_name>')`.

### `app/templates/`

Jinja2 templates mirroring the route structure:

```
templates/
├── layout/
│   └── base.html        # Base template with CSS, nav, flash messages
├── components/          # Reusable partials
├── home.html            # Main dashboard
├── auth/                # Login pages
├── admin/               # Admin config pages
├── admin_final/         # Admin review pages
├── budget/              # Requester workflow pages
│   ├── coming_soon.html # Placeholder for unbuilt work types
│   ├── department_home.html
│   ├── portfolio_landing.html
│   ├── work_item_detail.html
│   └── ...
└── errors/              # Error pages
```

### `app/seeds/`

Database seeding for development and initial setup:

```
seeds/
└── config_seed.py       # Creates work types, expense accounts, demo users, etc.
```

Run with:
```bash
python -c "from app import create_app; from app.seeds.config_seed import run_all_seeds; app = create_app(); app.app_context().push(); run_all_seeds()"
```

---

## Naming Conventions

| Convention | Example | Notes |
|------------|---------|-------|
| Models | `WorkItem`, `BudgetLineDetail` | PascalCase |
| Tables | `work_items`, `budget_line_details` | snake_case, plural |
| Routes | `work.portfolio_landing` | blueprint.function_name |
| Templates | `budget/portfolio_landing.html` | Mirrors route structure |
| URL slugs | `/budget/`, `/contracts/` | Lowercase, from WorkTypeConfig |

---

## Common Questions

### "Where do I add a new model?"

In the appropriate module under `app/models/`. Choose by domain:
- Org/user related: `org.py`
- Workflow related: `workflow.py`
- Budget specific: `budget.py`
- Contract specific: `contract.py`
- Supply specific: `supply.py`
- Logging/telemetry: `telemetry.py`

Then add it to `__init__.py` exports for backwards compatibility.

### "Where do I add a new route?"

In the appropriate blueprint under `app/routes/`. If it's a new functional area, create a new blueprint.

### "Where do I add shared utilities?"

- If it's route-specific: `app/routes/<area>/helpers/` (as a module in the package)
- If it's business logic: `app/services/` (email, notifications, etc.)
- If it's cross-cutting: `app/` root (like `line_details.py`)
- If it's a template partial: `app/templates/components/`
