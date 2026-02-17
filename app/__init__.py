from __future__ import annotations

import os
from datetime import datetime, timedelta

from flask import Flask, session, render_template, abort, request, redirect, url_for
from flask_migrate import Migrate
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()
migrate = Migrate()


def create_app() -> Flask:
    app = Flask(__name__, instance_relative_config=True)

    os.makedirs(app.instance_path, exist_ok=True)

    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-secret-key")

    db_path = os.path.join(app.instance_path, "magfest_budget.sqlite3")
    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get(
        "DATABASE_URL", f"sqlite:///{db_path}"
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)
    migrate.init_app(app, db)

    # Import models so migrations can detect them
    from . import models_old  # noqa: F401

    # -----------------------------
    # Helpers (demo auth + scoping)
    # -----------------------------

    def ensure_demo_budget_data():
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

        any_type = db.session.query(BudgetItemType).first()
        if any_type:
            db.session.commit()
            return

        groups_by_code = {g.code: g for g in db.session.query(ApprovalGroup).all()}

        demo_types = [
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

    def ensure_demo_users():
        from .models_old import User, UserRole, ApprovalGroup

        ensure_demo_budget_data()
        ensure_demo_org_data()

        any_user = db.session.query(User).first()
        if any_user:
            return

        groups_by_code = {g.code: g for g in db.session.query(ApprovalGroup).all()}
        tech = groups_by_code.get("TECH")
        hotel = groups_by_code.get("HOTEL")
        if not tech or not hotel:
            raise RuntimeError("Demo ApprovalGroups missing: expected TECH and HOTEL to exist.")
        tech_group_id = tech.id
        hotel_group_id = hotel.id

        demo_users = [
            # Plain users
            ("dev:pat", "pat@dev.local", "dev:pat", "Pat (No Dept)", True, [("REQUESTER", None)]),

            # Arcades
            ("dev:alex", "alex@dev.local", "dev:alex", "Alex (Arcades DH)", True, [("REQUESTER", None)]),
            ("dev:riley", "riley@dev.local", "dev:riley", "Riley (Arcades Editor)", True, [("REQUESTER", None)]),
            ("dev:sam", "sam@dev.local", "dev:sam", "Sam (Arcades Viewer)", True, [("REQUESTER", None)]),

            # Guests
            ("dev:jordan", "jordan@dev.local", "dev:jordan", "Jordan (Guests DH)", True, [("REQUESTER", None)]),
            ("dev:casey", "casey@dev.local", "dev:casey", "Casey (Guests Editor)", True, [("REQUESTER", None)]),

            # Mixed membership
            ("dev:morgan", "morgan@dev.local", "dev:morgan", "Morgan (Arcades View / Guests Edit)", True,
             [("REQUESTER", None)]),

            # Approvers
            ("dev:tech_approver", "tech.approver@dev.local", "dev:tech_approver", "Tech Approver (Demo)", True,
             [("APPROVER", tech_group_id)]),
            ("dev:hotel_approver", "hotel.approver@dev.local", "dev:hotel_approver", "Hotel Approver (Demo)", True,
             [("APPROVER", hotel_group_id)]),

            # Elevated
            ("dev:admin", "admin@dev.local", "dev:admin", "Admin (Demo)", True, [("ADMIN", None)]),
            ("dev:finance", "finance@dev.local", "dev:finance", "Finance (Demo)", True, [("FINANCE", None)]),
        ]

        for user_id, email, auth_subject, display_name, is_active, roles in demo_users:
            u = db.session.get(User, user_id)
            if not u:
                u = User(id=user_id)
                db.session.add(u)

            u.email = email
            u.auth_subject = auth_subject
            u.display_name = display_name
            u.is_active = is_active

            # roles: easiest is clear then recreate for demo users
            db.session.query(UserRole).filter_by(user_id=user_id).delete()
            for role_code, group_id in roles:
                db.session.add(UserRole(user_id=user_id, role_code=role_code, approval_group_id=group_id))

        db.session.commit()
        ensure_demo_department_memberships()

    def ensure_demo_org_data():
        # Requires Department + EventCycle models to exist in models_old.py
        from .models_old import Department, EventCycle

        # Seed EventCycles if empty
        any_cycle = db.session.query(EventCycle).first()
        if not any_cycle:
            cycles = [
                # code, name, active, default, sort
                ("SMF2026", "Super MAGFest 2026", True, True, 10),
                ("SMF2027", "Super MAGFest 2027", True, False, 20),
            ]
            for code, name, active, is_default, sort in cycles:
                db.session.add(
                    EventCycle(
                        code=code,
                        name=name,
                        is_active=active,
                        is_default=is_default,
                        sort_order=sort,
                    )
                )
            db.session.flush()

        # Seed Departments if empty
        any_dept = db.session.query(Department).first()
        if not any_dept:
            depts = [
                # code, name, active, sort
                ("TECHOPS", "TechOps", True, 10),
                ("HOTELS", "Hotels", True, 20),
                ("BROADCAST", "BroadcastOps", True, 30),
                ("FESTOPS", "FestOps", True, 40),
                ("SUPPLY", "SupplyOps", True, 50),
                ("REG", "Registration", True, 60),
                ("PANEL", "Panels", True, 70),
                ("GUEST", "Guests", True, 80),
                ("ARCADE", "Arcades", True, 90),
            ]
            for code, name, active, sort in depts:
                db.session.add(
                    Department(
                        code=code,
                        name=name,
                        is_active=active,
                        sort_order=sort,
                    )
                )

        db.session.commit()

    def ensure_demo_department_memberships():
        from .models_old import (
            User,
            Department,
            EventCycle,
            DepartmentMembership,
        )

        # Ensure org data exists (departments + cycles)
        ensure_demo_org_data()

        # --- fetch the event cycle we want to test ---
        cycle = (
            db.session.query(EventCycle)
            .filter(EventCycle.code == "SMF2026")
            .one()
        )

        # --- fetch departments we want to test ---
        dept_by_code = {
            d.code: d
            for d in db.session.query(Department)
            .filter(Department.code.in_(["ARCADE", "GUEST"]))
            .all()
        }

        missing = [c for c in ["ARCADE", "GUEST"] if c not in dept_by_code]
        if missing:
            raise RuntimeError(f"Missing demo departments: {missing}")

        def upsert_membership(
                *, user_id: str, dept_code: str,
                can_view: bool, can_edit: bool, is_department_head: bool
        ):
            dept = dept_by_code[dept_code]

            row = (
                db.session.query(DepartmentMembership)
                .filter(DepartmentMembership.user_id == user_id)
                .filter(DepartmentMembership.department_id == dept.id)
                .filter(DepartmentMembership.event_cycle_id == cycle.id)
                .one_or_none()
            )

            if not row:
                row = DepartmentMembership(
                    user_id=user_id,
                    department_id=dept.id,
                    event_cycle_id=cycle.id,
                )
                db.session.add(row)

            row.can_view = bool(can_view)
            row.can_edit = bool(can_edit)
            row.is_department_head = bool(is_department_head)

        # --- membership plan (truth table) ---
        membership_plan = [
            # Arcades
            ("dev:alex", "ARCADE", True, True, True),  # DH
            ("dev:riley", "ARCADE", True, True, False),  # editor
            ("dev:sam", "ARCADE", True, False, False),  # viewer

            # Guests
            ("dev:jordan", "GUEST", True, True, True),  # DH
            ("dev:casey", "GUEST", True, True, False),  # editor

            # Mixed: Arcades view + Guests edit
            ("dev:morgan", "ARCADE", True, False, False),
            ("dev:morgan", "GUEST", True, True, False),
        ]

        # Validate users exist (fail loudly if demo users aren’t seeded)
        user_ids = [u[0] for u in membership_plan]
        found = {u.id for u in db.session.query(User.id).filter(User.id.in_(user_ids)).all()}
        missing_users = [uid for uid in user_ids if uid not in found]
        if missing_users:
            raise RuntimeError(f"Missing demo users for memberships: {missing_users}")

        # Apply plan
        for user_id, dept_code, can_view, can_edit, is_dh in membership_plan:
            upsert_membership(
                user_id=user_id,
                dept_code=dept_code,
                can_view=can_view,
                can_edit=can_edit,
                is_department_head=is_dh,
            )

        db.session.commit()

    def get_active_user_id() -> str:
        return session.get("active_user_id") or "dev:alex"

    def get_active_user():
        from .models_old import User
        return db.session.get(User, get_active_user_id())

    def active_user_roles() -> list[str]:
        from .models_old import UserRole
        uid = get_active_user_id()
        rows = db.session.query(UserRole.role_code).filter(UserRole.user_id == uid).all()
        return [r[0] for r in rows]

    def has_role(role_code: str) -> bool:
        return role_code in set(active_user_roles())

    def is_admin() -> bool:
        return has_role("ADMIN")

    def is_finance() -> bool:
        return has_role("FINANCE")

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
        return is_admin() or (approval_group_id in active_user_approval_group_ids())

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

    def _recalculate_request_status_from_lines(revision):
        """
        Derive request.current_status from line review statuses for a revision.

        Rules:
        - NEEDS_REVISION is request-level only; do not overwrite it here.
        - Do not auto-promote to APPROVED. Final approval is explicit via /requests/<id>/approve.
        - Do not auto-downgrade APPROVED.
        - Otherwise, request stays SUBMITTED while any line review exists (regardless of mix).
        """
        from .models_old import LineReview, Request

        reviews = (
            db.session.query(LineReview.status)
            .join(LineReview.request_line)
            .filter(LineReview.request_line.has(revision_id=revision.id))
            .all()
        )

        if not reviews:
            return

        req = db.session.get(Request, revision.request_id)
        if not req:
            return

        cur = (req.current_status or "").upper()
        if cur == "NEEDS_REVISION":
            return
        if cur == "APPROVED":
            return

        # Under review state; line status detail is expressed in the UI
        req.current_status = "SUBMITTED"

    from .routes import register_all_routes, RouteHelpers

    register_all_routes(
        app,
        RouteHelpers(
            ensure_demo_users=ensure_demo_users,
            ensure_demo_budget_data=ensure_demo_budget_data,
            ensure_demo_org_data=ensure_demo_org_data,
            get_active_user_id=get_active_user_id,
            get_active_user=get_active_user,
            active_user_roles=active_user_roles,
            is_admin=is_admin,
            is_finance=is_finance,
            active_user_approval_group_ids=active_user_approval_group_ids,
            can_review_group=can_review_group,
            recalc_request_status_from_lines=_recalculate_request_status_from_lines,
        ),
    )

    return app
