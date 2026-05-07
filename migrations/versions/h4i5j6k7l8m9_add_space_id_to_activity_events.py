"""Add space_id to activity_events for AV scope filtering.

Revision ID: h4i5j6k7l8m9
Revises: g3h4i5j6k7l8
Create Date: 2026-05-06 12:05:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'h4i5j6k7l8m9'
down_revision = 'g3h4i5j6k7l8'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('activity_events') as batch_op:
        batch_op.add_column(sa.Column('space_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_activity_events_space_id', 'spaces',
            ['space_id'], ['id'],
        )
        batch_op.create_index('ix_activity_events_space_id', ['space_id'])


def downgrade():
    with op.batch_alter_table('activity_events') as batch_op:
        batch_op.drop_index('ix_activity_events_space_id')
        batch_op.drop_constraint('fk_activity_events_space_id', type_='foreignkey')
        batch_op.drop_column('space_id')
