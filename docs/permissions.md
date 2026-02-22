# Permissions & Access Control

This document explains how access control works in the system.

## Overview

Access is controlled at three levels:

1. **System Roles** - Global or work-type-scoped admin/approver access
2. **Memberships** - Department or division membership with work type scoping
3. **Request-Level** - Checkout locks for concurrent edit prevention

## System Roles

System roles are stored in the `UserRole` model and managed via Admin → Users.

### SUPER_ADMIN

Full access to everything:
- All departments, all work types
- All admin pages
- All approval queues
- Can finalize/unfinalize requests

### WORKTYPE_ADMIN

Admin access for a specific work type:
- See all departments for that work type
- Access admin pages for that work type
- Cannot access other work types

Example: "Budget Admin" can manage all budgets but not contracts.

### APPROVER

Can review lines routed to specific approval groups:
- Appears in approver dashboard
- Can approve/reject/request info on lines
- Scoped to one or more approval groups

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

## Permission Checks in Code

### Route-Level Checks

Most routes use context builders that handle permission checks:

```python
from app.routes.budget.helpers import get_portfolio_context, require_portfolio_view

@budget_bp.get("/<event>/<dept>/budget")
def portfolio_landing(event, dept):
    ctx = get_portfolio_context(event, dept)  # Builds context
    perms = require_portfolio_view(ctx)        # Aborts if no access
    # ... user has access, continue
```

### Permission Objects

The `PortfolioPerms` object tells you what the user can do:

```python
@dataclass
class PortfolioPerms:
    can_view: bool          # Can see the portfolio
    can_edit: bool          # Can edit draft requests
    can_submit: bool        # Can submit for review
    can_checkout: bool      # Can lock for editing
    is_admin: bool          # Has admin privileges
```

### Checking Work Type Access

```python
# In a membership context
if membership.can_view_work_type(work_type_id):
    # Show the work type

if membership.can_edit_work_type(work_type_id):
    # Allow editing
```

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

## Audit Trail

Permission-related changes are logged:
- User role changes (via UserRole)
- Membership changes (via DepartmentMembership, DivisionMembership)

Check `config_audit_log` table for history.
