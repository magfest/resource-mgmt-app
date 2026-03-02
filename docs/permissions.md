# Permissions & Access Control

This document explains how access control works in the system.

## Overview

Access is controlled at three levels:

1. **System Roles** - Global or work-type-scoped admin/approver access
2. **Memberships** - Department or division membership with work type scoping
3. **Request-Level** - Checkout locks for concurrent edit prevention

---

## Naming Conventions

To avoid confusion, we use consistent naming throughout the codebase:

| Term | Meaning |
|------|---------|
| `is_super_admin` | User has `SUPER_ADMIN` role (global admin access) |
| `is_worktype_admin` | User is admin for a specific work type (SUPER_ADMIN OR WORKTYPE_ADMIN) |
| `is_budget_admin()` | Convenience function to check budget work type admin status |

**Important distinctions:**

- `user_ctx.is_super_admin` - Checks if user is a **global** super admin
- `perms.is_worktype_admin` - Checks if user is admin for **this specific work type**

A super admin is always a worktype admin (for all work types), but a worktype admin is NOT a super admin.

---

## System Roles

System roles are stored in the `UserRole` model and managed via Admin → Users.

### SUPER_ADMIN

Full access to everything:
- All departments, all work types
- All admin pages (system config AND work type admin)
- All approval queues
- Can finalize/unfinalize requests

**Code check:** `user_ctx.is_super_admin` or `is_super_admin()`

### WORKTYPE_ADMIN

Admin access for a specific work type:
- See all departments for that work type
- Access admin pages for that work type only
- Cannot access system config pages (`/admin/`)
- Cannot access other work types

Example: "Budget Admin" can manage all budgets but not contracts.

**Code check:** `is_worktype_admin(user_ctx, work_type_id)` or `is_budget_admin(user_ctx)`

### APPROVER

Can review lines routed to specific approval groups:
- Appears in approver dashboard
- Can approve/reject/request info on lines
- Scoped to one or more approval groups

**Code check:** `user_ctx.approval_group_ids` contains the relevant group ID

---

## Permission Functions Reference

### Global Functions (in `app/__init__.py`)

```python
# Check if user is a super admin (respects beta testing role overrides)
is_super_admin() -> bool

# Check actual database role (ignores beta testing overrides)
# Use this only for checking if override is allowed
_has_super_admin_role() -> bool
```

### UserContext (built per-request)

```python
@dataclass
class UserContext:
    user_id: str
    user: User | None
    roles: tuple[str, ...]
    is_super_admin: bool           # True if SUPER_ADMIN role
    approval_group_ids: Set[int]   # Approval groups user can review
```

### Work Type Admin Checks (in `app/routes/work/helpers.py`)

```python
# Check if user is admin for a specific work type
is_worktype_admin(user_ctx: UserContext, work_type_id: int) -> bool

# Convenience: Check if user is budget admin
is_budget_admin(user_ctx: UserContext, work_type_id: int | None = None) -> bool
```

### Permission Objects

```python
@dataclass
class PortfolioPerms:
    can_view: bool              # Can see the portfolio
    can_edit: bool              # Can edit draft requests
    can_create_primary: bool    # Can create primary request
    can_create_supplementary: bool  # Can create supplementary
    is_worktype_admin: bool     # Is admin for THIS work type

@dataclass
class WorkItemPerms:
    can_view: bool
    can_edit: bool
    can_submit: bool
    can_add_lines: bool
    can_delete: bool
    can_checkout: bool
    can_checkin: bool
    can_request_info: bool
    can_respond_to_info: bool
    is_worktype_admin: bool     # Is admin for THIS work type
    is_draft: bool
    is_checked_out: bool
    is_checked_out_by_current_user: bool
```

---

## Memberships

Memberships grant department/division access and are scoped by:
- **Event Cycle** - Access is per-event (SMF2027, MAGStock 2027, etc.)
- **Work Type** - Access is per-work-type (Budget, Contracts, Supply)

### Department Membership

Direct access to one department:

```
DepartmentMembership
├── user_id
├── department_id
├── event_cycle_id
├── can_view (general flag)
├── can_edit (general flag)
├── is_department_head (informational)
└── work_type_access[] ← Per-work-type permissions
    ├── BUDGET: can_view=True, can_edit=True
    ├── CONTRACT: can_view=False, can_edit=False
    └── SUPPLY: can_view=True, can_edit=False
```

### Division Membership

Access to ALL departments in a division:

```
DivisionMembership
├── user_id
├── division_id
├── event_cycle_id
├── can_view
├── can_edit
├── is_division_head
└── work_type_access[] ← Applies to all departments in division
```

Division membership is useful for:
- Division heads who oversee multiple departments
- Cross-department roles

### Work Type Access

**Important**: Just having a membership doesn't grant work type access.

A user must have explicit work type access:

```python
# Check if user can view budgets for TechOps
membership = DepartmentMembership.query.filter_by(
    user_id=user.id,
    department_id=techops.id,
    event_cycle_id=smf2027.id,
).first()

can_view_budget = membership.can_view_work_type(budget_work_type.id)
```

This allows:
- Budget-only access (most common)
- Contracts access for specific people only
- View-only access for oversight roles

---

## Permission Checks in Code

### Route-Level Checks

Most routes use context builders that handle permission checks:

```python
from app.routes.work.helpers import get_portfolio_context, require_portfolio_view

@work_bp.get("/<event>/<dept>/budget")
def portfolio_landing(event, dept):
    ctx = get_portfolio_context(event, dept)  # Builds context
    perms = require_portfolio_view(ctx)        # Aborts 403 if no access
    # ... user has access, continue
```

### Admin Page Checks

```python
from app.routes.admin_final.helpers import require_budget_admin

@admin_final_bp.get("/budget/")
def budget_admin_home():
    user_ctx = get_user_ctx()
    require_budget_admin(user_ctx)  # Aborts 403 if not budget admin
    # ... user is budget admin, continue
```

### Checking Work Type Access

```python
# In a membership context
if membership.can_view_work_type(work_type_id):
    # Show the work type

if membership.can_edit_work_type(work_type_id):
    # Allow editing
```

---

## Admin UI for Permissions

### Managing System Roles

Admin → Users → Edit User

- Check "Super Admin" for full access
- Check work type admin boxes for work-type-scoped admin
- Check approval group boxes for approver access

### Managing Memberships

Admin → Departments → [Department] → Members

Or: Admin → Divisions → [Division] → Members

Each membership form shows:
- General permissions (legacy, informational)
- Work Type Access table with View/Edit checkboxes per work type

---

## Common Scenarios

### "User can see budgets but not contracts"

Give them:
- Department membership with BUDGET work type access (view + edit)
- No CONTRACT work type access

### "User can view all departments in a division"

Give them:
- Division membership with appropriate work type access

### "User can approve budget lines for a specific category"

Give them:
- APPROVER role for the relevant approval group(s)
- They don't need department membership (approvers see lines routed to their group)

### "User is department head but contracts are restricted"

Give them:
- Department membership, is_department_head=True
- BUDGET work type access (view + edit)
- No CONTRACT work type access (or view-only if needed for awareness)

### "User needs to manage ALL budgets but NOT system config"

Give them:
- WORKTYPE_ADMIN role for BUDGET work type
- They can access `/admin/budget/` but NOT `/admin/` (system config)

---

## Permission Hierarchy

```
SUPER_ADMIN (global)
  ├─ Can access /admin/ (system config)
  ├─ Can access all /admin/{worktype}/ pages
  ├─ Treated as worktype admin for ALL work types
  └─ Can use beta testing role override

WORKTYPE_ADMIN (scoped to work type)
  ├─ Can access /admin/{worktype}/ for their work type
  ├─ CANNOT access /admin/ (system config)
  ├─ Admin for that work type only
  └─ Equivalent to dept membership + admin powers for that work type

APPROVER (scoped to approval groups)
  ├─ Can review lines routed to their groups
  ├─ Appears in approval dashboard
  └─ No admin access

Department/Division Membership
  ├─ Can view/edit portfolios (per work type access)
  ├─ Can create/submit requests
  └─ Scoped by event cycle and work type
```

---

## Audit Trail

Permission-related changes are logged:
- User role changes (via UserRole)
- Membership changes (via DepartmentMembership, DivisionMembership)

Check `config_audit_log` table for history.
