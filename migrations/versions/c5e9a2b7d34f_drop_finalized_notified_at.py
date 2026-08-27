"""Drop work_items.finalized_notified_at.

The column tracked which released budgets the sweeper had already emailed.
With the sweeper gone there is no work list to filter, and idempotency comes
from the outbox dedup key, which is scoped to board_released_at.

Revision ID: c5e9a2b7d34f
Revises: b8d4f1a6c2e5
"""
import sqlalchemy as sa
from alembic import op

revision = 'c5e9a2b7d34f'
down_revision = 'b8d4f1a6c2e5'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('work_items', schema=None) as batch_op:
        batch_op.drop_index('ix_work_items_finalized_notified_at')
        batch_op.drop_column('finalized_notified_at')


def downgrade():
    # Restores the column empty. The values are not recoverable, and nothing
    # reads them any more; a downgrade is for schema shape, not for data.
    with op.batch_alter_table('work_items', schema=None) as batch_op:
        batch_op.add_column(sa.Column('finalized_notified_at', sa.DateTime(), nullable=True))
        batch_op.create_index('ix_work_items_finalized_notified_at', ['finalized_notified_at'])
