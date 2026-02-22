# MAGFest Budget System Documentation

## Overview

This application manages budget requests, contracts, and supply orders for MAGFest events. It uses a **generic work type architecture** that allows multiple request types (Budget, Contracts, Supply Orders) to share the same workflow engine.

## Documentation Index

| Document | Description |
|----------|-------------|
| [Architecture Overview](./architecture.md) | High-level system design and key concepts |
| [Directory Structure](./directory-structure.md) | Where files live and why |
| [Work Types](./work-types.md) | How the multi-work-type system works |
| [Permissions](./permissions.md) | RBAC, memberships, and access control |
| [Workflow](./workflow.md) | Request lifecycle: draft → submit → review → finalize |

## Quick Reference

### Key Concepts

- **Work Type**: A category of request (Budget, Contracts, Supply Orders)
- **Portfolio**: A department's collection of requests for one work type in one event cycle
- **Work Item**: A single request (e.g., "TechOps Primary Budget 2027")
- **Work Line**: An individual line item within a request
- **Line Detail**: Type-specific data for a line (BudgetLineDetail, ContractLineDetail, etc.)

### Tech Stack

- **Backend**: Python 3.11+, Flask, SQLAlchemy
- **Database**: SQLite (dev), PostgreSQL (prod)
- **Templates**: Jinja2
- **Auth**: Google OAuth (prod), Dev login (local)

## Getting Started

```bash
# Install dependencies
pip install -r requirements.txt

# Run locally
flask run

# Run seeds (creates demo data)
python -c "from app import create_app; from app.seeds.config_seed import run_all_seeds; app = create_app(); app.app_context().push(); run_all_seeds()"
```

## Questions?

- **Budget questions**: biz@magfest.org
- **Contracts questions**: biz@magfest.org
- **Supply/Warehouse questions**: festops@magfest.org
- **Technical issues**: File an issue or contact the dev team
