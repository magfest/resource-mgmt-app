"""Add updated_at column to work_item_comments

Comments can now be edited (by author on DRAFT items, or by admins).
The model needs an updated_at timestamp so the UI can show an "(edited)"
indicator when a comment was modified after creation.

For existing rows, backfill updated_at = created_at so the indicator stays
hidden for legacy data. The column is added nullable first, backfilled,
then altered to NOT NULL — standard pattern for adding a NOT NULL column
to a table with existing rows.

Revision ID: a7b8c9d0e1f2
Revises: z6a7b8c9d0e1
Create Date: 2026-05-17 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a7b8c9d0e1f2'
down_revision = 'z6a7b8c9d0e1'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('work_item_comments') as batch_op:
        batch_op.add_column(sa.Column('updated_at', sa.DateTime(), nullable=True))

    # Backfill existing rows so updated_at == created_at (legacy comments
    # should not appear as "(edited)" in the UI).
    op.execute("UPDATE work_item_comments SET updated_at = created_at")

    with op.batch_alter_table('work_item_comments') as batch_op:
        batch_op.alter_column(
            'updated_at',
            existing_type=sa.DateTime(),
            nullable=False,
        )
        batch_op.create_index(
            'ix_work_item_comments_updated_at',
            ['updated_at'],
        )


def downgrade():
    with op.batch_alter_table('work_item_comments') as batch_op:
        batch_op.drop_index('ix_work_item_comments_updated_at')
        batch_op.drop_column('updated_at')
