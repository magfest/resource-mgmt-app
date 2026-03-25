# MAGFest Budget System

> **Status: Active Development** — This application is functional and in use, but under active development. Features are still being added, UX is being refined, and some areas (contracts, supply orders, reporting) are incomplete. See the [Roadmap](ROADMAP.md) for what's planned and in progress.

A budget request and approval workflow application for [MAGFest](https://www.magfest.org/) events. Built with Flask, it currently handles multi-departmental/event budget requests. Long term the goal is to support contracts and supply orders through a configurable review and approval pipeline.

## What It Does

MAGFest is a volunteer-run nonprofit that produces multiple events each year. Each event has dozens of departments (Tech Ops, Panels, Hotels, etc.) that need to submit and get approval for their budgets. This system replaces spreadsheets and email chains with a structured workflow:

- **Requesters** (department volunteers) create budget requests with line items, hotel needs, badge counts, and notes
- **Reviewer groups** (subject matter experts) review and recommend approval on routed line items
- **Budget admins** make final approval decisions and finalize requests
- The system tracks everything with audit trails, role-based access, and status notifications

## Features

- **Multi-work-type support**: Budget requests, contracts, and supply orders (extensible)
- **Role-based access control**: Department members, division heads, reviewer groups, budget admins, super admins
- **Approval workflow**: Draft → Submit → Reviewer Group Review → Admin Final Review → Finalized
- **Multi-event support**: Manage budgets across different MAGFest events (Super, West, Stock, etc.)
- **Income tracking**: Departments that generate revenue can record estimated income for reference
- **Automated security scanning**: pip-audit in CI and pre-commit hooks, Dependabot alerts

## Tech Stack

- **Backend**: Python 3.13, Flask 3.1, SQLAlchemy 2.0, Alembic
- **Database**: PostgreSQL (production), SQLite (development)
- **Auth**: Keycloak SSO or Google OAuth via Authlib, with dev login mode
- **Deployment**: Docker (GHCR), targeting Kubernetes
- **CI**: GitHub Actions (security audit)

## Quick Start

### Prerequisites

- Python 3.13+
- pip

### Local Development

```bash
# Clone and setup
git clone https://github.com/magfest/Resource-mgmt-app.git
cd Resource-mgmt-app

# Create virtual environment
python -m venv .venv
.venv\Scripts\activate  # Windows
# source .venv/bin/activate  # macOS/Linux

# Install dependencies
pip install -r requirements-dev.txt
pre-commit install

# Copy environment template
cp .env.example .env

# Initialize database
flask db upgrade

# Seed reference data (optional)
python -c "from app import create_app; from app.seeds.config_seed import run_all_seeds; app = create_app(); app.app_context().push(); run_all_seeds()"

# Run development server
flask run
```

Visit `http://localhost:5000`

### Docker

```bash
docker build -t magfest-budget .
docker run -p 8000:8000 -e DATABASE_URL=sqlite:///budget.db magfest-budget
```

### Authentication

- **Development**: Set `DEV_LOGIN_ENABLED=true` for a local user switcher (no OAuth needed)
- **Production**: Keycloak SSO or Google OAuth

See `.env.example` for all configuration options.

## Project Structure

```
app/
├── models/          # Database models (org, workflow, budget, contract, supply)
├── routes/
│   ├── admin/       # System configuration (super admin)
│   ├── admin_final/ # Final review, reports, dashboards
│   ├── approvals/   # Reviewer group workflow
│   ├── dispatch/    # Reviewer assignment queue
│   └── work/        # Requester workflow (create, edit, submit)
├── routing/         # Pluggable approval routing strategies
├── services/        # Email (AWS SES), notifications
└── templates/       # Jinja2 templates
```

## Documentation

Detailed documentation is in the [`docs/`](docs/) folder:

- [Architecture Overview](docs/architecture.md)
- [Directory Structure](docs/directory-structure.md)
- [Work Types](docs/work-types.md)
- [Permissions & RBAC](docs/permissions.md)
- [Workflow](docs/workflow.md)

## Security

### Dependency Management

Dependencies are pinned with [pip-tools](https://pip-tools.readthedocs.io/) and audited for known vulnerabilities:

- `requirements.in` / `requirements-dev.in` — direct dependencies
- `requirements.txt` / `requirements-dev.txt` — compiled lockfiles with pinned versions

To update:

```bash
pip-compile --generate-hashes requirements.in -o requirements.txt --upgrade
pip-compile --generate-hashes requirements-dev.in -o requirements-dev.txt --upgrade
```

### Automated Scanning

- **Pre-commit**: [pip-audit](https://github.com/trailofbits/pip-audit) blocks commits with known CVEs
- **CI**: GitHub Actions runs pip-audit on every push and PR to `master`
- **Dependabot**: Alerts and automatic security PRs enabled

See the [Security & Infrastructure](ROADMAP.md#security--infrastructure) section of the Roadmap for planned improvements. To report a vulnerability, see [SECURITY.md](SECURITY.md).

### Tools / Disclosures
This project was developed with assistance from AI tools for both code and wording.
For example:
1. Claude Code, JetBrain "AI" Tools, and Gemini
2. Grammerly was used for wordsmiting, and grammer..

## Contributing

We welcome contributions! See [CONTRIBUTING.md](CONTRIBUTING.md) for setup instructions, code style, and PR guidelines.

Quick start:
1. Fork the repo and create a feature branch
2. `pip install -r requirements-dev.txt` and `pre-commit install`
3. `cp .env.example .env` — dev login works out of the box, no OAuth setup needed
4. `flask db upgrade` and `flask run`
5. Make changes, run `pytest`, and submit a PR

AI tools are permitted — see the contributing guide for expectations.

## License

This project is licensed under the [GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0).

