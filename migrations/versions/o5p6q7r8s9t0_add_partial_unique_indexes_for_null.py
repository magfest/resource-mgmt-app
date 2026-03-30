"""Add partial unique indexes for NULL-safe uniqueness on PostgreSQL

The standard unique constraints on work_line_reviews and user_roles don't
prevent duplicates when nullable columns (approval_group_id, work_type_id)
are NULL, because SQL treats NULL != NULL. These partial indexes enforce
uniqueness on the NULL subsets in PostgreSQL.

SQLite does not support partial indexes via CREATE INDEX ... WHERE, so
these are conditionally applied only on PostgreSQL. Application-level
guards (SELECT ... FOR UPDATE) protect both databases.

Revision ID: o5p6q7r8s9t0
Revises: 3ef69c594552
Create Date: 2026-03-29 23:30:00.000000

"""
from alembic import op

# revision identifiers, used by Alembic.
revision = 'o5p6q7r8s9t0'
down_revision = '3ef69c594552'
branch_labels = None
depends_on = None


def upgrade():
    # Only create partial indexes on PostgreSQL (SQLite doesn't support WHERE in CREATE INDEX)
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        return

    # -- work_line_reviews: prevent duplicate ADMIN_FINAL reviews per line --
    op.execute(
        "CREATE UNIQUE INDEX ix_wlr_admin_final_unique "
        "ON work_line_reviews (work_line_id, stage) "
        "WHERE approval_group_id IS NULL"
    )

    # -- user_roles: prevent duplicate global roles (both scoping columns NULL) --
    op.execute(
        "CREATE UNIQUE INDEX ix_user_roles_global_unique "
        "ON user_roles (user_id, role_code) "
        "WHERE work_type_id IS NULL AND approval_group_id IS NULL"
    )

    # -- user_roles: prevent duplicate work-type-scoped roles --
    op.execute(
        "CREATE UNIQUE INDEX ix_user_roles_worktype_unique "
        "ON user_roles (user_id, role_code, work_type_id) "
        "WHERE work_type_id IS NOT NULL AND approval_group_id IS NULL"
    )

    # -- user_roles: prevent duplicate approval-group-scoped roles --
    op.execute(
        "CREATE UNIQUE INDEX ix_user_roles_approvalgroup_unique "
        "ON user_roles (user_id, role_code, approval_group_id) "
        "WHERE work_type_id IS NULL AND approval_group_id IS NOT NULL"
    )


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name != 'postgresql':
        return

    op.execute("DROP INDEX IF EXISTS ix_wlr_admin_final_unique")
    op.execute("DROP INDEX IF EXISTS ix_user_roles_global_unique")
    op.execute("DROP INDEX IF EXISTS ix_user_roles_worktype_unique")
    op.execute("DROP INDEX IF EXISTS ix_user_roles_approvalgroup_unique")
