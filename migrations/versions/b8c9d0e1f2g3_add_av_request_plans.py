"""Add av_request_plans table.

Revision ID: b8c9d0e1f2g3
Revises: a7b8c9d0e1f2
Create Date: 2026-05-06 12:01:30.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'b8c9d0e1f2g3'
down_revision = 'a7b8c9d0e1f2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'av_request_plans',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('work_item_id', sa.Integer(), nullable=False),
        sa.Column('revision', sa.Integer(), nullable=False),
        sa.Column('gear_spec', sa.Text(), nullable=False),
        sa.Column('planning_notes', sa.Text(), nullable=True),
        sa.Column('authored_by_user_id', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['work_item_id'], ['work_items.id'], name='fk_av_request_plans_work_item_id'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('work_item_id', 'revision', name='uq_av_request_plans_work_item_revision'),
    )
    op.create_index('ix_av_request_plans_work_item_id', 'av_request_plans', ['work_item_id'])


def downgrade():
    op.drop_index('ix_av_request_plans_work_item_id', table_name='av_request_plans')
    op.drop_table('av_request_plans')
