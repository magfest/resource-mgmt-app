"""
Budget routes blueprint - requester workflow for portfolios and budget requests.
"""
from flask import Blueprint

# Create the blueprint
budget_bp = Blueprint("budget", __name__)

# Import route modules to register their routes with the blueprint
from . import portfolio
from . import work_items
from . import lines
