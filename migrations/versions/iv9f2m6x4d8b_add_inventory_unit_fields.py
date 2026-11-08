"""Add unit names, notes and typed fields to inventory

Adds name, notes and imported notes to units, gear kinds with typed fields
and choices, per-unit values, per-model defaults and tag links. Seeds five
gear kinds with guarded inserts keyed by code; rerunning adds nothing twice.

Revision ID: iv9f2m6x4d8b
Revises: iv7p3q9r2s4t
Create Date: 2026-11-07

"""
from alembic import op
import sqlalchemy as sa


revision = "iv9f2m6x4d8b"
down_revision = "iv7p3q9r2s4t"
branch_labels = None
depends_on = None

BIGINT = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
LIVE_NAME_KEY = "name_key IS NOT NULL AND condition != 'RETIRED'"

RESOLUTIONS = ["1920x1080", "1920x1200", "2560x1440", "4k"]

# (kind code, name, [(field code, name, type, unit label, [choices])])
KINDS = [
    ("TV", "TV", [
        ("BRAND", "Brand", "TEXT", None, []),
        ("SIZE", "Size", "NUMBER", "in", []),
        ("RESOLUTION", "Resolution", "CHOICE", None, RESOLUTIONS),
        ("HDMI", "HDMI ports", "NUMBER", None, []),
        ("DISPLAYPORT", "DisplayPort ports", "NUMBER", None, []),
    ]),
    ("MONITOR", "Monitor", [
        ("BRAND", "Brand", "TEXT", None, []),
        ("SIZE", "Size", "NUMBER", "in", []),
        ("RESOLUTION", "Resolution", "CHOICE", None, RESOLUTIONS),
        ("HDMI", "HDMI ports", "NUMBER", None, []),
        ("DISPLAYPORT", "DisplayPort ports", "NUMBER", None, []),
    ]),
    ("PRINTER", "Printer", [
        ("BRAND", "Brand", "TEXT", None, []),
        ("COLOR", "Color", "BOOL", None, []),
        ("DUPLEX", "Duplex", "BOOL", None, []),
    ]),
    ("LABEL_PRINTER", "Label printer", [
        ("BRAND", "Brand", "TEXT", None, []),
        ("LABEL_WIDTH", "Label width", "NUMBER", "in", []),
    ]),
    ("LAMINATOR", "Laminator", [
        ("BRAND", "Brand", "TEXT", None, []),
        ("WIDTH", "Width", "NUMBER", "in", []),
    ]),
]


def _scalar(conn, sql, **params):
    return conn.execute(sa.text(sql), params).scalar()


def upgrade():
    with op.batch_alter_table("inv_units") as batch:
        batch.add_column(sa.Column("name", sa.String(64), nullable=True))
        batch.add_column(sa.Column("name_key", sa.String(64), nullable=True))
        batch.add_column(sa.Column("notes", sa.Text(), nullable=True))
        batch.add_column(sa.Column("imported_notes", sa.Text(), nullable=True))
    op.create_index(
        "uq_inv_units_name_key", "inv_units", ["name_key"], unique=True,
        postgresql_where=sa.text(LIVE_NAME_KEY), sqlite_where=sa.text(LIVE_NAME_KEY),
    )

    op.create_table(
        "inv_gear_kinds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("code", sa.String(32), nullable=False, unique=True),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.create_table(
        "inv_fields",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("gear_kind_id", sa.Integer(),
                  sa.ForeignKey("inv_gear_kinds.id", name="fk_inv_fields_gear_kind_id"),
                  nullable=False),
        sa.Column("code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("field_type", sa.String(8), nullable=False),
        sa.Column("unit_label", sa.String(16), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("gear_kind_id", "code", name="uq_inv_fields_kind_code"),
    )
    op.create_index("ix_inv_fields_gear_kind_id", "inv_fields", ["gear_kind_id"])
    op.create_table(
        "inv_field_choices",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("field_id", sa.Integer(),
                  sa.ForeignKey("inv_fields.id", name="fk_inv_field_choices_field_id"),
                  nullable=False),
        sa.Column("label", sa.String(32), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("field_id", "label", name="uq_inv_field_choices_field_label"),
    )
    op.create_index("ix_inv_field_choices_field_id", "inv_field_choices", ["field_id"])

    op.create_table(
        "inv_unit_values",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("unit_id", sa.Integer(),
                  sa.ForeignKey("inv_units.id", name="fk_inv_unit_values_unit_id"),
                  nullable=False),
        sa.Column("field_id", sa.Integer(),
                  sa.ForeignKey("inv_fields.id", name="fk_inv_unit_values_field_id"),
                  nullable=False),
        sa.Column("choice_id", sa.Integer(),
                  sa.ForeignKey("inv_field_choices.id", name="fk_inv_unit_values_choice_id"),
                  nullable=True),
        sa.Column("number_value", sa.Numeric(8, 2), nullable=True),
        sa.Column("bool_value", sa.Boolean(), nullable=True),
        sa.Column("text_value", sa.String(128), nullable=True),
        sa.UniqueConstraint("unit_id", "field_id", name="uq_inv_unit_values_unit_field"),
    )
    op.create_index("ix_inv_unit_values_unit_id", "inv_unit_values", ["unit_id"])
    op.create_table(
        "inv_model_defaults",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("item_id", sa.Integer(),
                  sa.ForeignKey("supply_items.id", name="fk_inv_model_defaults_item_id"),
                  nullable=False),
        sa.Column("field_id", sa.Integer(),
                  sa.ForeignKey("inv_fields.id", name="fk_inv_model_defaults_field_id"),
                  nullable=False),
        sa.Column("choice_id", sa.Integer(),
                  sa.ForeignKey("inv_field_choices.id",
                                name="fk_inv_model_defaults_choice_id"),
                  nullable=True),
        sa.Column("number_value", sa.Numeric(8, 2), nullable=True),
        sa.Column("bool_value", sa.Boolean(), nullable=True),
        sa.Column("text_value", sa.String(128), nullable=True),
        sa.UniqueConstraint("item_id", "field_id", name="uq_inv_model_defaults_item_field"),
    )
    op.create_index("ix_inv_model_defaults_item_id", "inv_model_defaults", ["item_id"])
    op.create_table(
        "inv_tag_links",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("unit_id", sa.Integer(),
                  sa.ForeignKey("inv_units.id", name="fk_inv_tag_links_unit_id"),
                  nullable=False),
        sa.Column("identifier_id", sa.Integer(),
                  sa.ForeignKey("inv_unit_identifiers.id",
                                name="fk_inv_tag_links_identifier_id"),
                  nullable=False),
        sa.Column("shift_id", BIGINT,
                  sa.ForeignKey("inv_shifts.id", name="fk_inv_tag_links_shift_id"),
                  nullable=True),
        sa.Column("device_id", sa.Integer(),
                  sa.ForeignKey("inv_devices.id", name="fk_inv_tag_links_device_id"),
                  nullable=True),
        sa.Column("performed_by_user_id", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("undone_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_inv_tag_links_unit_id", "inv_tag_links", ["unit_id"])

    with op.batch_alter_table("supply_items") as batch:
        batch.add_column(sa.Column("gear_kind_id", sa.Integer(), nullable=True))
        batch.create_foreign_key("fk_supply_items_gear_kind_id", "inv_gear_kinds",
                                 ["gear_kind_id"], ["id"])

    _seed()


def _seed():
    conn = op.get_bind()
    for kind_pos, (code, name, fields) in enumerate(KINDS, start=1):
        kind_id = _scalar(conn, "SELECT id FROM inv_gear_kinds WHERE code = :c", c=code)
        if kind_id is None:
            conn.execute(
                sa.text("INSERT INTO inv_gear_kinds (code, name, position, is_active) "
                        "VALUES (:c, :n, :p, :a)"),
                {"c": code, "n": name, "p": kind_pos * 10, "a": True})
            kind_id = _scalar(conn, "SELECT id FROM inv_gear_kinds WHERE code = :c", c=code)
        for pos, (fcode, fname, ftype, unit_label, choices) in enumerate(fields, start=1):
            field_id = _scalar(
                conn, "SELECT id FROM inv_fields WHERE gear_kind_id = :k AND code = :c",
                k=kind_id, c=fcode)
            if field_id is None:
                conn.execute(
                    sa.text("INSERT INTO inv_fields (gear_kind_id, code, name, field_type, "
                            "unit_label, position, is_active) "
                            "VALUES (:k, :c, :n, :t, :u, :p, :a)"),
                    {"k": kind_id, "c": fcode, "n": fname, "t": ftype,
                     "u": unit_label, "p": pos * 10, "a": True})
                field_id = _scalar(
                    conn, "SELECT id FROM inv_fields WHERE gear_kind_id = :k AND code = :c",
                    k=kind_id, c=fcode)
            for cpos, label in enumerate(choices, start=1):
                if _scalar(conn, "SELECT id FROM inv_field_choices "
                                 "WHERE field_id = :f AND label = :l",
                           f=field_id, l=label) is None:
                    conn.execute(
                        sa.text("INSERT INTO inv_field_choices "
                                "(field_id, label, position, is_active) "
                                "VALUES (:f, :l, :p, :a)"),
                        {"f": field_id, "l": label, "p": cpos * 10, "a": True})


def downgrade():
    with op.batch_alter_table("supply_items") as batch:
        batch.drop_constraint("fk_supply_items_gear_kind_id", type_="foreignkey")
        batch.drop_column("gear_kind_id")
    for table in ("inv_tag_links", "inv_model_defaults", "inv_unit_values",
                  "inv_field_choices", "inv_fields", "inv_gear_kinds"):
        op.drop_table(table)
    op.drop_index("uq_inv_units_name_key", table_name="inv_units")
    with op.batch_alter_table("inv_units") as batch:
        for column in ("imported_notes", "notes", "name_key", "name"):
            batch.drop_column(column)
