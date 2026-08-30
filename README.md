# MAGFest Budget System

A request and approval workflow application for [MAGFest](https://www.magfest.org/) events, built with Flask. It is in production for budget requests.

MAGFest is a volunteer-run nonprofit that produces several events a year. Each event has dozens of departments (TechOps, Panels, Hotels, and others) that submit budgets and need them approved. This application replaces the spreadsheets and email chains that used to carry that work, with routed reviews, role-based access, and an audit trail.

## Who uses it

| Role | Does |
| --- | --- |
| Requester | Department volunteer. Creates a request with line items, hotel needs, badge counts, and notes |
| Approval group | Subject matter experts. Review the lines routed to their group and recommend a decision. The interface labels these Reviewer Groups |
| Budget admin | Sets the approved amounts, resolves recommendations, finalizes the request |
| Super admin | Configures events, divisions, departments, users, and reference data |

Requests move through Draft, Submitted, dispatch to approval groups (only BUDGET has a UI for this stage), approval group review, admin final review, and Finalized. [Workflow](docs/workflow.md) has the full state table.

## Work types

One workflow engine (portfolios, work items, lines, staged reviews) carries every request type. Each work type adds its own route package and template tree on top.

| Work type | State | What exists |
| --- | --- | --- |
| BUDGET | Complete | The full pipeline: dispatch, admin final, reports, comments, supplementary requests. The reference implementation |
| SUPPLY | Partial | Ordering, catalog, and admin final. No warehouse, fulfillment, or returns |
| TECHOPS | Partial | Request entry and line review. No dispatch stage, no admin final. Deployed to production so the team can do test runs and give feedback, not for general use |
| CONTRACT | Model only | Data model and an admin configuration page. No route package, no templates |
| AV | Not built | Not present on this branch |

BUDGET is the only work type in general production use. [Work Types](docs/work-types.md) holds the registry, the per-work-type configuration, and what differs between them.

## Tech stack

| Layer | Choice |
| --- | --- |
| Backend | Python, Flask 3.1, SQLAlchemy 2.0, Alembic |
| Database | PostgreSQL in production, SQLite in development |
| Auth | Keycloak SSO or Google OAuth via Authlib, plus a dev login mode |
| Deployment | Heroku with the Python buildpack, Gunicorn (`Procfile`, `app.json`); Heroku Scheduler runs the periodic `flask` commands listed in [docs/README.md](docs/README.md) |

## Run it locally

Production and the Docker image pin Python 3.13 (`.python-version`, `Dockerfile`). Python 3.12.13 also runs the application and the full test suite; verified August 2026. You also need pip.

```bash
git clone https://github.com/magfest/Resource-mgmt-app.git
cd Resource-mgmt-app

python3 -m venv .venv
source .venv/bin/activate
# Windows instead: .venv\Scripts\activate

pip install -r requirements-dev.txt
pre-commit install

cp .env.example .env

flask db upgrade
flask seed
flask run
```

Visit `http://localhost:5000`. Dev login is enabled by `.env.example`, so no OAuth setup is needed.

### Seeding

`flask seed [target]` is the manual seed command (`app/cli.py`). The default target is `all`.

| Target | Inserts |
| --- | --- |
| `bootstrap` | Schema-required rows: work types, approval groups, reference data, hotel expense accounts |
| `demo` | Replaceable `[Demo]` org content: departments, an event cycle, divisions, parking accounts |
| `all` | Both |

Seeding is insert-only and safe to re-run.

Seed through the `flask` CLI, not through `python -c`. `create_app()` refuses to start without `APP_ENV` (`app/__init__.py`), and only the `flask` CLI loads `.env`.

### git push runs the test suite

`pre-commit install` also installs a pre-push hook that runs the whole pytest suite (`.pre-commit-config.yaml`). Budget about a minute; 612 tests took 57 seconds in August 2026. Activate the virtualenv before you push. Without it the hook fails with `Executable pytest not found` and the push is blocked.

### Authentication

- Development: `DEV_LOGIN_ENABLED=true` gives a local user switcher, no OAuth needed
- Production: Keycloak SSO or Google OAuth

`.env.example` lists every configuration option with its default.

### Docker

The `Dockerfile` is not how this application is deployed. It builds a standalone Gunicorn image on port 8000, useful for a container check; deployment is the Heroku buildpack. The workflow that would publish that image to GHCR is a parked draft at `.github/workflows-drafts/docker-build.yml` and is not enabled.

```bash
docker build -t magfest-budget .
docker run -p 8000:8000 -e APP_ENV=development -e DATABASE_URL=sqlite:///budget.db magfest-budget
```

## Documentation

[`docs/`](docs/) is the index. Start there for [Architecture](docs/architecture.md), [Directory Structure](docs/directory-structure.md), [Work Types](docs/work-types.md), [Permissions](docs/permissions.md), and [Workflow](docs/workflow.md).

[ROADMAP.md](ROADMAP.md) is what is planned. [CONTRIBUTING.md](CONTRIBUTING.md) is how to work on it.

## Security

Dependencies are pinned with [pip-tools](https://pip-tools.readthedocs.io/): `requirements.in` and `requirements-dev.in` hold the direct dependencies, `requirements.txt` and `requirements-dev.txt` are the compiled lockfiles.

```bash
pip-compile --generate-hashes requirements.in -o requirements.txt --upgrade
pip-compile --generate-hashes requirements-dev.in -o requirements-dev.txt --upgrade
```

| Check | Where it runs |
| --- | --- |
| pip-audit against `requirements.txt` | pre-commit hook, and GitHub Actions on push and PR to `master` |
| bandit against `app/` | GitHub Actions on push and PR to `master` |
| pytest, full suite | pre-push hook, and GitHub Actions on push and PR to `master` |
| Dependabot weekly pip version updates | `.github/dependabot.yml` |

To report a vulnerability, see [SECURITY.md](SECURITY.md).

## Tools and disclosures

This project was developed with assistance from AI coding tools (Claude Code, JetBrains AI, Gemini) and writing tools (Grammarly).

## License

[GNU Affero General Public License v3.0](LICENSE) (AGPL-3.0).
