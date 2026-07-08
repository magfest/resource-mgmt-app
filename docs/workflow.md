# Request Workflow

This document explains the lifecycle of a request from creation to finalization.

## Lifecycle Overview

Stages are per-work-type: `WorkTypeConfig.uses_dispatch` and `has_admin_final`
control which phases exist. BUDGET uses all of them; TECHOPS skips dispatch and
admin-final (it auto-finalizes when the last line is decided).

```
┌─────────────────────────────────────────────────────────────────────────┐
│ REQUESTER PHASE                                                          │
│                                                                          │
│   DRAFT ──[Submit]──▶ AWAITING_DISPATCH   (work types with dispatch)     │
│   DRAFT ──[Submit]──▶ SUBMITTED           (work types without dispatch;  │
│                                            reviews created at submit)    │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
┌─────────────────────────────────────────────────────────────────────────┐
│ DISPATCH PHASE (uses_dispatch only — BUDGET)                             │
│                                                                          │
│   Admin assigns approval groups per line ──▶ item becomes SUBMITTED      │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
┌─────────────────────────────────────────────────────────────────────────┐
│ APPROVER PHASE (item stays SUBMITTED; statuses below are LINE-level)     │
│                                                                          │
│   PENDING ──▶ APPROVED | REJECTED | NEEDS_INFO | NEEDS_ADJUSTMENT        │
│                              kickbacks ──▶ requester responds ──▶ back   │
│                              to PENDING for re-review                    │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
┌─────────────────────────────────────────────────────────────────────────┐
│ FINALIZATION                                                             │
│                                                                          │
│   has_admin_final (BUDGET): admin sets authoritative amounts, then       │
│     [Finalize] ──▶ FINALIZED (remaining PENDING lines auto-approved)     │
│   otherwise (TECHOPS): auto-finalize when the last line is decided       │
└─────────────────────────────────────────────────────────────────────────┘
```

## Statuses

### Work Item Statuses

| Status | Description | Who Can Edit? |
|--------|-------------|---------------|
| DRAFT | Requester is building the request | Requester |
| AWAITING_DISPATCH | Submitted; waiting for approval group assignment (dispatch work types only) | No one |
| SUBMITTED | Lines under review | Reviewers (via checkout); requester responds to kickbacks |
| FINALIZED | Locked and complete | No one (admin can unfinalize) |
| PAUSED | Supplementary blocked by pending PRIMARY | No one |
| UNAPPROVED | Reopened after finalize | Per admin action |

(`NEEDS_INFO`/`NEEDS_ADJUSTMENT` are line-level, not item-level; a line kickback
sets `needs_requester_action` on the line and flags the item.)

### Work Line Statuses

| Status | Description | Next Step |
|--------|-------------|-----------|
| PENDING | Awaiting review | Approver reviews |
| NEEDS_INFO | Question asked | Requester responds |
| NEEDS_ADJUSTMENT | Change requested | Requester adjusts |
| APPROVED | Approved at current stage | Moves to next stage |
| REJECTED | Denied | Requester may revise |

## Review Stages

Lines go through two review stages:

### 1. Approval Group Review

Lines are routed to approval groups based on the work type's routing strategy:
- **Budget** (live): Routed via expense account
- **TechOps** (live): Routed via service type (category strategy)
- **Supply Orders** (in development): Will route via item category
- **Contracts** (future): Will route via contract type

Approvers in that group can:
- Approve the line
- Reject the line
- Request more information
- Request adjustment

### 2. Admin Final Review

After approval group approval, admins do a final review:
- Confirm amounts
- Override approved amounts if needed
- Add final notes

## The Checkout System

To prevent conflicts when editing:

1. **Checkout**: User locks the request for editing
2. **Edit**: Only the user with checkout can edit
3. **Release**: Checkout released on save or timeout

Admins can force-release checkouts if needed.

## Typical Flow

### Budget Request

1. **Requester creates draft**
   - Adds line items (expense account, quantity, price)
   - Saves progress

2. **Requester submits**
   - Status: DRAFT → AWAITING_DISPATCH; budget admins notified
   - Admin dispatches: assigns approval groups per line, creates review records
   - Status: AWAITING_DISPATCH → SUBMITTED

3. **Approvers review**
   - Each approval group sees their lines
   - Approve, reject, or request changes

4. **Back-and-forth** (if needed)
   - Approver: "What's this for?"
   - Requester: Responds with explanation
   - Approver: Approves

5. **Admin final review**
   - Admin sees all approved lines
   - Confirms or adjusts amounts
   - Finalizes the request

6. **Done**
   - Request locked
   - Amounts confirmed for the event

## Supplementary Requests

After the primary budget is finalized, requesters can add supplementary requests:

1. Create supplementary request (same portfolio)
   - Optionally add a reason (e.g., "Additional equipment needed", "Revised vendor quote")
2. Add new lines
3. Submit for review
4. Same approval flow

Supplemental requests are labeled as "Supplemental #1", "Supplemental #2", etc. in list views, based on creation order. The reason (if provided) is displayed alongside the date for easy identification.

Supplementary requests are common for:
- Unexpected needs discovered later
- Additional projects approved after initial budget
- Emergency purchases

## Notifications

Email notifications are sent at key workflow transitions via AWS SES:

- **Budget admins** notified when a request is submitted (awaiting dispatch)
- **Approval group members** notified when lines are dispatched to their group
- **Requesters** notified when a line needs their response (NEEDS_INFO or NEEDS_ADJUSTMENT)
- **Reviewers** notified when a requester responds to their feedback
- **Department members** notified when a request is finalized

Notification failures are non-blocking — the workflow operation completes even if email delivery fails. Failures are logged for troubleshooting.

## Key Routes

| Route | Purpose |
|-------|---------|
| `/<event>/<dept>/budget/` | Portfolio landing (see all requests) |
| `/<event>/<dept>/budget/primary/new` | Create primary request |
| `/<event>/<dept>/budget/item/<id>` | View/edit request |
| `/approvals/` | Approver dashboard |
| `/approvals/<group>/` | Approval group queue |
| `/admin/final/` | Admin final review dashboard |

## Code Locations

| Function | File |
|----------|------|
| Submit logic | `app/routes/work/work_items/actions.py` |
| Approval actions | `app/routes/approvals/reviews.py` |
| Admin finalization | `app/routes/admin_final/helpers.py` |
| Status computation | `app/routes/work/helpers/computations.py` |
| Notifications | `app/services/notifications.py` |
