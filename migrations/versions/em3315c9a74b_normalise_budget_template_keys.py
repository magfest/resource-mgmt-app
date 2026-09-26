"""Normalise BUDGET email template keys to budget_*

Revision ID: em3315c9a74b
Revises: tr7742b1c8e5

Off-sequence id on purpose, to avoid a head collision with concurrent
migration work.

BUDGET reached its templates through resolve_template_key's fallback, which
made "a work type owns its template rows" a rule BUDGET alone broke. After this
every work type resolves the same way.

No wording changes. Same templates, same recipients, same subjects.

DEPLOY: pause the Heroku Scheduler before running this, and resume afterwards.
The rename and the outbox rewrite are one transaction, so a non-empty queue is
safe, but the drainer must not be mid-run against a row it has already claimed
as SENDING. This is a manual step; nothing in the repository enforces it.

DOWNGRADE is not a full inverse. It restores the seven rows and any
non-terminal outbox rows, but rows written while migrated keep budget_* in
notification_logs and in terminal outbox rows, because this migration treats
those as history. An upgrade, downgrade and upgrade therefore leaves audit
rows under three different keys.
"""
import sqlalchemy as sa
from alembic import op

revision = 'em3315c9a74b'
down_revision = 'tr7742b1c8e5'
branch_labels = None
depends_on = None

RENAMES = (
    ("submitted", "budget_submitted"),
    ("dispatched", "budget_dispatched"),
    ("needs_attention", "budget_needs_attention"),
    ("response_received", "budget_response_received"),
    ("submission_confirmation", "budget_submission_confirmation"),
    ("finalized", "budget_finalized"),
    ("submission_reminder", "budget_submission_reminder"),
)

# Rows the drainer may still claim. A row left pointing at a deleted key is
# CANCELLED by the drainer (email_drainer.py:383-386), and CANCELLED is
# terminal: one silent drop, no retry, no alert. Terminal rows are history
# and keep their original key, as does notification_logs.
NON_TERMINAL = ("QUEUED", "SENDING", "RENDER_BLOCKED")


def _move(conn, renames):
    for old_key, new_key in renames:
        conn.execute(
            sa.text(
                "UPDATE email_templates SET template_key = :new "
                "WHERE template_key = :old"
            ),
            {"new": new_key, "old": old_key},
        )
        conn.execute(
            sa.text(
                "UPDATE email_outbox SET template_key = :new "
                "WHERE template_key = :old AND status IN :statuses"
            ).bindparams(sa.bindparam("statuses", expanding=True)),
            {"new": new_key, "old": old_key, "statuses": list(NON_TERMINAL)},
        )


def rename_budget_template_keys(conn):
    """Forward data change, importable so tests can drive it directly."""
    _move(conn, RENAMES)


def revert_budget_template_keys(conn):
    _move(conn, [(new, old) for old, new in RENAMES])


def upgrade():
    rename_budget_template_keys(op.get_bind())


def downgrade():
    revert_budget_template_keys(op.get_bind())
