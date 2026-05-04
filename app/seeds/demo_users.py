"""
Demo users seed — for /dev/login flow.

Provides ensure_demo_users() — invoked by the dev-login route. Layers
demo users (Pat/Alex/Riley/etc.) and their department memberships on
top of the canonical structural seed in bootstrap.py + demo_data.py.

Renamed from dev_seed.py during PR-B for naming consistency: all demo
content lives in files prefixed `demo_*.py`.

Idempotency contract: every function is safe to call on a populated DB.
- run_all_seeds() inserts only missing rows in canonical tables.
- Demo user creation early-exits if any User already exists, so this
  never layers dev users on top of real OAuth-created users in
  staging/prod.
- Department memberships are upserted by (user, dept, cycle) tuple.
"""
from __future__ import annotations

from app import db
from app.models import (
    ApprovalGroup,
    Department,
    DepartmentMembership,
    EventCycle,
    User,
    UserRole,
    ROLE_APPROVER,
    ROLE_SUPER_ADMIN,
)
from app.seeds.config_seed import run_all_seeds
from app.seeds.demo_data import (
    DEMO_DEPT_CODES,
    DEMO_EVENT_CYCLE_CODE,
)

_DEMO_DEPT_ARCADE = DEMO_DEPT_CODES["ARCADE"][0]   # "DEMO_ARCADE"
_DEMO_DEPT_GUESTS = DEMO_DEPT_CODES["GUESTS"][0]   # "DEMO_GUESTS"


def ensure_demo_users() -> None:
    """Seed canonical structural data + demo users + memberships.

    Calls run_all_seeds() to populate worktypes, approval groups, demo
    depts, etc. Then creates demo users (only if no User exists) and
    wires them into demo departments.

    Safe on staging/prod: the User existence check makes this a no-op
    after any real user has signed in via OAuth.
    """
    run_all_seeds()

    # Early-exit if any user exists. Prevents demo users from being added
    # on top of real OAuth-created users in staging/prod.
    if db.session.query(User).first():
        return

    groups_by_code = {g.code: g for g in db.session.query(ApprovalGroup).all()}
    tech = groups_by_code.get("TECH")
    hotel = groups_by_code.get("HOTEL")
    if not tech or not hotel:
        raise RuntimeError("Demo ApprovalGroups missing: expected TECH and HOTEL to exist.")
    tech_group_id = tech.id
    hotel_group_id = hotel.id

    # role format: (role_code, work_type_id, approval_group_id)
    demo_users = [
        # Plain users (no special role)
        ("dev:pat", "pat@dev.local", "dev:pat", "Pat (No Dept)", True, []),

        # Arcades
        ("dev:alex", "alex@dev.local", "dev:alex", "Alex (Arcades DH)", True, []),
        ("dev:riley", "riley@dev.local", "dev:riley", "Riley (Arcades Editor)", True, []),
        ("dev:sam", "sam@dev.local", "dev:sam", "Sam (Arcades Viewer)", True, []),

        # Guests
        ("dev:jordan", "jordan@dev.local", "dev:jordan", "Jordan (Guests DH)", True, []),
        ("dev:casey", "casey@dev.local", "dev:casey", "Casey (Guests Editor)", True, []),

        # Mixed membership
        ("dev:morgan", "morgan@dev.local", "dev:morgan", "Morgan (Arcades View / Guests Edit)", True, []),

        # Approvers (scoped to approval group)
        ("dev:tech_approver", "tech.approver@dev.local", "dev:tech_approver", "Tech Approver (Demo)", True,
         [(ROLE_APPROVER, None, tech_group_id)]),
        ("dev:hotel_approver", "hotel.approver@dev.local", "dev:hotel_approver", "Hotel Approver (Demo)", True,
         [(ROLE_APPROVER, None, hotel_group_id)]),

        # Elevated
        ("dev:admin", "admin@dev.local", "dev:admin", "Admin (Demo)", True, [(ROLE_SUPER_ADMIN, None, None)]),
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
        for role_code, work_type_id, approval_group_id in roles:
            db.session.add(UserRole(
                user_id=user_id,
                role_code=role_code,
                work_type_id=work_type_id,
                approval_group_id=approval_group_id,
            ))

    db.session.commit()
    _ensure_demo_department_memberships()


def _ensure_demo_department_memberships() -> None:
    """Wire demo users into DEMO_ARCADE / DEMO_GUESTS for the demo cycle.

    Called from ensure_demo_users() after demo users are created. Not
    intended to be called standalone — fails loudly if demo users or
    expected departments don't exist.
    """
    cycle = (
        db.session.query(EventCycle)
        .filter(EventCycle.code == DEMO_EVENT_CYCLE_CODE)
        .one_or_none()
    )
    if cycle is None:
        raise RuntimeError(
            f"Demo event cycle {DEMO_EVENT_CYCLE_CODE} not found — "
            f"demo_data.py should have created it."
        )

    expected_dept_codes = [_DEMO_DEPT_ARCADE, _DEMO_DEPT_GUESTS]
    dept_by_code = {
        d.code: d
        for d in db.session.query(Department)
        .filter(Department.code.in_(expected_dept_codes))
        .all()
    }

    missing = [c for c in expected_dept_codes if c not in dept_by_code]
    if missing:
        raise RuntimeError(f"Missing demo departments: {missing}")

    def upsert_membership(*, user_id: str, dept_code: str, is_department_head: bool):
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

        row.is_department_head = bool(is_department_head)

    # --- membership plan (truth table) ---
    # Format: (user_id, dept_code, is_department_head)
    # Work type access is managed separately via DepartmentMembershipWorkTypeAccess
    membership_plan = [
        # Arcades
        ("dev:alex", _DEMO_DEPT_ARCADE, True),    # DH
        ("dev:riley", _DEMO_DEPT_ARCADE, False),  # editor
        ("dev:sam", _DEMO_DEPT_ARCADE, False),    # viewer

        # Guests
        ("dev:jordan", _DEMO_DEPT_GUESTS, True),   # DH
        ("dev:casey", _DEMO_DEPT_GUESTS, False),   # editor

        # Mixed: Arcades view + Guests edit
        ("dev:morgan", _DEMO_DEPT_ARCADE, False),
        ("dev:morgan", _DEMO_DEPT_GUESTS, False),
    ]

    user_ids = [u[0] for u in membership_plan]
    found = {u.id for u in db.session.query(User.id).filter(User.id.in_(user_ids)).all()}
    missing_users = [uid for uid in user_ids if uid not in found]
    if missing_users:
        raise RuntimeError(f"Missing demo users for memberships: {missing_users}")

    for user_id, dept_code, is_dh in membership_plan:
        upsert_membership(
            user_id=user_id,
            dept_code=dept_code,
            is_department_head=is_dh,
        )

    db.session.commit()
