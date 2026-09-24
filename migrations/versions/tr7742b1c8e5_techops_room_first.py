"""TechOps room-first: space on lines, phone split, per-space answers

Revision ID: tr7742b1c8e5
Revises: sp5539947d06

Off-sequence id on purpose, to avoid a head collision with concurrent
migration work.
"""
from datetime import datetime

import sqlalchemy as sa
from alembic import op

revision = 'tr7742b1c8e5'
down_revision = 'sp5539947d06'
branch_labels = None
depends_on = None


def upgrade():
    # SQLite cannot ALTER a table to add a constrained column, so every
    # add_column runs inside a batch context. Postgres ignores the batching.
    with op.batch_alter_table('techops_line_details', schema=None) as batch_op:
        batch_op.add_column(sa.Column('space_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('parent_line_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('purpose', sa.String(length=8), nullable=True))
        batch_op.add_column(sa.Column('internal_only', sa.Boolean(), nullable=False,
                                      server_default=sa.false()))
        batch_op.create_foreign_key('fk_techops_line_details_space_id',
                                    'spaces', ['space_id'], ['id'])
        batch_op.create_foreign_key('fk_techops_line_details_parent_line_id',
                                    'work_lines', ['parent_line_id'], ['id'])
        batch_op.create_index('ix_techops_line_details_space_id', ['space_id'])
        batch_op.create_index('ix_techops_line_details_parent_line_id',
                              ['parent_line_id'])

    with op.batch_alter_table('techops_request_details', schema=None) as batch_op:
        batch_op.add_column(sa.Column('shared_space_confirmed', sa.Boolean(),
                                      nullable=False, server_default=sa.false()))

    op.create_table(
        'techops_request_spaces',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('work_item_id', sa.Integer(), nullable=False),
        sa.Column('space_id', sa.Integer(), nullable=False),
        sa.Column('answer', sa.String(length=8), nullable=False),
        sa.Column('no_services_reason', sa.Text(), nullable=True),
        sa.Column('wifi_requested', sa.Boolean(), nullable=True),
        sa.Column('wifi_declined_reason', sa.Text(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['work_item_id'], ['work_items.id'],
                                name='fk_techops_request_spaces_work_item_id'),
        sa.ForeignKeyConstraint(['space_id'], ['spaces.id'],
                                name='fk_techops_request_spaces_space_id'),
        sa.UniqueConstraint('work_item_id', 'space_id',
                            name='uq_techops_request_spaces_item_space'),
    )
    op.create_index('ix_techops_request_spaces_work_item_id',
                    'techops_request_spaces', ['work_item_id'])
    op.create_index('ix_techops_request_spaces_space_id',
                    'techops_request_spaces', ['space_id'])

    # `answer` allows NULL for a space the Task 10 picker added but the
    # requester has not yet answered "NEEDS"/"NOTHING". An assigned card
    # could always be rebuilt unanswered from the department's assignment
    # list; a picked space has no other record of being on the request,
    # so an unanswered pick has to persist or it is lost on the next save.
    # SQLite cannot alter a column in place, hence batch mode; Postgres
    # runs a plain ALTER under it. Fixed here rather than in a follow-up
    # migration because this table was created a few lines up, in this
    # same unreleased revision.
    with op.batch_alter_table('techops_request_spaces', schema=None) as batch_op:
        batch_op.alter_column('answer', existing_type=sa.String(length=8),
                              nullable=True)

    # Staging holds dry-run TECHOPS data only and production holds none, so
    # the old flat requests are removed rather than carried at two line
    # grains. Every table holding a TECHOPS work line or work item, read
    # off app/models/ on 2026-09-22, children before parents: SQLite does
    # not enforce foreign keys, so a wrong order passes there and fails on
    # PostgreSQL.
    #   by work_line_id: techops_line_details, work_line_audit_events,
    #                    work_line_comments, work_line_reviews,
    #                    activity_events
    #   by work_item_id: techops_request_details, techops_request_spaces,
    #                    work_item_audit_events, work_item_comments,
    #                    activity_events, notification_logs
    #   then work_lines, then work_items
    # email_outbox.work_item_id is ondelete=SET NULL, so it needs no delete.
    conn = op.get_bind()

    techops_lines_subquery = """
        SELECT wl.id FROM work_lines wl
        JOIN work_items wi ON wi.id = wl.work_item_id
        JOIN work_portfolios wp ON wp.id = wi.portfolio_id
        JOIN work_types wt ON wt.id = wp.work_type_id
        WHERE wt.code = 'TECHOPS'
    """
    techops_items_subquery = """
        SELECT wi.id FROM work_items wi
        JOIN work_portfolios wp ON wp.id = wi.portfolio_id
        JOIN work_types wt ON wt.id = wp.work_type_id
        WHERE wt.code = 'TECHOPS'
    """

    # techops_line_details.parent_line_id (added above in this migration)
    # points at another work_lines row inside the same deleted set. Null it
    # out first or the delete order becomes self-referential.
    conn.execute(sa.text(f"""
        UPDATE techops_line_details SET parent_line_id = NULL
        WHERE work_line_id IN ({techops_lines_subquery})
    """))

    conn.execute(sa.text(f"""
        DELETE FROM techops_line_details WHERE work_line_id IN ({techops_lines_subquery})
    """))
    conn.execute(sa.text(f"""
        DELETE FROM work_line_audit_events WHERE work_line_id IN ({techops_lines_subquery})
    """))
    conn.execute(sa.text(f"""
        DELETE FROM work_line_comments WHERE work_line_id IN ({techops_lines_subquery})
    """))
    conn.execute(sa.text(f"""
        DELETE FROM work_line_reviews WHERE work_line_id IN ({techops_lines_subquery})
    """))
    conn.execute(sa.text(f"""
        DELETE FROM activity_events WHERE work_line_id IN ({techops_lines_subquery})
    """))

    conn.execute(sa.text(f"""
        DELETE FROM techops_request_details WHERE work_item_id IN ({techops_items_subquery})
    """))
    conn.execute(sa.text(f"""
        DELETE FROM techops_request_spaces WHERE work_item_id IN ({techops_items_subquery})
    """))
    conn.execute(sa.text(f"""
        DELETE FROM work_item_audit_events WHERE work_item_id IN ({techops_items_subquery})
    """))
    conn.execute(sa.text(f"""
        DELETE FROM work_item_comments WHERE work_item_id IN ({techops_items_subquery})
    """))
    conn.execute(sa.text(f"""
        DELETE FROM activity_events WHERE work_item_id IN ({techops_items_subquery})
    """))
    conn.execute(sa.text(f"""
        DELETE FROM notification_logs WHERE work_item_id IN ({techops_items_subquery})
    """))

    conn.execute(sa.text(f"""
        DELETE FROM work_lines WHERE id IN ({techops_lines_subquery})
    """))
    conn.execute(sa.text(f"""
        DELETE FROM work_items WHERE id IN ({techops_items_subquery})
    """))

    # Room-first splits PHONE into PHONE_NUMBER and DESK_PHONE. The row
    # stays for rollback and historical line preservation, matching the
    # BANDWIDTH precedent in seed_techops_service_types.
    conn.execute(sa.text(
        "UPDATE techops_service_types SET is_active = 0 WHERE code = 'PHONE'"))

    techops_net_id = conn.execute(sa.text(
        "SELECT id FROM approval_groups WHERE code = 'TECHOPS_NET'")).scalar()
    techops_gen_id = conn.execute(sa.text(
        "SELECT id FROM approval_groups WHERE code = 'TECHOPS_GEN'")).scalar()
    if techops_net_id is None or techops_gen_id is None:
        raise RuntimeError(
            "approval_groups TECHOPS_NET and TECHOPS_GEN must exist before "
            "this migration runs; seed approval groups first.")

    # created_at/updated_at are NOT NULL with no server default (app-side
    # default only), so the raw insert must supply them. Descriptions go
    # through bind params: adjacent quoted SQL literals across lines are
    # not valid SQL, unlike Python's implicit string concatenation.
    now = datetime.utcnow()
    phone_number_desc = (
        "One number to configure. Purpose, caller ID, and how calls "
        "are delivered. Handsets are requested separately."
    )
    desk_phone_desc = (
        "One handset to provision and physically place. Rings the "
        "number on its parent line."
    )
    no_services_desc = (
        "A space the department confirmed needs nothing. Reviewed so "
        "an empty room does not go unanswered."
    )
    conn.execute(sa.text("""
        INSERT INTO techops_service_types
            (code, name, description, default_approval_group_id, is_active,
             sort_order, instance_noun, created_at, updated_at)
        VALUES
            ('PHONE_NUMBER', 'Phone number', :phone_number_desc,
             :net_id, 1, 41, 'phone line', :now, :now),
            ('DESK_PHONE', 'Desk phone', :desk_phone_desc,
             :net_id, 1, 42, 'desk phone', :now, :now),
            ('NO_SERVICES', 'No services needed', :no_services_desc,
             :gen_id, 1, 70, NULL, :now, :now)
    """), {
        "net_id": techops_net_id, "gen_id": techops_gen_id, "now": now,
        "phone_number_desc": phone_number_desc,
        "desk_phone_desc": desk_phone_desc,
        "no_services_desc": no_services_desc,
    })


def downgrade():
    # The TECHOPS work items and lines deleted in upgrade() are gone for
    # good; this reverses the schema and catalog changes only, not that
    # data loss.
    conn = op.get_bind()
    conn.execute(sa.text(
        "DELETE FROM techops_service_types "
        "WHERE code IN ('PHONE_NUMBER', 'DESK_PHONE', 'NO_SERVICES')"))
    conn.execute(sa.text(
        "UPDATE techops_service_types SET is_active = 1 WHERE code = 'PHONE'"))

    op.drop_table('techops_request_spaces')
    with op.batch_alter_table('techops_request_details', schema=None) as batch_op:
        batch_op.drop_column('shared_space_confirmed')
    with op.batch_alter_table('techops_line_details', schema=None) as batch_op:
        batch_op.drop_index('ix_techops_line_details_parent_line_id')
        batch_op.drop_index('ix_techops_line_details_space_id')
        batch_op.drop_column('internal_only')
        batch_op.drop_column('purpose')
        batch_op.drop_column('parent_line_id')
        batch_op.drop_column('space_id')
