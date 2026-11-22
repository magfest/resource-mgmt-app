"""Add the projector, barcode scanner and camera gear kinds

Each kind gets one MODEL text field. Rows are guarded by code, so a database that
already has any of them keeps its rows and positions.

Revision ID: iv6h2r9c3m5p
Revises: iv3k8w1n5q7c
Create Date: 2026-11-07

"""
from alembic import op
import sqlalchemy as sa


revision = "iv6h2r9c3m5p"
down_revision = "iv3k8w1n5q7c"
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

KINDS = [("PROJECTOR", "Projector"), ("BARCODE_SCANNER", "Barcode scanner"),
         ("CAMERA", "Camera")]


def _id(conn, table, **keys):
    where = [table.c[name] == value for name, value in keys.items()]
    return conn.execute(sa.select(table.c.id).where(*where)).scalar()


def upgrade():
    conn = op.get_bind()
    for code, name in KINDS:
        kind_id = _id(conn, kinds_t, code=code)
        if kind_id is None:
            # New kinds go after every existing one, whatever its position.
            last = conn.execute(sa.select(sa.func.max(kinds_t.c.position))).scalar() or 0
            conn.execute(kinds_t.insert().values(code=code, name=name, position=last + 1,
                                                 is_active=True))
            kind_id = _id(conn, kinds_t, code=code)
        if _id(conn, fields_t, gear_kind_id=kind_id, code="MODEL") is None:
            conn.execute(fields_t.insert().values(
                gear_kind_id=kind_id, code="MODEL", name="Model", field_type="TEXT",
                unit_label=None, position=0, is_active=True, on_label=False,
                label_text=None))


def downgrade():
    # Units and supply items may reference these kinds. The rows stay; they are reference data.
    pass
