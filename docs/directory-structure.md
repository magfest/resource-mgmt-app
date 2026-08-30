# Directory Structure

This document explains where files live and the reasoning behind the organization.

## Top-Level Structure

```
magfest-budget/
├── app/                    # Main application code
│   ├── models/             # Database models (package)
│   ├── services/           # Business logic services (email, notifications, slack; see below)
│   ├── cli.py              # Flask CLI commands (seed, send-submission-reminders, drain-email-outbox, prune-email-audit)
│   ├── line_details.py     # Generic line detail helpers (see note below)
│   ├── secrets.py          # Env-var + optional AWS Secrets Manager loading
│   ├── security_audit.py   # Security/audit event logging helpers (see below)
│   ├── wsgi.py             # WSGI entry point (gunicorn: app.wsgi:app)
│   ├── routing/            # Approval routing strategies
│   ├── routes/             # Flask blueprints and route handlers
│   ├── seeds/              # Database seeding scripts
│   ├── static/             # Static assets (images)
│   └── templates/          # Jinja2 HTML templates
├── docs/                   # Documentation (you are here)
├── migrations/             # Alembic database migrations
├── tests/                  # pytest suite (unit/ + integration/)
├── Procfile                # Heroku process definition (web only)
└── requirements.txt        # Python dependencies
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
├── telemetry.py     # ActivityEvent, NotificationLog, SecurityAuditLog, EmailTemplate, SiteContent
└── email_outbox.py  # EmailOutbox, EmailSuppression, EmailMessageBody
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
| **telemetry** | ActivityEvent, NotificationLog, SecurityAuditLog, EmailTemplate, SiteContent | Logging, audit, and admin-editable content |
| **email_outbox** | EmailOutbox, EmailSuppression, EmailMessageBody | Outbound email queue and bounce suppression |

### `app/services/`

Business logic outside route handling. Email is split across six modules by
concern; the rest is one module each.

`email.py` no longer decides whether or when to send; it only builds and
hands one message to SES (`send_via_ses`, never raises) and writes the
`NotificationLog` row. Dedup, pacing, and retry moved to the outbox.
`email_drainer.py` backs both scheduled commands in `app/cli.py`:
`drain-email-outbox` (line 54) and `prune-email-audit` (line 76).

```
services/
├── email.py           # SES transport + NotificationLog writer. Sends via send_via_ses()
├── email_drainer.py   # Drives `drain-email-outbox` + `prune-email-audit` (cli.py:54,76)
├── email_enqueue.py   # Enqueue side: INSERTs email_outbox rows, does not send
├── email_errors.py    # Classifies botocore SES error codes into retry actions
├── email_health.py    # Read-only outbox health queries (operator page, support lookup)
├── email_templates.py # Database-backed email template loading and Jinja2 rendering
├── images.py          # Supply catalog images: validate, resize, strip EXIF, upload to S3
├── notifications.py   # Work-item lifecycle notifications; queues outbox rows, does not send
├── slack.py           # Slack channel notifications (debounce, circuit breaker)
└── slack_messages.py  # Slack Block Kit message formatters
```

See [Email outbox](./email-outbox.md) for how the queue, dedup, and retry
ladders behave. This section only says where the code lives.

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
├── admin/               # Admin config pages, plus the standalone security_logs blueprint
├── admin_final/         # Admin final review workflow + reports (BUDGET)
├── approvals/           # Approver workflow (shared across work types)
├── dispatch/            # Dispatch queue (BUDGET; only BUDGET reaches dispatch through a UI)
└── work/                # Requester workflow
    ├── __init__.py      # Blueprint setup (registers per-work-type packages)
    ├── department.py    # Department landing page
    ├── division.py      # Division landing page (all departments in a division)
    ├── portfolio.py     # Generic <work_type_slug> route (BUDGET in practice; see below)
    ├── lines.py         # Line item CRUD (BUDGET)
    ├── helpers/         # Shared across work types (package)
    │   ├── __init__.py  # Re-exports everything
    │   ├── context.py   # PortfolioContext, PortfolioPerms, WorkItemPerms
    │   ├── checkout.py  # Checkout/checkin functionality; see workflow.md#checkout-and-locking
    │   ├── lifecycle.py # Status transitions, auto-finalize
    │   ├── event_enablement.py  # Which divisions/departments participate in which events
    │   ├── review_state.py      # Reads the two review-stage records to say who acts next
    │   ├── expense_accounts.py  # Expense account queries
    │   ├── computations.py      # Totals, line status summaries
    │   └── formatting.py        # Status labels, currency, public IDs
    ├── techops/         # TECHOPS work type, its own package (the reference pattern)
    │   └── __init__, portfolio, create, edit, submit, view, admin, form_utils
    ├── supply/          # SUPPLY work type, its own package (admin.py has its own
    │   │                #   admin-final routes; see below)
    │   └── __init__, portfolio, catalog, order, submit, view, admin, form_utils
    └── work_items/      # Work item routes (BUDGET: create, view, edit, actions)
```

Several of these names read as shared engine code and are not. `work_items/`,
`lines.py`, `dispatch/`, and most of `admin_final/` are BUDGET's implementation;
they predate TECHOPS and SUPPLY and never got renamed. `portfolio.py` is a
generic `<work_type_slug>` route, but only BUDGET reaches it in practice. Flask
prefers the literal `/techops` and `/supply` segments, so other slugs fall
through to a coming-soon page. `techops/`, `supply/`, and `work/helpers/` are
the genuinely shared or per-work-type code. `admin_final/` is BUDGET's
admin-final implementation; SUPPLY runs its own admin-final stage in
`supply/admin.py` (`supply_admin_finalize_view`, `supply_admin_finalize`).

The blueprint is registered as `work` so URL generation uses `url_for('work.<route_name>')`.
Lifecycle, statuses, and checkout locking are documented in
[`workflow.md`](workflow.md#checkout-and-locking), not here.

### `app/templates/`

Jinja2 templates mirroring the route structure:

```
templates/
├── layout/
│   └── base.html        # Base template with CSS, nav, flash messages
├── components/          # Reusable partials (_top_nav.html, cards, banners)
├── macros/              # Shared Jinja macros (status_pill, comments, audit_log,
│                        #   checkout_banner); import `with context`
├── partials/            # Per-work-type "how it works" copy blocks
├── home.html            # Main dashboard
├── changelog.html       # Changelog page
├── auth/                # Login pages
├── dev/                 # Dev-only pages (login, impersonation, DB info)
├── admin/               # Admin config pages, including security_logs
├── admin_final/         # Admin review pages + reports (BUDGET)
├── approvals/           # Approver review pages (shared across work types)
├── dispatch/            # Dispatch queue pages (BUDGET)
├── budget/              # BUDGET work type pages
│   ├── coming_soon.html # Placeholder for unbuilt work types
│   ├── department_home.html
│   ├── portfolio_landing.html
│   ├── work_item_detail.html
│   └── ...
├── techops/             # TECHOPS work type pages, own tree (the reference pattern)
├── supply/              # SUPPLY work type pages, own tree
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

(An empty DB is also auto-migrated and seeded on first request; see
`run_seed_once()` in `app/__init__.py`.)

### `app/security_audit.py`

Logging helpers for security-relevant events: authentication, admin actions,
and access to sensitive data. `log_security_event()` and its per-event
convenience wrappers (`log_login_success`, `log_access_denied`, and similar)
add a `SecurityAuditLog` row to the session but do not commit; the caller
commits. `app/routes/admin/security_logs.py` renders these events for
SUPER_ADMIN review. This is distinct from `docs/security.md`, which covers CSP.

### `app/static/`

Static assets served directly by Flask: currently just `images/favicon.png`.

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
- If it's business logic: `app/services/` (see the module breakdown above)
- If it's cross-cutting: `app/` root (like `line_details.py`)
- If it's a template partial: `app/templates/components/`
