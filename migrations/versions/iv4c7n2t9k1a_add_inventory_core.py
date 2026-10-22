"""Add FestOps inventory tables and SupplyItem inventory flags

Creates the container tree, serialized units and identifiers, cached stock,
the append-only movement log, shared devices, badge sessions, counts, scan
receipts, and label sequences. Seeds seven container kinds with guarded
inserts.

Revision ID: iv4c7n2t9k1a
Revises: tn5w9c3k7r2p
Create Date: 2026-10-07

"""
from alembic import op
import sqlalchemy as sa


revision = "iv4c7n2t9k1a"
down_revision = "tn5w9c3k7r2p"
branch_labels = None
depends_on = None

BIGINT = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
ACTIVE_LABEL = "retired_at IS NULL AND kind = 'FESTOPS_LABEL'"

KINDS = [
    ("LOCATION", "Location", 10),
    ("TRUCK", "Truck", 20),
    ("CAGE", "Cage", 30),
    ("GAYLORD", "Gaylord", 40),
    ("PALLET", "Pallet", 50),
    ("TOTE", "Tote", 60),
    ("BIN", "Bin", 70),
]


def upgrade():
    with op.batch_alter_table("supply_items") as batch:
        batch.add_column(sa.Column("is_serialized", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))
        batch.add_column(sa.Column("is_orderable", sa.Boolean(), nullable=False,
                                   server_default=sa.true()))
        batch.add_column(sa.Column("barcode", sa.String(64), nullable=True))
        batch.create_index("ix_supply_items_barcode", ["barcode"])

    op.create_table(
        "inv_container_kinds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(32), nullable=False, unique=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "inv_containers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("label_code", sa.String(16), nullable=False, unique=True),
        sa.Column("kind_id", sa.Integer(),
                  sa.ForeignKey("inv_container_kinds.id", name="fk_inv_containers_kind_id"),
                  nullable=False),
        sa.Column("name", sa.String(128), nullable=True),
        sa.Column("parent_id", sa.Integer(),
                  sa.ForeignKey("inv_containers.id", name="fk_inv_containers_parent_id"),
                  nullable=True),
        sa.Column("owner_department_id", sa.Integer(),
                  sa.ForeignKey("departments.id", name="fk_inv_containers_owner_department_id"),
                  nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by_user_id", sa.String(64), nullable=True),
    )
    op.create_index("ix_inv_containers_kind_id", "inv_containers", ["kind_id"])
    op.create_index("ix_inv_containers_parent_id", "inv_containers", ["parent_id"])

    op.create_table(
        "inv_units",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("item_id", sa.Integer(),
                  sa.ForeignKey("supply_items.id", name="fk_inv_units_item_id"), nullable=False),
        sa.Column("condition", sa.String(16), nullable=False),
        sa.Column("ownership", sa.String(16), nullable=False),
        sa.Column("vendor_name", sa.String(128), nullable=True),
        sa.Column("rental_reference", sa.String(128), nullable=True),
        sa.Column("current_container_id", sa.Integer(),
                  sa.ForeignKey("inv_containers.id", name="fk_inv_units_current_container_id"),
                  nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by_user_id", sa.String(64), nullable=True),
    )
    op.create_index("ix_inv_units_item_id", "inv_units", ["item_id"])
    op.create_index("ix_inv_units_current_container_id", "inv_units", ["current_container_id"])

    op.create_table(
        "inv_unit_identifiers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("unit_id", sa.Integer(),
                  sa.ForeignKey("inv_units.id", name="fk_inv_unit_identifiers_unit_id"),
                  nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("value", sa.String(128), nullable=False),
        sa.Column("normalized_value", sa.String(128), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("retired_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_inv_unit_identifiers_unit_id", "inv_unit_identifiers", ["unit_id"])
    op.create_index("ix_inv_unit_identifiers_normalized_value", "inv_unit_identifiers",
                    ["normalized_value"])
    op.create_index("uq_inv_unit_identifiers_active_label", "inv_unit_identifiers",
                    ["normalized_value"], unique=True,
                    postgresql_where=sa.text(ACTIVE_LABEL),
                    sqlite_where=sa.text(ACTIVE_LABEL))

    op.create_table(
        "inv_stock",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("item_id", sa.Integer(),
                  sa.ForeignKey("supply_items.id", name="fk_inv_stock_item_id"), nullable=False),
        sa.Column("container_id", sa.Integer(),
                  sa.ForeignKey("inv_containers.id", name="fk_inv_stock_container_id"),
                  nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("item_id", "container_id", name="uq_inv_stock_item_container"),
        sa.CheckConstraint("quantity >= 0", name="ck_inv_stock_nonnegative"),
    )
    op.create_index("ix_inv_stock_item_id", "inv_stock", ["item_id"])
    op.create_index("ix_inv_stock_container_id", "inv_stock", ["container_id"])

    op.create_table(
        "inv_devices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False, unique=True),
        sa.Column("enrolled_by_user_id", sa.String(64),
                  sa.ForeignKey("users.id", name="fk_inv_devices_enrolled_by"), nullable=False),
        sa.Column("enrolled_at", sa.DateTime(), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_at", sa.DateTime(), nullable=True),
        sa.Column("revoked_by_user_id", sa.String(64), nullable=True),
    )
    op.create_table(
        "inv_badge_sessions",
        sa.Column("id", BIGINT, primary_key=True),
        sa.Column("device_id", sa.Integer(),
                  sa.ForeignKey("inv_devices.id", name="fk_inv_badge_sessions_device_id"),
                  nullable=False),
        sa.Column("badge_code", sa.String(16), nullable=False),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("last_activity_at", sa.DateTime(), nullable=False),
        sa.Column("ended_at", sa.DateTime(), nullable=True),
        sa.Column("end_reason", sa.String(16), nullable=True),
    )
    op.create_index("ix_inv_badge_sessions_device_id", "inv_badge_sessions", ["device_id"])

    op.create_table(
        "inv_counts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("container_id", sa.Integer(),
                  sa.ForeignKey("inv_containers.id", name="fk_inv_counts_container_id"),
                  nullable=False),
        sa.Column("user_id", sa.String(64), nullable=True),
        sa.Column("badge_session_id", BIGINT,
                  sa.ForeignKey("inv_badge_sessions.id", name="fk_inv_counts_badge_session_id"),
                  nullable=True),
        sa.Column("device_id", sa.Integer(),
                  sa.ForeignKey("inv_devices.id", name="fk_inv_counts_device_id"), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("submitted_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_inv_counts_container_id", "inv_counts", ["container_id"])
    op.create_table(
        "inv_count_lines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("count_id", sa.Integer(),
                  sa.ForeignKey("inv_counts.id", name="fk_inv_count_lines_count_id"),
                  nullable=False),
        sa.Column("item_id", sa.Integer(),
                  sa.ForeignKey("supply_items.id", name="fk_inv_count_lines_item_id"),
                  nullable=False),
        sa.Column("expected_quantity", sa.Integer(), nullable=False),
        sa.Column("counted_quantity", sa.Integer(), nullable=True),
        sa.Column("outcome", sa.String(16), nullable=True),
    )
    op.create_index("ix_inv_count_lines_count_id", "inv_count_lines", ["count_id"])

    op.create_table(
        "inv_movements",
        sa.Column("id", BIGINT, primary_key=True),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("item_id", sa.Integer(),
                  sa.ForeignKey("supply_items.id", name="fk_inv_movements_item_id"), nullable=True),
        sa.Column("unit_id", sa.Integer(),
                  sa.ForeignKey("inv_units.id", name="fk_inv_movements_unit_id"), nullable=True),
        sa.Column("subject_container_id", sa.Integer(),
                  sa.ForeignKey("inv_containers.id",
                                name="fk_inv_movements_subject_container_id"), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=True),
        sa.Column("from_container_id", sa.Integer(),
                  sa.ForeignKey("inv_containers.id", name="fk_inv_movements_from_container_id"),
                  nullable=True),
        sa.Column("to_container_id", sa.Integer(),
                  sa.ForeignKey("inv_containers.id", name="fk_inv_movements_to_container_id"),
                  nullable=True),
        sa.Column("performed_by_user_id", sa.String(64), nullable=True),
        sa.Column("badge_session_id", BIGINT,
                  sa.ForeignKey("inv_badge_sessions.id",
                                name="fk_inv_movements_badge_session_id"), nullable=True),
        sa.Column("device_id", sa.Integer(),
                  sa.ForeignKey("inv_devices.id", name="fk_inv_movements_device_id"),
                  nullable=True),
        sa.Column("count_id", sa.Integer(),
                  sa.ForeignKey("inv_counts.id", name="fk_inv_movements_count_id"), nullable=True),
        sa.Column("note", sa.String(256), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.CheckConstraint("quantity IS NULL OR quantity > 0",
                           name="ck_inv_movements_quantity_positive"),
    )
    for col in ("item_id", "unit_id", "subject_container_id", "from_container_id",
                "to_container_id", "badge_session_id", "device_id", "created_at"):
        op.create_index(f"ix_inv_movements_{col}", "inv_movements", [col])

    op.create_table(
        "inv_scan_requests",
        sa.Column("id", BIGINT, primary_key=True),
        sa.Column("client_request_id", sa.String(64), nullable=False, unique=True),
        sa.Column("response_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_table(
        "inv_label_sequences",
        sa.Column("prefix", sa.String(4), primary_key=True),
        sa.Column("next_value", sa.Integer(), nullable=False),
    )

    bind = op.get_bind()
    for code, name, sort_order in KINDS:
        exists = bind.execute(
            sa.text("SELECT id FROM inv_container_kinds WHERE code = :c"), {"c": code},
        ).scalar()
        if exists is None:
            bind.execute(
                sa.text(
                    "INSERT INTO inv_container_kinds (code, name, sort_order, is_active, created_at) "
                    "VALUES (:c, :n, :s, :a, CURRENT_TIMESTAMP)"
                ),
                {"c": code, "n": name, "s": sort_order, "a": True},
            )


def downgrade():
    for table in ("inv_label_sequences", "inv_scan_requests", "inv_movements",
                  "inv_count_lines", "inv_counts", "inv_badge_sessions", "inv_devices",
                  "inv_stock", "inv_unit_identifiers", "inv_units", "inv_containers",
                  "inv_container_kinds"):
        op.drop_table(table)
    with op.batch_alter_table("supply_items") as batch:
        batch.drop_index("ix_supply_items_barcode")
        batch.drop_column("barcode")
        batch.drop_column("is_orderable")
        batch.drop_column("is_serialized")
