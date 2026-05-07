"""Add av_request_details and av_line_details tables.

Revision ID: a7b8c9d0e1f2
Revises: z6a7b8c9d0e1
Create Date: 2026-05-06 12:01:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'a7b8c9d0e1f2'
down_revision = 'z6a7b8c9d0e1'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'av_request_details',
        sa.Column('work_item_id', sa.Integer(), nullable=False),
        sa.Column('space_id', sa.Integer(), nullable=False),
        sa.Column('priority', sa.String(length=32), nullable=False),
        sa.Column('duration_model', sa.String(length=32), nullable=False),
        sa.Column('duration_hours', sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column('duration_slots', sa.Integer(), nullable=True),
        sa.Column('duration_notes', sa.Text(), nullable=True),
        sa.Column('dept_sourced_gear_mode', sa.String(length=16), nullable=False),
        sa.Column('dept_sourced_gear_text', sa.Text(), nullable=True),
        sa.Column('primary_contact_name', sa.String(length=256), nullable=False),
        sa.Column('primary_contact_email', sa.String(length=256), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by_user_id', sa.String(length=64), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('updated_by_user_id', sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(['work_item_id'], ['work_items.id'], name='fk_av_request_details_work_item_id'),
        sa.ForeignKeyConstraint(['space_id'], ['spaces.id'], name='fk_av_request_details_space_id'),
        sa.PrimaryKeyConstraint('work_item_id'),
    )
    op.create_index('ix_av_request_details_space_id', 'av_request_details', ['space_id'])

    op.create_table(
        'av_line_details',
        sa.Column('work_line_id', sa.Integer(), nullable=False),
        sa.Column('description', sa.Text(), nullable=False),
        sa.Column('gear_specificity', sa.String(length=32), nullable=False),
        sa.Column('suggested_gear_text', sa.Text(), nullable=True),
        sa.Column('routed_approval_group_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['work_line_id'], ['work_lines.id'], name='fk_av_line_details_work_line_id'),
        sa.ForeignKeyConstraint(['routed_approval_group_id'], ['approval_groups.id'], name='fk_av_line_details_routed_approval_group_id'),
        sa.PrimaryKeyConstraint('work_line_id'),
    )
    op.create_index('ix_av_line_details_routed_approval_group_id', 'av_line_details', ['routed_approval_group_id'])


def downgrade():
    op.drop_index('ix_av_line_details_routed_approval_group_id', table_name='av_line_details')
    op.drop_table('av_line_details')
    op.drop_index('ix_av_request_details_space_id', table_name='av_request_details')
    op.drop_table('av_request_details')
