"""Supply finalized (with changes) and closed email templates

Nothing queued supply_finalized before this; the finalize route now does.
The body gains a "what changed" block, and supply_closed is new.

Revision ID: sm7p2c9x4f6b
Revises: sr4k8w2q6n1d

Every write is guarded. Production is never seeded, so a missing
supply_finalized row is inserted; an edited one is left alone. A downgrade
touches a row only while its body is still the one this migration wrote.
"""
import logging
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = "sm7p2c9x4f6b"
down_revision = "sr4k8w2q6n1d"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

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

# Copied from ws8841d5f27c. An exact match means no admin has edited it.
_FINALIZED_HEAD = (
    "Your supply order has been finalized.\n\n"
    + _HEADER
    + "\nOpen the order to see what was approved for each item.\n\n"
)
_FINALIZED_TAIL = _VIEW_LINE + "/supply/item/{{ work_item.public_id }}\n"
FINALIZED_BODY_OLD = _FINALIZED_HEAD + _FINALIZED_TAIL

_CHANGES_BLOCK = (
    "{% if changes %}What changed from your request:\n"
    "{% for c in changes %}- Line {{ c.line_number }}, {{ c.item_name }}: "
    '{% if c.kind == "removed" %}removed. {{ c.reason }}'
    '{% elif c.kind == "added" %}added by Supply admin, {{ c.final }} {{ c.unit }}'
    '{% elif c.kind == "requested_changed" %}requested quantity changed from '
    "{{ c.department_quantity }} to {{ c.requested }}. {{ c.reason }}"
    "{% else %}approved {{ c.final }} of {{ c.requested }}{% endif %}\n"
    "{% endfor %}\n"
    "{% endif %}\n"
)
FINALIZED_BODY_NEW = _FINALIZED_HEAD + _CHANGES_BLOCK + _FINALIZED_TAIL

FINALIZED = {
    "template_key": "supply_finalized",
    "name": "Supply Finalized",
    "description": (
        "Sent to the ordering department when an admin finalizes the "
        "order. Supply has has_admin_final=True, so this is reachable."
    ),
    "subject": "[MAGFest Supply] Order finalized - {{ work_item.public_id }}",
}

CLOSED = {
    "template_key": "supply_closed",
    "name": "Supply Order Closed",
    "description": "Sent to the ordering department when a Supply admin closes an order.",
    "subject": "[MAGFest Supply] Order closed - {{ work_item.public_id }}",
    "body_text": (
        "Your supply order has been closed by Supply Ops.\n\n"
        + _HEADER
        + "\nReason: {{ reason }}\n\n"
        "Nothing on this order will be filled. Place a new order if you "
        "still need supplies.\n\n"
        + _VIEW_LINE + "/supply/item/{{ work_item.public_id }}\n"
    ),
}


def _body(conn, key):
    return conn.execute(
        sa.text("SELECT body_text FROM email_templates WHERE template_key = :k"),
        {"k": key},
    ).fetchone()


def _insert(conn, row, body):
    now = datetime.utcnow()
    conn.execute(
        sa.text(
            "INSERT INTO email_templates "
            "(template_key, name, description, subject, body_text, "
            " is_active, version, created_at, updated_at) "
            "VALUES (:k, :n, :d, :s, :b, :active, 1, :now, :now)"
        ),
        {
            "k": row["template_key"], "n": row["name"], "d": row["description"],
            "s": row["subject"], "b": body,
            # Bound bool: PostgreSQL rejects an integer literal in a boolean column.
            "active": True, "now": now,
        },
    )


def _set_body(conn, key, body):
    # The version bump flags per-event overrides of the old body as stale, as
    # the admin template editor does on every save.
    conn.execute(
        sa.text("UPDATE email_templates SET body_text = :b, updated_at = :now, "
                "version = version + 1 WHERE template_key = :k"),
        {"b": body, "k": key, "now": datetime.utcnow()},
    )


def seed_closed_template(conn):
    if _body(conn, CLOSED["template_key"]) is None:
        _insert(conn, CLOSED, CLOSED["body_text"])


def update_finalized_template(conn):
    current = _body(conn, FINALIZED["template_key"])
    if current is None:
        _insert(conn, FINALIZED, FINALIZED_BODY_NEW)
    elif current[0] == FINALIZED_BODY_OLD:
        _set_body(conn, FINALIZED["template_key"], FINALIZED_BODY_NEW)
    elif current[0] != FINALIZED_BODY_NEW:
        logger.warning("supply_finalized body was edited; leaving it unchanged")


def downgrade_templates(conn):
    closed = _body(conn, CLOSED["template_key"])
    if closed is not None and closed[0] == CLOSED["body_text"]:
        conn.execute(
            sa.text("DELETE FROM email_templates WHERE template_key = :k"),
            {"k": CLOSED["template_key"]},
        )
    finalized = _body(conn, FINALIZED["template_key"])
    if finalized is not None and finalized[0] == FINALIZED_BODY_NEW:
        _set_body(conn, FINALIZED["template_key"], FINALIZED_BODY_OLD)


def upgrade():
    conn = op.get_bind()
    update_finalized_template(conn)
    seed_closed_template(conn)


def downgrade():
    downgrade_templates(op.get_bind())
