"""Add venues and spaces

Revision ID: sp01a2b3c4d5
Revises: c9e2a7b4d1f6
"""
import sqlalchemy as sa
from alembic import op

revision = 'sp01a2b3c4d5'
down_revision = 'c9e2a7b4d1f6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'venues',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(length=32), nullable=False),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by_user_id', sa.String(length=64), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('updated_by_user_id', sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('code', name='uq_venues_code'),
    )
    op.create_index('ix_venues_code', 'venues', ['code'])

    op.create_table(
        'spaces',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('venue_id', sa.Integer(), nullable=False),
        sa.Column('parent_id', sa.Integer(), nullable=True),
        sa.Column('event_cycle_id', sa.Integer(), nullable=True),
        sa.Column('name', sa.String(length=128), nullable=False),
        sa.Column('code', sa.String(length=32), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('dimensions', sa.Text(), nullable=True),
        sa.Column('area_sqft', sa.Integer(), nullable=True),
        sa.Column('location_note', sa.Text(), nullable=True),
        sa.Column('sort_order', sa.Integer(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by_user_id', sa.String(length=64), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('updated_by_user_id', sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(['venue_id'], ['venues.id'],
                                name='fk_spaces_venue_id'),
        sa.ForeignKeyConstraint(['parent_id'], ['spaces.id'],
                                name='fk_spaces_parent_id'),
        sa.ForeignKeyConstraint(['event_cycle_id'], ['event_cycles.id'],
                                name='fk_spaces_event_cycle_id'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('venue_id', 'code', name='uq_space_code_per_venue'),
    )
    op.create_index('ix_spaces_venue_id', 'spaces', ['venue_id'])
    op.create_index('ix_spaces_parent_id', 'spaces', ['parent_id'])
    op.create_index('ix_spaces_event_cycle_id', 'spaces', ['event_cycle_id'])

    # batch_alter_table: SQLite cannot add a foreign key with ALTER TABLE.
    with op.batch_alter_table('event_cycles') as batch_op:
        batch_op.add_column(sa.Column('venue_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key('fk_event_cycles_venue_id', 'venues',
                                    ['venue_id'], ['id'])
        batch_op.create_index('ix_event_cycles_venue_id', ['venue_id'])


def downgrade():
    with op.batch_alter_table('event_cycles') as batch_op:
        batch_op.drop_index('ix_event_cycles_venue_id')
        batch_op.drop_constraint('fk_event_cycles_venue_id', type_='foreignkey')
        batch_op.drop_column('venue_id')

    op.drop_index('ix_spaces_event_cycle_id', table_name='spaces')
    op.drop_index('ix_spaces_parent_id', table_name='spaces')
    op.drop_index('ix_spaces_venue_id', table_name='spaces')
    op.drop_table('spaces')

    op.drop_index('ix_venues_code', table_name='venues')
    op.drop_table('venues')
