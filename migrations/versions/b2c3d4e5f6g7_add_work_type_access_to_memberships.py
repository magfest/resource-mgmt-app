"""Add work type access to memberships

Revision ID: b2c3d4e5f6g7
Revises: a1b2c3d4e5f6
Create Date: 2026-02-21 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b2c3d4e5f6g7'
down_revision = 'a1b2c3d4e5f6'
branch_labels = None
depends_on = None


def upgrade():
    # Create department_membership_work_type_access table
    op.create_table('department_membership_work_type_access',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('department_membership_id', sa.Integer(), nullable=False),
        sa.Column('work_type_id', sa.Integer(), nullable=False),
        sa.Column('can_view', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('can_edit', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['department_membership_id'], ['department_memberships.id'], name='fk_dmwta_membership_id'),
        sa.ForeignKeyConstraint(['work_type_id'], ['work_types.id'], name='fk_dmwta_work_type_id'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('department_membership_id', 'work_type_id', name='uq_dmwta_membership_work_type')
    )
    op.create_index('ix_dmwta_membership_id', 'department_membership_work_type_access', ['department_membership_id'])
    op.create_index('ix_dmwta_work_type_id', 'department_membership_work_type_access', ['work_type_id'])

    # Create division_membership_work_type_access table
    op.create_table('division_membership_work_type_access',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('division_membership_id', sa.Integer(), nullable=False),
        sa.Column('work_type_id', sa.Integer(), nullable=False),
        sa.Column('can_view', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('can_edit', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['division_membership_id'], ['division_memberships.id'], name='fk_divmwta_membership_id'),
        sa.ForeignKeyConstraint(['work_type_id'], ['work_types.id'], name='fk_divmwta_work_type_id'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('division_membership_id', 'work_type_id', name='uq_divmwta_membership_work_type')
    )
    op.create_index('ix_divmwta_membership_id', 'division_membership_work_type_access', ['division_membership_id'])
    op.create_index('ix_divmwta_work_type_id', 'division_membership_work_type_access', ['work_type_id'])


def downgrade():
    op.drop_index('ix_divmwta_work_type_id', table_name='division_membership_work_type_access')
    op.drop_index('ix_divmwta_membership_id', table_name='division_membership_work_type_access')
    op.drop_table('division_membership_work_type_access')

    op.drop_index('ix_dmwta_work_type_id', table_name='department_membership_work_type_access')
    op.drop_index('ix_dmwta_membership_id', table_name='department_membership_work_type_access')
    op.drop_table('department_membership_work_type_access')
