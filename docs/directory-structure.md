# Directory Structure

This document explains where files live and the reasoning behind the organization.

## Top-Level Structure

```
magfest-budget/
├── app/                    # Main application code
│   ├── models.py           # All database models
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

### `app/models.py`

All SQLAlchemy models in one file. Key model groups:

| Group | Models | Purpose |
|-------|--------|---------|
| **Core** | User, EventCycle | Users and event cycles |
| **Organization** | Division, Department, Memberships | Org structure and access |
| **Work Types** | WorkType, WorkTypeConfig | Define request types |
| **Requests** | WorkPortfolio, WorkItem, WorkLine | The actual requests |
| **Line Details** | BudgetLineDetail, ContractLineDetail, SupplyOrderLineDetail | Type-specific line data |
| **Reviews** | WorkLineReview, ApprovalGroup | Review workflow |
| **Reference Data** | ExpenseAccount, SpendType, ContractType, SupplyCategory | Lookup tables |

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
    ├── portfolio.py     # Portfolio landing, placeholder routes
    ├── work_items.py    # Work item CRUD
    ├── lines.py         # Line item CRUD
    └── helpers.py       # Context builders, permission checks, status computation
```

The `work/` folder handles ALL work types via the generic system. The URL structure is:
- `/<event>/<dept>/budget/` → Budget requests
- `/<event>/<dept>/contracts/` → Contract requests (placeholder for now)
- `/<event>/<dept>/supply/` → Supply requests (placeholder for now)

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

In `app/models.py`. Keep related models grouped together.

### "Where do I add a new route?"

In the appropriate blueprint under `app/routes/`. If it's a new functional area, create a new blueprint.

### "Where do I add shared utilities?"

- If it's route-specific: `app/routes/<area>/helpers.py`
- If it's cross-cutting: `app/` root (like `line_details.py`)
- If it's a template partial: `app/templates/components/`
