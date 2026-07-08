"""
Supply work-type routes — requester ordering workflow (catalog + cart),
submit, and FestOps admin final review.

Handlers register against the shared work_bp blueprint at literal URL
segments under /<event>/<dept>/supply/... so Flask's matcher prefers
them over the generic <work_type_slug> fallback rule.
"""
from . import portfolio
from . import order
from . import view
from . import catalog
from . import submit
from . import admin
