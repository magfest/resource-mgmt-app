"""Add space combination members and retire permanent combos

Revision ID: sp03c4d5e6f7
Revises: sp02b3c4d5e6
"""
import sqlalchemy as sa
from alembic import op

revision = 'sp03c4d5e6f7'
down_revision = 'sp02b3c4d5e6'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'space_combination_members',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('combination_space_id', sa.Integer(), nullable=False),
        sa.Column('member_space_id', sa.Integer(), nullable=False),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by_user_id', sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(['combination_space_id'], ['spaces.id'],
                                name='fk_scm_combination_space_id'),
        sa.ForeignKeyConstraint(['member_space_id'], ['spaces.id'],
                                name='fk_scm_member_space_id'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('combination_space_id', 'member_space_id',
                            name='uq_space_combination_member'),
    )
    op.create_index('ix_space_combination_members_combination_space_id',
                    'space_combination_members', ['combination_space_id'])
    op.create_index('ix_space_combination_members_member_space_id',
                    'space_combination_members', ['member_space_id'])

    # A COMBO without an event was always a room that divides. Combination is
    # now an event decision, so these rows return to what they describe.
    op.execute(
        "UPDATE spaces SET kind = 'ROOM' "
        "WHERE kind = 'COMBO' AND event_cycle_id IS NULL"
    )


def downgrade():
    op.drop_index('ix_space_combination_members_member_space_id',
                  table_name='space_combination_members')
    op.drop_index('ix_space_combination_members_combination_space_id',
                  table_name='space_combination_members')
    op.drop_table('space_combination_members')
    # The ROOM rows are not converted back. Which of them were COMBO is not
    # recorded, and guessing would invent structure the venue does not have.
