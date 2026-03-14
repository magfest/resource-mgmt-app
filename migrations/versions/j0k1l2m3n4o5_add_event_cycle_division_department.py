"""Add event_cycle_divisions and event_cycle_departments tables.

Revision ID: j0k1l2m3n4o5
Revises: i9j0k1l2m3n4
Create Date: 2026-03-13

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'j0k1l2m3n4o5'
down_revision = 'i9j0k1l2m3n4'
branch_labels = None
depends_on = None


def upgrade():
    # Create event_cycle_divisions table
    op.create_table(
        'event_cycle_divisions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('event_cycle_id', sa.Integer(), nullable=False),
        sa.Column('division_id', sa.Integer(), nullable=False),
        sa.Column('is_enabled', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('created_by_user_id', sa.String(length=64), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_by_user_id', sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(['event_cycle_id'], ['event_cycles.id'], name='fk_ecd_event_cycle_id'),
        sa.ForeignKeyConstraint(['division_id'], ['divisions.id'], name='fk_ecd_division_id'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_cycle_id', 'division_id', name='uq_ecdiv_event_div'),
    )
    op.create_index('ix_event_cycle_divisions_event_cycle_id', 'event_cycle_divisions', ['event_cycle_id'])
    op.create_index('ix_event_cycle_divisions_division_id', 'event_cycle_divisions', ['division_id'])

    # Create event_cycle_departments table
    op.create_table(
        'event_cycle_departments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('event_cycle_id', sa.Integer(), nullable=False),
        sa.Column('department_id', sa.Integer(), nullable=False),
        sa.Column('is_enabled', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('created_by_user_id', sa.String(length=64), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False, server_default=sa.text('CURRENT_TIMESTAMP')),
        sa.Column('updated_by_user_id', sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(['event_cycle_id'], ['event_cycles.id'], name='fk_ecdept_event_cycle_id'),
        sa.ForeignKeyConstraint(['department_id'], ['departments.id'], name='fk_ecdept_department_id'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('event_cycle_id', 'department_id', name='uq_ecd_event_dept'),
    )
    op.create_index('ix_event_cycle_departments_event_cycle_id', 'event_cycle_departments', ['event_cycle_id'])
    op.create_index('ix_event_cycle_departments_department_id', 'event_cycle_departments', ['department_id'])


def downgrade():
    op.drop_index('ix_event_cycle_departments_department_id', table_name='event_cycle_departments')
    op.drop_index('ix_event_cycle_departments_event_cycle_id', table_name='event_cycle_departments')
    op.drop_table('event_cycle_departments')

    op.drop_index('ix_event_cycle_divisions_division_id', table_name='event_cycle_divisions')
    op.drop_index('ix_event_cycle_divisions_event_cycle_id', table_name='event_cycle_divisions')
    op.drop_table('event_cycle_divisions')
