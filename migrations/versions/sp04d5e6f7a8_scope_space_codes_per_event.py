"""Scope space codes per event

Revision ID: sp04d5e6f7a8
Revises: sp03c4d5e6f7
"""
import sqlalchemy as sa
from alembic import op

revision = 'sp04d5e6f7a8'
down_revision = 'sp03c4d5e6f7'
branch_labels = None
depends_on = None


def upgrade():
    # batch_alter_table: SQLite cannot drop a constraint in place.
    with op.batch_alter_table('spaces') as batch_op:
        batch_op.drop_constraint('uq_space_code_per_venue', type_='unique')

    op.create_index('ix_spaces_code_permanent', 'spaces',
                    ['venue_id', 'code'], unique=True,
                    sqlite_where=sa.text('event_cycle_id IS NULL'),
                    postgresql_where=sa.text('event_cycle_id IS NULL'))
    op.create_index('ix_spaces_code_per_event', 'spaces',
                    ['venue_id', 'code', 'event_cycle_id'], unique=True,
                    sqlite_where=sa.text('event_cycle_id IS NOT NULL'),
                    postgresql_where=sa.text('event_cycle_id IS NOT NULL'))


def downgrade():
    # This fails once two events share a code, which the upgrade exists to
    # allow. Reconcile the duplicates by hand before downgrading.
    op.drop_index('ix_spaces_code_per_event', table_name='spaces')
    op.drop_index('ix_spaces_code_permanent', table_name='spaces')
    with op.batch_alter_table('spaces') as batch_op:
        batch_op.create_unique_constraint('uq_space_code_per_venue',
                                          ['venue_id', 'code'])
