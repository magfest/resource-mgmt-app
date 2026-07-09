"""Null out junk placeholder strings in supply_items text columns

Earlier CSV imports stored blank cells as literal 'None'/'nan' strings
(visible under the catalog qty inputs). The parser is hardened as of
this release; this cleans rows imported before the fix.

Revision ID: r5t9w2x6y3z8
Revises: q9w4e7r2t5y8
Create Date: 2026-07-08

"""
from alembic import op


# revision identifiers, used by Alembic.
revision = 'r5t9w2x6y3z8'
down_revision = 'q9w4e7r2t5y8'
branch_labels = None
depends_on = None

_COLUMNS = ("notes", "order_guidance", "location_zone", "bin_location")


def upgrade():
    for column in _COLUMNS:
        op.execute(
            f"UPDATE supply_items SET {column} = NULL "
            f"WHERE lower(trim({column})) IN ('none', 'nan', 'null')"
        )


def downgrade():
    # Data cleanup only — nothing sensible to restore.
    pass
