"""Supply: pickup_time replaces needed_by_date + delivery_location

Departments pick orders up from a staging location (communicated before
the event) — they are not delivered. The requester now picks a pickup
slot from a hardcoded list; the display string is stored as a snapshot.

Data loss note: existing staging/dev orders lose their delivery details.
Accepted — no prod deployment of SUPPLY exists yet.

Revision ID: q9w4e7r2t5y8
Revises: p3r8t2v7w4x9
Create Date: 2026-07-08

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'q9w4e7r2t5y8'
down_revision = 'p3r8t2v7w4x9'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('supply_order_details', schema=None) as batch_op:
        batch_op.add_column(sa.Column('pickup_time', sa.String(length=120), nullable=True))
        batch_op.drop_column('delivery_location')
        batch_op.drop_column('needed_by_date')


def downgrade():
    with op.batch_alter_table('supply_order_details', schema=None) as batch_op:
        batch_op.add_column(sa.Column('needed_by_date', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('delivery_location', sa.String(length=256), nullable=True))
        batch_op.drop_column('pickup_time')
