"""Add quantity_confidence to supply order lines

Requesters rate how sure they are of each quantity. The column is nullable:
lines submitted before it existed have no answer, and drafts fill it in
before submit.

Revision ID: qc7s2p9d4f1a
Revises: sc4t8b2m6k1q
Create Date: 2026-10-07

"""
from alembic import op
import sqlalchemy as sa


revision = 'qc7s2p9d4f1a'
down_revision = 'sc4t8b2m6k1q'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('supply_order_line_details') as batch_op:
        batch_op.add_column(sa.Column('quantity_confidence', sa.String(length=16), nullable=True))


def downgrade():
    with op.batch_alter_table('supply_order_line_details') as batch_op:
        batch_op.drop_column('quantity_confidence')
