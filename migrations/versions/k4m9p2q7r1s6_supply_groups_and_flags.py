"""SUPPLY-scoped approval groups, category remap, uses_dispatch=False

Idempotent data migration: inserts the three SUPPLY approval groups if
missing, remaps supply_categories.approval_group_id off BUDGET-scoped
groups, and flips the SUPPLY WorkTypeConfig to uses_dispatch=False.
Seeds handle fresh databases; this handles environments seeded earlier.

Revision ID: k4m9p2q7r1s6
Revises: ext0001flag2
Create Date: 2026-07-06

"""
from alembic import op
import sqlalchemy as sa

revision = 'k4m9p2q7r1s6'
down_revision = 'ext0001flag2'
branch_labels = None
depends_on = None

GROUPS = [
    ("SUPPLY_GEN", "Supply — General", "Reviews office, event, and signage supply orders", 10),
    ("SUPPLY_TECH", "Supply — Tech", "Reviews technical equipment supply orders", 20),
    ("SUPPLY_LOG", "Supply — Logistics/Safety", "Reviews safety and medical supply orders", 30),
]
CATEGORY_TO_GROUP = {
    "OFFICE": "SUPPLY_GEN", "EVENT": "SUPPLY_GEN", "SIGNAGE": "SUPPLY_GEN",
    "TECH": "SUPPLY_TECH", "SAFETY": "SUPPLY_LOG",
}


def upgrade():
    bind = op.get_bind()
    wt_id = bind.execute(
        sa.text("SELECT id FROM work_types WHERE code = 'SUPPLY'")
    ).scalar()
    if wt_id is None:
        return  # SUPPLY never seeded here; seeds will do everything

    for code, name, description, sort_order in GROUPS:
        exists = bind.execute(
            sa.text("SELECT id FROM approval_groups WHERE work_type_id = :wt AND code = :c"),
            {"wt": wt_id, "c": code},
        ).scalar()
        if exists is None:
            bind.execute(
                sa.text(
                    "INSERT INTO approval_groups "
                    "(work_type_id, code, name, description, is_active, sort_order, created_at, updated_at) "
                    "VALUES (:wt, :c, :n, :d, :a, :s, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {"wt": wt_id, "c": code, "n": name, "d": description, "a": True, "s": sort_order},
            )

    for cat_code, group_code in CATEGORY_TO_GROUP.items():
        bind.execute(
            sa.text(
                "UPDATE supply_categories SET approval_group_id = ("
                "  SELECT id FROM approval_groups WHERE work_type_id = :wt AND code = :g"
                ") WHERE code = :cat"
            ),
            {"wt": wt_id, "g": group_code, "cat": cat_code},
        )

    bind.execute(
        sa.text("UPDATE work_type_configs SET uses_dispatch = :f WHERE work_type_id = :wt"),
        {"f": False, "wt": wt_id},
    )


def downgrade():
    pass  # data-only forward fix; reverting the mapping would restore a crash
