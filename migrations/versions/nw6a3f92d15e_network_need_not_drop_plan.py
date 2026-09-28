"""Reword the wired network service and stop treating it as per-drop

Revision ID: nw6a3f92d15e
Revises: sv2e7c41b9d8

Off-sequence id on purpose, to avoid a head collision with concurrent
migration work.

The form asked how many network drops a room needs and where each one goes.
That is an installation plan, and a department head has no way to make it: a
parent room may need one drop with cables run between sections where a
broadcast stage needs four. The requester now says what needs a cable and
the network team decides the drops, so the service is one line per room
rather than one per drop.

`instance_noun` is cleared for the same reason. It marked ETHERNET as a
repeating group, and there is no longer anything to repeat.

Lines saved before this are left exactly as they are. They remain valid,
the reviewer templates render either shape, and replace_lines rewrites a
room's lines in the new shape the next time anybody saves it. A data
migration would rewrite real requests to buy nothing.

Guarded on the exact old text, so a description edited by hand through the
admin page is left alone in either direction.
"""
import sqlalchemy as sa
from alembic import op

revision = 'nw6a3f92d15e'
down_revision = 'sv2e7c41b9d8'
branch_labels = None
depends_on = None

OLD_NAME = "Hardwired ethernet"
NEW_NAME = "Wired network"

OLD_DESCRIPTION = (
    "Wired network drop at a specific location for a specific use. Call out "
    "heavy bandwidth needs (streaming, large transfers) in the per-drop "
    "usage notes."
)
NEW_DESCRIPTION = (
    "Tell us what needs a cable and what it does. The network team works out "
    "how many drops that takes."
)


def _retext(conn, old_name, new_name, old_desc, new_desc, instance_noun):
    conn.execute(
        sa.text(
            "UPDATE techops_service_types SET name = :new_name "
            "WHERE code = 'ETHERNET' AND name = :old_name"
        ),
        {"new_name": new_name, "old_name": old_name},
    )
    conn.execute(
        sa.text(
            "UPDATE techops_service_types SET description = :new_desc "
            "WHERE code = 'ETHERNET' AND description = :old_desc"
        ),
        {"new_desc": new_desc, "old_desc": old_desc},
    )
    # Not guarded on the old value: this one is structural rather than
    # wording, and an operator has no reason to have set it by hand.
    conn.execute(
        sa.text(
            "UPDATE techops_service_types SET instance_noun = :noun "
            "WHERE code = 'ETHERNET'"
        ),
        {"noun": instance_noun},
    )


def reword_wired_network(conn):
    """Forward data change, importable so tests can drive it directly."""
    _retext(conn, OLD_NAME, NEW_NAME, OLD_DESCRIPTION, NEW_DESCRIPTION, None)


def restore_hardwired_ethernet(conn):
    """Inverse, guarded the same way."""
    _retext(conn, NEW_NAME, OLD_NAME, NEW_DESCRIPTION, OLD_DESCRIPTION, "drop")


def upgrade():
    reword_wired_network(op.get_bind())


def downgrade():
    restore_hardwired_ethernet(op.get_bind())
