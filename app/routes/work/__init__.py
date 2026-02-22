"""
Work routes blueprint - requester workflow for portfolios and work requests.

Handles all work types: Budget, Contracts, Supply Orders, etc.
"""
from flask import Blueprint

# Create the blueprint
work_bp = Blueprint("work", __name__)

# Import route modules to register their routes with the blueprint
from . import department
from . import portfolio
from . import work_items
from . import lines
