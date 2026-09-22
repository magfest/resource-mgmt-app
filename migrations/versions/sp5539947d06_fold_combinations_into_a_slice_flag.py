"""Fold combinations into a slice flag

A combination stops being its own spaces row. It becomes
space_event_overrides.combined_into_space_id, a nullable FK from a slice to
the slice it folds into for one event. space_combination_members and the
COMBO kind are retired.

Revision ID: sp5539947d06
Revises: sp04d5e6f7a8
"""
import sqlalchemy as sa
from alembic import op

revision = 'sp5539947d06'
down_revision = 'sp04d5e6f7a8'
branch_labels = None
depends_on = None


def upgrade():
    # A COMBO row and everything hanging off it describe a decision, not a
    # building. They exist only on dev databases; the repository owner's
    # has none as of 2026-09-21, verified. Children first, so a dependent
    # row is never left pointing at a space this migration is about to
    # delete.
    op.execute(
        "DELETE FROM space_assignments WHERE space_id IN "
        "(SELECT id FROM spaces WHERE kind = 'COMBO')"
    )
    op.execute(
        "DELETE FROM space_event_overrides WHERE space_id IN "
        "(SELECT id FROM spaces WHERE kind = 'COMBO')"
    )
    op.execute("DELETE FROM spaces WHERE kind = 'COMBO'")

    op.drop_index('ix_space_combination_members_member_space_id',
                  table_name='space_combination_members')
    op.drop_index('ix_space_combination_members_combination_space_id',
                  table_name='space_combination_members')
    op.drop_table('space_combination_members')

    with op.batch_alter_table('space_event_overrides', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('combined_into_space_id', sa.Integer(), nullable=True))
        batch_op.create_foreign_key(
            'fk_space_event_overrides_combined_into_space_id',
            'spaces', ['combined_into_space_id'], ['id'],
        )
        batch_op.create_index(
            batch_op.f('ix_space_event_overrides_combined_into_space_id'),
            ['combined_into_space_id'],
        )


def downgrade():
    # Recreates the column and the table. Cannot recover the COMBO rows and
    # their assignments and overrides; those were deleted, not converted,
    # and downgrading after upgrading on a database that had any is data
    # loss the schema change cannot undo.
    with op.batch_alter_table('space_event_overrides', schema=None) as batch_op:
        batch_op.drop_index(
            batch_op.f('ix_space_event_overrides_combined_into_space_id'))
        batch_op.drop_constraint(
            'fk_space_event_overrides_combined_into_space_id',
            type_='foreignkey')
        batch_op.drop_column('combined_into_space_id')

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
