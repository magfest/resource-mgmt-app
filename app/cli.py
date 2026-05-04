"""Flask CLI commands.

Currently exposes `flask seed` for manual seed control. Auto-seeding still
happens via the run_seed_once hook in app/__init__.py for the common case
(empty DB on first request). This CLI is the manual override for when an
operator wants to re-seed after a partial wipe or add demo content back.
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
