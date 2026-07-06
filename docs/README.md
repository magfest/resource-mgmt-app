# MAGFest Budget System Documentation

## Overview

This application manages work requests for MAGFest events through a shared workflow engine with per-work-type modules. **Budget Requests and TechOps Requests are live**; Supply Orders and AV Requests are in development, Contracts planned.

## Documentation Index

| Document | Description |
|----------|-------------|
| [Architecture Overview](./architecture.md) | High-level system design and key concepts |
| [Directory Structure](./directory-structure.md) | Where files live and why |
| [Work Types](./work-types.md) | How the multi-work-type system works |
| [Adding a Work Type](./adding-a-work-type.md) | The 10-step recipe (TECHOPS is the reference) |
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
- **Deployment**: Heroku (`Procfile`, `app.json`), Gunicorn; Heroku Scheduler for periodic CLI jobs

## Getting Started

```bash
# Install dependencies (use dev requirements for local development)
pip install -r requirements-dev.txt

# Set up environment
cp .env.example .env

# Initialize database and run locally
flask db upgrade
flask run

# Run seeds (bootstrap + demo data; also auto-runs on first request of an empty DB)
flask seed all
```

Dev login is enabled by default — no OAuth setup needed for local development.