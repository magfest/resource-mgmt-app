"""Keep unit notes as typed entries that are never overwritten

Adds inv_unit_notes and copies each unit's notes and imported_notes into it as
ADMIN notes. The two old columns stay in place and are not read after this.

Revision ID: ix8p3k6v2n5d
Revises: iw5u2n8k4r7d
Create Date: 2026-11-08

"""
from alembic import op
import sqlalchemy as sa


revision = "ix8p3k6v2n5d"
down_revision = "iw5u2n8k4r7d"
branch_labels = None
depends_on = None

# Both copies share the unit's created_at and ids break the tie, so EARLIER goes in last to read as the newest note.
# (old column, note source)
COPIES = (("imported_notes", "IMPORT"), ("notes", "EARLIER"))


def upgrade():
    op.create_table(
        "inv_unit_notes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("unit_id", sa.Integer(),
                  sa.ForeignKey("inv_units.id", name="fk_inv_unit_notes_unit_id"),
                  nullable=False),
        sa.Column("note_type", sa.String(16), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("created_by_user_id", sa.String(64), nullable=True),
    )
    op.create_index("ix_inv_unit_notes_unit_id", "inv_unit_notes", ["unit_id"])
    op.create_index("ix_inv_unit_notes_created_at", "inv_unit_notes", ["created_at"])

    conn = op.get_bind()
    for column, source in COPIES:
        conn.execute(
            sa.text(
                "INSERT INTO inv_unit_notes "
                "(unit_id, note_type, source, body, created_at, created_by_user_id) "
                f"SELECT id, 'ADMIN', :source, {column}, created_at, NULL FROM inv_units "
                f"WHERE {column} IS NOT NULL AND TRIM({column}) <> ''"),
            {"source": source})


def downgrade():
    conn = op.get_bind()
    for column, source in COPIES:
        rows = conn.execute(
            sa.text("SELECT unit_id, body FROM inv_unit_notes WHERE source = :source "
                    "ORDER BY unit_id, created_at, id"),
            {"source": source}).fetchall()
        joined = {}
        for unit_id, body in rows:
            joined.setdefault(unit_id, []).append(body)
        for unit_id, bodies in joined.items():
            conn.execute(
                sa.text(f"UPDATE inv_units SET {column} = :body WHERE id = :id"),
                {"body": "\n\n".join(bodies), "id": unit_id})
    op.drop_index("ix_inv_unit_notes_created_at", table_name="inv_unit_notes")
    op.drop_index("ix_inv_unit_notes_unit_id", table_name="inv_unit_notes")
    op.drop_table("inv_unit_notes")
