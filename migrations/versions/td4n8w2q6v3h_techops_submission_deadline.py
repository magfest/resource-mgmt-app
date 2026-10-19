"""TechOps request deadline on event cycles

A department may return its own submitted TechOps request to draft until this
date. The column is nullable, and a missing date keeps the earlier rule:
pull-back stays open until TechOps starts reviewing.

Revision ID: td4n8w2q6v3h
Revises: sb7q2m4x9k1d
Create Date: 2026-10-18

"""
import sqlalchemy as sa
from alembic import op

revision = 'td4n8w2q6v3h'
down_revision = 'sb7q2m4x9k1d'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('event_cycles') as batch_op:
        batch_op.add_column(
            sa.Column('techops_submission_deadline', sa.Date(), nullable=True))


def downgrade():
    with op.batch_alter_table('event_cycles') as batch_op:
        batch_op.drop_column('techops_submission_deadline')
