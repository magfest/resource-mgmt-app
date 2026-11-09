"""Add is_fixed to inventory container kinds.

Revision ID: iy4f7k2m9q3s
Revises: ix8p3k6v2n5d
"""
import sqlalchemy as sa
from alembic import op

revision = "iy4f7k2m9q3s"
down_revision = "ix8p3k6v2n5d"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("inv_container_kinds") as batch:
        batch.add_column(sa.Column("is_fixed", sa.Boolean(), nullable=False,
                                   server_default=sa.false()))
    # Production is never seeded; the bound UPDATE does nothing when LOCATION is absent.
    op.get_bind().execute(
        sa.text("UPDATE inv_container_kinds SET is_fixed = :t WHERE code = 'LOCATION'"),
        {"t": True})


def downgrade():
    with op.batch_alter_table("inv_container_kinds") as batch:
        batch.drop_column("is_fixed")
