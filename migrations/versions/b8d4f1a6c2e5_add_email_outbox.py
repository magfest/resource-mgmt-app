"""add email outbox

Creates email_outbox, email_suppression, and email_message_bodies. Adds
event_cycle_id and three delivery-tracking columns to notification_logs.

The two new BigInteger PKs use with_variant(Integer(), "sqlite") to match
notification_logs.id (see e7a1c3d9f0b2): SQLite's rowid autoincrement only
applies to a column declared exactly INTEGER PRIMARY KEY, and a plain BIGINT
PRIMARY KEY fails NOT NULL on every insert there.

Revision ID: b8d4f1a6c2e5
Revises: e7a1c3d9f0b2
Create Date: 2026-08-23 17:31:59.919948

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b8d4f1a6c2e5'
down_revision = 'e7a1c3d9f0b2'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'email_outbox',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
        sa.Column('template_key', sa.String(length=64), nullable=False),
        sa.Column('recipient_email', sa.String(length=256), nullable=False),
        sa.Column('recipient_user_id', sa.String(length=64), nullable=True),
        sa.Column('dispatch_at', sa.DateTime(), nullable=False),
        sa.Column('status', sa.String(length=16), nullable=False),
        sa.Column('work_item_id', sa.Integer(), nullable=True),
        sa.Column('event_cycle_id', sa.Integer(), nullable=True),
        sa.Column('department_id', sa.Integer(), nullable=True),
        sa.Column('work_type_id', sa.Integer(), nullable=True),
        sa.Column('context_json', sa.Text(), nullable=True),
        sa.Column('dedup_key', sa.String(length=256), nullable=True),
        sa.Column('claimed_at', sa.DateTime(), nullable=True),
        sa.Column('claimed_by', sa.String(length=64), nullable=True),
        sa.Column('attempt_count', sa.Integer(), nullable=False),
        sa.Column('blocked_since', sa.DateTime(), nullable=True),
        sa.Column('last_error', sa.Text(), nullable=True),
        sa.Column('sent_at', sa.DateTime(), nullable=True),
        sa.Column('provider_message_id', sa.String(length=128), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['work_item_id'], ['work_items.id'], name='fk_email_outbox_work_item_id', ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['event_cycle_id'], ['event_cycles.id'], name='fk_email_outbox_event_cycle_id', ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['department_id'], ['departments.id'], name='fk_email_outbox_department_id', ondelete='SET NULL'),
        sa.ForeignKeyConstraint(['work_type_id'], ['work_types.id'], name='fk_email_outbox_work_type_id', ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('dedup_key'),
    )
    with op.batch_alter_table('email_outbox', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_email_outbox_template_key'), ['template_key'], unique=False)
        batch_op.create_index(batch_op.f('ix_email_outbox_dispatch_at'), ['dispatch_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_email_outbox_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_email_outbox_work_item_id'), ['work_item_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_email_outbox_event_cycle_id'), ['event_cycle_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_email_outbox_department_id'), ['department_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_email_outbox_work_type_id'), ['work_type_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_email_outbox_claimed_by'), ['claimed_by'], unique=False)
        batch_op.create_index(batch_op.f('ix_email_outbox_created_at'), ['created_at'], unique=False)
        batch_op.create_index('ix_email_outbox_status_dispatch', ['status', 'dispatch_at'], unique=False)

    op.create_table(
        'email_suppression',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('email', sa.String(length=256), nullable=False),
        sa.Column('reason', sa.String(length=256), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by_user_id', sa.String(length=64), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('email'),
    )
    with op.batch_alter_table('email_suppression', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_email_suppression_email'), ['email'], unique=True)

    op.create_table(
        'email_message_bodies',
        sa.Column('id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
        sa.Column('notification_log_id', sa.BigInteger().with_variant(sa.Integer(), 'sqlite'), nullable=False),
        sa.Column('subject', sa.String(length=256), nullable=True),
        sa.Column('body_text', sa.Text(), nullable=True),
        sa.Column('body_html', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(['notification_log_id'], ['notification_logs.id'], name='fk_email_message_bodies_log_id', ondelete='CASCADE'),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('notification_log_id'),
    )
    with op.batch_alter_table('email_message_bodies', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_email_message_bodies_notification_log_id'), ['notification_log_id'], unique=True)
        batch_op.create_index(batch_op.f('ix_email_message_bodies_created_at'), ['created_at'], unique=False)

    with op.batch_alter_table('notification_logs', schema=None) as batch_op:
        batch_op.add_column(sa.Column('event_cycle_id', sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column('delivery_status', sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column('delivery_updated_at', sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column('delivery_detail', sa.Text(), nullable=True))
        batch_op.create_foreign_key('fk_notification_logs_event_cycle_id', 'event_cycles', ['event_cycle_id'], ['id'])
        batch_op.create_index(batch_op.f('ix_notification_logs_event_cycle_id'), ['event_cycle_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_notification_logs_delivery_status'), ['delivery_status'], unique=False)


def downgrade():
    with op.batch_alter_table('notification_logs', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_notification_logs_delivery_status'))
        batch_op.drop_index(batch_op.f('ix_notification_logs_event_cycle_id'))
        batch_op.drop_constraint('fk_notification_logs_event_cycle_id', type_='foreignkey')
        batch_op.drop_column('delivery_detail')
        batch_op.drop_column('delivery_updated_at')
        batch_op.drop_column('delivery_status')
        batch_op.drop_column('event_cycle_id')

    with op.batch_alter_table('email_message_bodies', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_email_message_bodies_created_at'))
        batch_op.drop_index(batch_op.f('ix_email_message_bodies_notification_log_id'))
    op.drop_table('email_message_bodies')

    with op.batch_alter_table('email_suppression', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_email_suppression_email'))
    op.drop_table('email_suppression')

    with op.batch_alter_table('email_outbox', schema=None) as batch_op:
        batch_op.drop_index('ix_email_outbox_status_dispatch')
        batch_op.drop_index(batch_op.f('ix_email_outbox_created_at'))
        batch_op.drop_index(batch_op.f('ix_email_outbox_claimed_by'))
        batch_op.drop_index(batch_op.f('ix_email_outbox_work_type_id'))
        batch_op.drop_index(batch_op.f('ix_email_outbox_department_id'))
        batch_op.drop_index(batch_op.f('ix_email_outbox_event_cycle_id'))
        batch_op.drop_index(batch_op.f('ix_email_outbox_work_item_id'))
        batch_op.drop_index(batch_op.f('ix_email_outbox_status'))
        batch_op.drop_index(batch_op.f('ix_email_outbox_dispatch_at'))
        batch_op.drop_index(batch_op.f('ix_email_outbox_template_key'))
    op.drop_table('email_outbox')
