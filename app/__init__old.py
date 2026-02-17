from __future__ import annotations

import os

from flask import Flask, session
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy

from datetime import datetime, timedelta



db = SQLAlchemy()
migrate = Migrate()


def create_app() -> Flask:
    app = Flask(__name__, instance_relative_config=True)

    # Ensure instance folder exists (Flask convention for local state like SQLite DB)
    os.makedirs(app.instance_path, exist_ok=True)

    # Basic config
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-secret-key")

    # SQLite database stored in instance/ for local dev
    db_path = os.path.join(app.instance_path, "magfest_budget.sqlite3")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
        "DATABASE_URL", f"sqlite:///{db_path}"
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    # Init extensions
    db.init_app(app)
    migrate.init_app(app, db)

    # Simple sanity route
    @app.get("/")
    def index():
        return {
            "ok": True,
            "app": "magfest-budget",
            "db": app.config["SQLALCHEMY_DATABASE_URI"],
        }

    # Import models so migrations can detect them
    from . import models_old  # noqa: F401

    #---Helpers
    def ensure_demo_users():
        """Create a small set of demo users/roles if none exist yet."""
        from .models_old import User, UserRole, ApprovalGroup

        ensure_demo_budget_data()

        any_user = db.session.query(User).first()
        if any_user:
            return

        groups_by_code = {g.code: g for g in db.session.query(ApprovalGroup).all()}
        tech_group_id = groups_by_code["TECH"].id
        hotel_group_id = groups_by_code["HOTEL"].id

        demo_users = [
            ("dev:requester", "Requester (Demo)", True, [("REQUESTER", None)]),
            ("dev:tech_approver", "Tech Approver (Demo)", True, [("APPROVER", tech_group_id)]),
            ("dev:hotel_approver", "Hotel Approver (Demo)", True, [("APPROVER", hotel_group_id)]),
            ("dev:admin", "Admin (Demo)", True, [("ADMIN", None)]),
            ("dev:finance", "Finance (Demo)", True, [("FINANCE", None)]),
            ("dev:alex", "Alex (Demo)", True, [("REQUESTER", None)]),
        ]

        for user_id, display_name, is_active, roles in demo_users:
            u = User(id=user_id, display_name=display_name, is_active=is_active)
            db.session.add(u)
            db.session.flush()

            for role_code, group_id in roles:
                db.session.add(UserRole(user_id=u.id, role_code=role_code, approval_group_id=group_id))

        db.session.commit()

    def get_active_user_id() -> str:
        # default keeps your existing sample routes working
        return session.get("active_user_id") or "dev:alex"

    def get_active_user():
        from .models_old import User
        uid = get_active_user_id()
        return db.session.get(User, uid)

    def active_user_roles() -> list[str]:
        from .models_old import UserRole
        uid = get_active_user_id()
        rows = (
            db.session.query(UserRole.role_code)
            .filter(UserRole.user_id == uid)
            .all()
        )
        return [r[0] for r in rows]

    def has_role(role_code: str) -> bool:
        return role_code in set(active_user_roles())

    def is_admin() -> bool:
        return has_role("ADMIN")

    def is_finance() -> bool:
        return has_role("FINANCE")

    def ensure_demo_budget_data():
        """Create approval groups + a few budget item types if none exist yet."""
        from .models_old import ApprovalGroup, BudgetItemType

        any_group = db.session.query(ApprovalGroup).first()
        if not any_group:
            groups = [
                ("TECH", "Tech", True, 10),
                ("HOTEL", "Hotel", True, 20),
                ("OTHER", "Other", True, 30),
            ]
            for code, name, active, sort in groups:
                db.session.add(
                    ApprovalGroup(code=code, name=name, is_active=active, sort_order=sort)
                )
            db.session.flush()

        # Only seed types if none exist
        any_type = db.session.query(BudgetItemType).first()
        if any_type:
            db.session.commit()
            return

        # Look up group IDs
        groups_by_code = {
            g.code: g for g in db.session.query(ApprovalGroup).all()
        }

        demo_types = [
            # item_id, item_name, spend_type, approval_group_code, description
            ("ITM-TECH-001", "Radios (Rental)", "Divvy", "TECH", "Handheld radios rental for operations"),
            ("ITM-TECH-002", "iPads / Laptops (Rental)", "Divvy", "TECH", "Hartford rental computing devices"),
            ("ITM-HOTEL-001", "Ethernet Drops", "Hotel Fee", "HOTEL", "Hardline internet drops from venue"),
            ("ITM-OTH-001", "Office Supplies", "Bank", "OTHER", "General office supplies"),
        ]

        for item_id, name, spend, group_code, desc in demo_types:
            g = groups_by_code[group_code]
            db.session.add(
                BudgetItemType(
                    item_id=item_id,
                    item_name=name,
                    item_description=desc,
                    spend_type=spend,
                    approval_group_id=g.id,
                    is_active=True,
                )
            )

        db.session.commit()

    def active_user_approval_group_ids() -> set[int]:
        from .models_old import UserRole
        uid = get_active_user_id()
        rows = (
            db.session.query(UserRole.approval_group_id)
            .filter(UserRole.user_id == uid)
            .filter(UserRole.role_code == "APPROVER")
            .filter(UserRole.approval_group_id.isnot(None))
            .all()
        )
        return {int(r[0]) for r in rows if r[0] is not None}

    def can_review_group(approval_group_id: int) -> bool:
        if is_admin():
            return True
        return approval_group_id in active_user_approval_group_ids()

    def _recalculate_request_status_from_lines(revision):
        from .models_old import LineReview, Request

        reviews = (
            db.session.query(LineReview.status)
            .join(LineReview.request_line)
            .filter(LineReview.request_line.has(revision_id=revision.id))
            .all()
        )

        if not reviews:
            return

        statuses = {r[0] for r in reviews}

        req = db.session.get(Request, revision.request_id)
        if not req:
            return

        if "KICKED_BACK" in statuses:
            req.current_status = "NEEDS_REVISION"
        elif statuses == {"APPROVED"}:
            req.current_status = "APPROVED"
        else:
            req.current_status = "SUBMITTED"

        db.session.commit()

    #--Context Processor
    @app.context_processor
    def inject_active_user():
        u = get_active_user()
        roles = active_user_roles()
        return {
            "active_user": u,
            "active_user_id": get_active_user_id(),
            "active_user_roles": roles,
            "is_admin": is_admin(),
            "is_finance": is_finance(),
        }




    return app
