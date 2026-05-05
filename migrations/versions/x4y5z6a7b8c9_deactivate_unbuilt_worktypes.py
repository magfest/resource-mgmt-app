"""Deactivate CONTRACT, SUPPLY, TECHOPS work types

Three work types are deactivated by this migration:

* CONTRACT and SUPPLY have no requester UI yet and were appearing in the
  request-creation pickers, confusing users who'd click in and find
  nothing they could actually do. Hiding them via is_active=False keeps
  any pre-existing portfolios reachable by direct URL but removes them
  from new-entry-point pickers.
* TECHOPS shipped to staging on 2026-04-30 but is still in beta dry-run
  with Heather + Mark. Production should not surface it yet. Staging
  will flip TECHOPS back to is_active=True via the new admin Work
  Types page after this migration runs.

Revision ID: x4y5z6a7b8c9
Revises: w3x4y5z6a7b8
Create Date: 2026-05-04 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'x4y5z6a7b8c9'
down_revision = 'w3x4y5z6a7b8'
branch_labels = None
depends_on = None


CODES_TO_DEACTIVATE = ("CONTRACT", "SUPPLY", "TECHOPS")


def upgrade():
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE work_types SET is_active = :active "
            "WHERE code IN :codes"
        ).bindparams(sa.bindparam("codes", expanding=True)),
        {"active": False, "codes": list(CODES_TO_DEACTIVATE)},
    )


def downgrade():
    bind = op.get_bind()
    bind.execute(
        sa.text(
            "UPDATE work_types SET is_active = :active "
            "WHERE code IN :codes"
        ).bindparams(sa.bindparam("codes", expanding=True)),
        {"active": True, "codes": list(CODES_TO_DEACTIVATE)},
    )
