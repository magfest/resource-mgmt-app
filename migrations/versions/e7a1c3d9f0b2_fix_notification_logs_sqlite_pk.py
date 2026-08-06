"""Fix notification_logs.id for SQLite autoincrement

SQLite's rowid autoincrement alias only applies to a column declared exactly
INTEGER PRIMARY KEY. notification_logs.id was created as plain BIGINT PRIMARY
KEY (see 9f9a0ec53783), which SQLite treats as an ordinary column: every
insert that omits id fails NOT NULL. Undiscovered until a test exercised a
real send_email() call against SQLite instead of mocking it away.

Postgres (prod) has no such quirk and keeps BIGINT; the model's
with_variant(Integer(), "sqlite") only changes what SQLite sees, so this
migration is effectively a no-op there.

Revision ID: e7a1c3d9f0b2
Revises: d4b8f2a6c9e1
Create Date: 2026-08-05 18:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'e7a1c3d9f0b2'
down_revision = 'd4b8f2a6c9e1'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('notification_logs') as batch_op:
        batch_op.alter_column(
            'id',
            existing_type=sa.BigInteger(),
            type_=sa.BigInteger().with_variant(sa.Integer(), 'sqlite'),
            existing_nullable=False,
        )


def downgrade():
    with op.batch_alter_table('notification_logs') as batch_op:
        batch_op.alter_column(
            'id',
            existing_type=sa.BigInteger().with_variant(sa.Integer(), 'sqlite'),
            type_=sa.BigInteger(),
            existing_nullable=False,
        )
