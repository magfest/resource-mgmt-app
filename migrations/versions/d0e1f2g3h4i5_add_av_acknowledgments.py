"""Add av_acknowledgments table.

Revision ID: d0e1f2g3h4i5
Revises: c9d0e1f2g3h4
Create Date: 2026-05-06 12:02:30.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'd0e1f2g3h4i5'
down_revision = 'c9d0e1f2g3h4'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'av_acknowledgments',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('scope_id', sa.Integer(), nullable=False),
        sa.Column('department_id', sa.Integer(), nullable=False),
        sa.Column('state', sa.String(length=32), nullable=False),
        sa.Column('acknowledged_by_user_id', sa.String(length=64), nullable=True),
        sa.Column('acknowledged_at', sa.DateTime(), nullable=True),
        sa.Column('concern_text', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['scope_id'], ['av_scopes.id'], name='fk_av_acknowledgments_scope_id'),
        sa.ForeignKeyConstraint(['department_id'], ['departments.id'], name='fk_av_acknowledgments_department_id'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('scope_id', 'department_id', name='uq_av_acknowledgments_scope_dept'),
    )
    op.create_index('ix_av_acknowledgments_scope_id', 'av_acknowledgments', ['scope_id'])
    op.create_index('ix_av_acknowledgments_department_id', 'av_acknowledgments', ['department_id'])
    op.create_index('ix_av_acknowledgments_dept_state', 'av_acknowledgments', ['department_id', 'state'])


def downgrade():
    op.drop_index('ix_av_acknowledgments_dept_state', table_name='av_acknowledgments')
    op.drop_index('ix_av_acknowledgments_department_id', table_name='av_acknowledgments')
    op.drop_index('ix_av_acknowledgments_scope_id', table_name='av_acknowledgments')
    op.drop_table('av_acknowledgments')
