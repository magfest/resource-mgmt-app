"""Backfill the TechOps approval groups and service types where they are missing

Production has an active TECHOPS work type but never ran `flask seed`, which
is the only thing that creates the TechOps catalog. With no service types,
the request form hides its department-wide services and saving any request
raises. Earlier TechOps migrations skipped their inserts on such a database
because they look for TECHOPS_GEN and TECHOPS_NET first.

Revision ID: cb3k7r9t2m5x
Revises: pp4t7m2k9q1a
Create Date: 2026-10-11

"""
from datetime import datetime

from alembic import op
import sqlalchemy as sa


revision = 'cb3k7r9t2m5x'
down_revision = 'pp4t7m2k9q1a'
branch_labels = None
depends_on = None


# Values match app/seeds/bootstrap.py as of 2026-10-11. The migration's test
# rebuilds the catalog from the seed and compares, so the two cannot drift.
# (code, name, description, sort_order)
GROUPS = (
    ("TECHOPS_NET", "TechOps Networking",
     "Reviews network and phone services (WiFi, ethernet, bandwidth, phone lines)",
     10),
    ("TECHOPS_GEN", "TechOps Generic",
     "Reviews dedicated radio channels, consultations, and any other TechOps requests",
     20),
    ("TREASURY", "Treasury",
     "Reviews department payment setups: Square hardware, cash, and receipts",
     30),
)

# (code, name, description, group_code, sort_order, instance_noun, is_active)
SERVICE_TYPES = (
    ("WIFI", "WiFi coverage / access",
     "Staff and event ops WiFi for a space. Attendee access is rare and needs "
     "a specific reason. Call out heavy bandwidth needs such as streaming or "
     "large transfers.",
     "TECHOPS_NET", 10, None, True),
    ("ETHERNET", "Wired network",
     "Tell us what needs a cable and what it does. The network team works out "
     "how many drops that takes.",
     "TECHOPS_NET", 20, None, True),
    ("BANDWIDTH", "Special bandwidth usage",
     "Streaming, large file transfers, attendees-on-network, or other heavy "
     "bandwidth needs",
     "TECHOPS_NET", 30, None, False),
    ("PHONE", "Hardwired phone line",
     "Dedicated phone line at a location, internal-only or external-callable",
     "TECHOPS_NET", 40, "phone line", False),
    ("PHONE_NUMBER", "Phone number",
     "One phone number and the desk phones that ring it. Say what it needs to "
     "do and where the phones sit.",
     "TECHOPS_NET", 41, "phone line", True),
    ("DESK_PHONE", "Desk phone",
     "One handset to provision and physically place. Rings the number on its "
     "parent line.",
     "TECHOPS_NET", 42, "desk phone", True),
    ("PRINTER", "Printer",
     "A black-and-white laser printer on loan from TechOps. Say how it "
     "connects and roughly how much it prints.",
     "TECHOPS_GEN", 45, "printer", True),
    ("RADIO_CHANNEL", "Dedicated radio channel",
     "Most departments use the general channel, and there are shared channels "
     "free for a quick side conversation. Dedicated channels suit teams with "
     "constant traffic, like concert security or logistics. If you think one "
     "would help, ask here.",
     "TECHOPS_GEN", 50, "channel", True),
    ("PAYMENTS", "Taking payments (card or cash)",
     "Your department's payments as a whole. Treasury and BizOps follow up to "
     "configure Square.",
     "TREASURY", 55, None, True),
    ("OTHER", "Something else, or ask TechOps for advice",
     "Anything not covered above. Use this to ask TechOps to talk something "
     "through with you.",
     "TECHOPS_GEN", 60, None, True),
    ("NO_SERVICES", "No services needed",
     "A space the department confirmed needs nothing. Reviewed so an empty "
     "room does not go unanswered.",
     "TECHOPS_GEN", 70, None, True),
)


def _scalar(conn, sql, **params):
    return conn.execute(sa.text(sql), params).scalar()


def upgrade():
    conn = op.get_bind()
    techops_id = _scalar(conn, "SELECT id FROM work_types WHERE code = 'TECHOPS'")
    # A fresh database has no TECHOPS row until the seed runs, and the seed
    # then creates the catalog itself.
    if techops_id is None:
        return

    now = datetime.utcnow()
    group_ids = {}
    for code, name, description, sort_order in GROUPS:
        group_id = _scalar(
            conn,
            "SELECT id FROM approval_groups WHERE code = :c AND work_type_id = :wt",
            c=code, wt=techops_id)
        if group_id is None:
            # Booleans and timestamps are bound parameters: PostgreSQL rejects
            # an integer literal in a boolean column.
            conn.execute(
                sa.text(
                    "INSERT INTO approval_groups "
                    "(work_type_id, code, name, description, is_active, "
                    " sort_order, created_at, updated_at) "
                    "VALUES (:wt, :c, :n, :d, :active, :s, :now, :now)"),
                {"wt": techops_id, "c": code, "n": name, "d": description,
                 "active": True, "s": sort_order, "now": now},
            )
            group_id = _scalar(
                conn,
                "SELECT id FROM approval_groups "
                "WHERE code = :c AND work_type_id = :wt",
                c=code, wt=techops_id)
        group_ids[code] = group_id

    # Insert only what is missing. An existing row may carry an admin's
    # edits, so it is never updated here.
    for code, name, description, group, sort_order, noun, active in SERVICE_TYPES:
        if _scalar(conn, "SELECT id FROM techops_service_types WHERE code = :c",
                   c=code) is not None:
            continue
        conn.execute(
            sa.text(
                "INSERT INTO techops_service_types "
                "(code, name, description, default_approval_group_id, "
                " is_active, sort_order, instance_noun, created_at, updated_at) "
                "VALUES (:c, :n, :d, :g, :active, :s, :i, :now, :now)"),
            {"c": code, "n": name, "d": description, "g": group_ids[group],
             "active": active, "s": sort_order, "i": noun, "now": now},
        )


def downgrade():
    # Deliberately a no-op. A backfilled row cannot be told apart from a
    # seeded one, and removing either breaks every TechOps request.
    pass
