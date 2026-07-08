"""Add order_guidance to supply_items

Short nullable requester-facing hint (e.g. "1 roll covers roughly one
booth setup") rendered next to the qty input in the catalog at decision
time, aimed at once-a-year volunteers who over/under-order.

Revision ID: p3r8t2v7w4x9
Revises: n7q2s8t4u9v3
Create Date: 2026-07-06

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'p3r8t2v7w4x9'
down_revision = 'n7q2s8t4u9v3'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('supply_items', schema=None) as batch_op:
        batch_op.add_column(sa.Column('order_guidance', sa.String(length=160), nullable=True))


def downgrade():
    with op.batch_alter_table('supply_items', schema=None) as batch_op:
        batch_op.drop_column('order_guidance')
