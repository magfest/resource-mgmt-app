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

    @app.cli.command("drain-email-outbox")
    @with_appcontext
    def drain_email_outbox_command():
        """Send queued email. The only code path that calls SES.

        Runs under Heroku Scheduler every 10 minutes. Adding the Scheduler job
        is a manual step; it is not reproducible from this repo.
        """
        from app.services.email_drainer import drain_outbox

        summary = drain_outbox()
        click.echo(
            f"claimed={summary.claimed} sent={summary.sent} failed={summary.failed} "
            f"suppressed={summary.suppressed} cancelled={summary.cancelled} "
            f"render_blocked={summary.render_blocked} pruned={summary.pruned}"
        )
        if summary.stopped_reason:
            click.echo(f"stopped: {summary.stopped_reason}")

    @app.cli.command("send-submission-reminders")
    @click.argument("event_code")
    @click.option(
        "--send",
        is_flag=True,
        default=False,
        help="Queue the emails. Without this flag, the command runs as a dry-run.",
    )
    @with_appcontext
    def send_submission_reminders_command(event_code, send):
        """Send budget-submission reminder emails for an event.

        \b
        EVENT_CODE  Required. The EventCycle.code (e.g. SMF2027).

        Dry-run by default - prints the list of departments + recipients
        and a sample rendered email body, but queues nothing. Pass --send
        to queue the rows.

        Queues one email_outbox row per recipient and exits. The Heroku
        Scheduler drainer sends them; this command never calls SES.

        Exit codes:
          0  Success (dry-run completed, or the queue run finished)
          1  Event code didn't resolve, or event inactive in non-interactive mode
          2  Template 'submission_reminder' not found or inactive
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
            click.echo("DRY RUN - nothing queued. Pass --send to queue the rows.")
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
                f"Would queue: {total_would_send} emails across "
                f"{len(targets) - skipped} departments"
                + (f" ({skipped} skipped, no members)" if skipped else "")
            )
            click.echo("Re-run with --send to queue them.")
            sys.exit(0)

        # Live run. Queues rows; the drainer sends them.
        click.echo(f"Queueing submission reminders for {cycle.code}...")
        summary = send_submission_reminders(cycle, dry_run=False)
        click.echo(
            f"Queued: {summary.rows_queued} / {summary.recipients_total} rows "
            f"across {summary.targets_with_recipients} departments"
        )
        if summary.rows_queued < summary.recipients_total:
            # A same-day re-run is the normal cause: the dedup key is keyed to
            # the calendar day, so the second run queues nothing new.
            click.echo(
                f"Already queued today: "
                f"{summary.recipients_total - summary.rows_queued}"
            )
        if summary.targets_without_recipients:
            click.echo(
                f"Skipped (no members): "
                f"{', '.join(summary.targets_without_recipients)}"
            )

        sys.exit(0)

    @app.cli.command("send-board-release-emails")
    @click.option(
        "--send",
        is_flag=True,
        default=False,
        help="Queue the emails. Without this flag, the command runs as a dry-run.",
    )
    @with_appcontext
    def send_board_release_emails_command(send):
        """Queue release emails for departments whose budgets the board released.

        Selects finalized work items that are released but not yet notified,
        queues one email_outbox row per department member, and stamps the item.
        Dry-run by default; pass --send to queue.

        Runs under Heroku Scheduler. No web request queues these, because a
        bulk board release fans out one row per department member and the
        Slack announcement per item would exceed Heroku's 30-second request
        limit.

        \b
        Exit codes:
          0  Success (dry-run completed, or every item queued and stamped)
          1  At least one item raised or failed to commit; left unstamped for
             the next scheduled run
          2  Template 'finalized' not found or inactive
        """
        import sys
        from datetime import datetime

        from app.models import (
            WorkItem,
            WorkPortfolio,
            WorkTypeConfig,
            EmailTemplate,
        )
        from app.models.constants import WORK_ITEM_STATUS_FINALIZED
        from app.services import notifications

        # Guard missing/inactive template, same contract as
        # send-submission-reminders: an operator can silence board-release
        # emails at the DB level and this command should exit loudly, not
        # loop stamping nothing.
        template = EmailTemplate.query.filter_by(template_key='finalized').first()
        if template is None:
            click.echo(
                "Email template 'finalized' not found. Run migrations.",
                err=True,
            )
            sys.exit(2)
        if not template.is_active:
            click.echo(
                "Email template 'finalized' is inactive (is_active=False) "
                "in the email_templates table. Re-activate before sending.",
                err=True,
            )
            sys.exit(2)

        # Joined to WorkTypeConfig.uses_board_release, matching
        # get_held_budgets() in admin_final/helpers.py. Both write sites for
        # board_released_at (release_event_budgets and finalize_work_item)
        # already check this flag, so the join is defence in depth, not the
        # only guard. This command runs unattended under Heroku Scheduler
        # with no reviewer between a bad write and a sent email, so a work
        # type that should never be swept in stays excluded even if a future
        # write site forgets the check.
        pending = (
            WorkItem.query
            .join(WorkPortfolio, WorkItem.portfolio_id == WorkPortfolio.id)
            .join(WorkTypeConfig, WorkPortfolio.work_type_id == WorkTypeConfig.work_type_id)
            .filter(WorkTypeConfig.uses_board_release == True)  # noqa: E712
            .filter(WorkItem.status == WORK_ITEM_STATUS_FINALIZED)
            .filter(WorkItem.board_released_at.isnot(None))
            .filter(WorkItem.finalized_notified_at.is_(None))
            .filter(WorkItem.is_archived == False)  # noqa: E712
            .filter(WorkPortfolio.is_archived == False)  # noqa: E712
            .order_by(WorkItem.public_id.asc())
            .all()
        )

        if not pending:
            click.echo("No released budgets awaiting notification.")
            sys.exit(0)

        click.echo(f"Released budgets awaiting notification: {len(pending)}")
        for item in pending:
            click.echo(f"  {item.public_id:<28} {item.portfolio.department.name}")
        click.echo()

        if not send:
            click.echo("DRY RUN - nothing queued. Pass --send to queue.")
            sys.exit(0)

        failures = 0
        for item in pending:
            # Captured before the commit below: a commit expires every
            # loaded instance, and a later DB-level failure leaves the session
            # needing an explicit rollback before it can run any query again,
            # including the implicit SELECT an `item.public_id` access would
            # trigger. Reading it now, while the session is known-good, avoids
            # that trap.
            public_id = item.public_id

            try:
                notifications.notify_work_item_finalized(item)
                # finalized_notified_at now means "queued", not "delivered".
                # Delivery truth lives in email_outbox.status and
                # NotificationLog, queryable per item.
                #
                # Stamp even when the department has no members. Departments
                # without members are expected rather than exceptional, and
                # re-listing them every run fills the pending table with rows
                # nobody can act on.
                item.finalized_notified_at = datetime.utcnow()
                # Commit per item, not once after the loop. The outbox rows and
                # the stamp must land together, and one bad item later in
                # `pending` must not roll back stamps earlier items already
                # earned.
                db.session.commit()
            except Exception as exc:
                failures += 1
                click.echo(f"  FAILED {public_id}: {exc}", err=True)
                # A DB-level failure leaves the session unusable until it is
                # rolled back. Without this, this item's failure poisons the
                # rest of the loop.
                db.session.rollback()
                continue

            # Slack is a webhook call with a 10 second timeout. Run it after
            # the commit; inside the transaction it would hold the work item's
            # row locks for that long. notify_work_item_finalized no longer
            # posts to Slack, so without this line the channel goes quiet with
            # no error and no failing test.
            notifications.announce_work_item_event(item, 'finalized')

        click.echo(f"Queued {len(pending) - failures} of {len(pending)}.")
        if failures:
            sys.exit(1)
        sys.exit(0)
