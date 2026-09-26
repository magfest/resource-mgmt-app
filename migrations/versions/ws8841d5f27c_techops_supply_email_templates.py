"""TechOps and Supply email templates

Revision ID: ws8841d5f27c
Revises: em3315c9a74b

Off-sequence id on purpose, to avoid a head collision with concurrent
migration work.

em3315c9a74b renamed BUDGET's rows to budget_*, which removed the bare keys
resolve_template_key falls back to. Every kind a live work type can emit needs
a row of its own from here on, or enqueue_email blocks it and logs one line per
recipient with nothing else to show for it.

TECHOPS gets no finalized row. It has has_admin_final=False and auto-finalizes,
and try_auto_finalize sends nothing at all. That gap is separate work.

Every insert is guarded. This migration runs after `flask seed` on a fresh
database and again on any retried deploy.
"""
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = 'ws8841d5f27c'
down_revision = 'em3315c9a74b'
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

TEMPLATES = (
    {
        "template_key": "techops_submitted",
        "name": "TechOps Submitted",
        "description": (
            "Sent to the reviewers a TechOps request routes to, when it leaves "
            "DRAFT. TechOps has no dispatch step; these reviewers act directly."
        ),
        "subject": "[MAGFest TechOps] New request - {{ work_item.public_id }}",
        "body_text": (
            "A department has submitted a TechOps request for review.\n\n"
            + _HEADER
            + "\n" + _VIEW_LINE + "/techops/item/{{ work_item.public_id }}\n"
        ),
    },
    {
        "template_key": "techops_needs_attention",
        "name": "TechOps Needs Attention",
        "description": (
            "Sent to the requesting department when a reviewer asks a question "
            "about a TechOps line or asks for a change."
        ),
        "subject": "[MAGFest TechOps] Action needed - {{ work_item.public_id }}",
        "body_text": (
            "A reviewer needs something from you on a TechOps request.\n\n"
            + _HEADER
            + "\nOpen the request to see which services are waiting on you, and "
            "reply there.\n\n"
            + _VIEW_LINE + "/techops/item/{{ work_item.public_id }}\n"
        ),
    },
    {
        "template_key": "techops_response_received",
        "name": "TechOps Response Received",
        "description": (
            "Sent to the reviewer who asked, when the department answers."
        ),
        "subject": "[MAGFest TechOps] Response received - {{ work_item.public_id }}",
        "body_text": (
            "A department has responded on a TechOps request you were "
            "reviewing.\n\n"
            + _HEADER
            + "\n" + _VIEW_LINE + "/techops/item/{{ work_item.public_id }}\n"
        ),
    },
    {
        "template_key": "techops_submission_confirmation",
        "name": "TechOps Submission Confirmation",
        "description": (
            "Sent to the submitting department, and to the primary contact "
            "named on the request, when a TechOps request leaves DRAFT."
        ),
        "subject": "[MAGFest TechOps] Request received - {{ work_item.public_id }}",
        "body_text": (
            "Your TechOps request was received. TechOps will reach out if "
            "anything needs clarifying.\n\n"
            + _HEADER
            # No service count. expand_to_lines emits one WorkLine per phone
            # number AND per handset, plus one for a "nothing needed" answer
            # (line_grain.py:170,213), so line_count reads 3 for one phone with
            # two handsets, and 3 for a request that asked for nothing at all.
            + "\nNothing is confirmed yet. You will hear from TechOps if a room "
            "or a service needs discussing.\n\n"
            + _VIEW_LINE + "/techops/item/{{ work_item.public_id }}\n"
        ),
    },
    {
        "template_key": "supply_submitted",
        "name": "Supply Submitted",
        "description": (
            "Sent to the reviewers a supply order routes to, when it leaves "
            "DRAFT. Supply has no dispatch step."
        ),
        "subject": "[MAGFest Supply] New order - {{ work_item.public_id }}",
        "body_text": (
            "A department has submitted a supply order for review.\n\n"
            + _HEADER
            # No item count: notify_work_item_submitted passes no
            # extra_context, and Jinja renders an unknown name as empty, so
            # this would show a blank where a number belongs.
            + "\n" + _VIEW_LINE + "/supply/item/{{ work_item.public_id }}\n"
        ),
    },
    {
        "template_key": "supply_needs_attention",
        "name": "Supply Needs Attention",
        "description": (
            "Sent to the ordering department when a reviewer asks a question "
            "about a line or asks for a change."
        ),
        "subject": "[MAGFest Supply] Action needed - {{ work_item.public_id }}",
        "body_text": (
            "A reviewer needs something from you on a supply order.\n\n"
            + _HEADER
            + "\nOpen the order to see which items are waiting on you, and "
            "reply there.\n\n"
            + _VIEW_LINE + "/supply/item/{{ work_item.public_id }}\n"
        ),
    },
    {
        "template_key": "supply_response_received",
        "name": "Supply Response Received",
        "description": "Sent to the reviewer who asked, when the department answers.",
        "subject": "[MAGFest Supply] Response received - {{ work_item.public_id }}",
        "body_text": (
            "A department has responded on a supply order you were "
            "reviewing.\n\n"
            + _HEADER
            + "\n" + _VIEW_LINE + "/supply/item/{{ work_item.public_id }}\n"
        ),
    },
    {
        "template_key": "supply_finalized",
        "name": "Supply Finalized",
        "description": (
            "Sent to the ordering department when an admin finalizes the "
            "order. Supply has has_admin_final=True, so this is reachable."
        ),
        "subject": "[MAGFest Supply] Order finalized - {{ work_item.public_id }}",
        "body_text": (
            "Your supply order has been finalized.\n\n"
            + _HEADER
            + "\nOpen the order to see what was approved for each item.\n\n"
            + _VIEW_LINE + "/supply/item/{{ work_item.public_id }}\n"
        ),
    },
    {
        "template_key": "supply_submission_confirmation",
        "name": "Supply Submission Confirmation",
        "description": (
            "Sent to the ordering department when a supply order leaves DRAFT."
        ),
        "subject": "[MAGFest Supply] Order received - {{ work_item.public_id }}",
        "body_text": (
            "Your supply order was received and is waiting for review.\n\n"
            + _HEADER
            + "Items ordered: {{ line_count }}\n"
            "\nNothing is confirmed yet. You will hear back if an item needs "
            "discussing.\n\n"
            + _VIEW_LINE + "/supply/item/{{ work_item.public_id }}\n"
        ),
    },
)


def seed_work_type_templates(conn):
    """Insert each row that is not already present. Importable so tests can
    drive it directly."""
    now = datetime.utcnow()
    for row in TEMPLATES:
        exists = conn.execute(
            sa.text("SELECT id FROM email_templates WHERE template_key = :k"),
            {"k": row["template_key"]},
        ).scalar()
        if exists is not None:
            # An admin may have edited the wording. Never overwrite it.
            continue
        conn.execute(
            sa.text(
                "INSERT INTO email_templates "
                "(template_key, name, description, subject, body_text, "
                " is_active, version, created_at, updated_at) "
                "VALUES (:k, :n, :d, :s, :b, :active, 1, :now, :now)"
            ),
            {
                "k": row["template_key"], "n": row["name"],
                "d": row["description"], "s": row["subject"],
                "b": row["body_text"],
                # Bound Python bool, not a literal 1: PostgreSQL will not put
                # an integer in a boolean column.
                "active": True, "now": now,
            },
        )


def remove_work_type_templates(conn):
    for row in TEMPLATES:
        conn.execute(
            sa.text("DELETE FROM email_templates WHERE template_key = :k"),
            {"k": row["template_key"]},
        )


def upgrade():
    seed_work_type_templates(op.get_bind())


def downgrade():
    remove_work_type_templates(op.get_bind())
