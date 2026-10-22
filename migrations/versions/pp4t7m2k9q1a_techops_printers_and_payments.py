"""Add the Treasury approval group and the PRINTER and PAYMENTS services

Revision ID: pp4t7m2k9q1a
Revises: tc5f9a2e7b3d
Create Date: 2026-10-10

"""
from datetime import datetime

from alembic import op
import sqlalchemy as sa


revision = 'pp4t7m2k9q1a'
down_revision = 'tc5f9a2e7b3d'
branch_labels = None
depends_on = None


def _id(conn, sql, **params):
    return conn.execute(sa.text(sql), params).scalar()


def upgrade():
    conn = op.get_bind()
    techops_id = _id(conn, "SELECT id FROM work_types WHERE code = 'TECHOPS'")
    techops_gen_id = None
    if techops_id is not None:
        techops_gen_id = _id(
            conn,
            "SELECT id FROM approval_groups WHERE code = 'TECHOPS_GEN' "
            "AND work_type_id = :wt", wt=techops_id)
    if techops_id is None or techops_gen_id is None:
        # A fresh database. `flask seed` creates these rows after
        # `flask db upgrade`, so raising here would block a new environment.
        return

    now = datetime.utcnow()
    treasury_id = _id(
        conn,
        "SELECT id FROM approval_groups WHERE code = 'TREASURY' "
        "AND work_type_id = :wt", wt=techops_id)
    if treasury_id is None:
        conn.execute(
            sa.text(
                "INSERT INTO approval_groups "
                "(work_type_id, code, name, description, is_active, "
                " sort_order, created_at, updated_at) "
                "VALUES (:wt, 'TREASURY', 'Treasury', :d, :active, 30, :now, :now)"),
            {"wt": techops_id, "active": True, "now": now,
             "d": "Reviews department payment setups: Square hardware, "
                  "cash, and receipts"},
        )
        treasury_id = _id(
            conn,
            "SELECT id FROM approval_groups WHERE code = 'TREASURY' "
            "AND work_type_id = :wt", wt=techops_id)

    new_types = (
        ("PRINTER", "Printer",
         "A black-and-white laser printer on loan from TechOps. Say how it "
         "connects and roughly how much it prints.",
         techops_gen_id, 45, "printer"),
        ("PAYMENTS", "Taking payments (card or cash)",
         "Your department's payments as a whole. Treasury and BizOps follow "
         "up to configure Square.",
         treasury_id, 55, None),
    )
    for code, name, description, group_id, sort_order, noun in new_types:
        if _id(conn, "SELECT id FROM techops_service_types WHERE code = :c",
               c=code) is not None:
            continue
        conn.execute(
            sa.text(
                "INSERT INTO techops_service_types "
                "(code, name, description, default_approval_group_id, "
                " is_active, sort_order, instance_noun, created_at, updated_at) "
                "VALUES (:c, :n, :d, :g, :active, :s, :i, :now, :now)"),
            {"c": code, "n": name, "d": description, "g": group_id,
             "active": True, "s": sort_order, "i": noun, "now": now},
        )


def downgrade():
    conn = op.get_bind()
    conn.execute(sa.text(
        "DELETE FROM techops_service_types WHERE code IN ('PRINTER', 'PAYMENTS')"))
    conn.execute(sa.text("DELETE FROM approval_groups WHERE code = 'TREASURY'"))
