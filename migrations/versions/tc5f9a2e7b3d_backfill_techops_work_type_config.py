"""Backfill the TECHOPS work_type_configs row where it is missing

Production has an active TECHOPS work_types row with no config row. The
bootstrap seed creates config rows; no migration ever did for TECHOPS.
get_active_work_types() inner-joins the config, so TechOps vanished from the
home page and every picker built on that helper, whatever a user's access.

Revision ID: tc5f9a2e7b3d
Revises: so3k8v1n5w2e
Create Date: 2026-10-08

"""
from alembic import op
import sqlalchemy as sa


revision = 'tc5f9a2e7b3d'
down_revision = 'so3k8v1n5w2e'
branch_labels = None
depends_on = None


def upgrade():
    conn = op.get_bind()
    techops_id = conn.execute(
        sa.text("SELECT id FROM work_types WHERE code = 'TECHOPS'")
    ).scalar()
    # A fresh database has no TECHOPS row until the seed runs, and the seed
    # then creates the config itself.
    if techops_id is None:
        return
    exists = conn.execute(
        sa.text("SELECT work_type_id FROM work_type_configs WHERE work_type_id = :w"),
        {"w": techops_id},
    ).scalar()
    if exists is not None:
        return
    # Values match the TECHOPS block in app/seeds/bootstrap.py. Booleans are
    # bound Python values, not literals: PostgreSQL rejects 0/1 in a boolean.
    conn.execute(
        sa.text(
            "INSERT INTO work_type_configs "
            "(work_type_id, url_slug, public_id_prefix, line_detail_type, "
            " routing_strategy, supports_supplementary, supports_fixed_costs, "
            " uses_dispatch, has_admin_final, uses_board_release, "
            " item_singular, item_plural, line_singular, line_plural) "
            "VALUES (:w, 'techops', 'TEC', 'techops', 'category', "
            " :f, :f, :f, :f, :f, "
            " 'TechOps Request', 'TechOps Requests', 'Service', 'Services')"
        ),
        {"w": techops_id, "f": False},
    )


def downgrade():
    # Deliberately a no-op. Removing the row cannot tell a backfilled config
    # from a seeded one, and deleting either hides TechOps again.
    pass
