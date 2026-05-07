"""Backfill AV_TEAM ApprovalGroup and AV WorkTypeConfig.default_approval_group_id.

For existing databases that have the AV WorkType row from earlier seeding
but predate the AV_TEAM ApprovalGroup. Idempotent: skips if already present.

Revision ID: g3h4i5j6k7l8
Revises: f2g3h4i5j6k7
Create Date: 2026-05-06 12:04:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'g3h4i5j6k7l8'
down_revision = 'f2g3h4i5j6k7'
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()

    # Find AV work type
    av_wt_row = bind.execute(sa.text(
        "SELECT id FROM work_types WHERE code = 'AV'"
    )).first()
    if not av_wt_row:
        return  # AV worktype not present; nothing to do
    av_wt_id = av_wt_row[0]

    # Insert AV_TEAM approval group if missing
    existing = bind.execute(sa.text(
        "SELECT id FROM approval_groups WHERE work_type_id = :wt_id AND code = 'AV_TEAM'"
    ), {"wt_id": av_wt_id}).first()

    if not existing:
        bind.execute(sa.text(
            "INSERT INTO approval_groups (work_type_id, code, name, description, is_active, sort_order) "
            "VALUES (:wt_id, 'AV_TEAM', 'AV Team', "
            "'Reviews and plans AV requests for shared spaces', :is_active, 10)"
        ), {"wt_id": av_wt_id, "is_active": True})

    # Find AV_TEAM id (just inserted or pre-existing)
    av_team_id = bind.execute(sa.text(
        "SELECT id FROM approval_groups WHERE work_type_id = :wt_id AND code = 'AV_TEAM'"
    ), {"wt_id": av_wt_id}).first()[0]

    # Update WorkTypeConfig.default_approval_group_id if NULL
    bind.execute(sa.text(
        "UPDATE work_type_configs SET default_approval_group_id = :ag_id "
        "WHERE work_type_id = :wt_id AND default_approval_group_id IS NULL"
    ), {"ag_id": av_team_id, "wt_id": av_wt_id})


def downgrade():
    bind = op.get_bind()

    av_wt_row = bind.execute(sa.text(
        "SELECT id FROM work_types WHERE code = 'AV'"
    )).first()
    if not av_wt_row:
        return
    av_wt_id = av_wt_row[0]

    # Null out the FK first, then delete the approval group
    bind.execute(sa.text(
        "UPDATE work_type_configs SET default_approval_group_id = NULL "
        "WHERE work_type_id = :wt_id"
    ), {"wt_id": av_wt_id})
    bind.execute(sa.text(
        "DELETE FROM approval_groups WHERE work_type_id = :wt_id AND code = 'AV_TEAM'"
    ), {"wt_id": av_wt_id})
