"""Add the Unit station and a needs-asset-tag setting

Adds supply_items.needs_asset_tag and moves devices and enroll codes on the
retired Tag, TV check and Printer check stations to the Unit station.

Revision ID: iw5u2n8k4r7d
Revises: iv6h2r9c3m5p
Create Date: 2026-11-08

"""
from alembic import op
import sqlalchemy as sa


revision = "iw5u2n8k4r7d"
down_revision = "iv6h2r9c3m5p"
branch_labels = None
depends_on = None

OLD_STATIONS = ("TAG", "TV_CHECK", "PRINTER_CHECK")
TABLES = ("inv_devices", "inv_enroll_codes")


def upgrade():
    with op.batch_alter_table("supply_items") as batch:
        batch.add_column(sa.Column("needs_asset_tag", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))
    conn = op.get_bind()
    for table in TABLES:
        conn.execute(
            sa.text(f"UPDATE {table} SET station_code = :new "
                    "WHERE station_code IN (:a, :b, :c)"),
            {"new": "UNIT", "a": OLD_STATIONS[0], "b": OLD_STATIONS[1], "c": OLD_STATIONS[2]})


def downgrade():
    conn = op.get_bind()
    for table in TABLES:
        conn.execute(sa.text(f"UPDATE {table} SET station_code = :old WHERE station_code = :new"),
                     {"old": "TV_CHECK", "new": "UNIT"})
    with op.batch_alter_table("supply_items") as batch:
        batch.drop_column("needs_asset_tag")
