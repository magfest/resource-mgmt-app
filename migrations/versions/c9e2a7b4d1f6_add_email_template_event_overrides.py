"""Add email_template_event_overrides

Revision ID: c9e2a7b4d1f6
Revises: c5e9a2b7d34f
"""
import sqlalchemy as sa
from alembic import op

revision = 'c9e2a7b4d1f6'
down_revision = 'c5e9a2b7d34f'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'email_template_event_overrides',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email_template_id', sa.Integer(), nullable=False),
        sa.Column('event_cycle_id', sa.Integer(), nullable=False),
        sa.Column('subject', sa.String(length=256), nullable=True),
        sa.Column('body_text', sa.Text(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=True),
        sa.Column('send_window_start', sa.DateTime(), nullable=True),
        sa.Column('send_window_end', sa.DateTime(), nullable=True),
        sa.Column('base_version_at_override', sa.Integer(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by_user_id', sa.String(length=64), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('updated_by_user_id', sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(['email_template_id'], ['email_templates.id'],
                                name='fk_eteo_email_template_id'),
        sa.ForeignKeyConstraint(['event_cycle_id'], ['event_cycles.id'],
                                name='fk_eteo_event_cycle_id'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email_template_id', 'event_cycle_id',
                            name='uq_email_template_event_override'),
    )
    op.create_index('ix_eteo_email_template_id',
                    'email_template_event_overrides', ['email_template_id'])
    op.create_index('ix_eteo_event_cycle_id',
                    'email_template_event_overrides', ['event_cycle_id'])


def downgrade():
    op.drop_index('ix_eteo_event_cycle_id',
                  table_name='email_template_event_overrides')
    op.drop_index('ix_eteo_email_template_id',
                  table_name='email_template_event_overrides')
    op.drop_table('email_template_event_overrides')
