"""Add label texts to inventory fields and revise the gear kind seed

Fields gain on_label and label_text, choices gain label_text. The seed adds a
MODEL field to every kind and printer specs, keyed by kind and field code.
Existing rows get new positions and label settings; missing rows are inserted.

Revision ID: iv3k8w1n5q7c
Revises: iv9f2m6x4d8b
Create Date: 2026-11-07

"""
from alembic import op
import sqlalchemy as sa


revision = "iv3k8w1n5q7c"
down_revision = "iv9f2m6x4d8b"
branch_labels = None
depends_on = None

kinds_t = sa.table(
    "inv_gear_kinds", sa.column("id", sa.Integer), sa.column("code", sa.String),
    sa.column("name", sa.String), sa.column("position", sa.Integer),
    sa.column("is_active", sa.Boolean))
fields_t = sa.table(
    "inv_fields", sa.column("id", sa.Integer), sa.column("gear_kind_id", sa.Integer),
    sa.column("code", sa.String), sa.column("name", sa.String),
    sa.column("field_type", sa.String), sa.column("unit_label", sa.String),
    sa.column("position", sa.Integer), sa.column("is_active", sa.Boolean),
    sa.column("on_label", sa.Boolean), sa.column("label_text", sa.String))
choices_t = sa.table(
    "inv_field_choices", sa.column("id", sa.Integer), sa.column("field_id", sa.Integer),
    sa.column("label", sa.String), sa.column("label_text", sa.String),
    sa.column("position", sa.Integer), sa.column("is_active", sa.Boolean))

MODEL = ("MODEL", "Model", "TEXT", None, [], False, None)

# (kind code, name, [(field code, name, type, unit, [(choice, label_text)], on_label,
# label_text)]). List order is position, from 0.
KINDS = [
    ("TV", "TV", [
        MODEL,
        ("BRAND", "Brand", "TEXT", None, [], False, None),
        ("SIZE", "Size", "NUMBER", "in", [], True, None),
        ("RESOLUTION", "Resolution", "CHOICE", None,
         [("720p", None), ("1080p", None), ("4k", None)], True, None),
        ("HDMI", "HDMI", "BOOL", None, [], True, "HDMI"),
        ("DISPLAYPORT", "DisplayPort", "BOOL", None, [], True, "DP"),
    ]),
    ("MONITOR", "Monitor", [
        MODEL,
        ("SIZE", "Size", "NUMBER", "in", [], True, None),
        ("RESOLUTION", "Resolution", "CHOICE", None,
         [("1920x1080", None), ("1920x1200", None), ("2560x1440", None), ("4k", None)],
         True, None),
        ("HDMI", "HDMI", "BOOL", None, [], True, "HDMI"),
        ("DISPLAYPORT", "DisplayPort", "BOOL", None, [], True, "DP"),
    ]),
    ("PRINTER", "Printer", [
        MODEL,
        ("PRINT", "Print", "CHOICE", None, [("Black and white", "BW"), ("Color", "C")],
         True, None),
        ("USB", "USB", "BOOL", None, [], True, "USB"),
        ("DUPLEX", "Duplex", "BOOL", None, [], True, "DUPLEX"),
        ("TONER", "Toner cartridge", "TEXT", None, [], False, None),
    ]),
    ("LABEL_PRINTER", "Label printer", [
        MODEL,
        ("MEDIA", "Tape or media", "TEXT", None, [], False, None),
    ]),
    ("LAMINATOR", "Laminator", [MODEL]),
]
# PRINT replaces COLOR. COLOR is retired, not deleted, so saved values still show.
RETIRED = [("PRINTER", "COLOR")]


def _id(conn, table, **keys):
    where = [table.c[name] == value for name, value in keys.items()]
    return conn.execute(sa.select(table.c.id).where(*where)).scalar()


def upgrade():
    with op.batch_alter_table("inv_fields") as batch:
        batch.add_column(sa.Column("on_label", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))
        batch.add_column(sa.Column("label_text", sa.String(16), nullable=True))
    with op.batch_alter_table("inv_field_choices") as batch:
        batch.add_column(sa.Column("label_text", sa.String(16), nullable=True))
    _seed()


def _seed():
    conn = op.get_bind()
    for kind_pos, (code, name, fields) in enumerate(KINDS, start=1):
        kind_id = _id(conn, kinds_t, code=code)
        if kind_id is None:
            conn.execute(kinds_t.insert().values(code=code, name=name, position=kind_pos,
                                                 is_active=True))
            kind_id = _id(conn, kinds_t, code=code)
        for pos, (fcode, fname, ftype, unit, choices, on_label, text) in enumerate(fields):
            field_id = _id(conn, fields_t, gear_kind_id=kind_id, code=fcode)
            label = {"position": pos, "on_label": on_label, "label_text": text}
            if field_id is None:
                conn.execute(fields_t.insert().values(
                    gear_kind_id=kind_id, code=fcode, name=fname, field_type=ftype,
                    unit_label=unit, is_active=True, **label))
                field_id = _id(conn, fields_t, gear_kind_id=kind_id, code=fcode)
            else:
                conn.execute(fields_t.update().where(fields_t.c.id == field_id)
                             .values(**label))
            for cpos, (clabel, ctext) in enumerate(choices, start=1):
                choice_id = _id(conn, choices_t, field_id=field_id, label=clabel)
                if choice_id is None:
                    conn.execute(choices_t.insert().values(
                        field_id=field_id, label=clabel, label_text=ctext, position=cpos,
                        is_active=True))
                else:
                    conn.execute(choices_t.update().where(choices_t.c.id == choice_id)
                                 .values(label_text=ctext, position=cpos))
        for kind_code, fcode in RETIRED:
            if kind_code != code:
                continue
            conn.execute(fields_t.update()
                         .where(fields_t.c.gear_kind_id == kind_id, fields_t.c.code == fcode)
                         .values(is_active=False, on_label=False, position=len(fields)))


def downgrade():
    with op.batch_alter_table("inv_field_choices") as batch:
        batch.drop_column("label_text")
    with op.batch_alter_table("inv_fields") as batch:
        batch.drop_column("label_text")
        batch.drop_column("on_label")
