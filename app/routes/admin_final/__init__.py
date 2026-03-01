"""
Admin Final Review routes blueprint - final approval workflow for admins.
"""
from flask import Blueprint

# Create the blueprint
admin_final_bp = Blueprint("admin_final", __name__)

# Import route modules to register their routes with the blueprint
from . import dashboard
from . import reviews
from . import ledger_report
from . import department_report
from . import workload_report
