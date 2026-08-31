# Permissions and Access Control

This document answers one question: what decides whether a given user may see or
change a given thing. Three mechanisms decide it, and they are independent.

| Mechanism | Stored in | Scope |
|-----------|-----------|-------|
| System roles | `UserRole` (`app/models/workflow.py:187`) | Global, one work type, or one approval group |
| Memberships | `DepartmentMembership`, `DivisionMembership` (`app/models/org.py:227`, `:115`) | One department or division, for one event cycle |
| Checkout lock | Columns on `work_item` | One work item, while a reviewer holds it |

A user with no role and no membership sees nothing beyond the dashboard.

## Roles and memberships

| Role | Scope | Granted by | Revoked by |
| --- | --- | --- | --- |
| `SUPER_ADMIN` | Global. Every department, work type, and admin page | Admin → Users → Edit User, "Super Admin" box. Writes a `UserRole` row with both scope columns NULL | Same form with the box cleared |
| `WORKTYPE_ADMIN` | One work type, via `user_roles.work_type_id` | Admin → Users → Edit User, the box for that work type | Same form with the box cleared |
| `APPROVER` | One approval group, via `user_roles.approval_group_id` | Admin → Users → Edit User, the box for that group | Same form with the box cleared |
| Department membership | One department, one event cycle | Admin → Departments → [Department] → Members → Add Member (`admin/departments.py:453`) | Delete on the member row (`departments.py:603`) |
| Division membership | Every department in one division, one event cycle | Admin → Divisions → [Division] → Members → Add Member (`admin/divisions.py:372`) | Delete on the member row (`divisions.py:495`) |

All three role checkboxes post to the same handler. `_update_user_roles()`
(`admin/users.py:309`) deletes every existing role row for the user, then rewrites
the set from the submitted form. An unchecked box is a revocation.

Memberships also arrive in bulk through Admin → Data Upload, which accepts CSV for
department and division memberships. The POST importers are
`department_memberships_upload()` (`admin/data_upload.py:958`) and
`division_memberships_upload()` (`:1102`). The
upload updates an existing membership for the same user, unit, and event cycle
rather than creating a second one (`:1044`).

## Membership is event-scoped

A membership grants access for one event cycle, not forever. Both models carry
`event_cycle_id` (`org.py:246`, `:139`) and both include it in their unique
constraint (`:270`, `:162`). A user who worked SMF2026 has no access to SMF2027
until someone adds a membership for that cycle.

Membership alone grants nothing either. Per-work-type rows in
`DepartmentMembershipWorkTypeAccess` (`org.py:294`) and
`DivisionMembershipWorkTypeAccess` (`:186`) carry the actual view and edit flags,
read through `can_view_work_type()` and `can_edit_work_type()`, declared once per
membership model (`org.py:283`, `:288` for departments; `:175`, `:180` for
divisions).
The `can_view` and `can_edit` columns on the membership row itself are legacy and
are not what `build_portfolio_perms()` consults.

## What a WORKTYPE_ADMIN can reach

A work-type admin is not a scaled-down super admin. The split is per page, not per
prefix, and two pages under `/admin/config/` fall on opposite sides.

| Page | Guard | Who gets in |
|------|-------|-------------|
| `/admin/` system dashboard | `require_admin(user_ctx)` (`admin_final/dashboard.py:261`) | SUPER_ADMIN only |
| `/admin/config/users/` | `@require_super_admin` | SUPER_ADMIN only |
| `/admin/config/approval-groups/` | `@require_super_admin` (`admin/approval_groups.py:66`) | SUPER_ADMIN only |
| `/admin/config/expense-accounts/` | `@require_budget_admin` (`admin/expense_accounts.py:144`) | Budget admin or SUPER_ADMIN |
| `/admin/budget/` | Checked inside the view (`admin_final/dashboard.py:301`) | Budget admin or SUPER_ADMIN |

Approval groups are shared infrastructure, so their config pages sit behind
SUPER_ADMIN even though budget admins use the groups daily.

## Guards: the module decides how you call it

`app/routes/admin/helpers.py` exports decorators. `app/routes/admin_final/helpers.py`
exports callables that abort 403. The module you import from determines the call
shape, and the two misuses fail in opposite directions. Decorating a view with the
callable raises `AttributeError` at decoration time, so the module fails to import.
Calling the decorator inline as a guard is the silent one: it binds `f` to the
`user_ctx` you passed, returns a wrapper you discard, runs no check, and leaves the
view unguarded.

One name exists in both modules. Learn the rule rather than the pair; the next
collision will follow the same rule.

| Name | Decorator (`admin/helpers.py`) | Callable (`admin_final/helpers.py`) |
|------|-------------------------------|-------------------------------------|
| `require_budget_admin` | `:48`, takes `f` | `:119`, takes `user_ctx` |
| `require_super_admin` | `:28`, takes `f` | none |
| `require_supply_admin` | `:69`, takes `f` | none |
| `require_admin` | none | `:113`, takes `user_ctx`, checks SUPER_ADMIN |

```python
# Decorator form
@expense_accounts_bp.get("/")
@require_budget_admin
def list_accounts():
    ...

# Callable form
@admin_final_bp.get("/admin/budget/")
def budget_admin_home():
    user_ctx = get_user_ctx()
    require_budget_admin(user_ctx)
```

## Where the permission helpers live

Every helper below takes `user_ctx`, a `UserContext` (`app/routes/__init__.py:55`).
It is a frozen per-request snapshot of `user_id`, `user`, `roles`,
`is_super_admin`, and `approval_group_ids`; build it with `get_user_ctx()`. The
helpers are spread across three modules, and the module is not guessable from the
name.

| Function | Module | Kind |
|----------|--------|------|
| `get_user_ctx()` |  `app/routes/__init__.py:77` | Returns the request's `UserContext` |
| `is_super_admin()` | `app/__init__.py:543` | Zero-arg, reads the session |
| `_has_super_admin_role()` | `app/__init__.py:523` | Zero-arg, ignores beta overrides |
| `is_worktype_admin(user_ctx, work_type_id)` | `work/helpers/context.py:243` | Boolean |
| `is_budget_admin(user_ctx, work_type_id=None)` | `work/helpers/context.py:257` | Boolean |
| `is_any_worktype_admin(user_ctx)` | `work/helpers/context.py:265` | Boolean |
| `build_portfolio_perms(ctx)` | `work/helpers/context.py:282` | Returns `PortfolioPerms` |
| `build_work_item_perms(item, ctx)` | `work/helpers/checkout.py:241` | Returns `WorkItemPerms` |
| `require_portfolio_view(ctx)` | `work/helpers/context.py:355` | Callable, aborts 403 |
| `require_portfolio_edit(ctx)` | `work/helpers/context.py:363` | Callable, aborts 403 |
| `require_work_item_view(item, ctx)` | `work/helpers/context.py:371` | Callable, aborts 403 |
| `is_reviewer_for_line(line, user_ctx)` | `approvals/helpers.py:130` | Boolean |
| `can_respond_to_work_item(item, ctx, user_ctx)` | `approvals/helpers.py:148` | Boolean |

`build_work_item_perms()` lives in `checkout.py` while the `WorkItemPerms`
dataclass it returns is declared in `context.py:67`. Both builders take a
`PortfolioContext`, so call `get_portfolio_context()` first.

`is_any_worktype_admin()` has no callers as of August 2026 and is kept on
purpose. The two `require_any_worktype_admin` guards that called it were deleted
as dead code; the predicate stays for the cross-work-type admin check that is
likely to be wanted again.

`is_budget_admin()` takes an optional `work_type_id` and forwards to
`is_worktype_admin()`. When callers pass a work type, the name no longer describes
what it checks: `build_portfolio_perms()` calls
`is_budget_admin(ctx.user_ctx, ctx.work_type.id)` for TechOps portfolios too
(`context.py:284`).

## Beta testing role override

`is_super_admin()` respects a session role override; `_has_super_admin_role()`
does not. The override applies only when `BETA_TESTING_MODE` is set and only for a
user who holds the role in the database (`app/__init__.py:533-540`). Values `none`
and `approver` drop the caller to non-admin, and `approver` narrows
`approval_group_ids` to the single group chosen in the session. Use
`_has_super_admin_role()` only to decide whether an override is permitted.

## Permission dicts

`PortfolioPerms` (`context.py:52`) and `WorkItemPerms` (`context.py:67`) are frozen
dataclasses that templates read instead of calling the checks themselves.

```python
@dataclass(frozen=True)
class PortfolioPerms:
    can_view: bool
    can_edit: bool
    can_create_primary: bool
    can_create_supplementary: bool
    is_worktype_admin: bool     # admin for THIS work type

@dataclass(frozen=True)
class WorkItemPerms:
    can_view: bool
    can_edit: bool
    can_submit: bool
    can_recall: bool
    can_add_lines: bool
    can_delete: bool
    can_checkout: bool
    can_checkin: bool
    is_worktype_admin: bool
    is_draft: bool
    is_checked_out: bool
    is_checked_out_by_current_user: bool
```

The four lock fields on `WorkItemPerms` follow the checkout rules described in
[Workflow](workflow.md#checkout-and-locking), which is authoritative for locking.

`can_create_supplementary` also depends on the event cycle. The primary must be
FINALIZED unless the cycle sets `allow_early_supplementary` (`context.py:323-327`).

## Duplicate role rows in dev

The `uq_user_role_scoped_once` UniqueConstraint on `user_roles` does not stop
duplicate rows when both scope columns are NULL, because SQL treats NULL as not
equal to NULL. A user can hold two `SUPER_ADMIN` rows as far as that constraint is
concerned.

Two other layers cover the gap:

1. PostgreSQL partial indexes from migration `o5p6q7r8s9t0`:
   `ix_user_roles_global_unique`, `ix_user_roles_worktype_unique`,
   `ix_user_roles_approvalgroup_unique`.
2. Application checks in the admin role routes, which clear the role set before
   rewriting it.

SQLite dev databases get the constraint but not the partial indexes. Duplicate
global role rows can therefore appear in dev and not in production. Reproduce any
suspected duplicate-role bug against PostgreSQL before treating it as real.

## Role changes are not in the config audit log

Editing a user writes a `config_audit_log` row only when `track_changes()` finds a
difference, and `_user_to_dict()` (`admin/users.py:45`) reports just `email`,
`display_name`, and `is_active`. Granting or revoking a role changes none of those
fields. As of August 2026, a role change made on its own leaves no audit row, and
the only record is the current contents of `user_roles`.

Membership changes do log, through the `log_config_change()` calls in
`admin/departments.py` and `admin/divisions.py`.
