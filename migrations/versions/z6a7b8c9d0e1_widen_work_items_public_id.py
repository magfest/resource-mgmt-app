"""Widen work_items.public_id from VARCHAR(32) to VARCHAR(100)

The deterministic public_id format is:
    {EVENT_CODE}-{DEPT_CODE}-{WORKTYPE_PREFIX}-{SEQ}

With EventCycle.code at String(32), Department.code at String(32), and
WorkTypeConfig.public_id_prefix at String(8), the theoretical max length
is ~80 chars. The original 32-char column silently truncated only the
working-case minimum (short event + short dept codes); longer codes like
"FY27-ANNUAL-OVRH" + "STOPS_COMMIT" overflow and the INSERT fails with
StringDataRightTruncation, producing a 500 with no usable log.

Widening to VARCHAR(100) leaves comfortable headroom and is a metadata-only
change on Postgres (no table rewrite, AccessExclusiveLock held only for
ms). The existing unique index on public_id is preserved automatically.

Revision ID: z6a7b8c9d0e1
Revises: y5z6a7b8c9d0
Create Date: 2026-05-14 21:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'z6a7b8c9d0e1'
down_revision = 'y5z6a7b8c9d0'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('work_items') as batch_op:
        batch_op.alter_column(
            'public_id',
            existing_type=sa.String(length=32),
            type_=sa.String(length=100),
            existing_nullable=True,
        )


def downgrade():
    with op.batch_alter_table('work_items') as batch_op:
        batch_op.alter_column(
            'public_id',
            existing_type=sa.String(length=100),
            type_=sa.String(length=32),
            existing_nullable=True,
        )
