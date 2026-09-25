"""
TechOps work-type routes — requester workflow for TechOps service requests.

Handlers register against the shared work_bp blueprint at literal
URL segments under /<event>/<dept>/techops/... so Flask's matcher
prefers them over the generic <work_type_slug> rule used as a
coming-soon fallback for not-yet-built worktypes.
"""
from . import portfolio
from . import create
from . import edit
from . import preview
from . import submit
from . import view
from . import admin
