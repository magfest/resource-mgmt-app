"""Fix activity_events.id and notification_logs.id PK type for SQLite.

These columns were originally created as BigInteger, which SQLite cannot
autoincrement — INSERTs hit "NOT NULL constraint failed: <table>.id".
The SQLAlchemy model uses BigInteger().with_variant(Integer, "sqlite")
which works for db.create_all() in tests, but doesn't migrate existing
tables created by the original schema migration.

This migration rebuilds both tables on SQLite via batch_alter_table to
change id type to Integer. No-op on PostgreSQL (BIGINT autoincrement
works fine there and we don't want to downgrade the type in prod).

Revision ID: i5j6k7l8m9n0
Revises: h4i5j6k7l8m9
Create Date: 2026-05-07 03:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'i5j6k7l8m9n0'
down_revision = 'h4i5j6k7l8m9'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    if bind.dialect.name != 'sqlite':
        # PostgreSQL BIGSERIAL/BIGINT-IDENTITY autoincrement works fine.
        # No-op here so we don't risk downgrading column types in prod.
        return

    # SQLite: rebuild both tables with Integer PK for autoincrement to work.
    with op.batch_alter_table('activity_events') as batch_op:
        batch_op.alter_column(
            'id',
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
            existing_nullable=False,
        )

    with op.batch_alter_table('notification_logs') as batch_op:
        batch_op.alter_column(
            'id',
            existing_type=sa.BigInteger(),
            type_=sa.Integer(),
            existing_nullable=False,
        )


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != 'sqlite':
        return

    with op.batch_alter_table('activity_events') as batch_op:
        batch_op.alter_column(
            'id',
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
            existing_nullable=False,
        )

    with op.batch_alter_table('notification_logs') as batch_op:
        batch_op.alter_column(
            'id',
            existing_type=sa.Integer(),
            type_=sa.BigInteger(),
            existing_nullable=False,
        )
