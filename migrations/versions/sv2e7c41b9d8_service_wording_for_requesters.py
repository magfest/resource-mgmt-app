"""Reword the WiFi, radio and catch-all services for requesters

Revision ID: sv2e7c41b9d8
Revises: pd5c19e4a7b3

Off-sequence id on purpose, to avoid a head collision with concurrent
migration work.

These three rows are read by a department head filling the form once a year,
and each was written for someone who already knows how TechOps works. WiFi
offered attendee access as an equal option, so "it is for attendees" became a
throwaway answer. The radio row named the service and said nothing about who
needs one, and anything offered without guidance gets requested in case it is
useful. "Other / consultation" hid the one place on the form where you can
ask for help behind a word nobody uses.

seed_techops_service_types is insert-only, so the seed alone never reaches a
database that already exists. This migration moves those.

Guarded on the exact old text, so a row edited by hand through the admin page
is left alone in either direction. An operator's wording outranks this one.

ETHERNET is deliberately not here. Its block is being rebuilt, and two
migrations editing one row is churn with no gain.
"""
import sqlalchemy as sa
from alembic import op

revision = 'sv2e7c41b9d8'
down_revision = 'pd5c19e4a7b3'
branch_labels = None
depends_on = None

# (code, column, old value, new value). Verbatim from the seed as it stood
# before this change, and from the seed as it stands after.
REWORDINGS = (
    (
        "WIFI", "name",
        "WiFi access/coverage",
        "WiFi coverage / access",
    ),
    (
        "WIFI", "description",
        "WiFi coverage for staff or attendees in a specific area or for a "
        "use case. Call out heavy bandwidth needs (streaming, large "
        "transfers, attendees on network) in the description.",
        "Staff and event ops WiFi for a space. Attendee access is rare and "
        "needs a specific reason. Call out heavy bandwidth needs such as "
        "streaming or large transfers.",
    ),
    (
        "RADIO_CHANNEL", "description",
        "Reserved channel on the event radio system",
        "Most departments use the general channel, and there are shared "
        "channels free for a quick side conversation. Dedicated channels "
        "suit teams with constant traffic, like concert security or "
        "logistics. If you think one would help, ask here.",
    ),
    (
        "OTHER", "name",
        "Other / consultation",
        "Something else, or ask TechOps for advice",
    ),
    (
        "OTHER", "description",
        "Anything not covered above, including general consultation requests",
        "Anything not covered above. Use this to ask TechOps to talk "
        "something through with you.",
    ),
)


def _apply(conn, forward: bool) -> None:
    for code, column, old, new in REWORDINGS:
        # The column name is interpolated from this module's own tuple, never
        # from anything a caller supplies.
        conn.execute(
            sa.text(
                f"UPDATE techops_service_types SET {column} = :new "
                f"WHERE code = :code AND {column} = :old"
            ),
            {
                "code": code,
                "new": new if forward else old,
                "old": old if forward else new,
            },
        )


def reword_services_for_requesters(conn):
    """Forward data change, importable so tests can drive it directly."""
    _apply(conn, forward=True)


def restore_service_wording(conn):
    """Inverse, guarded the same way."""
    _apply(conn, forward=False)


def upgrade():
    reword_services_for_requesters(op.get_bind())


def downgrade():
    restore_service_wording(op.get_bind())
