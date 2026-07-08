"""Supply order details table + drop line delivery columns

Revision ID: h8t3v6w1x9y4
Revises: k4m9p2q7r1s6
Create Date: 2026-07-06

"""
from alembic import op
import sqlalchemy as sa


revision = 'h8t3v6w1x9y4'
down_revision = 'k4m9p2q7r1s6'
branch_labels = None
depends_on = None


def upgrade():
    # Create supply_order_details table (order-level delivery details)
    op.create_table('supply_order_details',
    sa.Column('work_item_id', sa.Integer(), nullable=False),
    sa.Column('needed_by_date', sa.Date(), nullable=True),
    sa.Column('delivery_location', sa.String(length=256), nullable=True),
    sa.Column('additional_notes', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(), nullable=False),
    sa.Column('created_by_user_id', sa.String(length=64), nullable=True),
    sa.Column('updated_at', sa.DateTime(), nullable=False),
    sa.Column('updated_by_user_id', sa.String(length=64), nullable=True),
    sa.ForeignKeyConstraint(['work_item_id'], ['work_items.id'], name='fk_supply_order_details_work_item_id'),
    sa.PrimaryKeyConstraint('work_item_id')
    )

    # Drop delivery columns from supply_order_line_details
    with op.batch_alter_table('supply_order_line_details', schema=None) as batch_op:
        batch_op.drop_column('needed_by_date')
        batch_op.drop_column('delivery_location')


def downgrade():
    # Re-add columns to supply_order_line_details
    with op.batch_alter_table('supply_order_line_details', schema=None) as batch_op:
        batch_op.add_column(sa.Column('delivery_location', sa.String(length=256), nullable=True))
        batch_op.add_column(sa.Column('needed_by_date', sa.Date(), nullable=True))

    # Drop supply_order_details table
    op.drop_table('supply_order_details')
