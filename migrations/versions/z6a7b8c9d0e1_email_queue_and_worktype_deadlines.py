"""Add scheduled_notifications and event_cycle_work_type_deadlines tables

Introduces the generic time-triggered email queue and per-worktype deadline
overrides. See docs/superpowers/specs/2026-05-19-email-system-design.md.

Also seeds 7 deadline-reminder email templates with initial copy. Templates
are editable post-migration via the admin email_templates UI.

Revision ID: z6a7b8c9d0e1
Revises: y5z6a7b8c9d0
Create Date: 2026-05-20

"""
from datetime import datetime

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision = 'z6a7b8c9d0e1'
down_revision = 'y5z6a7b8c9d0'
branch_labels = None
depends_on = None


# Initial copy for the 7 deadline-reminder templates. Mirrors Appendix B
# of the design spec. Admins can edit these post-migration via the email
# templates UI; this just provides defaults.
_TEMPLATES = [
    {
        "template_key": "deadline_reminder_t_minus_7",
        "name": "Deadline reminder - 7 days before",
        "description": "Sent 7 days before a (event x worktype) submission deadline to depts whose PRIMARY work item is missing or in DRAFT.",
        "subject": "Heads up: {{ work_type.name }} request for {{ event_cycle.name }} is due in 7 days",
        "body_text": (
            "Hi {{ department.name }} team,\n\n"
            "Just a heads-up that your {{ work_type.name }} request for {{ event_cycle.name }} is due on "
            "{{ deadline.strftime('%A, %B %d, %Y') }} - about a week from now.\n\n"
            "If you haven't started yet, now is a good time to get going. You can view and edit your request here:\n\n"
            "{{ portfolio_url }}\n\n"
            "Reach out on Slack if you need a hand or have questions about scope.\n\n"
            "Thanks!"
        ),
    },
    {
        "template_key": "deadline_reminder_t_minus_1",
        "name": "Deadline reminder - 1 day before",
        "description": "Sent the day before a (event x worktype) submission deadline.",
        "subject": "Reminder: {{ work_type.name }} request for {{ event_cycle.name }} is due tomorrow",
        "body_text": (
            "Hi {{ department.name }} team,\n\n"
            "This is a reminder that your {{ work_type.name }} request for {{ event_cycle.name }} is due tomorrow "
            "({{ deadline.strftime('%A, %B %d, %Y') }}).\n\n"
            "If you're still working on it, please prioritize wrapping it up. Submit when you're ready:\n\n"
            "{{ portfolio_url }}\n\n"
            "If you're stuck or need an extension, message us on Slack so we can help before the deadline."
        ),
    },
    {
        "template_key": "deadline_reminder_t_0",
        "name": "Deadline reminder - due today",
        "description": "Sent on the day of a (event x worktype) submission deadline.",
        "subject": "Due today: {{ work_type.name }} request for {{ event_cycle.name }}",
        "body_text": (
            "Hi {{ department.name }} team,\n\n"
            "Your {{ work_type.name }} request for {{ event_cycle.name }} is due today "
            "({{ deadline.strftime('%A, %B %d, %Y') }}).\n\n"
            "Please submit before end of day:\n\n"
            "{{ portfolio_url }}\n\n"
            "If you can't make today's deadline, please message us on Slack right away so we can plan accordingly."
        ),
    },
    {
        "template_key": "overdue_reminder_t_plus_1",
        "name": "Overdue reminder - 1 day late",
        "description": "Sent 1 day after deadline if PRIMARY work item is still missing or DRAFT.",
        "subject": "Overdue: {{ work_type.name }} request for {{ event_cycle.name }} was due yesterday",
        "body_text": (
            "Hi {{ department.name }} team,\n\n"
            "Your {{ work_type.name }} request for {{ event_cycle.name }} was due yesterday "
            "({{ deadline.strftime('%A, %B %d, %Y') }}) and hasn't been submitted yet.\n\n"
            "Please submit as soon as possible:\n\n"
            "{{ portfolio_url }}\n\n"
            "If something is blocking you, message us on Slack so we can help unblock it."
        ),
    },
    {
        "template_key": "overdue_reminder_t_plus_2",
        "name": "Overdue reminder - 2 days late",
        "description": "Sent 2 days after deadline if PRIMARY work item is still missing or DRAFT.",
        "subject": "2 days overdue: {{ work_type.name }} request for {{ event_cycle.name }}",
        "body_text": (
            "Hi {{ department.name }} team,\n\n"
            "Your {{ work_type.name }} request for {{ event_cycle.name }} is now 2 days overdue. "
            "The deadline was {{ deadline.strftime('%A, %B %d, %Y') }}.\n\n"
            "Please submit today if at all possible:\n\n"
            "{{ portfolio_url }}\n\n"
            "If you're facing a real blocker, please reply on Slack so we can help figure out next steps together."
        ),
    },
    {
        "template_key": "overdue_reminder_t_plus_4",
        "name": "Overdue reminder - 4 days late",
        "description": "Sent 4 days after deadline if PRIMARY work item is still missing or DRAFT.",
        "subject": "4 days overdue: {{ work_type.name }} request for {{ event_cycle.name }} needs attention",
        "body_text": (
            "Hi {{ department.name }} team,\n\n"
            "Your {{ work_type.name }} request for {{ event_cycle.name }} is 4 days overdue. "
            "The deadline was {{ deadline.strftime('%A, %B %d, %Y') }}.\n\n"
            "This is starting to put downstream planning at risk. Please submit today, or reach out on "
            "Slack so we can find a path forward together:\n\n"
            "{{ portfolio_url }}"
        ),
    },
    {
        "template_key": "overdue_reminder_t_plus_7",
        "name": "Overdue reminder - 1 week late (final automated)",
        "description": "Final automated reminder, sent 7 days after deadline. Further follow-up is manual.",
        "subject": "Final reminder: {{ work_type.name }} request for {{ event_cycle.name }} is a week overdue",
        "body_text": (
            "Hi {{ department.name }} team,\n\n"
            "Your {{ work_type.name }} request for {{ event_cycle.name }} is now a week overdue "
            "({{ deadline.strftime('%A, %B %d, %Y') }}).\n\n"
            "This is the last automatic reminder you'll receive. Future follow-up will be a direct "
            "conversation rather than an automated email.\n\n"
            "Please submit immediately if you can:\n\n"
            "{{ portfolio_url }}\n\n"
            "If circumstances have changed and your department isn't submitting a {{ work_type.name }} "
            "request this cycle, please let us know on Slack so we can update the schedule."
        ),
    },
]


def upgrade():
    # ---- scheduled_notifications ----
    op.create_table(
        "scheduled_notifications",
        # SQLite needs literal INTEGER for rowid autoincrement; Postgres gets BIGINT.
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True),
        sa.Column("template_key", sa.String(64), nullable=False, index=True),
        sa.Column("recipient_email", sa.String(256), nullable=False, index=True),
        sa.Column("recipient_user_id", sa.String(64), nullable=True, index=True),
        sa.Column("dispatch_at", sa.DateTime(), nullable=False, index=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="QUEUED", index=True),
        sa.Column(
            "work_item_id",
            sa.Integer(),
            sa.ForeignKey("work_items.id", ondelete="SET NULL", name="fk_sched_notif_work_item_id"),
            nullable=True, index=True,
        ),
        sa.Column(
            "event_cycle_id",
            sa.Integer(),
            sa.ForeignKey("event_cycles.id", ondelete="SET NULL", name="fk_sched_notif_event_cycle_id"),
            nullable=True, index=True,
        ),
        sa.Column(
            "work_type_id",
            sa.Integer(),
            sa.ForeignKey("work_types.id", ondelete="SET NULL", name="fk_sched_notif_work_type_id"),
            nullable=True, index=True,
        ),
        sa.Column("metadata_json", sa.Text(), nullable=True),
        sa.Column("dedup_key", sa.String(256), nullable=True, unique=True, index=True),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("claimed_by", sa.String(64), nullable=True),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("provider_message_id", sa.String(128), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
    )
    op.create_index(
        "ix_sched_notif_status_dispatch",
        "scheduled_notifications",
        ["status", "dispatch_at"],
    )

    # ---- event_cycle_work_type_deadlines ----
    op.create_table(
        "event_cycle_work_type_deadlines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "event_cycle_id",
            sa.Integer(),
            sa.ForeignKey("event_cycles.id", ondelete="CASCADE", name="fk_ecwtd_event_cycle_id"),
            nullable=False, index=True,
        ),
        sa.Column(
            "work_type_id",
            sa.Integer(),
            sa.ForeignKey("work_types.id", ondelete="CASCADE", name="fk_ecwtd_work_type_id"),
            nullable=False, index=True,
        ),
        sa.Column("submission_deadline", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_by_user_id", sa.String(64), nullable=True),
        sa.UniqueConstraint("event_cycle_id", "work_type_id", name="uq_ecwtd_event_worktype"),
    )

    # ---- seed 7 deadline reminder email templates ----
    email_templates = sa.table(
        "email_templates",
        sa.column("template_key", sa.String),
        sa.column("name", sa.String),
        sa.column("description", sa.Text),
        sa.column("subject", sa.String),
        sa.column("body_text", sa.Text),
        sa.column("is_active", sa.Boolean),
        sa.column("version", sa.Integer),
        sa.column("created_at", sa.DateTime),
        sa.column("updated_at", sa.DateTime),
    )

    now = datetime.utcnow()
    op.bulk_insert(
        email_templates,
        [
            {**t, "is_active": True, "version": 1, "created_at": now, "updated_at": now}
            for t in _TEMPLATES
        ],
    )


def downgrade():
    # Remove seeded templates first (no FKs, safe to delete by key).
    keys = ",".join(f"'{t['template_key']}'" for t in _TEMPLATES)
    op.execute(sa.text(f"DELETE FROM email_templates WHERE template_key IN ({keys})"))

    op.drop_table("event_cycle_work_type_deadlines")

    op.drop_index("ix_sched_notif_status_dispatch", table_name="scheduled_notifications")
    op.drop_table("scheduled_notifications")
