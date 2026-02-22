"""Add multi work type models

Revision ID: a1b2c3d4e5f6
Revises: 774f10adfdeb
Create Date: 2026-02-21 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = '774f10adfdeb'
branch_labels = None
depends_on = None


def upgrade():
    # Create work_type_configs table
    op.create_table('work_type_configs',
        sa.Column('work_type_id', sa.Integer(), nullable=False),
        sa.Column('url_slug', sa.String(32), nullable=False),
        sa.Column('public_id_prefix', sa.String(8), nullable=False),
        sa.Column('line_detail_type', sa.String(32), nullable=False),
        sa.Column('routing_strategy', sa.String(32), nullable=False, server_default='direct'),
        sa.Column('default_approval_group_id', sa.Integer(), nullable=True),
        sa.Column('supports_supplementary', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('supports_fixed_costs', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('item_singular', sa.String(32), nullable=False, server_default='Request'),
        sa.Column('item_plural', sa.String(32), nullable=False, server_default='Requests'),
        sa.Column('line_singular', sa.String(32), nullable=False, server_default='Line'),
        sa.Column('line_plural', sa.String(32), nullable=False, server_default='Lines'),
        sa.ForeignKeyConstraint(['work_type_id'], ['work_types.id'], name='fk_work_type_configs_work_type_id'),
        sa.ForeignKeyConstraint(['default_approval_group_id'], ['approval_groups.id'], name='fk_work_type_configs_default_approval_group_id'),
        sa.PrimaryKeyConstraint('work_type_id')
    )
    op.create_index('ix_work_type_configs_url_slug', 'work_type_configs', ['url_slug'], unique=True)

    # Create contract_types table
    op.create_table('contract_types',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(32), nullable=False),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('approval_group_id', sa.Integer(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by_user_id', sa.String(64), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('updated_by_user_id', sa.String(64), nullable=True),
        sa.ForeignKeyConstraint(['approval_group_id'], ['approval_groups.id'], name='fk_contract_types_approval_group_id'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_contract_types_code', 'contract_types', ['code'], unique=True)
    op.create_index('ix_contract_types_approval_group_id', 'contract_types', ['approval_group_id'])

    # Create contract_line_details table
    op.create_table('contract_line_details',
        sa.Column('work_line_id', sa.Integer(), nullable=False),
        sa.Column('contract_type_id', sa.Integer(), nullable=False),
        sa.Column('routed_approval_group_id', sa.Integer(), nullable=True),
        sa.Column('vendor_name', sa.String(256), nullable=False),
        sa.Column('vendor_contact', sa.String(256), nullable=True),
        sa.Column('contract_amount_cents', sa.Integer(), nullable=False),
        sa.Column('start_date', sa.Date(), nullable=True),
        sa.Column('end_date', sa.Date(), nullable=True),
        sa.Column('terms_summary', sa.Text(), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['work_line_id'], ['work_lines.id'], name='fk_contract_line_details_work_line_id'),
        sa.ForeignKeyConstraint(['contract_type_id'], ['contract_types.id'], name='fk_contract_line_details_contract_type_id'),
        sa.ForeignKeyConstraint(['routed_approval_group_id'], ['approval_groups.id'], name='fk_contract_line_details_routed_approval_group_id'),
        sa.PrimaryKeyConstraint('work_line_id')
    )
    op.create_index('ix_contract_line_details_contract_type_id', 'contract_line_details', ['contract_type_id'])
    op.create_index('ix_contract_line_details_routed_approval_group_id', 'contract_line_details', ['routed_approval_group_id'])
    op.create_index('ix_contract_line_details_approval_routing', 'contract_line_details', ['routed_approval_group_id', 'contract_type_id'])

    # Create supply_categories table
    op.create_table('supply_categories',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('code', sa.String(32), nullable=False),
        sa.Column('name', sa.String(128), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('approval_group_id', sa.Integer(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by_user_id', sa.String(64), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('updated_by_user_id', sa.String(64), nullable=True),
        sa.ForeignKeyConstraint(['approval_group_id'], ['approval_groups.id'], name='fk_supply_categories_approval_group_id'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_supply_categories_code', 'supply_categories', ['code'], unique=True)
    op.create_index('ix_supply_categories_approval_group_id', 'supply_categories', ['approval_group_id'])

    # Create supply_items table
    op.create_table('supply_items',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('category_id', sa.Integer(), nullable=False),
        sa.Column('item_name', sa.String(256), nullable=False),
        sa.Column('unit', sa.String(32), nullable=False),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('image_url', sa.String(512), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('is_limited', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('is_popular', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('is_expendable', sa.Boolean(), nullable=False, server_default='1'),
        sa.Column('notes_required', sa.Boolean(), nullable=False, server_default='0'),
        sa.Column('internal_type', sa.String(32), nullable=True),
        sa.Column('unit_cost_cents', sa.Integer(), nullable=True),
        sa.Column('qty_on_hand', sa.Integer(), nullable=True),
        sa.Column('location_zone', sa.String(32), nullable=True),
        sa.Column('bin_location', sa.String(32), nullable=True),
        sa.Column('sort_order', sa.Integer(), nullable=False, server_default='0'),
        sa.Column('created_at', sa.DateTime(), nullable=False),
        sa.Column('created_by_user_id', sa.String(64), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=False),
        sa.Column('updated_by_user_id', sa.String(64), nullable=True),
        sa.ForeignKeyConstraint(['category_id'], ['supply_categories.id'], name='fk_supply_items_category_id'),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_supply_items_category_id', 'supply_items', ['category_id'])

    # Create supply_order_line_details table
    op.create_table('supply_order_line_details',
        sa.Column('work_line_id', sa.Integer(), nullable=False),
        sa.Column('item_id', sa.Integer(), nullable=False),
        sa.Column('routed_approval_group_id', sa.Integer(), nullable=True),
        sa.Column('quantity_requested', sa.Integer(), nullable=False),
        sa.Column('quantity_approved', sa.Integer(), nullable=True),
        sa.Column('needed_by_date', sa.Date(), nullable=True),
        sa.Column('delivery_location', sa.String(256), nullable=True),
        sa.Column('requester_notes', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(['work_line_id'], ['work_lines.id'], name='fk_supply_order_line_details_work_line_id'),
        sa.ForeignKeyConstraint(['item_id'], ['supply_items.id'], name='fk_supply_order_line_details_item_id'),
        sa.ForeignKeyConstraint(['routed_approval_group_id'], ['approval_groups.id'], name='fk_supply_order_line_details_routed_approval_group_id'),
        sa.PrimaryKeyConstraint('work_line_id')
    )
    op.create_index('ix_supply_order_line_details_item_id', 'supply_order_line_details', ['item_id'])
    op.create_index('ix_supply_order_line_details_routed_approval_group_id', 'supply_order_line_details', ['routed_approval_group_id'])
    op.create_index('ix_supply_order_line_details_approval_routing', 'supply_order_line_details', ['routed_approval_group_id', 'item_id'])


def downgrade():
    # Drop tables in reverse order
    op.drop_index('ix_supply_order_line_details_approval_routing', table_name='supply_order_line_details')
    op.drop_index('ix_supply_order_line_details_routed_approval_group_id', table_name='supply_order_line_details')
    op.drop_index('ix_supply_order_line_details_item_id', table_name='supply_order_line_details')
    op.drop_table('supply_order_line_details')

    op.drop_index('ix_supply_items_category_id', table_name='supply_items')
    op.drop_table('supply_items')

    op.drop_index('ix_supply_categories_approval_group_id', table_name='supply_categories')
    op.drop_index('ix_supply_categories_code', table_name='supply_categories')
    op.drop_table('supply_categories')

    op.drop_index('ix_contract_line_details_approval_routing', table_name='contract_line_details')
    op.drop_index('ix_contract_line_details_routed_approval_group_id', table_name='contract_line_details')
    op.drop_index('ix_contract_line_details_contract_type_id', table_name='contract_line_details')
    op.drop_table('contract_line_details')

    op.drop_index('ix_contract_types_approval_group_id', table_name='contract_types')
    op.drop_index('ix_contract_types_code', table_name='contract_types')
    op.drop_table('contract_types')

    op.drop_index('ix_work_type_configs_url_slug', table_name='work_type_configs')
    op.drop_table('work_type_configs')
