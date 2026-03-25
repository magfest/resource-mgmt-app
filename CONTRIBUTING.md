# Contributing to the MAGFest Budget System

Thanks for your interest in contributing! MAGFest is volunteer-run, and this project is no different — every contribution helps.

## Getting Started

1. Fork the repo and clone it locally
2. Create a virtual environment and install dev dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # or .venv\Scripts\activate on Windows
   pip install -r requirements-dev.txt
   ```
3. Install pre-commit hooks:
   ```bash
   pre-commit install
   ```
4. Copy the environment file:
   ```bash
   cp .env.example .env
   ```
5. Set up the database and (optionally) seed demo data:
   ```bash
   flask db upgrade
   python -c "from app import create_app; from app.seeds.config_seed import run_all_seeds; app = create_app(); app.app_context().push(); run_all_seeds()"
   ```
6. Run the app:
   ```bash
   flask run
   ```
   Dev login is enabled by default — no OAuth setup needed.

## Making Changes

### Branch and PR workflow

- Create a feature branch from `master` (e.g., `add-csv-export`, `fix-dispatch-redirect`)
- Keep PRs focused — one logical change per PR
- Write a clear PR description explaining **what** you changed and **why**

### Code style

- No strict linter enforced yet, but try to match the style of surrounding code
- Use meaningful variable names — this is a workflow app, clarity matters more than brevity
- Follow existing patterns for routes, templates, and models (see `CLAUDE.md` for architecture reference)

### Templates and frontend

- No inline event handlers (`onclick`, `onchange`, etc.) — we use Content Security Policy with nonces
- Use `nonce="{{ csp_nonce }}"` on all `<script>` blocks
- Keep JavaScript in `<script>` blocks at the bottom of templates, not in separate files (current convention)

### Testing

- Run tests before submitting: `pytest`
- If you're adding a new route or changing business logic, adding tests is appreciated but not required for every PR
- The test suite uses SQLite in-memory — no database setup needed

### What makes a good PR

- Solves one problem or adds one feature
- Doesn't introduce unrelated cleanup or refactoring
- Works with both SQLite (dev) and PostgreSQL (prod)
- Doesn't break the existing approval workflow (draft -> submit -> review -> finalize)

## What to Work On

- Check the [Roadmap](ROADMAP.md) for planned features and known issues
- If you want to take on something larger, open an issue first to discuss the approach

## AI Tools

AI-assisted development tools (Copilot, Claude, etc.) are permitted. We expect contributors to **understand and own what they submit**. PRs should reflect thoughtful changes, not bulk-generated rewrites. If AI helped, that's fine. Just make sure the output makes sense for this codebase and you can explain what it does.

## Questions?

Open an issue or reach out to the maintainers. We're happy to help you get oriented.
