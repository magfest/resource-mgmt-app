# Directory Structure

This document explains where files live and the reasoning behind the organization.

## Top-Level Structure

```
magfest-budget/
├── app/                    # Main application code
│   ├── models/             # Database models (package)
│   ├── services/           # Business logic services (email, notifications, slack)
│   ├── cli.py              # Flask CLI commands (seed, send-submission-reminders)
│   ├── line_details.py     # Generic line detail helpers (see note below)
│   ├── secrets.py          # Env-var + optional AWS Secrets Manager loading
│   ├── routing/            # Approval routing strategies
│   ├── routes/             # Flask blueprints and route handlers
│   ├── seeds/              # Database seeding scripts
│   └── templates/          # Jinja2 HTML templates
├── docs/                   # Documentation (you are here)
├── migrations/             # Alembic database migrations
├── tests/                  # pytest suite (unit/ + integration/)
├── Procfile                # Heroku process definition (web only)
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
├── techops.py       # TechOpsServiceType, TechOpsLineDetail, TechOpsRequestDetail
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
├── category.py          # Category routing (TECHOPS service types; SUPPLY categories)
└── registry.py          # Strategy lookup + cross-work-type guard
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
├── admin/               # Admin config pages (departments, users, supply catalog, etc.)
├── admin_final/         # Admin final review workflow + reports (BUDGET)
├── approvals/           # Approver workflow (shared across work types)
├── dispatch/            # Dispatch queue (BUDGET)
└── work/                # Requester workflow
    ├── __init__.py      # Blueprint setup (registers per-work-type packages)
    ├── department.py    # Department landing page
    ├── division.py      # Division landing page (all departments in a division)
    ├── portfolio.py     # Portfolio landing (BUDGET) + coming-soon fallback for unbuilt slugs
    ├── lines.py         # Line item CRUD (BUDGET)
    ├── helpers/         # Helper functions (package)
    │   ├── __init__.py  # Re-exports everything
    │   ├── context.py   # PortfolioContext, PortfolioPerms, WorkItemPerms
    │   ├── checkout.py  # Checkout/checkin functionality
    │   ├── lifecycle.py # Status transitions, auto-finalize
    │   ├── expense_accounts.py  # Expense account queries
    │   ├── computations.py      # Totals, line status summaries
    │   └── formatting.py        # Status labels, currency, public IDs
    ├── techops/         # TECHOPS work type (own package — the reference pattern)
    │   └── __init__, portfolio, create, edit, submit, view, admin, form_utils
    └── work_items/      # Work item routes (BUDGET: create, view, edit, actions)
```

**Work-type pattern**: each work type has its own package under `work/` with literal
URL segments (`/techops/`), which Flask prefers over the generic `<work_type_slug>`
fallback in `portfolio.py` (that fallback renders a coming-soon page for unbuilt
types). The older top-level modules (`portfolio.py`, `lines.py`, `work_items/`) are
BUDGET's implementation. See `docs/adding-a-work-type.md`.

The blueprint is registered as `work` so URL generation uses `url_for('work.<route_name>')`.

### `app/templates/`

Jinja2 templates mirroring the route structure:

```
templates/
├── layout/
│   └── base.html        # Base template with CSS, nav, flash messages
├── components/          # Reusable partials (_top_nav.html, cards, banners)
├── macros/              # Shared Jinja macros (status_pill, comments, audit_log,
│                        #   checkout_banner) — import `with context`
├── home.html            # Main dashboard
├── auth/                # Login pages
├── admin/               # Admin config pages
├── admin_final/         # Admin review pages + reports
├── dispatch/            # Dispatch queue pages
├── budget/              # BUDGET work type pages
│   ├── coming_soon.html # Placeholder for unbuilt work types
│   ├── department_home.html
│   ├── portfolio_landing.html
│   ├── work_item_detail.html
│   └── ...
├── techops/             # TECHOPS work type pages (own tree — the pattern)
└── errors/              # Error pages
```

Each work type gets its own template tree; shared pieces live in `macros/` and
`components/`.

### `app/seeds/`

Database seeding for development and initial setup:

```
seeds/
├── bootstrap.py         # Canonical seed: work types, approval groups, configs, catalogs
├── config_seed.py       # Backwards-compatible wrapper around bootstrap.py
├── demo_data.py         # Operator-replaceable [Demo] org content
└── demo_users.py        # Demo user accounts
```

Run with:
```bash
flask seed all           # see app/cli.py for targets
```

(An empty DB is also auto-migrated and seeded on first request — see
`run_seed_once()` in `app/__init__.py`.)

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
