"""Add supply_order_spaces: where a supply order will be used

One row per space named on an order, with the display name snapshotted at
save so combined spaces keep their event alias on every page.

Revision ID: so3k8v1n5w2e
Revises: qc7s2p9d4f1a
Create Date: 2026-10-07

"""
from alembic import op
import sqlalchemy as sa


revision = 'so3k8v1n5w2e'
down_revision = 'qc7s2p9d4f1a'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'supply_order_spaces',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('work_item_id', sa.Integer(),
                  sa.ForeignKey('work_items.id', name='fk_supply_order_spaces_work_item_id'),
                  nullable=False),
        sa.Column('space_id', sa.Integer(),
                  sa.ForeignKey('spaces.id', name='fk_supply_order_spaces_space_id'),
                  nullable=False),
        sa.Column('space_label', sa.Text(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by_user_id', sa.String(length=64), nullable=True),
        sa.UniqueConstraint('work_item_id', 'space_id',
                            name='uq_supply_order_spaces_work_item_space'),
    )
    op.create_index('ix_supply_order_spaces_work_item_id',
                    'supply_order_spaces', ['work_item_id'])
    op.create_index('ix_supply_order_spaces_space_id',
                    'supply_order_spaces', ['space_id'])


def downgrade():
    op.drop_index('ix_supply_order_spaces_space_id', table_name='supply_order_spaces')
    op.drop_index('ix_supply_order_spaces_work_item_id', table_name='supply_order_spaces')
    op.drop_table('supply_order_spaces')
