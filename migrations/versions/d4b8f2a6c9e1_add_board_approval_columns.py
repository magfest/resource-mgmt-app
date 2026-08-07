"""Add board approval columns to event_cycles and work_items

Departments may not be told their budget is final until the board approves the
event topline. EventCycle.board_approved_at is that single gate; it latches.
WorkItem.board_released_at records when a budget was released, and
finalized_notified_at records when its email left.

No new WorkItem.status value. Release state is derived from these two columns,
which leaves the 35 existing FINALIZED checks working untouched.

Nullable. Work items already FINALIZED before this deploy are backfilled to
both columns equal to finalized_at, since the old inline send already told
them; only work items finalized after this deploy wait on the event latch.
event_cycles.board_approved_at itself gets no backfill and starts NULL for
every event, since it should only govern finalizes from here forward.

Also adds work_type_configs.uses_board_release, which scopes the board hold to
BUDGET so the shared status derivation leaves other work types alone. Backfills
the flag to true for BUDGET, since the bootstrap seed only inserts new configs
and never updates one that already exists.

Revision ID: d4b8f2a6c9e1
Revises: c1d2e3f4a5b6
Create Date: 2026-08-05 12:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd4b8f2a6c9e1'
down_revision = 'c1d2e3f4a5b6'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('event_cycles') as batch_op:
        batch_op.add_column(sa.Column('board_approved_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('board_approved_by_user_id', sa.String(length=64), nullable=True))

    with op.batch_alter_table('work_items') as batch_op:
        batch_op.add_column(sa.Column('board_released_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('finalized_notified_at', sa.DateTime(), nullable=True))
        batch_op.create_index('ix_work_items_board_released_at', ['board_released_at'])
        batch_op.create_index('ix_work_items_finalized_notified_at', ['finalized_notified_at'])

    with op.batch_alter_table('work_type_configs') as batch_op:
        batch_op.add_column(sa.Column(
            'uses_board_release', sa.Boolean(), nullable=False,
            server_default=sa.false(),
        ))

    # bootstrap.py only inserts a WorkTypeConfig when one is absent; it never
    # updates an existing row. Without this backfill, an existing BUDGET
    # config keeps the server_default False and the board hold never engages.
    bind = op.get_bind()
    bind.execute(sa.text("""
        UPDATE work_type_configs
           SET uses_board_release = :true_val
         WHERE work_type_id IN (
            SELECT id FROM work_types WHERE code = 'BUDGET'
         )
    """), {"true_val": True})

    # Budgets finalized before this deploy were already emailed by the old
    # inline send at finalize time. Without this backfill they read NULL for
    # both columns and would display "Pending Board Approval", inflate the
    # release page topline, and get a second "finalized" email once an admin
    # records board approval for their event. Runs after the
    # uses_board_release backfill above, since it filters on that flag.
    bind.execute(sa.text("""
        UPDATE work_items
           SET board_released_at = finalized_at,
               finalized_notified_at = finalized_at
         WHERE status = :finalized_status
           AND finalized_at IS NOT NULL
           AND board_released_at IS NULL
           AND portfolio_id IN (
                SELECT wp.id
                  FROM work_portfolios wp
                  JOIN work_type_configs wtc ON wtc.work_type_id = wp.work_type_id
                 WHERE wtc.uses_board_release = :true_val
           )
    """), {"finalized_status": "FINALIZED", "true_val": True})


def downgrade():
    with op.batch_alter_table('work_type_configs') as batch_op:
        batch_op.drop_column('uses_board_release')

    with op.batch_alter_table('work_items') as batch_op:
        batch_op.drop_index('ix_work_items_finalized_notified_at')
        batch_op.drop_index('ix_work_items_board_released_at')
        batch_op.drop_column('finalized_notified_at')
        batch_op.drop_column('board_released_at')

    with op.batch_alter_table('event_cycles') as batch_op:
        batch_op.drop_column('board_approved_by_user_id')
        batch_op.drop_column('board_approved_at')
