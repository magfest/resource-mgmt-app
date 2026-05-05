"""
Admin routes for configuration management.

These routes require SUPER_ADMIN role access.
"""
from flask import Blueprint

from .expense_accounts import expense_accounts_bp
from .approval_groups import approval_groups_bp
from .departments import departments_bp
from .divisions import divisions_bp
from .event_cycles import event_cycles_bp
from .locks import locks_bp
from .users import users_bp
from .reference_data import reference_data_bp
from .data_upload import data_upload_bp
from .contract_types import contract_types_bp
from .supply_categories import supply_categories_bp
from .supply_items import supply_items_bp
from .email_templates import email_templates_bp
from .site_content import site_content_bp
from .work_types import work_types_bp

admin_config_bp = Blueprint('admin_config', __name__, url_prefix='/admin/config')

# Register sub-blueprints
admin_config_bp.register_blueprint(expense_accounts_bp)
admin_config_bp.register_blueprint(approval_groups_bp)
admin_config_bp.register_blueprint(departments_bp)
admin_config_bp.register_blueprint(divisions_bp)
admin_config_bp.register_blueprint(event_cycles_bp)
admin_config_bp.register_blueprint(locks_bp)
admin_config_bp.register_blueprint(users_bp)
admin_config_bp.register_blueprint(reference_data_bp)
admin_config_bp.register_blueprint(data_upload_bp)
admin_config_bp.register_blueprint(contract_types_bp)
admin_config_bp.register_blueprint(supply_categories_bp)
admin_config_bp.register_blueprint(supply_items_bp)
admin_config_bp.register_blueprint(email_templates_bp)
admin_config_bp.register_blueprint(site_content_bp)
admin_config_bp.register_blueprint(work_types_bp)
