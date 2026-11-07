"""Add the inventory print queue and item totals

Creates printers, job-type routes, print jobs (queue and history in one
table), typed item totals and device enrollment codes. No reference rows are seeded; an inventory
admin sets printers and routes. station_code is free text, so the new PRINT
and RECEIVE stations need no schema change.

Revision ID: iv7p3q9r2s4t
Revises: iv4c7n2t9k1a
Create Date: 2026-11-07

"""
from alembic import op
import sqlalchemy as sa


revision = "iv7p3q9r2s4t"
down_revision = "iv4c7n2t9k1a"
branch_labels = None
depends_on = None

BIGINT = sa.BigInteger().with_variant(sa.Integer(), "sqlite")


def upgrade():
    op.create_table(
        "inv_printers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("dpi", sa.Integer(), nullable=False),
        sa.Column("width_in", sa.Numeric(4, 2), nullable=False),
        sa.Column("height_in", sa.Numeric(4, 2), nullable=False),
        sa.Column("stock", sa.String(64), nullable=True),
        sa.Column("device_id", sa.Integer(),
                  sa.ForeignKey("inv_devices.id", name="fk_inv_printers_device_id"),
                  nullable=True),
        sa.Column("device_name", sa.String(128), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_index("ix_inv_printers_device_id", "inv_printers", ["device_id"])

    op.create_table(
        "inv_print_routes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("job_type", sa.String(16), nullable=False, unique=True),
        sa.Column("printer_id", sa.Integer(),
                  sa.ForeignKey("inv_printers.id", name="fk_inv_print_routes_printer_id"),
                  nullable=False),
    )

    op.create_table(
        "inv_print_jobs",
        sa.Column("id", BIGINT, primary_key=True),
        sa.Column("printer_id", sa.Integer(),
                  sa.ForeignKey("inv_printers.id", name="fk_inv_print_jobs_printer_id"),
                  nullable=False),
        sa.Column("job_type", sa.String(16), nullable=False),
        sa.Column("unit_id", sa.Integer(),
                  sa.ForeignKey("inv_units.id", name="fk_inv_print_jobs_unit_id"),
                  nullable=True),
        sa.Column("container_id", sa.Integer(),
                  sa.ForeignKey("inv_containers.id", name="fk_inv_print_jobs_container_id"),
                  nullable=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("fields", sa.JSON(), nullable=False),
        sa.Column("printed_date", sa.Date(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="QUEUED"),
        sa.Column("source", sa.String(8), nullable=False),
        sa.Column("batch_id", sa.String(32), nullable=True),
        sa.Column("client_request_id", sa.String(64), nullable=True, unique=True),
        sa.Column("error", sa.String(200), nullable=True),
        sa.Column("performed_by_user_id", sa.String(64), nullable=True),
        sa.Column("shift_id", BIGINT,
                  sa.ForeignKey("inv_shifts.id", name="fk_inv_print_jobs_shift_id"),
                  nullable=True),
        sa.Column("device_id", sa.Integer(),
                  sa.ForeignKey("inv_devices.id", name="fk_inv_print_jobs_device_id"),
                  nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("claimed_at", sa.DateTime(), nullable=True),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_inv_print_jobs_printer_id", "inv_print_jobs", ["printer_id"])
    op.create_index("ix_inv_print_jobs_status", "inv_print_jobs", ["status"])
    op.create_index("ix_inv_print_jobs_batch_id", "inv_print_jobs", ["batch_id"])

    op.create_table(
        "inv_item_totals",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("item_id", sa.Integer(),
                  sa.ForeignKey("supply_items.id", name="fk_inv_item_totals_item_id"),
                  nullable=False, unique=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("set_by_user_id", sa.String(64), nullable=True),
        sa.Column("set_at", sa.DateTime(), nullable=False),
    )

    op.create_table(
        "inv_enroll_codes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("station_code", sa.String(16), nullable=False),
        sa.Column("home_container_id", sa.Integer(),
                  sa.ForeignKey("inv_containers.id",
                                name="fk_inv_enroll_codes_home_container_id"),
                  nullable=True),
        sa.Column("created_by_user_id", sa.String(64),
                  sa.ForeignKey("users.id", name="fk_inv_enroll_codes_created_by"),
                  nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("expires_at", sa.DateTime(), nullable=False),
        sa.Column("used_at", sa.DateTime(), nullable=True),
        sa.Column("device_id", sa.Integer(),
                  sa.ForeignKey("inv_devices.id", name="fk_inv_enroll_codes_device_id"),
                  nullable=True),
    )


def downgrade():
    for table in ("inv_enroll_codes", "inv_item_totals", "inv_print_jobs", "inv_print_routes", "inv_printers"):
        op.drop_table(table)
