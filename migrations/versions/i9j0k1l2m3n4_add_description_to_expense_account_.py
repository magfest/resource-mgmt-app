"""Add description to expense_account_event_overrides

Revision ID: i9j0k1l2m3n4
Revises: caa7a2c1bd85
Create Date: 2026-03-09 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'i9j0k1l2m3n4'
down_revision = 'caa7a2c1bd85'
branch_labels = None
depends_on = None


def upgrade():
    # Add description column to expense_account_event_overrides
    # This allows per-event descriptions that override the base expense account description
    with op.batch_alter_table('expense_account_event_overrides', schema=None) as batch_op:
        batch_op.add_column(sa.Column('description', sa.Text(), nullable=True))


def downgrade():
    with op.batch_alter_table('expense_account_event_overrides', schema=None) as batch_op:
        batch_op.drop_column('description')
