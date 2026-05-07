"""Add av_scopes table.

Revision ID: c9d0e1f2g3h4
Revises: b8c9d0e1f2g3
Create Date: 2026-05-06 12:02:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'c9d0e1f2g3h4'
down_revision = 'b8c9d0e1f2g3'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'av_scopes',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('space_id', sa.Integer(), nullable=False),
        sa.Column('version', sa.Integer(), nullable=False),
        sa.Column('state', sa.String(length=32), nullable=False),
        sa.Column('scope_text', sa.Text(), nullable=False),
        sa.Column('changes_since_previous_text', sa.Text(), nullable=True),
        sa.Column('authored_by_user_id', sa.String(length=64), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('published_at', sa.DateTime(), nullable=True),
        sa.Column('locked_at', sa.DateTime(), nullable=True),
        sa.Column('locked_by_user_id', sa.String(length=64), nullable=True),
        sa.Column('force_locked', sa.Boolean(), nullable=False),
        sa.Column('force_lock_reason', sa.Text(), nullable=True),
        sa.Column('superseded_at', sa.DateTime(), nullable=True),
        sa.Column('superseded_by_scope_id', sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(['space_id'], ['spaces.id'], name='fk_av_scopes_space_id'),
        sa.ForeignKeyConstraint(['superseded_by_scope_id'], ['av_scopes.id'], name='fk_av_scopes_superseded_by_scope_id'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('space_id', 'version', name='uq_av_scopes_space_version'),
    )
    op.create_index('ix_av_scopes_space_id', 'av_scopes', ['space_id'])
    op.create_index('ix_av_scopes_space_state', 'av_scopes', ['space_id', 'state'])


def downgrade():
    op.drop_index('ix_av_scopes_space_state', table_name='av_scopes')
    op.drop_index('ix_av_scopes_space_id', table_name='av_scopes')
    op.drop_table('av_scopes')
