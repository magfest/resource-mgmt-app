"""Add partial unique index for one PRIMARY work item per portfolio

Closes a race window where two simultaneous POSTs to /<event>/<dept>/budget/primary
could both pass the .first() pre-check at app/routes/work/work_items/create.py:77
before either commits, resulting in two non-archived PRIMARY work items per
portfolio. The app-level check still runs first (so users get a friendly flash
on the common path), but this index is the authoritative guard.

Pre-flight verified zero existing violations:
    SELECT portfolio_id, COUNT(*) FROM work_items
    WHERE request_kind = 'PRIMARY' AND is_archived = false
    GROUP BY portfolio_id HAVING COUNT(*) > 1;
returned no rows on staging on 2026-05-04.

Postgres-only: SQLite does not support ``CREATE INDEX ... WHERE`` via this
syntax, so the migration is a no-op there. This matches the pattern in
o5p6q7r8s9t0_add_partial_unique_indexes_for_null.py.

Revision ID: z6a7b8c9d0e1
Revises: y5z6a7b8c9d0
Create Date: 2026-05-04 14:00:00.000000

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'z6a7b8c9d0e1'
down_revision = 'y5z6a7b8c9d0'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        return

    op.execute(
        "CREATE UNIQUE INDEX ix_work_items_one_primary_per_portfolio "
        "ON work_items (portfolio_id) "
        "WHERE request_kind = 'PRIMARY' AND is_archived = false"
    )


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        return

    op.execute("DROP INDEX IF EXISTS ix_work_items_one_primary_per_portfolio")
