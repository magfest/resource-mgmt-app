"""Add allow_early_supplementary flag to event_cycles

Lets admins relax the rule that "a supplementary budget request can
only be created after the primary is FINALIZED" on a per-event basis.
Used for events like the FY corporate-budget cycle where a department
intentionally splits its budget into multiple sibling requests
(e.g. capex + opex, or rent + everything else) and shouldn't be
forced to wait for the primary to be finalized first.

Defaults to False so existing event cycles keep the strict rule.

Revision ID: y5z6a7b8c9d0
Revises: x4y5z6a7b8c9
Create Date: 2026-05-04 12:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'y5z6a7b8c9d0'
down_revision = 'x4y5z6a7b8c9'
branch_labels = None
depends_on = None


def _has_column(bind, table: str, column: str) -> bool:
    insp = sa.inspect(bind)
    return any(c["name"] == column for c in insp.get_columns(table))


def upgrade():
    # Idempotent: in dev environments, ``db.create_all()`` may have
    # already added the column from the model definition before this
    # migration ran. Skip the ALTER TABLE when the column is already
    # present so dev DBs don't fail the upgrade with "duplicate column".
    bind = op.get_bind()
    if _has_column(bind, 'event_cycles', 'allow_early_supplementary'):
        return

    op.add_column(
        'event_cycles',
        sa.Column(
            'allow_early_supplementary',
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade():
    bind = op.get_bind()
    if not _has_column(bind, 'event_cycles', 'allow_early_supplementary'):
        return
    op.drop_column('event_cycles', 'allow_early_supplementary')
