"""Store the wired-network answer on each TechOps space card

"No" leaves no ETHERNET line behind, so without its own column the answer
reloads as unanswered. Existing rows stay NULL; the form falls back to the
ETHERNET line for them.

Revision ID: tn5w9c3k7r2p
Revises: td4n8w2q6v3h
Create Date: 2026-10-21

"""
import sqlalchemy as sa
from alembic import op

revision = 'tn5w9c3k7r2p'
down_revision = 'td4n8w2q6v3h'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('techops_request_spaces') as batch_op:
        batch_op.add_column(
            sa.Column('network_needed', sa.String(length=8), nullable=True))


def downgrade():
    with op.batch_alter_table('techops_request_spaces') as batch_op:
        batch_op.drop_column('network_needed')
