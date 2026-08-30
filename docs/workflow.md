# Request Workflow

This document answers two questions: which states a request moves through, and
who may hold the editing lock on it. It is the authoritative copy of both. Other
documents link here instead of restating the rules.

## Stages by work type

Stages are per work type. `WorkTypeConfig.uses_dispatch` and `has_admin_final`
decide which phases a request passes through. Both columns default to False
(`app/models/workflow.py:102-103`), so a new work type opts into a stage.

Seeded values as of August 2026 (`app/seeds/bootstrap.py:168-262`):

| Work type | uses_dispatch | has_admin_final | uses_board_release | Route package |
|-----------|---------------|-----------------|--------------------|---------------|
| BUDGET | Yes | Yes | Yes | `app/routes/work/work_items/` |
| SUPPLY | No | Yes | No | `app/routes/work/supply/` |
| TECHOPS | No | No | No | `app/routes/work/techops/` |
| CONTRACT | Yes | Yes | No | None |
| AV | No | No | No | None |

CONTRACT carries the dispatch flag but has no route package and no template
tree. No CONTRACT request can reach dispatch, so BUDGET is the only work type
with a UI for that stage. SUPPLY is the combination the other documents do not
cover: no dispatch, admin final yes.

```
┌─────────────────────────────────────────────────────────────────────────┐
│ REQUESTER PHASE                                                         │
│                                                                         │
│   DRAFT ──[Submit]──▶ AWAITING_DISPATCH   (work types with dispatch)    │
│   DRAFT ──[Submit]──▶ SUBMITTED           (work types without dispatch; │
│                                           reviews created at submit)    │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
┌─────────────────────────────────────────────────────────────────────────┐
│ DISPATCH PHASE (uses_dispatch only; BUDGET is the only one with a UI)   │
│                                                                         │
│   Admin assigns approval groups per line ──▶ item becomes SUBMITTED     │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
┌─────────────────────────────────────────────────────────────────────────┐
│ APPROVER PHASE (item stays SUBMITTED; statuses below are LINE-level)    │
│                                                                         │
│   PENDING ──▶ APPROVED | REJECTED | NEEDS_INFO | NEEDS_ADJUSTMENT       │
│               kickbacks ──▶ requester responds ──▶ back to PENDING      │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
┌─────────────────────────────────────────────────────────────────────────┐
│ FINALIZATION                                                            │
│                                                                         │
│   has_admin_final (BUDGET, SUPPLY): admin sets authoritative amounts,   │
│     then [Finalize] ──▶ FINALIZED (PENDING lines auto-approved)         │
│   otherwise (TECHOPS, AV): auto-finalize when the last line is decided  │
└─────────────────────────────────────────────────────────────────────────┘
```

## Work item transitions

Every transition below writes `work_item.status`. The only other writers are
the dev seeding routes at `app/routes/dev.py:465` and `:522`, which are not
reachable in production.

| State | Trigger | Next state | Who can do it |
|-------|---------|------------|---------------|
| DRAFT | Submit on a work type with `uses_dispatch` | AWAITING_DISPATCH | Portfolio editor (`lifecycle.py:63`) |
| DRAFT | Submit on a work type without `uses_dispatch`; reviews are created inline | SUBMITTED | Portfolio editor (`lifecycle.py:82`) |
| AWAITING_DISPATCH | Recall to draft | DRAFT | Portfolio editor or work-type admin (`lifecycle.py:112`) |
| AWAITING_DISPATCH | Dispatch after approval groups are assigned per line | SUBMITTED | Budget admin or super admin (`dispatch/dashboard.py:280`) |
| SUBMITTED | Finalize on a work type with `has_admin_final` | FINALIZED | Budget admin (`admin_final/helpers.py:541`) |
| SUBMITTED | Last approval-group review decided, `has_admin_final` false | FINALIZED | Whichever approver decides the last line (`lifecycle.py:169`) |
| SUBMITTED | Finalize a supply order, setting approved quantities per line | FINALIZED | SUPPLY work-type admin or super admin (`work/supply/admin.py:525`) |
| FINALIZED | Unfinalize with a written reason | SUBMITTED | Budget admin (`admin_final/helpers.py:742`) |
| SUBMITTED (supplementary) | Its PRIMARY is unfinalized | PAUSED | Budget admin, as a side effect (`admin_final/helpers.py:784`) |
| PAUSED (supplementary) | Its PRIMARY is re-finalized and the supplementary still routes | SUBMITTED | Budget admin, as a side effect (`admin_final/helpers.py:627`) |

Unfinalize also clears `board_released_at` and withdraws any unsent release
email, so a re-finalize notifies the department again with the new numbers
(`admin_final/helpers.py:745-756`).

## Statuses declared but never written

Three work item statuses exist in `app/models/constants.py` and are assigned
nowhere in `app/`.

| Constant | Declared at | Reality |
|----------|-------------|---------|
| `UNDER_REVIEW` | `constants.py:21` | Display-only. Compared against in reports and derived by `compute_line_status_summary()`. The database never holds it. |
| `UNAPPROVED` | `constants.py:23` | Read by nothing and written by nothing. Its trailing comment "reopened after finalize" describes intent that was never built; unfinalize sets SUBMITTED. |
| `NEEDS_INFO` at item level | `constants.py:29` | Never assigned to `work_item.status` by current code. Rows stamped by the removed action may still hold it, so the reads stay. Line-level NEEDS_INFO is a different column and is used. |

The item-level and line-level NEEDS_INFO distinction matters. No current code
path writes item-level NEEDS_INFO. A request-level "Request Information" action
once did. No template posted to its response route, so those work items could
never be checked out again, and the action was removed (`constants.py:24-28`).

The item-level reads were kept on purpose, for rows stamped before that removal:
`admin_final/helpers.py:906` and `:1027`, and `computations.py:295` and `:351`.
They are compatibility handling, not dead branches. Do not delete them.

## Derived display statuses

`compute_line_status_summary()` (`app/routes/work/helpers/computations.py:213-239`)
returns a display string that templates branch on. It is not the stored status.

| Display string | Condition | Label (`formatting.py:70-88`) | Constant exists? |
|----------------|-----------|-------------------------------|------------------|
| DRAFT | Stored status is DRAFT | Draft | Yes |
| AWAITING_DISPATCH | Stored status is AWAITING_DISPATCH | Pending Review | Yes |
| PENDING_BOARD_APPROVAL | FINALIZED, `uses_board_release`, `board_released_at` is null | Pending FY Budget Approval | No |
| FINALIZED | FINALIZED and released, or a work type without board release | Finalized | Yes |
| NEEDS_RESPONSE | At least one NEEDS_INFO line and one NEEDS_ADJUSTMENT line | Response Needed | No |
| NEEDS_INFO | At least one NEEDS_INFO line | Info Requested | Line-level only |
| NEEDS_ADJUSTMENT | At least one NEEDS_ADJUSTMENT line | Changes Requested | Line-level only |
| UNDER_REVIEW | SUBMITTED with at least one PENDING line | Under Review | Yes, unwritten |
| SUBMITTED | SUBMITTED with no PENDING lines | Under Review | Yes |
| PAUSED | Fallback: any other stored status passes through unchanged (`computations.py:240-241`) | Paused | Yes |

`PENDING_BOARD_APPROVAL` and `NEEDS_RESPONSE` appear in no constants file.
Templates compare against them as bare string literals: `home.html:168` and
`:178`, `components/_work_item_card.html:15` and `:41`,
`budget/division_home.html:67`, `budget/department_home.html:56`, and
`macros/status_pill.html:41`. Rewriting those literals into a constant import
raises `ImportError` at best and silently drops the branch at worst, because the
name does not exist. Leave them as literals.

## Line statuses and review statuses

`WorkLine.status` and `WorkLineReview.status` are separate columns written by
different code paths. Do not assume they agree; finalization is the step that
forces them into line (`admin_final/helpers.py:500-535`).

| Status | Meaning | Next step |
|--------|---------|-----------|
| PENDING | Awaiting a reviewer decision | Approver reviews |
| NEEDS_INFO | Reviewer asked a question | Requester responds |
| NEEDS_ADJUSTMENT | Reviewer asked for a change | Requester adjusts the line |
| APPROVED | Approved at the current stage | Moves to the next stage |
| APPROVED_NEEDS_REVIEW | Recommended with comments. Terminal for the approval group, awaits an admin decision | Admin decides; finalize resolves it to APPROVED at the recommended amount |
| REJECTED | Denied. Finalize books it at zero | Requester may revise |

`NEEDS_ADJUSTMENT` is line level only.

## Review stages

Lines carry `current_review_stage`, either APPROVAL_GROUP or ADMIN_FINAL.

Routing to an approval group uses the work type's strategy
(`app/routing/registry.py`). BUDGET routes by expense account, TECHOPS and
SUPPLY by category, CONTRACT would route by contract type, AV by the config's
default group. See [Work types](work-types.md) for the strategy per type.

Approval group actions, all at `app/routes/approvals/reviews.py`: approve
(`:247`), approve with comments (`:254`), reject (`:261`), request information
(`:268`), and request adjustment (`:275`). A kickback sets
`needs_requester_action` on the line; the requester's response returns it to
PENDING for re-review.

Admin final review is where the authoritative amounts are set. An admin can
override an approval group recommendation, and finalize auto-approves any
remaining PENDING line at the requested amount.

## Board release

FINALIZED does not mean the department was told. A finished budget waits for the
board to approve the event topline. Release state is derived, not stored.

| Field | Meaning |
|-------|---------|
| `EventCycle.board_approved_at` (`app/models/org.py:41`) | The gate. Set once, on the first release run (`admin_final/helpers.py:1444`). |
| `WorkItem.board_released_at` (`app/models/workflow.py:303`) | Stamped per item at release (`admin_final/helpers.py:1449`), and at finalize when the board already approved (`:558`). |

Release queues the department email inside the same transaction as the stamp and
the audit row, so neither can exist without the other. Delivery is not recorded
on the work item; see [Email outbox](email-outbox.md). The release runs from
`/admin/final-review/board-release/` and refuses outright if the email template
is missing or inactive.

Board release is scoped to `WorkTypeConfig.uses_board_release`, which as of
August 2026 is BUDGET only. Without that scope every finalized TechOps and
Supply item would display as held forever.

## Checkout and locking

Checkout is a row-level lock on a work item that prevents two reviewers from
deciding the same lines at once. It is not an edit lock for requesters; drafts
are not checkoutable at all.

Rules from `app/routes/work/helpers/checkout.py`:

- Only a SUBMITTED item can be checked out (`:111`). No other status qualifies.
- Eligible holders (`:116-123`): a SUPER_ADMIN, anyone with `approval_group_ids`,
  or the WORKTYPE_ADMIN for that item's work type. Work-type admins were added
  because admin-final decisions require the lock.
- `can_checkout()` returns `tuple[bool, str]`, not a bare bool (`:105`). Callers
  that treat the return value as truthy always see True.
- Admin-final decisions refuse without the lock: "You must check out this item
  before making an admin decision" (`admin_final/reviews.py:173`).

The refusal string on the eligibility check still reads "Only reviewers can
checkout work items" (`checkout.py:123`). A work-type admin who is not a
reviewer passes that check anyway. The string is stale wording, not the rule.

### Timeouts

Defaults in minutes (`checkout.py:34-38`), overridable through the
`CHECKOUT_TIMEOUTS` config key (`:43`):

| Role | Timeout |
|------|---------|
| APPROVER | 30 |
| SUPER_ADMIN | 120 |
| Anything else | 30 |

### Expiry

An abandoned lock stops blocking on its own. `is_checked_out()` returns False
once `checked_out_expires_at` has passed (`checkout.py:60-66`), so `can_checkout()`
falls through and the next eligible reviewer's checkout overwrites the stale row
(`checkout.py:135-155`). Nobody needs to intervene. Review is never blocked
waiting for an admin to clear a lock.

The columns keep their old values until something overwrites or clears them, so a
row can name a holder whose lock no longer blocks anything. `get_checkout_info()`
reports that state as `is_expired` (`checkout.py:69-93`).

### Release before expiry

Two actions clear a lock ahead of its expiry. Neither fires as a side effect of a
review decision.

| Path | Who | Where |
|------|-----|-------|
| End the review session | The current holder, always allowed | `/checkin` route, `work_items/actions.py:215` |
| Force release | SUPER_ADMIN anywhere, or the WORKTYPE_ADMIN for that item's work type | `checkout.py:175-186` |

A TechOps admin cannot force-release a Budget item. Force release is work-type
scoped, not global.

`checkin_work_item()` has exactly one call site, `work_items/actions.py:246`,
inside the explicit `/checkin` route. Nothing releases a lock as a side effect of
a review decision, and a NEEDS_INFO request does not release it. Documentation
predating August 2026 claimed a NEEDS_INFO auto-release; that was wrong.

### Admin lock dashboard

`/admin/locks` (`app/routes/admin/locks.py:44`) lists active and expired locks for
a SUPER_ADMIN. It offers two manual actions: release one named lock
(`locks.py:87`), and clear every expired lock at once (`locks.py:111`).

Both are buttons a person clicks. `release_expired_checkouts()`
(`checkout.py:196-215`) has one caller, the `/release-expired` POST route at
`locks.py:113`. It appears in no CLI command, no `Procfile` entry, and no
`app.json` scheduler config. Nothing runs it on a timer. It nulls columns that
`is_checked_out()` already ignores, so it is housekeeping, not the mechanism that
frees a lock.

### Lock columns and audit trail

Three nullable columns on `work_item` hold the whole lock
(`app/models/workflow.py:284-286`):

| Column | Holds |
|--------|-------|
| `checked_out_by_user_id` | Who took the lock |
| `checked_out_at` | When they took it |
| `checked_out_expires_at` | The moment it stops blocking |

Both ends write an item-level audit event, `CHECKOUT` and `CHECKIN`
(`constants.py:153-154`). Checkout records the expiry it granted
(`work_items/actions.py:200`). Checkin records the previous holder and whether the
release was forced (`work_items/actions.py:250`).

### Checkout in the permissions dict

`build_work_item_perms()` exposes four lock fields on `WorkItemPerms`
(`checkout.py:280-306`). Templates and routes read these rather than calling
`can_checkout()` directly.

| Field | True when |
|-------|-----------|
| `can_checkout` | The user is a reviewer for this item, the item is SUBMITTED, and no unexpired lock exists |
| `can_checkin` | The user holds the lock, or is a work-type admin and a lock exists |
| `is_checked_out` | An unexpired lock exists |
| `is_checked_out_by_current_user` | That unexpired lock belongs to the current user |

`is_worktype_admin` returns True for a SUPER_ADMIN as well
(`work/helpers/context.py:243-246`), so `can_checkin` covers both admin cases.
See [Permissions](permissions.md) for the rest of the dict.

## Supplementary requests

After the primary budget finalizes, a requester can add supplementary requests in
the same portfolio. They share the portfolio's public ID sequence and follow the
same approval flow. A reason is optional and shows beside the date in list views.

Supplementary requests pause when their PRIMARY is unfinalized, and resume when
it finalizes again. Resuming requires every line to still have a budget detail, a
routed approval group, and an approval-group review (`admin_final/helpers.py:610-627`).

## Notifications

Notifications are queued to the email outbox inside the caller's transaction and
drained by a scheduled job. See [Email outbox](email-outbox.md) for delivery,
retries, and suppression.

| Trigger | Audience | Function (`app/services/notifications.py`) |
|---------|----------|--------------------------------------------|
| Request submitted | Work-type admins | `notify_work_item_submitted` (`:58`) |
| Request submitted | The submitter, as a receipt | `notify_submission_confirmation` (`:79`) |
| Lines dispatched, or a line rerouted by an admin | Members of each routed approval group | `notify_work_item_dispatched` (`:133`) |
| Line marked NEEDS_INFO or NEEDS_ADJUSTMENT | Department members | `notify_needs_attention` (`:163`) |
| Requester responded to a kickback | The reviewer who asked | `notify_response_received` (`:184`) |
| Board release, or finalize after board approval | Department members | `notify_work_item_finalized` (`:241`) |

One unreachable recipient does not cost the others their email. Each recipient
row is enqueued inside a savepoint (`notifications.py:272`).

## Key routes

Paths are written as registered. The portfolio landing route has no trailing
slash; the admin dashboards do.

| Route | Purpose |
|-------|---------|
| `/<event>/<dept>/budget` | Portfolio landing (`work/portfolio.py:90`) |
| `/<event>/<dept>/budget/primary/new` | Create a primary request (`work_items/create.py:30`) |
| `/<event>/<dept>/budget/item/<public_id>/submit` | Submit for review (`work_items/actions.py:37`) |
| `/<event>/<dept>/budget/item/<public_id>/checkout` | Acquire the lock (`work_items/actions.py:165`) |
| `/<event>/<dept>/budget/item/<public_id>/checkin` | Release the lock (`work_items/actions.py:216`) |
| `/approvals/` | Approver dashboard (`approvals/dashboard.py:18`) |
| `/approvals/<group_code>` | Approval group queue (`approvals/dashboard.py:35`) |
| `/dispatch/item/<id>/dispatch` | Assign groups and dispatch (`dispatch/dashboard.py:210`) |
| `/admin/final-review/` | Admin final review dashboard (`admin_final/dashboard.py:84`) |
| `/admin/final-review/finalize/<id>` | Finalize a request (`admin_final/dashboard.py:134`) |
| `/admin/final-review/board-release/` | Record board approval and release held budgets (`admin_final/dashboard.py:515`) |

## Code locations

| Concern | File |
|---------|------|
| Submit, recall, auto-finalize | `app/routes/work/helpers/lifecycle.py` |
| Checkout and locking | `app/routes/work/helpers/checkout.py` |
| Approval group decisions | `app/routes/approvals/reviews.py` |
| Admin final, finalize, board release | `app/routes/admin_final/helpers.py` |
| Derived status computation | `app/routes/work/helpers/computations.py` |
| Status labels | `app/routes/work/helpers/formatting.py` |
| Notification enqueue | `app/services/notifications.py` |
