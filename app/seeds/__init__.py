"""Seed scripts for initial database population.

Three layers:
- bootstrap.py    — schema-required rows (worktypes, approval groups, etc.)
- demo_data.py    — operator-replaceable [Demo] org content
- demo_users.py   — demo users (Pat/Alex/etc.) for the /dev/login flow

config_seed.run_all_seeds() composes bootstrap + demo_data and is what the
auto-seed hook calls. The CLI `flask seed [bootstrap|demo|all]` exposes
each layer for manual operator control.
"""

from .config_seed import run_all_seeds
from .bootstrap import run_bootstrap
from .demo_data import run_demo_data
from .demo_users import ensure_demo_users

__all__ = ["run_all_seeds", "run_bootstrap", "run_demo_data", "ensure_demo_users"]
