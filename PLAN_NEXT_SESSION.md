# Next Session Plan

## Bugs to Fix First

### 1. Expense Account Editing Blocked
**Problem:** Admin says "Modifications Blocked" even for accounts only referenced by DRAFT budgets.

**Current Logic (in `expense_accounts.py`):**
```python
def _can_modify_expense_account(account_id: int) -> tuple[bool, str]:
    count = (
        db.session.query(BudgetLineDetail)
        .join(WorkLine)
        .join(WorkItem)
        .filter(BudgetLineDetail.expense_account_id == account_id)
        .filter(WorkItem.status != WORK_ITEM_STATUS_DRAFT)  # Only blocks non-draft
        .count()
    )
```

**Investigation Needed:**
- Check if the query is working correctly
- Verify `WORK_ITEM_STATUS_DRAFT` constant matches actual status values
- May need to allow editing for DRAFT-only references

**Proposed Fix Options:**
- A) Allow editing if only DRAFT items reference it
- B) Add a "force edit" option for admins
- C) Show which items are blocking (for debugging)


### 2. Event Overrides Creation Error
**Problem:** Errors when trying to create event overrides.

**Investigation Needed:**
- Capture the actual error message
- Check if the `ExpenseAccountEventOverride` model has all required fields
- Verify the form template has correct field names
- Check foreign key constraints (event_cycle_id, expense_account_id)

**Likely Issues:**
- Missing required fields in form
- Foreign key constraint violation
- Template field name mismatch


---

## Chunk C: Supplementary + Checkout System

### Overview
Build supplementary request flow and implement a checkout/lock system for reviewers.

### URL Structure

| URL | Method | Purpose |
|-----|--------|---------|
| `/<event>/<dept>/budget/supplementary/new` | GET | Create SUPPLEMENTARY form |
| `/<event>/<dept>/budget/supplementary` | POST | Submit SUPPLEMENTARY creation |
| `/<event>/<dept>/budget/item/<public_id>/checkout` | POST | Check out for editing |
| `/<event>/<dept>/budget/item/<public_id>/checkin` | POST | Check in (release lock) |
| `/admin/locks` | GET | View all active locks |
| `/admin/locks/<lock_id>/release` | POST | Force release a lock |

### Database Changes Needed

Check if these fields exist on `WorkItem`:
```python
checked_out_by_user_id: str | None
checked_out_at: datetime | None
checkout_expires_at: datetime | None
```

If not, add migration.

### Implementation Details

#### 1. Create SUPPLEMENTARY Flow

**Gating Rule:** Can only create SUPPLEMENTARY if:
- PRIMARY exists for this portfolio
- PRIMARY status == FINALIZED

**Route: `supplementary_new` (GET)**
- Check PRIMARY is FINALIZED
- Show confirmation page similar to `primary_new.html`

**Route: `supplementary_create` (POST)**
- Validate PRIMARY is FINALIZED
- Create WorkItem with `request_kind=SUPPLEMENTARY`
- Redirect to edit page

**Template:** `supplementary_new.html` (copy/modify from `primary_new.html`)


#### 2. Checkout System

**Checkout Rules:**
- Only SUBMITTED items can be checked out
- Only reviewers/admins can check out
- Checkout has a timeout (configurable, e.g., 30 minutes)
- Expired checkouts auto-release

**Role-Based Timeouts:**
```python
CHECKOUT_TIMEOUTS = {
    "approver": timedelta(minutes=30),
    "super_admin": timedelta(hours=2),
}
```

**Functions to Add in `helpers.py`:**
```python
def checkout_work_item(item: WorkItem, user_ctx: UserContext) -> bool
def checkin_work_item(item: WorkItem, user_ctx: UserContext) -> bool
def is_checked_out(item: WorkItem) -> bool
def get_checkout_info(item: WorkItem) -> dict | None
def release_expired_checkouts() -> int
def can_checkout(item: WorkItem, user_ctx: UserContext) -> bool
```

**Lock Visibility for Requesters:**
- Requesters see "Under Review" status
- Cannot see who has it checked out
- Cannot edit while checked out

**Super-Admin Lock Release:**
- Admin page to view all active locks
- Force release button


#### 3. NEEDS_INFO Auto-Transition

**Trigger:** When a reviewer adds a comment requesting information

**Implementation Options:**
- A) Explicit button "Request Info" that adds comment + transitions status
- B) Detect keywords in comment (e.g., "please clarify", "need more info")
- C) Checkbox when adding comment: "This requests information from requester"

**Recommended:** Option C - explicit checkbox is clearest

**Status Flow:**
```
SUBMITTED -> NEEDS_INFO (reviewer requests info)
NEEDS_INFO -> SUBMITTED (requester responds)
```

**Fields Needed on WorkItem:**
```python
needs_info_requested_at: datetime | None
needs_info_requested_by_user_id: str | None
```


### Files to Create/Modify

**Create:**
- `app/templates/budget/supplementary_new.html`
- `app/templates/admin/locks/list.html`

**Modify:**
- `app/routes/budget/helpers.py` - Add checkout functions
- `app/routes/budget/work_items.py` - Add supplementary and checkout routes
- `app/routes/admin/__init__.py` - Add locks admin routes
- `app/templates/budget/work_item_detail.html` - Show checkout status
- `app/templates/budget/work_item_edit.html` - Show checkout status


### Implementation Sequence

1. **Fix bugs first**
   - Debug expense account modification blocking
   - Debug event override creation errors

2. **Add SUPPLEMENTARY flow**
   - Add `can_create_supplementary` check
   - Create routes and template
   - Test creation flow

3. **Add checkout fields** (if needed)
   - Create migration
   - Run migration

4. **Implement checkout system**
   - Add helper functions
   - Add checkout/checkin routes
   - Update templates to show lock status

5. **Add admin lock management**
   - Create admin routes
   - Create admin template

6. **Implement NEEDS_INFO transition**
   - Add comment route with checkbox
   - Implement status transition logic


### Testing Checklist

**SUPPLEMENTARY Flow:**
- [ ] Cannot create SUPPLEMENTARY when PRIMARY is DRAFT
- [ ] Cannot create SUPPLEMENTARY when PRIMARY is SUBMITTED
- [ ] Can create SUPPLEMENTARY when PRIMARY is FINALIZED
- [ ] SUPPLEMENTARY appears in portfolio landing

**Checkout System:**
- [ ] Requester cannot check out
- [ ] Approver can check out SUBMITTED item
- [ ] Checked-out item shows lock icon
- [ ] Other reviewers cannot edit checked-out item
- [ ] Checkout expires after timeout
- [ ] Check-in releases lock

**Admin Locks:**
- [ ] Admin can see all active locks
- [ ] Admin can force release any lock

**NEEDS_INFO:**
- [ ] Reviewer can request info via comment
- [ ] Status changes to NEEDS_INFO
- [ ] Requester can respond
- [ ] Status changes back to SUBMITTED
