"""
Admin routes for configuration management.

These routes require SUPER_ADMIN role access.
"""
from flask import Blueprint

from .expense_accounts import expense_accounts_bp
from .approval_groups import approval_groups_bp
from .departments import departments_bp
from .event_cycles import event_cycles_bp
from .locks import locks_bp

admin_config_bp = Blueprint('admin_config', __name__, url_prefix='/admin/config')

# Register sub-blueprints
admin_config_bp.register_blueprint(expense_accounts_bp)
admin_config_bp.register_blueprint(approval_groups_bp)
admin_config_bp.register_blueprint(departments_bp)
admin_config_bp.register_blueprint(event_cycles_bp)
admin_config_bp.register_blueprint(locks_bp)
