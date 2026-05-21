"""Merge submission_confirmation with work_item_comments updated_at heads

No-op merge to unify two parallel migration heads that landed on
master from independent branches and forked the revision graph:

- a7b8c9d0e1f2  Add updated_at to work_item_comments  (from fix/request-edit-form-updates)
- a8b9c0d1e2f3  Add submission_confirmation template  (from feature/submission-confirmation-email)

Both branched off y5z6a7b8c9d0 and merged to master, leaving Alembic
with two heads. `flask db upgrade` cannot pick a target when multiple
heads exist (release script crashed on Heroku staging 2026-05-21).
This merge migration's down_revision tuple joins them so upgrade
resolves to a single head again. No schema work — the underlying
migrations stay independent.

Revision ID: 33f688ff7587
Revises: a7b8c9d0e1f2, a8b9c0d1e2f3
Create Date: 2026-05-20 22:50:14.515904

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '33f688ff7587'
down_revision = ('a7b8c9d0e1f2', 'a8b9c0d1e2f3')
branch_labels = None
depends_on = None


def upgrade():
    pass


def downgrade():
    pass
