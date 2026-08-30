# Contributing to the MAGFest Budget System

MAGFest is volunteer-run, and so is this application. Contributions are welcome.

This page answers three questions in order: how do I run it, what should I work on, and what gets my change accepted.

## Run it locally

Production and the Docker image pin Python 3.13 (`.python-version`, `Dockerfile`). Python 3.12.13 also runs the application and the full test suite; verified August 2026.

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

Visit `http://localhost:5000`. Dev login is enabled by `.env.example`, so no OAuth setup is needed. The local user switcher lets you act as a requester, a reviewer, or an admin.

`flask seed` takes a target: `bootstrap` for schema-required rows, `demo` for replaceable `[Demo]` departments and event cycle, `all` for both. `all` is the default. Seeding is insert-only and safe to re-run.

Seed through the `flask` CLI, not through `python -c`. `create_app()` refuses to start without `APP_ENV`, and only the `flask` CLI loads `.env`.

## What to work on

- [ROADMAP.md](ROADMAP.md) lists planned features and known issues
- For anything larger than a bug fix, open an issue first and agree on the approach

Before you change code, read [docs/architecture.md](docs/architecture.md) for how the workflow engine fits together and [docs/directory-structure.md](docs/directory-structure.md) for where things live. BUDGET is the complete work type and the reference implementation; TECHOPS and SUPPLY are partial, and the tables in [docs/work-types.md](docs/work-types.md) say what each one has.

## What gets a change accepted

### Branch and PR

- Branch from `master`, named for the change (`add-csv-export`, `fix-dispatch-redirect`)
- One logical change per pull request
- Say what changed and why in the description

A good PR solves one problem and carries no unrelated cleanup. It works on both SQLite and PostgreSQL, and leaves the approval workflow (draft, submit, review, finalize) working.

### Code style

- No linter is enforced. Match the surrounding code
- Name variables for what they hold. This is a workflow application; clarity beats brevity
- Follow the existing route, template, and model patterns

### Templates and frontend

- No inline event handlers (`onclick`, `onchange`). The application sends a Content Security Policy with nonces, and inline handlers are blocked
- Put `nonce="{{ csp_nonce }}"` on every `<script>` block
- Keep JavaScript in `<script>` blocks at the bottom of the template rather than in separate files. That is the current convention

### Tests

Run `pytest`. The suite uses in-memory SQLite, so no database setup is needed.

`git push` runs the full suite through a pre-push hook that `pre-commit install` set up. Budget about a minute; 612 tests took 57 seconds in August 2026. Activate the virtualenv before you push. Without it the hook fails with `Executable pytest not found` and the push is blocked.

New tests are appreciated for a new route or a change in business logic. They are not required on every PR.

## AI Tools

AI-assisted development tools (Copilot, Claude, etc.) are permitted. We expect contributors to **understand and own what they submit**. PRs should reflect thoughtful changes, not bulk-generated rewrites. If AI helped, that's fine. Just make sure the output makes sense for this codebase and you can explain what it does.
