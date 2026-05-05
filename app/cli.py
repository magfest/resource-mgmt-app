"""Flask CLI commands.

- `flask seed`: manual seed control. Auto-seeding also fires via the
  run_seed_once hook in app/__init__.py for empty DBs on first request.
- `flask release-expired-checkouts`: scheduled cleanup of stale review
  checkouts; intended to run on Heroku Scheduler so stale locks don't
  linger in dashboards/audit logs past their expiration window.
"""
from __future__ import annotations

import click
from flask import Flask
from flask.cli import with_appcontext


def register_cli(app: Flask) -> None:
    """Register CLI commands on the Flask app. Called from create_app()."""

    @app.cli.command("seed")
    @click.argument(
        "target",
        type=click.Choice(["bootstrap", "demo", "all"], case_sensitive=False),
        default="all",
    )
    @with_appcontext
    def seed_command(target):
        """Seed the database. TARGET is one of: bootstrap, demo, all (default).

        \b
        bootstrap  Schema-required rows (worktypes, approval groups,
                   reference data, hotel expense accounts).
        demo       Operator-replaceable [Demo] org content (depts,
                   event cycle, divisions, parking accounts).
        all        Both (this is what the auto-seed hook runs).

        Idempotent at every layer: insert-only, never updates existing
        rows, never re-adds deleted rows. Safe to re-run on populated
        DBs (staging/prod).
        """
        from app.seeds.bootstrap import run_bootstrap
        from app.seeds.demo_data import run_demo_data

        target = target.lower()

        if target in ("bootstrap", "all"):
            run_bootstrap()

        if target in ("demo", "all"):
            run_demo_data()

        click.echo(f"\nflask seed {target}: done.")

    @app.cli.command("release-expired-checkouts")
    @with_appcontext
    def release_expired_checkouts_command():
        """Release work-item checkouts whose expiration has passed.

        Idempotent — running twice in a row releases zero on the second pass.
        The release_expired_checkouts() helper mutates rows but does not
        commit (see app/routes/admin/locks.py for the manual-trigger pattern),
        so this command commits explicitly.
        """
        from app import db
        from app.routes.work.helpers.checkout import release_expired_checkouts

        count = release_expired_checkouts()
        db.session.commit()
        click.echo(f"Released {count} expired checkout(s).")
