"""Add budget_line_details.account_default_unit_price_cents

Records the expense account's effective default price at the moment an admin
line tool wrote the line. A stored unit price that differs from this snapshot
is a deliberate one-off override.

Nullable with no backfill. Existing rows read NULL, which the badge treats as
"no snapshot" rather than "no override", so historical lines are unaffected.
On PostgreSQL this is a catalog-only change; no table rewrite.

Revision ID: c1d2e3f4a5b6
Revises: r5t9w2x6y3z8
Create Date: 2026-08-03 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c1d2e3f4a5b6'
down_revision = 'r5t9w2x6y3z8'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('budget_line_details') as batch_op:
        batch_op.add_column(
            sa.Column('account_default_unit_price_cents', sa.Integer(), nullable=True)
        )


def downgrade():
    with op.batch_alter_table('budget_line_details') as batch_op:
        batch_op.drop_column('account_default_unit_price_cents')
