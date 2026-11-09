"""Add inventory sign-in credentials, shift owners and the keypad lock.

Revision ID: k7s2n9c4w1pd
Revises: iy4f7k2m9q3s
"""
import sqlalchemy as sa
from alembic import op

revision = "k7s2n9c4w1pd"
down_revision = "iy4f7k2m9q3s"
branch_labels = None
depends_on = None


def _has_table(name):
    return sa.inspect(op.get_bind()).has_table(name)


def _columns(table):
    return {c["name"] for c in sa.inspect(op.get_bind()).get_columns(table)}


def upgrade():
    if not _has_table("inv_credentials"):
        op.create_table(
            "inv_credentials",
            sa.Column("user_id", sa.String(64), nullable=False),
            sa.Column("pin_digest", sa.String(64), nullable=True),
            sa.Column("pin_set_at", sa.DateTime(), nullable=True),
            sa.Column("card_digest", sa.String(64), nullable=True),
            sa.Column("card_printed_at", sa.DateTime(), nullable=True),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_inv_credentials_user_id"),
            sa.PrimaryKeyConstraint("user_id"),
            sa.UniqueConstraint("pin_digest", name="uq_inv_credentials_pin_digest"),
            sa.UniqueConstraint("card_digest", name="uq_inv_credentials_card_digest"),
        )

    if "user_id" not in _columns("inv_shifts"):
        with op.batch_alter_table("inv_shifts") as batch:
            batch.add_column(sa.Column("user_id", sa.String(64), nullable=True))
            batch.create_foreign_key("fk_inv_shifts_user_id", "users", ["user_id"], ["id"])
            batch.create_index("ix_inv_shifts_user_id", ["user_id"])
    with op.batch_alter_table("inv_shifts") as batch:
        batch.alter_column("person_name", existing_type=sa.String(64), nullable=True)

    if "pin_failures" not in _columns("inv_devices"):
        with op.batch_alter_table("inv_devices") as batch:
            batch.add_column(sa.Column("pin_failures", sa.Integer(), nullable=False,
                                       server_default=sa.text("0")))
            batch.add_column(sa.Column("pin_locked_until", sa.DateTime(), nullable=True))

    # A route is admin configuration, not reference data; copy it only if the unit route exists.
    bind = op.get_bind()
    unit = bind.execute(sa.text(
        "SELECT printer_id FROM inv_print_routes WHERE job_type = 'OWNED_UNIT'")).first()
    has_card = bind.execute(sa.text(
        "SELECT 1 FROM inv_print_routes WHERE job_type = 'SIGNIN_CARD'")).first()
    if unit and not has_card:
        bind.execute(
            sa.text("INSERT INTO inv_print_routes (job_type, printer_id) VALUES (:j, :p)"),
            {"j": "SIGNIN_CARD", "p": unit[0]})


def downgrade():
    bind = op.get_bind()
    bind.execute(sa.text("DELETE FROM inv_print_routes WHERE job_type = 'SIGNIN_CARD'"))

    if "pin_failures" in _columns("inv_devices"):
        with op.batch_alter_table("inv_devices") as batch:
            batch.drop_column("pin_locked_until")
            batch.drop_column("pin_failures")

    # Shifts without a typed name cannot satisfy NOT NULL. Every table
    # that references a shift is detached first.
    for table in ("inv_counts", "inv_movements", "inv_checks", "inv_prep_status", "inv_flags"):
        if "shift_id" in _columns(table):
            bind.execute(sa.text(
                f"UPDATE {table} SET shift_id = NULL WHERE shift_id IN "
                "(SELECT id FROM inv_shifts WHERE person_name IS NULL)"))
    bind.execute(sa.text("DELETE FROM inv_shifts WHERE person_name IS NULL"))
    with op.batch_alter_table("inv_shifts") as batch:
        batch.alter_column("person_name", existing_type=sa.String(64), nullable=False)
    if "user_id" in _columns("inv_shifts"):
        with op.batch_alter_table("inv_shifts") as batch:
            batch.drop_index("ix_inv_shifts_user_id")
            batch.drop_constraint("fk_inv_shifts_user_id", type_="foreignkey")
            batch.drop_column("user_id")

    if _has_table("inv_credentials"):
        op.drop_table("inv_credentials")
