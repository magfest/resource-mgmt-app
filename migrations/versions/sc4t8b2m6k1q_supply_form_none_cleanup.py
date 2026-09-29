"""Null out "None" text written by the Supply admin edit forms

The item and category edit forms printed NULL columns as the text "None",
and saving unchanged stored it. The forms are fixed alongside this revision;
this cleans rows saved before the fix. r5t9w2x6y3z8 ran the same cleanup once
for imports, which is why rows saved after it were still dirty.

Revision ID: sc4t8b2m6k1q
Revises: nw6a3f92d15e
Create Date: 2026-09-28

"""
from alembic import op
from sqlalchemy import text


revision = 'sc4t8b2m6k1q'
down_revision = 'nw6a3f92d15e'
branch_labels = None
depends_on = None

_TARGETS = (
    ("supply_items", "notes"),
    ("supply_items", "order_guidance"),
    ("supply_items", "location_zone"),
    ("supply_items", "bin_location"),
    ("supply_items", "internal_type"),
    ("supply_categories", "description"),
)


def clean_supply_junk(conn):
    for table, column in _TARGETS:
        conn.execute(text(
            f"UPDATE {table} SET {column} = NULL "
            f"WHERE lower(trim({column})) IN ('none', 'nan', 'null')"
        ))


def upgrade():
    clean_supply_junk(op.get_bind())


def downgrade():
    # The junk values carry no information to restore.
    pass
