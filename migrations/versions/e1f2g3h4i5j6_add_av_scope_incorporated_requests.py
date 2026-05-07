"""Add av_scope_incorporated_requests link table.

Revision ID: e1f2g3h4i5j6
Revises: d0e1f2g3h4i5
Create Date: 2026-05-06 12:03:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'e1f2g3h4i5j6'
down_revision = 'd0e1f2g3h4i5'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'av_scope_incorporated_requests',
        sa.Column('scope_id', sa.Integer(), nullable=False),
        sa.Column('work_item_id', sa.Integer(), nullable=False),
        sa.Column('incorporated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['scope_id'], ['av_scopes.id'], name='fk_av_scope_incorporated_requests_scope_id'),
        sa.ForeignKeyConstraint(['work_item_id'], ['work_items.id'], name='fk_av_scope_incorporated_requests_work_item_id'),
        sa.PrimaryKeyConstraint('scope_id', 'work_item_id'),
    )


def downgrade():
    op.drop_table('av_scope_incorporated_requests')
