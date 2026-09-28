"""Correct the PHONE_NUMBER service description

Revision ID: pd5c19e4a7b3
Revises: ws8841d5f27c

Off-sequence id on purpose, to avoid a head collision with concurrent
migration work.

The room-first migration wrote this row's description, and the phone block
rebuild made every clause of it wrong. There is no purpose question and no
caller ID field, and desk phones are asked for inside the phone block rather
than somewhere else. The last sentence costs the most: it sends a requester
looking for a separate handset section that does not exist.

The seed carries the corrected wording for a fresh database, but
seed_techops_service_types is insert-only, so an existing row keeps whatever
it has. This migration is what moves the rows already out there.

Guarded on the exact old text. A description edited by hand through the admin
page is left alone, in either direction, because an operator's edit outranks
this one.
"""
import sqlalchemy as sa
from alembic import op

revision = 'pd5c19e4a7b3'
down_revision = 'ws8841d5f27c'
branch_labels = None
depends_on = None

# Verbatim from tr7742b1c8e5, which inserted the row. Matching on the whole
# string rather than on the code alone is what makes this safe to run against
# a database somebody has already corrected by hand.
OLD_DESCRIPTION = (
    "One number to configure. Purpose, caller ID, and how calls "
    "are delivered. Handsets are requested separately."
)

# Verbatim from app/seeds/bootstrap.py. The two must agree, or a fresh
# database and a migrated one describe the same service differently.
NEW_DESCRIPTION = (
    "One phone number and the desk phones that ring it. Say what "
    "it needs to do and where the phones sit."
)


def _retext(conn, old: str, new: str) -> None:
    conn.execute(
        sa.text(
            "UPDATE techops_service_types SET description = :new "
            "WHERE code = 'PHONE_NUMBER' AND description = :old"
        ),
        {"new": new, "old": old},
    )


def correct_phone_number_description(conn):
    """Forward data change, importable so tests can drive it directly."""
    _retext(conn, OLD_DESCRIPTION, NEW_DESCRIPTION)


def restore_phone_number_description(conn):
    """Inverse, guarded the same way."""
    _retext(conn, NEW_DESCRIPTION, OLD_DESCRIPTION)


def upgrade():
    correct_phone_number_description(op.get_bind())


def downgrade():
    restore_phone_number_description(op.get_bind())
