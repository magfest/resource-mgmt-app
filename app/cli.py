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

from app import db


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

    @app.cli.command("send-submission-reminders")
    @click.argument("event_code")
    @click.option(
        "--send",
        is_flag=True,
        default=False,
        help="Actually send emails. Without this flag, the command runs as a dry-run.",
    )
    @with_appcontext
    def send_submission_reminders_command(event_code, send):
        """Send budget-submission reminder emails for an event.

        \b
        EVENT_CODE  Required. The EventCycle.code (e.g. SMF2027).

        Dry-run by default - prints the list of departments + recipients
        and a sample rendered email body, but sends nothing. Pass --send
        to actually fire.

        Exit codes:
          0  Success (dry-run completed or all sends succeeded)
          1  Event code didn't resolve, or event inactive in non-interactive mode
          2  Template 'submission_reminder' not found or inactive
          3  At least one send_email() returned False (partial-send failure)
        """
        import sys
        from app.models import EventCycle, EmailTemplate
        from app.services.notifications import (
            get_departments_needing_submission_reminder,
            send_submission_reminders,
        )
        from app.services.email_templates import render_email_template

        # Resolve the event.
        cycle = EventCycle.query.filter_by(code=event_code).first()
        if cycle is None:
            click.echo(f"Event code {event_code!r} not found.", err=True)
            sys.exit(1)

        # Guard inactive events.
        if not cycle.is_active:
            if sys.stdin.isatty():
                click.echo(
                    f"Event {cycle.code} ({cycle.name}) is inactive (is_active=False).",
                    err=True,
                )
                if not click.confirm("Send reminders for this inactive event?", default=False):
                    click.echo("Aborted.", err=True)
                    sys.exit(1)
            else:
                click.echo(
                    f"Event {cycle.code} is inactive (is_active=False); refusing to "
                    f"proceed in non-interactive mode.",
                    err=True,
                )
                sys.exit(1)

        # Guard missing/inactive template.
        template = EmailTemplate.query.filter_by(
            template_key='submission_reminder',
        ).first()
        if template is None:
            click.echo(
                "Email template 'submission_reminder' not found. Run migrations.",
                err=True,
            )
            sys.exit(2)
        if not template.is_active:
            click.echo(
                "Email template 'submission_reminder' is inactive (is_active=False) "
                "in the email_templates table. Re-activate before sending.",
                err=True,
            )
            sys.exit(2)

        # Show the plan header (both dry-run and live).
        click.echo(f"Event: {cycle.code} ({cycle.name})")
        click.echo()

        targets = get_departments_needing_submission_reminder(cycle)
        if not targets:
            click.echo("No departments need a reminder. Nothing to send.")
            sys.exit(0)

        # Show the per-dept table only in dry-run mode.
        if not send:
            click.echo("DRY RUN - no emails will be sent. Pass --send to actually send.")
            click.echo()
            click.echo(f"Departments needing reminder: {len(targets)}")
            for t in targets:
                marker = "  [no members]" if not t.recipient_emails else ""
                click.echo(
                    f"  {t.department_code:<10} {t.department_name:<30} "
                    f"{len(t.recipient_emails):>3} recipients{marker}"
                )
            click.echo()

            # Show a sample rendered email from the first target with recipients.
            first_with_recipients = next(
                (t for t in targets if t.recipient_emails), None,
            )
            if first_with_recipients:
                from app.models import Department
                dept = db.session.get(Department, first_with_recipients.department_id)
                rendered = render_email_template('submission_reminder', {
                    'department': dept,
                    'event_cycle': cycle,
                    'base_url': 'https://budget.magfest.org',
                })
                if rendered:
                    click.echo("Sample rendered email (first target):")
                    click.echo("  -----------------------------------------")
                    click.echo(f"  Subject: {rendered.subject}")
                    click.echo("  Body:")
                    for line in rendered.body_text.splitlines():
                        click.echo(f"    {line}")
                    click.echo("  -----------------------------------------")
                    click.echo()

            total_would_send = sum(len(t.recipient_emails) for t in targets)
            skipped = sum(1 for t in targets if not t.recipient_emails)
            click.echo(
                f"Would send: {total_would_send} emails across "
                f"{len(targets) - skipped} departments"
                + (f" ({skipped} skipped, no members)" if skipped else "")
            )
            click.echo("Re-run with --send to actually send.")
            sys.exit(0)

        # Live send.
        click.echo(f"Sending submission reminders for {cycle.code}...")
        summary = send_submission_reminders(cycle, dry_run=False)
        click.echo(
            f"Sent: {summary.emails_sent} / {summary.emails_attempted} emails "
            f"across {summary.targets_with_recipients} departments"
        )
        if summary.targets_without_recipients:
            click.echo(
                f"Skipped (no members): "
                f"{', '.join(summary.targets_without_recipients)}"
            )

        if summary.emails_sent < summary.emails_attempted:
            sys.exit(3)
        sys.exit(0)
