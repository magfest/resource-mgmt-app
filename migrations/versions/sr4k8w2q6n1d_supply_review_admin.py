"""Add Supply review and admin-edit columns.

Revision ID: sr4k8w2q6n1d
Revises: ia6r3d8m2v5k
"""
import sqlalchemy as sa
from alembic import op

revision = "sr4k8w2q6n1d"
down_revision = "ia6r3d8m2v5k"
branch_labels = None
depends_on = None

_LINE_COLUMNS = [
    ("quantity_reviewed", sa.Integer()),
    ("department_quantity", sa.Integer()),
    ("removed_at", sa.DateTime()),
    ("removed_by_user_id", sa.String(64)),
    ("removed_reason", sa.Text()),
    ("added_by_admin_user_id", sa.String(64)),
]
_ORDER_COLUMNS = [
    ("closed_at", sa.DateTime()),
    ("closed_by_user_id", sa.String(64)),
    ("closed_reason", sa.Text()),
]


def _columns(table):
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def set_review_session_flags(conn):
    """Turn review sessions off for SUPPLY and TECHOPS. No-op when absent."""
    work_types = sa.table("work_types", sa.column("id", sa.Integer), sa.column("code", sa.String))
    configs = sa.table(
        "work_type_configs",
        sa.column("work_type_id", sa.Integer),
        sa.column("uses_review_sessions", sa.Boolean),
    )
    ids = sa.select(work_types.c.id).where(work_types.c.code.in_(["SUPPLY", "TECHOPS"]))
    conn.execute(
        sa.update(configs)
        .where(configs.c.work_type_id.in_(ids))
        .values(uses_review_sessions=False)
    )


def _add_missing(table, specs):
    existing = _columns(table)
    missing = [(n, t) for n, t in specs if n not in existing]
    if missing:
        with op.batch_alter_table(table) as batch:
            for name, type_ in missing:
                batch.add_column(sa.Column(name, type_, nullable=True))


def upgrade():
    if "uses_review_sessions" not in _columns("work_type_configs"):
        with op.batch_alter_table("work_type_configs") as batch:
            batch.add_column(sa.Column("uses_review_sessions", sa.Boolean(), nullable=False,
                                       server_default=sa.true()))
    _add_missing("supply_order_line_details", _LINE_COLUMNS)
    _add_missing("supply_order_details", _ORDER_COLUMNS)
    set_review_session_flags(op.get_bind())


def _drop_present(table, specs):
    existing = _columns(table)
    present = [n for n, _ in specs if n in existing]
    if present:
        with op.batch_alter_table(table) as batch:
            for name in present:
                batch.drop_column(name)


def downgrade():
    _drop_present("supply_order_details", _ORDER_COLUMNS)
    _drop_present("supply_order_line_details", _LINE_COLUMNS)
    _drop_present("work_type_configs", [("uses_review_sessions", None)])
