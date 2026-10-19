"""TechOps sent-back email template

A TechOps admin can return a submitted request to draft, and the department
is emailed the reason. enqueue_email blocks any kind without a row, so the
sent_back kind needs this one. The insert is guarded; production is never
seeded, and an admin may already have edited the wording.

Revision ID: sb7q2m4x9k1d
Revises: cb3k7r9t2m5x
Create Date: 2026-10-18

"""
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = 'sb7q2m4x9k1d'
down_revision = 'cb3k7r9t2m5x'
branch_labels = None
depends_on = None

_VIEW_LINE = (
    "View the request:\n"
    "{{ base_url }}/{{ work_item.portfolio.event_cycle.code }}"
    "/{{ work_item.portfolio.department.code }}"
)

_HEADER = (
    "Request: {{ work_item.public_id }}\n"
    "Department: {{ work_item.portfolio.department.name }}\n"
    "Event: {{ work_item.portfolio.event_cycle.name }}\n"
)

TEMPLATE = {
    "template_key": "techops_sent_back",
    "name": "TechOps Sent Back",
    "description": (
        "Sent to the requesting department and the request's primary contact "
        "when a TechOps admin returns a submitted request to draft."
    ),
    "subject": "[MAGFest TechOps] Request returned to you - {{ work_item.public_id }}",
    "body_text": (
        "TechOps has returned your request to draft so you can change it.\n\n"
        + _HEADER
        + "\nReason from TechOps:\n{{ reason }}\n\n"
        "Make your changes and submit it again. TechOps reviews it from the start.\n\n"
        + _VIEW_LINE + "/techops/item/{{ work_item.public_id }}\n"
    ),
}


def seed_sent_back_template(conn):
    """Insert the row unless it exists. Importable so tests can drive it."""
    exists = conn.execute(
        sa.text("SELECT id FROM email_templates WHERE template_key = :k"),
        {"k": TEMPLATE["template_key"]},
    ).scalar()
    if exists is not None:
        return
    now = datetime.utcnow()
    conn.execute(
        sa.text(
            "INSERT INTO email_templates "
            "(template_key, name, description, subject, body_text, "
            " is_active, version, created_at, updated_at) "
            "VALUES (:k, :n, :d, :s, :b, :active, 1, :now, :now)"
        ),
        {
            "k": TEMPLATE["template_key"], "n": TEMPLATE["name"],
            "d": TEMPLATE["description"], "s": TEMPLATE["subject"],
            "b": TEMPLATE["body_text"],
            # Bound Python bool: PostgreSQL will not put an integer in a boolean column.
            "active": True, "now": now,
        },
    )


def remove_sent_back_template(conn):
    conn.execute(
        sa.text("DELETE FROM email_templates WHERE template_key = :k"),
        {"k": TEMPLATE["template_key"]},
    )


def upgrade():
    seed_sent_back_template(op.get_bind())


def downgrade():
    remove_sent_back_template(op.get_bind())
