# Budget Line Review Page — Action & State Map

**Page:** `/<event>/<dept>/budget/item/<public_id>/line/<n>/review`
**Route:** `approvals.line_review` (GET) → `app/templates/budget/line_review.html`
**Date mapped:** 2026-07-23 (working tree; line numbers are approximate and drift as the file changes)

This document maps every button on the budget line-review page: what it does, what
state it changes, and what happens in the tricky flows (admin reset, admin skip of the
reviewer group, kickbacks). The **Concerns** section at the end lists the bugs and
inconsistencies found while tracing — read that first if you're deciding what to fix.

---

## 1. The two-stage model

A budget line is reviewed in **two independent stages**, each with its own review record
(`WorkLineReview` rows, distinguished by `stage`):

- **APPROVAL_GROUP (AG) review** — the reviewer group's *recommendation*.
  Retrieved by `get_review_for_line(line)` (`approvals/helpers.py:266`, filters
  `stage=APPROVAL_GROUP`). In the template this is `review` and also `ag_review`.
- **ADMIN_FINAL review** — the admin's *authoritative* decision.
  Retrieved by `get_admin_final_review(line)`. In the template this is `admin_review`.

The catch that causes most bugs: **`line.status` is a single field that both stages
write to.** It cannot represent "AG recommended AND admin hasn't decided" and
"admin needs info" distinctly — it just holds the last write. Any code that treats
`line.status` as the source of truth for *which stage did what* will misread mixed states.

### State fields touched by review actions
| Field | Meaning | Written by |
|---|---|---|
| `line.status` | Overall line status (PENDING / APPROVED / REJECTED / NEEDS_INFO / NEEDS_ADJUSTMENT / APPROVED_NEEDS_REVIEW) | both stages (`sync_line_status` for AG, `apply_admin_final_decision` for admin) |
| `ag_review.status` | AG recommendation (PENDING / APPROVED / APPROVED_NEEDS_REVIEW / REJECTED / NEEDS_INFO / NEEDS_ADJUSTMENT) | AG actions |
| `admin_review.status` | Admin final decision (PENDING / APPROVED / REJECTED / NEEDS_INFO) | admin actions |
| `line.approved_amount_cents` | Authoritative approved amount | admin approve / finalize |
| `ag_review.approved_amount_cents` | Reviewer's *recommended* amount | AG approve / recommend |
| `line.needs_requester_action` | Flag: requester must respond | AG or admin needs-info/needs-adjustment; cleared on respond/reset |
| `line.current_review_stage` | Which stage the line is at (APPROVAL_GROUP / ADMIN_FINAL) | most actions |
| checkout (`checked_out_by_user_id`) | Reviewer lock | checkout/checkin |

---

## 2. Actions catalog

### 2a. Approval-group (reviewer) actions
All go through: route → `_handle_review_action` (`approvals/reviews.py:261`) →
`apply_review_decision` (`approvals/helpers.py:532`) → `validate_review_transition`
(note-required + role + admin-final guard) → `sync_line_status` (`helpers.py:383`,
maps `review.status` → `line.status`) → audit `REVIEW_DECISION` + a **PUBLIC** comment
(Task 13) → `try_auto_finalize`.

**Preconditions for all AG decisions:** acting user is a reviewer for the line
(`is_reviewer_for_line`) **and holds the checkout** (`apply_review_decision:559`) **and**
the AG review is `PENDING` **and** the admin has **not** already made a terminal decision
(Task 12 guard in `validate_review_transition`). These are surfaced as `can_decide`
(`reviews.py:154`).

| Button | Route | AG review → | line.status → | Other effects |
|---|---|---|---|---|
| Approve | `approvals.line_approve` | APPROVED | APPROVED | captures recommended amount (`ag_review.approved_amount_cents`) |
| Recommend With Comments | `approvals.line_approve_needs_review` | APPROVED_NEEDS_REVIEW | APPROVED_NEEDS_REVIEW | **note required**; captures recommended amount |
| Reject | `approvals.line_reject` | REJECTED | REJECTED | note required |
| Need Info | `approvals.line_needs_info` | NEEDS_INFO | NEEDS_INFO | note required; `needs_requester_action=True` |
| Need Adjustment | `approvals.line_needs_adjustment` | NEEDS_ADJUSTMENT | NEEDS_ADJUSTMENT | note required; `needs_requester_action=True` |
| Reset (RESET) | `approvals.line_reset` | PENDING | PENDING | ADMIN-only role; **not linked from any template** (see Concern C7) |

### 2b. Admin-final actions
Go through: route → `_handle_admin_decision` (`admin_final/reviews.py:108`) →
`apply_admin_final_decision` (`admin_final/helpers.py:334`) → audit `ADMIN_FINAL` + a
**PUBLIC** comment (Task 13). **Precondition: `require_budget_admin` only — NO checkout
check** (Concern C3).

| Button | Route | admin_review → | line.status → | Other effects |
|---|---|---|---|---|
| Approve | `admin_final.line_approve` | APPROVED | APPROVED | sets **authoritative** `line.approved_amount_cents`; amount-override needs a note if ≠ recommended |
| Reject | `admin_final.line_reject` | REJECTED | REJECTED | note required; `approved_amount_cents=None` |
| Need Info | `admin_final.line_needs_info` | NEEDS_INFO | NEEDS_INFO | note required; `needs_requester_action=True`; `current_review_stage=ADMIN_FINAL` |
| Reset for Re-review | `admin_final.line_reset` → `reset_line_for_rereview` (`helpers.py:687`) | PENDING (decided_at/by cleared) | PENDING | `approved_amount_cents=None`; `needs_requester_action=False`; **does NOT touch `ag_review`** |

### 2c. Requester actions
| Button | Route | Precondition (`can_respond`, `reviews.py:161`) | Effect |
|---|---|---|---|
| Submit Response | `approvals.line_respond` | `line.needs_requester_action` **and `review`(=AG).status ∈ {NEEDS_INFO, NEEDS_ADJUSTMENT}`** and `can_respond_to_work_item` | AG review + line → PENDING; clears `needs_requester_action` |
| Submit Adjustment | `approvals.line_adjust` | same as respond | edits line detail (qty/price/desc) + responds |

### 2d. Comments (both stages)
| Button | Route | Notes |
|---|---|---|
| Add Comment | `approvals.line_comment` | standalone thread; **admin-only checkbox** available to reviewers + admins (`is_admin or can_review`); the only channel for non-public notes (Task 13) |

**Note on decision notes:** every decision (AG or admin) stores its note in **three** places —
`review.note`, a **PUBLIC** `WorkLineComment` (with a prefix like `[ADMIN INFO REQUESTED]`),
and the audit event's `note`. Task 13 made all decision notes public; genuinely private
notes must use the standalone comment form's admin-only checkbox (which creates a comment
but **no** audit event, so it never appears in Line History).

---

## 3. What shows on the page (button visibility)

- **Header pill** (`line_review.html:22`): stage-aware — AG-stage APPROVED/REJECTED render
  as blue "REVIEWER RECOMMENDED" / red "REVIEWER REJECTED"; admin-final renders the plain
  status.
- **Two-stage decision trail** (right column, above the tabs): driven by the **review
  records** (`ag_review`/`admin_review`), NOT `line.status` (Task 15 fix), so it stays
  visible after an admin reset. Reviewer part from `ag_review`, admin part from `admin_review`.
- **Reviewer Group Review tab / Admin Final Review tab**: shown to `is_admin`
  (super admin OR work-type admin — Task 16). The AG decision form shows only when
  `can_decide`. The admin decision form shows when the admin hasn't made a terminal decision.
- **"Awaiting Requester Response"** notice + response form: the notice shows for
  `line.status ∈ {NEEDS_INFO, NEEDS_ADJUSTMENT}`; the **form inside it** shows only when
  `can_respond` (which keys on the AG review — see Concern C1).
- **Checkout banner**: "You have this item checked out / You can make review decisions"
  when the acting user holds the checkout.

---

## 4. Special flows

### 4a. Normal happy path
Submit → dispatch (AG reviews assigned) → AG decides (recommend/approve/reject) →
admin final decides → finalize. `line.status` tracks the latest stage; on finalize any
still-`APPROVED_NEEDS_REVIEW` line resolves to APPROVED at the recommended amount.

### 4b. Admin skips / bypasses the reviewer group
An admin can finalize from `SUBMITTED` before the AG reviews (admin bypass is allowed by
`can_finalize_work_item`). The **AG review stays `PENDING`**. Task 12 added a guard so the
AG can no longer act once the admin has a *terminal* decision — but the AG review still
displays as un-decided ("No Reviewer Group Review Yet" in the admin card).

### 4c. Admin "Reset for Re-review"
`reset_line_for_rereview` resets **only** `admin_review` + `line.status` → PENDING and
clears the approved amount. **`ag_review` is left untouched.** Result: `line.status=PENDING`
but `ag_review` still holds its recommendation. The trail (Task 15) keeps the AG
recommendation visible; the admin re-decides on the Admin Final tab. The default
(Reviewer Group) tab is empty in this state (Concern C8).

### 4d. Admin override
Admin can approve a line the AG rejected, or reject one the AG recommended. The trail shows
both ("Reviewer Recommended" + "Admin Rejected"). `line.status` reflects the **admin**
decision; `line.approved_amount_cents` is authoritative from the admin.

### 4e. Kickbacks
- **AG kickback** (Need Info / Need Adjustment): AG review + line → NEEDS_INFO/ADJUSTMENT,
  `needs_requester_action=True`. Requester's response works (`can_respond` + `line_respond`
  both key on the AG review). ✔
- **Admin kickback** (admin Need Info): admin_review + line → NEEDS_INFO,
  `needs_requester_action=True`. **Requester's response is BROKEN** — see Concern C1. �’

---

## 5. Concerns (prioritized)

### C1 — 🔴 BUG: admin-stage kickback has no working requester response path
When an admin uses **Need Info** (or the code path for needs-adjustment), it sets
`admin_review.status=NEEDS_INFO`, `line.status=NEEDS_INFO`, `needs_requester_action=True`
(`apply_admin_final_decision`, `helpers.py:400-406`). But the requester's response path keys
on the **AG** review:
- `can_respond` (`reviews.py:161`) requires `review.status ∈ {NEEDS_INFO, NEEDS_ADJUSTMENT}`
  where `review = get_review_for_line` = the AG review (often APPROVED_NEEDS_REVIEW) → `False`.
- `line_respond` (`reviews.py:394, 407`) does the same check and would reject the POST with
  "This line is not awaiting your response."

**Symptom (observed):** requester sees the "Awaiting Requester Response" banner but **no
response form**, and cannot reply. **Root fix options:** (a) make `can_respond`/`line_respond`
recognize an admin-stage kickback (key on `line.status`/`needs_requester_action` and route
the response to whichever review is in a NEEDS_* state), or (b) decide that admins should
kick back to the *reviewer group*, not the requester, and remove/redefine the admin
Need-Info button. This is a product decision — **do not guess**.

### C2 — 🟠 Systemic root cause: `line.status` overloads two stages
The admin-bypass bug (Task 12), the reset-visibility bug (Task 15), and C1 above are all the
same shape: code read `line.status` as if it told you *which stage did what*. It can't.
Every display/gate that needs stage-specific truth should read `ag_review`/`admin_review`.
Consider a small helper (e.g. `line_stage_state(line) -> {ag, admin}`) or explicit derived
properties, so future code stops reaching for `line.status`.

### C3 — 🟠 Checkout asymmetry: admin decisions bypass the lock
AG decisions require the acting user to hold the checkout (`apply_review_decision:559`).
Admin decisions require **only** `require_budget_admin` (`admin_final/reviews.py:110`) — **no
checkout**. So two admins (or an admin + a reviewer) can act on the same line concurrently
with no lock on the admin side. Decide whether admin-final decisions should also respect
checkout, or whether that's intentional.

### C4 — 🟡 Reset semantics are split and one path is dead
- The UI "Reset for Re-review" button → `reset_line_for_rereview` (`helpers.py:687`): resets
  `admin_review` + line, **not** `ag_review`.
- `apply_admin_final_decision`'s `REVIEW_ACTION_RESET` branch (`helpers.py:408-412`) does a
  similar reset but appears **unreachable** — no route calls `_handle_admin_decision` with
  RESET. Likely dead code; confirm and remove, or converge the two.
- There is also an AG-stage `approvals.line_reset` (RESET, ADMIN-only) that resets the
  **AG** review — but it is **not linked from any template** (Concern C7).

### C5 — 🟡 `needs_requester_action` is set by both stages but serviced by only one
Both AG and admin needs-info set the flag, but only the AG path can clear it (via respond).
An admin kickback leaves the flag permanently stuck from the requester's side (ties to C1).

### C6 — 🟡 Task-12 guard is asymmetric on admin non-terminal states
The AG-action guard blocks the AG when `admin_review` is APPROVED/REJECTED, but **not** when
it's NEEDS_INFO or PENDING. So after an admin Need-Info, the AG could still act if its own
review is PENDING. Probably a corner case, but confirm it's intended.

### C7 — 🟢 Orphan route: `approvals.line_reset` (AG reset) has no UI
It exists and works (ADMIN-only) but nothing links to it. Either wire it (e.g. an admin
"send back to reviewer group" action that also clears the admin decision) or remove it.

### C8 — 🟢 After an admin reset, the default tab is empty
The default "Reviewer Group Review" tab shows no form (the AG already decided; `can_decide`
is false). The admin must switch to "Admin Final Review" to re-decide. Consider defaulting
to the Admin Final tab when there's a pending admin decision on an already-reviewed line.

### C9 — 🟢 Decision notes are stored three times
`review.note` + a public comment + the audit note. Consistent since Task 13 (all public),
but the redundancy means three places to keep in sync. Not urgent; note for future cleanup.

---

## 6. Suggested next steps (not yet actioned)
1. Decide C1 (admin kickback → requester vs reviewer group). This is the live bug.
2. Decide C3 (checkout on admin decisions).
3. Clean up C4 dead reset path + C7 orphan route once C1/C3 are settled.
4. Consider C2 (a stage-state helper) to stop the recurring `line.status` misreads.
