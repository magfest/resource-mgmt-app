"""FestOps inventory domain logic.

Imports nothing from app.routes, so routes, CLI commands, and tests can all
call it. Functions here flush but never commit; the caller owns the
transaction.
"""
