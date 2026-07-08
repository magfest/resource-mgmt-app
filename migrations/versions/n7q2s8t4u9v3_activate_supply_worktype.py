"""Activate SUPPLY WorkType

Flips work_types.is_active to True for the SUPPLY row so the worktype
appears in user-facing pickers (admin user-role editor, division/
department config, request creation pickers). The seed already lists
SUPPLY as active=True, but seed_work_types only updates existing rows
when re-seeded — environments that ran an earlier seed (where SUPPLY
was active=False) need this migration to flip the row in place.

Note: The uses_dispatch flag was already flipped in the preceding
supply-groups-and-flags migration (k4m9p2q7r1s6). This migration
ONLY flips is_active.

Revision ID: n7q2s8t4u9v3
Revises: h8t3v6w1x9y4
Create Date: 2026-07-06 00:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'n7q2s8t4u9v3'
down_revision = 'h8t3v6w1x9y4'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    bind.execute(
        sa.text("UPDATE work_types SET is_active = :active WHERE code = :code"),
        {"active": True, "code": "SUPPLY"},
    )


def downgrade():
    bind = op.get_bind()
    bind.execute(
        sa.text("UPDATE work_types SET is_active = :active WHERE code = :code"),
        {"active": False, "code": "SUPPLY"},
    )
