"""Add next_public_id_seq to work_portfolios

Revision ID: n4o5p6q7r8s9
Revises: m3n4o5p6q7r8
Create Date: 2026-03-23 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'n4o5p6q7r8s9'
down_revision = 'm3n4o5p6q7r8'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('work_portfolios', schema=None) as batch_op:
        batch_op.add_column(sa.Column('next_public_id_seq', sa.Integer(), nullable=False, server_default='1'))

    # Remove server default after column is populated
    with op.batch_alter_table('work_portfolios', schema=None) as batch_op:
        batch_op.alter_column('next_public_id_seq', server_default=None)


def downgrade():
    with op.batch_alter_table('work_portfolios', schema=None) as batch_op:
        batch_op.drop_column('next_public_id_seq')
