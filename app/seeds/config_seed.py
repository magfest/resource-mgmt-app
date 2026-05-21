"""
Master seed wrapper.

Composes the two layered seeds:
- bootstrap.py — schema-required rows the app cannot function without
- demo_data.py — operator-replaceable starter content (marked `[Demo] `)

Use the CLI for granular control:
    flask seed bootstrap   # only schema-required rows
    flask seed demo        # only [Demo] starter content
    flask # both (default)

This module is also called by the auto-seed hook in app/__init__.py
(run_seed_once) when an empty DB is detected on first request.

For demo USERS (Pat/Alex/Riley/etc. for /dev/login), see demo_users.py.
"""
from __future__ import annotations

from app.seeds.bootstrap import run_bootstrap
from app.seeds.demo_data import run_demo_data


def run_all_seeds() -> None:
    """Run bootstrap + demo seed.

    Order matters: bootstrap creates approval groups and spend types that
    demo_data's parking accounts depend on. run_bootstrap commits before
    run_demo_data starts, so the dependency is satisfied.
    """
    print("=" * 60)
    print("Running all seeds (bootstrap + demo)...")
    print("=" * 60)

    run_bootstrap()
    run_demo_data()

    print("=" * 60)
    print("All seeds complete.")
    print("=" * 60)


if __name__ == "__main__":
    # Allow running directly for testing: python -m app.seeds.config_seed
    from app import create_app
    app = create_app()
    with app.app_context():
        run_all_seeds()
