"""Add av_ack_sla_days to event_cycles.

Default 7 days. Used for advisory SLA countdown on AVScope OPEN_FOR_INPUT.

Revision ID: f2g3h4i5j6k7
Revises: e1f2g3h4i5j6
Create Date: 2026-05-06 12:03:30.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'f2g3h4i5j6k7'
down_revision = 'e1f2g3h4i5j6'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'event_cycles',
        sa.Column('av_ack_sla_days', sa.Integer(), nullable=False, server_default='7'),
    )
    # Drop server_default after backfill — column has Python default for new rows.
    # SQLite does not support ALTER COLUMN directly; use batch_alter_table.
    with op.batch_alter_table('event_cycles') as batch_op:
        batch_op.alter_column('av_ack_sla_days', server_default=None)


def downgrade():
    op.drop_column('event_cycles', 'av_ack_sla_days')
