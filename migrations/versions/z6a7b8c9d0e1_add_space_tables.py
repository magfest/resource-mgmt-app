"""Add spaces and space_department_assignments tables.

Revision ID: z6a7b8c9d0e1
Revises: y5z6a7b8c9d0
Create Date: 2026-05-06 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'z6a7b8c9d0e1'
down_revision = 'y5z6a7b8c9d0'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'spaces',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('event_cycle_id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=64), nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('location', sa.String(length=256), nullable=True),
        sa.Column('square_feet', sa.Integer(), nullable=True),
        sa.Column('push_in_at', sa.DateTime(), nullable=True),
        sa.Column('push_out_at', sa.DateTime(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by_user_id', sa.String(length=64), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('updated_by_user_id', sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(['event_cycle_id'], ['event_cycles.id'], name='fk_spaces_event_cycle_id'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_cycle_id', 'code', name='uq_spaces_event_cycle_id_code'),
    )
    op.create_index('ix_spaces_event_cycle_id', 'spaces', ['event_cycle_id'])

    op.create_table(
        'space_department_assignments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('space_id', sa.Integer(), nullable=False),
        sa.Column('department_id', sa.Integer(), nullable=False),
        sa.Column('assigned_at', sa.DateTime(), nullable=False),
        sa.Column('assigned_by_user_id', sa.String(length=64), nullable=False),
        sa.Column('unassigned_at', sa.DateTime(), nullable=True),
        sa.Column('unassigned_by_user_id', sa.String(length=64), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['space_id'], ['spaces.id'], name='fk_space_department_assignments_space_id'),
        sa.ForeignKeyConstraint(['department_id'], ['departments.id'], name='fk_space_department_assignments_department_id'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_space_department_assignments_space_id', 'space_department_assignments', ['space_id'])
    op.create_index('ix_space_department_assignments_department_id', 'space_department_assignments', ['department_id'])
    op.create_index('ix_space_department_assignments_space_dept', 'space_department_assignments', ['space_id', 'department_id'])
    op.create_index('ix_space_department_assignments_active', 'space_department_assignments', ['department_id', 'unassigned_at'])


def downgrade():
    op.drop_index('ix_space_department_assignments_active', table_name='space_department_assignments')
    op.drop_index('ix_space_department_assignments_space_dept', table_name='space_department_assignments')
    op.drop_index('ix_space_department_assignments_department_id', table_name='space_department_assignments')
    op.drop_index('ix_space_department_assignments_space_id', table_name='space_department_assignments')
    op.drop_table('space_department_assignments')
    op.drop_index('ix_spaces_event_cycle_id', table_name='spaces')
    op.drop_table('spaces')
