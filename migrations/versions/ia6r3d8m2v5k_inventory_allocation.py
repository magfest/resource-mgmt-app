"""Add inventory allocation tables and the item pre-allocation columns.

Revision ID: ia6r3d8m2v5k
Revises: k7s2n9c4w1pd
"""
import sqlalchemy as sa
from alembic import op

revision = "ia6r3d8m2v5k"
down_revision = "k7s2n9c4w1pd"
branch_labels = None
depends_on = None

_FK = "fk_supply_items_techops_service_type_id"


def _has_table(name):
    return sa.inspect(op.get_bind()).has_table(name)


def _columns(table):
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def _fk_names(table):
    return {fk["name"] for fk in sa.inspect(op.get_bind()).get_foreign_keys(table)}


def _bigint_pk():
    return sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                     primary_key=True)


def upgrade():
    if "needs_pre_allocation" not in _columns("supply_items"):
        with op.batch_alter_table("supply_items") as batch:
            batch.add_column(sa.Column("needs_pre_allocation", sa.Boolean(), nullable=False,
                                       server_default=sa.false()))
    if "techops_service_type_id" not in _columns("supply_items"):
        with op.batch_alter_table("supply_items") as batch:
            batch.add_column(sa.Column("techops_service_type_id", sa.Integer(), nullable=True))
            batch.create_foreign_key(_FK, "techops_service_types",
                                     ["techops_service_type_id"], ["id"])

    if not _has_table("inv_allocations"):
        op.create_table(
            "inv_allocations",
            _bigint_pk(),
            sa.Column("unit_id", sa.Integer(), nullable=False),
            sa.Column("work_line_id", sa.Integer(), nullable=True),
            sa.Column("event_cycle_id", sa.Integer(), nullable=False),
            sa.Column("department_id", sa.Integer(), nullable=False),
            sa.Column("line_label", sa.String(255), nullable=False),
            sa.Column("allocated_by_user_id", sa.String(64), nullable=True),
            sa.Column("allocated_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["unit_id"], ["inv_units.id"],
                                    name="fk_inv_allocations_unit_id"),
            sa.ForeignKeyConstraint(["work_line_id"], ["work_lines.id"],
                                    name="fk_inv_allocations_work_line_id",
                                    ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["event_cycle_id"], ["event_cycles.id"],
                                    name="fk_inv_allocations_event_cycle_id"),
            sa.ForeignKeyConstraint(["department_id"], ["departments.id"],
                                    name="fk_inv_allocations_department_id"),
            sa.UniqueConstraint("unit_id", name="uq_inv_allocations_unit_id"),
        )
        op.create_index("ix_inv_allocations_work_line_id", "inv_allocations", ["work_line_id"])
        op.create_index("ix_inv_allocations_event_cycle_id", "inv_allocations",
                        ["event_cycle_id"])
        op.create_index("ix_inv_allocations_department_id", "inv_allocations",
                        ["department_id"])

    if not _has_table("inv_allocation_events"):
        op.create_table(
            "inv_allocation_events",
            _bigint_pk(),
            sa.Column("unit_id", sa.Integer(), nullable=False),
            sa.Column("action", sa.String(16), nullable=False),
            sa.Column("work_line_id", sa.Integer(), nullable=True),
            sa.Column("event_cycle_id", sa.Integer(), nullable=False),
            sa.Column("department_id", sa.Integer(), nullable=False),
            sa.Column("line_label", sa.String(255), nullable=False),
            sa.Column("performed_by_user_id", sa.String(64), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(["unit_id"], ["inv_units.id"],
                                    name="fk_inv_allocation_events_unit_id"),
            sa.ForeignKeyConstraint(["work_line_id"], ["work_lines.id"],
                                    name="fk_inv_allocation_events_work_line_id",
                                    ondelete="SET NULL"),
            sa.ForeignKeyConstraint(["event_cycle_id"], ["event_cycles.id"],
                                    name="fk_inv_allocation_events_event_cycle_id"),
            sa.ForeignKeyConstraint(["department_id"], ["departments.id"],
                                    name="fk_inv_allocation_events_department_id"),
        )
        op.create_index("ix_inv_allocation_events_unit_id", "inv_allocation_events",
                        ["unit_id"])
        op.create_index("ix_inv_allocation_events_created_at", "inv_allocation_events",
                        ["created_at"])


def downgrade():
    if _has_table("inv_allocation_events"):
        op.drop_table("inv_allocation_events")
    if _has_table("inv_allocations"):
        op.drop_table("inv_allocations")

    if _FK in _fk_names("supply_items"):
        with op.batch_alter_table("supply_items") as batch:
            batch.drop_constraint(_FK, type_="foreignkey")
    if "techops_service_type_id" in _columns("supply_items"):
        with op.batch_alter_table("supply_items") as batch:
            batch.drop_column("techops_service_type_id")
    if "needs_pre_allocation" in _columns("supply_items"):
        with op.batch_alter_table("supply_items") as batch:
            batch.drop_column("needs_pre_allocation")
