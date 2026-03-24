# MAGFest Budget System

A Flask-based budget request and approval system for MAGFest events.

## Features

- **Multi-work-type support**: Budget requests, contracts, and supply orders (extensible)
- **Role-based access control**: Department members, department heads, approvers, finance, super-admins
- **Approval workflow**: Submit → Review → Approve/Reject → Finalize
- **Multi-event support**: Manage budgets across different MAGFest events (Super, West, Stock, etc.)

## Quick Start

### Prerequisites

- Python 3.11+
- pip

### Local Development

```bash
# Clone and setup
git clone <repo-url>
cd magfest-budget

# Create virtual environment
python -m venv .venv
.venv\Scripts\activate  # Windows
# source .venv/bin/activate  # macOS/Linux

# Install dependencies
pip install -r requirements.txt
pre-commit install #allow for pip-aduit before any commits

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

### Authentication

- **Development**: Dev login enabled by default (user switcher)
- **Production**: Keycloak SSO or Google OAuth

See `.env.example` for configuration options.

## Documentation

Detailed documentation is in the [`docs/`](docs/) folder:

- [Architecture Overview](docs/architecture.md)
- [Directory Structure](docs/directory-structure.md)
- [Work Types](docs/work-types.md)
- [Permissions & RBAC](docs/permissions.md)
- [Workflow](docs/workflow.md)

## Project Structure

```
magfest-budget/
├── app/                    # Flask application
│   ├── models.py           # Database models
│   ├── routes/             # Route blueprints
│   │   ├── admin/          # Admin configuration
│   │   ├── admin_final/    # Final review workflow
│   │   ├── approvals/      # Approver workflow
│   │   └── work/           # Requester workflow
│   ├── routing/            # Approval routing strategies
│   ├── seeds/              # Database seeding
│   └── templates/          # Jinja2 templates
├── docs/                   # Documentation
├── migrations/             # Alembic migrations
└── requirements.txt        # Python dependencies
```

## Tech Stack

- **Backend**: Flask, SQLAlchemy, Flask-Migrate
- **Database**: PostgreSQL (production), SQLite (development)
- **Auth**: Keycloak/Google OAuth via authlib
- **Deployment**: AWS AppRunner

## Security

### Dependency Management

Dependencies are managed with [pip-tools](https://pip-tools.readthedocs.io/):

- `requirements.in` — direct production dependencies
- `requirements-dev.in` — dev/test dependencies (inherits from `requirements.in`)
- `requirements.txt` / `requirements-dev.txt` — compiled lockfiles with pinned versions

To update dependencies:

```bash
pip-compile --generate-hashes requirements.in -o requirements.txt --upgrade
pip-compile --generate-hashes requirements-dev.in -o requirements-dev.txt --upgrade
```

Always compile both files together to keep versions in sync.

### Automated Vulnerability Scanning

- **Pre-commit hook**: [pip-audit](https://github.com/trailofbits/pip-audit) runs before every commit via the [pre-commit](https://pre-commit.com/) framework, blocking commits with known CVEs.
- **CI**: A GitHub Actions workflow (`.github/workflows/security.yml`) runs `pip-audit` on every push and PR to `master`.
- **Dependabot**: GitHub Dependabot alerts and security updates are enabled to notify of newly disclosed vulnerabilities.

### Setup for new contributors

After cloning and installing dependencies, install the pre-commit hooks:

```bash
pip install -r requirements-dev.txt
pre-commit install
```

For more detail, see [SECURITY_ROADMAP.md](SECURITY_ROADMAP.md).

## Contributing

1. Create a feature branch
2. Make changes
3. Test locally
4. Submit PR for review

## License

Internal MAGFest project.
