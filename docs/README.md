# MAGFest Budget System Documentation

## Overview

This application manages budget requests for MAGFest events. It uses a **generic work type architecture** designed to eventually support multiple request types (contracts, supply orders) through the same workflow engine. Currently, only **Budget Requests** are live — contracts and supply orders are planned for future releases.

## Documentation Index

| Document | Description |
|----------|-------------|
| [Architecture Overview](./architecture.md) | High-level system design and key concepts |
| [Directory Structure](./directory-structure.md) | Where files live and why |
| [Work Types](./work-types.md) | How the multi-work-type system works |
| [Permissions](./permissions.md) | RBAC, memberships, and access control |
| [Workflow](./workflow.md) | Request lifecycle: draft → submit → review → finalize |
| [Security](./security.md) | CSP, inline scripts, audit logging |
| [Design Language](./design-language.md) | UI patterns, buttons, pills, spacing |
| [Scaling & Monitoring](./scaling-and-monitoring.md) | Infrastructure, connection pooling, capacity |

## Quick Reference

### Key Concepts

- **Work Type**: A category of request (Budget, Contracts, Supply Orders)
- **Portfolio**: A department's collection of requests for one work type in one event cycle
- **Work Item**: A single request (e.g., "TechOps Primary Budget 2027")
- **Work Line**: An individual line item within a request
- **Line Detail**: Type-specific data for a line (BudgetLineDetail, ContractLineDetail, etc.)

### Tech Stack

- **Backend**: Python 3.13, Flask 3.1, SQLAlchemy 2.0, Alembic
- **Database**: SQLite (dev), PostgreSQL (prod)
- **Templates**: Jinja2
- **Auth**: Keycloak SSO or Google OAuth (prod), Dev login (local)
- **Deployment**: Docker (GHCR), Gunicorn

## Getting Started

```bash
# Install dependencies (use dev requirements for local development)
pip install -r requirements-dev.txt

# Set up environment
cp .env.example .env

# Initialize database and run locally
flask db upgrade
flask run

# Run seeds (creates demo data, optional)
python -c "from app import create_app; from app.seeds.config_seed import run_all_seeds; app = create_app(); app.app_context().push(); run_all_seeds()"
```

Dev login is enabled by default — no OAuth setup needed for local development.