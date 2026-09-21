"""Add space event overrides and assignments

Revision ID: sp02b3c4d5e6
Revises: sp01a2b3c4d5
"""
import sqlalchemy as sa
from alembic import op

revision = 'sp02b3c4d5e6'
down_revision = 'sp01a2b3c4d5'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'space_event_overrides',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('space_id', sa.Integer(), nullable=False),
        sa.Column('event_cycle_id', sa.Integer(), nullable=False),
        sa.Column('alias', sa.String(length=128), nullable=True),
        sa.Column('is_available', sa.Boolean(), nullable=False),
        sa.Column('unavailable_reason', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by_user_id', sa.String(length=64), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('updated_by_user_id', sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(['space_id'], ['spaces.id'],
                                name='fk_space_event_overrides_space_id'),
        sa.ForeignKeyConstraint(['event_cycle_id'], ['event_cycles.id'],
                                name='fk_space_event_overrides_event_cycle_id'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('space_id', 'event_cycle_id',
                            name='uq_space_event_override'),
    )
    op.create_index('ix_space_event_overrides_space_id', 'space_event_overrides',
                    ['space_id'])
    op.create_index('ix_space_event_overrides_event_cycle_id',
                    'space_event_overrides', ['event_cycle_id'])

    op.create_table(
        'space_assignments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('space_id', sa.Integer(), nullable=False),
        sa.Column('event_cycle_id', sa.Integer(), nullable=False),
        sa.Column('department_id', sa.Integer(), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by_user_id', sa.String(length=64), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('updated_by_user_id', sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(['space_id'], ['spaces.id'],
                                name='fk_space_assignments_space_id'),
        sa.ForeignKeyConstraint(['event_cycle_id'], ['event_cycles.id'],
                                name='fk_space_assignments_event_cycle_id'),
        sa.ForeignKeyConstraint(['department_id'], ['departments.id'],
                                name='fk_space_assignments_department_id'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('space_id', 'event_cycle_id', 'department_id',
                            name='uq_space_assignment_per_event'),
    )
    op.create_index('ix_space_assignments_space_id', 'space_assignments',
                    ['space_id'])
    op.create_index('ix_space_assignments_event_cycle_id', 'space_assignments',
                    ['event_cycle_id'])
    op.create_index('ix_space_assignments_department_id', 'space_assignments',
                    ['department_id'])


def downgrade():
    op.drop_index('ix_space_assignments_department_id',
                  table_name='space_assignments')
    op.drop_index('ix_space_assignments_event_cycle_id',
                  table_name='space_assignments')
    op.drop_index('ix_space_assignments_space_id', table_name='space_assignments')
    op.drop_table('space_assignments')

    op.drop_index('ix_space_event_overrides_event_cycle_id',
                  table_name='space_event_overrides')
    op.drop_index('ix_space_event_overrides_space_id',
                  table_name='space_event_overrides')
    op.drop_table('space_event_overrides')
