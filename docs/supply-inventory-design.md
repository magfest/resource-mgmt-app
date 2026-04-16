# Supply Order & Inventory Management System - Design Document

**Status:** DRAFT - Speccing  
**Branch:** `Supply-Order-framing`  
**Date:** 2026-04-13  

---

## 1. Problem Statement

MAGFest departments need to request supplies (radios, office materials, signage, safety equipment, etc.) for events. Today this is handled outside the budget system through ad-hoc communication. The supply system needs to support:

1. **Pre-event supply ordering** - Departments submit bulk orders from a catalog, with approval workflow
2. **At-event supply ordering** - Supplementary requests during the event ("we forgot X" / "we need more Y")
3. **Warehouse inventory tracking** - Know what we have, where it is, and what's committed
4. **Fulfillment / picking** - Warehouse staff pick and stage approved orders
5. **Helpdesk walk-up requests** - Badge scan, quick issue, auto-checkout for non-expendables
6. **Return tracking** - Non-expendable items (radios, laptops) tracked per-department, returned by event end

This is significantly more complex than the budget workflow because it extends beyond request-and-approve into physical operations (inventory, fulfillment, distribution, returns).

---

## 2. Existing Infrastructure

### What's Already Built
| Component | Status | Notes |
|-----------|--------|-------|
| `SupplyCategory` model | Complete | Categories with approval group routing |
| `SupplyItem` model | Complete | Catalog items with inventory fields (`qty_on_hand`, `location_zone`, `bin_location`, `is_expendable`) |
| `SupplyOrderLineDetail` model | Complete | Links to WorkLine, has `quantity_requested`/`quantity_approved` |
| WorkType config (SUPPLY) | Seeded | URL slug `supply`, prefix `SUP`, category routing, `supports_supplementary = False` (needs change) |
| Admin UI for categories/items | Complete | CRUD for catalog management |
| `CategoryRoutingStrategy` | Complete | Routes lines to approval groups via category |
| Generic line detail helpers | Complete | `get_line_amount_cents()`, `get_line_description()` work for supply |
| Line display/form partials | Partial | `_line_display.html` and `_line_form_fields.html` exist but aren't wired up |

### What's Missing
- All user-facing supply order workflows (create, edit, submit, review)
- Inventory management system (stock levels, transactions, auditing)
- Fulfillment/picking workflow
- Helpdesk / ad-hoc request system
- Return tracking for non-expendable items
- Serial/lot tracking for high-value items
- Warehouse operations UI

---

## 3. Two-Phase Ordering Model

A critical design principle: supply ordering operates in **two distinct phases** tied to the event lifecycle. This affects stock visibility, approval urgency, and fulfillment.

```
 PRE-EVENT                    LOAD-IN              AT-EVENT                  TEARDOWN
 ────────────────────────────  ──────────────────  ────────────────────────  ──────────────
 Phase 1: Bulk Orders         Warehouse moves     Phase 2: Event Orders     Returns
 Dept submits PRIMARY         Stock → Expo hall    SUPPLEMENTARY requests   Non-expendables
 orders from catalog          back area            Helpdesk walk-ups        due back by
 No stock levels shown        Pick & stage         Stock levels visible     midnight
 (ordering to match)          bulk orders          (we have what we have)

 Inventory Management (continuous) ──────────────────────────────────────────────────────
 Stock levels, serial tracking, transactions
```

### Phase 1: Pre-Event Bulk Orders
- Departments submit PRIMARY supply orders from the catalog
- **No stock visibility** for requesters - they order what they need, procurement matches
- Standard approval workflow (dispatch → approval group → admin final)
- Fulfillment happens during load-in

### Phase 2: At-Event Orders
- Departments submit SUPPLEMENTARY supply orders ("we need more" / "we forgot")
- **Stock levels visible** - at this point, what's in the warehouse is what's available
- Potentially faster approval (manager on-site with spending authority)
- Helpdesk walk-ups are the fastest path (badge scan → issue → done)

**Config change needed:** Set `supports_supplementary = True` for the SUPPLY work type. Supplementary orders are the primary mechanism for at-event ordering through the formal workflow.

---

## 4. Domain A: Supply Order Requests

### Overview
Departments browse a supply catalog, add items to a request, submit for approval, and warehouse staff fulfill approved orders. A single order can span multiple categories (laptops + paper towels + pens in one order), with lines routing to different approval groups.

### User Roles
| Role | Actions |
|------|---------|
| **Requester** (dept member) | Browse catalog, create order, add lines, submit |
| **Approval Group Reviewer** | Review requested quantities, approve/reject/adjust |
| **Supply Admin** (worktype admin) | Dispatch, final review, manage fulfillment |
| **Warehouse Staff** | Pick, pack, stage fulfilled orders |

### Lifecycle

```
                    REQUESTER                    ADMIN              APPROVAL         ADMIN          WAREHOUSE
                    ─────────                    ─────              ────────         ─────          ─────────
                    Create order (DRAFT)
                    Browse catalog
                    Add lines (item + qty)
                    Submit ─────────────────►  Dispatch ──────────► Review
                                               (assign groups)     Approve qty
                                                                   Adjust qty (up or down)
                                                                   Reject
                                                                   Needs info ──► Respond
                                                                   │
                                                                   ▼
                                                                Final review ──► Pick & stage
                                                                FINALIZED        (FULFILLMENT)
                                                                                 Mark ready
                                                                                 ▼
                                                                                 FULFILLED
```

### Key Differences from Budget Workflow

| Aspect | Budget | Supply |
|--------|--------|--------|
| Line content | Free-text + expense account | Item from catalog |
| Approval unit | Dollar amount | Quantity (can be adjusted up or down) |
| Post-approval | Done (money allocated) | Fulfillment required |
| Supplementary requests | Yes | Yes - key mechanism for at-event orders |
| Stock visibility | N/A | Phase-dependent (none pre-event, visible at-event) |
| Multi-category per order | N/A (expense accounts) | Yes - lines route to different approval groups |

### Order Statuses (extends WorkItem statuses)
The existing WorkItem statuses handle DRAFT through FINALIZED. Supply orders need additional post-approval states:

```
DRAFT → AWAITING_DISPATCH → SUBMITTED → FINALIZED → AWAITING_FULFILLMENT → FULFILLED
                                                      ▲                      │
                                                      │ (partial fill)       │
                                                      └──────────────────────┘
```

Fulfillment states live on individual WorkLines (via `FulfillmentRecord`), not on the WorkItem. This allows partial fulfillment - some items in stock, others backordered.

### Catalog Browsing UX
Requesters need to browse the supply catalog when adding lines.

**Proposed UX:**
- Items grouped by category (accordion or tabs)
- Search/filter across items
- Show: item name, unit, unit cost (if tracked)
- Flag `is_limited` items with a warning
- Flag `is_popular` items with a highlight
- `notes_required` items show a mandatory notes field
- Quantity picker with unit label

### Stock Visibility (Phase-Dependent)

| Phase | What requesters see |
|-------|-------------------|
| **Pre-event** (PRIMARY orders) | No stock info - order what you need, procurement will source it |
| **At-event** (SUPPLEMENTARY orders) | Stock levels shown - this is what's actually available |

This distinction could be driven by a flag on the `EventCycle` (e.g., `supply_phase = "PRE_EVENT" | "AT_EVENT"`) or by whether the event's start date has passed. The phase switch would toggle stock visibility in the catalog UI and potentially adjust approval routing for faster at-event turnaround.

### Quantity Adjustment
Unlike budget (where rejection is common), supply orders rarely get fully cancelled. The typical action is **quantity adjustment** - a reviewer approves but changes the quantity up or down. The `quantity_approved` field on `SupplyOrderLineDetail` already supports this. The approval UI should make quantity adjustment the primary action, with full rejection as a secondary option.

---

## 5. Domain B: Inventory Management

### Overview
Track what's in the warehouse: quantities, locations, and every transaction that changes stock levels. This is the backbone that all other domains depend on.

### Current Model Gaps

The `SupplyItem` model has basic fields (`qty_on_hand`, `location_zone`, `bin_location`) but lacks:

1. **Transaction history** - No record of why stock changed
2. **Committed vs. available** - No way to track items approved but not yet picked
3. **Par levels** - No reorder point or target stock levels
4. **Serial/lot tracking** - Non-expendable items (radios, laptops) need individual unit tracking

### Proposed New Models

#### `InventoryTransaction`
Records every stock change with an audit trail.

```
InventoryTransaction
├── id
├── item_id (FK → supply_items)
├── asset_id (FK → asset_units, nullable - for serialized items)
├── event_cycle_id (FK → event_cycles, nullable)
├── transaction_type (ENUM: see below)
├── quantity_change (+/-)
├── quantity_after (running balance)
├── reference_type (nullable: "work_line", "helpdesk_request", "return", "adjustment")
├── reference_id (nullable: FK to source record)
├── notes
├── created_at
└── created_by_user_id
```

**Transaction types:**
| Type | Direction | Trigger |
|------|-----------|---------|
| `RECEIVED` | + | New stock arrives from vendor |
| `COMMITTED` | (no qty change, tracks commitment) | Order approved |
| `PICKED` | - | Warehouse pulls item for order |
| `RETURNED` | + | Non-expendable item checked back in |
| `ADJUSTED` | +/- | Manual inventory correction |
| `HELPDESK_ISSUED` | - | Ad-hoc helpdesk distribution |
| `WRITTEN_OFF` | - | Damaged, lost, or consumed |

#### Stock Level Tracking
**Approach: Denormalized balance + transactions**
- Keep `qty_on_hand` on `SupplyItem` as a running balance
- Update it whenever a transaction is created
- Transactions serve as audit trail and reconciliation safety net
- Fast reads for catalog browsing (important at-event when stock visibility is on)
- Add a periodic reconciliation check that compares the denormalized balance against the transaction sum

#### Additional Item Fields Needed

```python
# On SupplyItem (new fields)
qty_committed = Column(Integer, default=0)      # Approved but not yet picked
par_level = Column(Integer, nullable=True)       # Reorder point (mainly high-value items)
requires_serial_tracking = Column(Boolean, default=False)  # Enables individual unit tracking

# Computed property
@property
def qty_available(self):
    return (self.qty_on_hand or 0) - (self.qty_committed or 0)
```

### Single Location Model
The warehouse effectively has one "current location" that changes over the event lifecycle:
1. **Pre-event:** Real warehouse
2. **Load-in:** Expo hall back area (pick & stage here)
3. **At-event:** Helpdesk storage

`location_zone` and `bin_location` on `SupplyItem` are sufficient. No need for multi-location tracking - the whole warehouse moves as a unit.

### Reconciliation
For **high-value items** (laptops, radios, expensive equipment), periodic reconciliation is important. For expendables (pens, paper towels), exact counts don't matter at event end.

The item's `is_expendable` flag and a potential `is_high_value` flag can drive which items require reconciliation:
- High-value non-expendables: Full serial tracking, mandatory reconciliation
- Low-value expendables: Quantity tracking only, no end-of-event reconciliation needed

---

## 6. Serial / Asset Tracking

### Overview
High-value non-expendable items need individual unit tracking. Radio #47, Laptop SN:ABC123, rented radio with vendor barcode RNT-0042 - each needs its own identity.

### Design Principle
Not every supply item needs serial tracking. Pens don't have serial numbers. The system must handle both:
- **Quantity-tracked items** (most items): Track counts, not individual units
- **Serial-tracked items** (radios, laptops, tools): Track individual units with barcodes

`SupplyItem.requires_serial_tracking` flag determines which mode applies.

### Proposed Model

#### `AssetUnit`
Represents a single trackable physical unit of a supply item.

```
AssetUnit
├── id
├── item_id (FK → supply_items)
├── barcode (String, unique - supports multiple barcode formats)
├── barcode_source ("MAGFEST" | "VENDOR" | "MANUFACTURER")
├── serial_number (String, nullable - manufacturer serial if applicable)
├── status ("AVAILABLE" | "CHECKED_OUT" | "RESERVED" | "DAMAGED" | "RETIRED" | "LOST")
├── condition ("NEW" | "GOOD" | "FAIR" | "POOR")
├── vendor_name (String, nullable - for rental units)
├── rental_contract_ref (String, nullable - ties to rental agreement)
├── acquisition_type ("OWNED" | "RENTED")
├── notes (Text, nullable)
├── created_at
├── created_by_user_id
├── updated_at
└── updated_by_user_id
```

### Barcode Flexibility
The `barcode` field is a freeform string to support:
- **MAGFest-generated barcodes:** e.g., `MAG-RAD-0047` (printed labels for MAGFest-owned items)
- **Vendor rental barcodes:** e.g., `RNT-20260412-042` (whatever the rental company uses)
- **Manufacturer serial numbers:** Can double as barcode if unique
- **QR codes:** The content of the QR code is just a string

The system looks up assets by barcode scan without caring about the format.

### Integration with Checkout
When a serial-tracked item is checked out:
1. `AssetUnit.status` → `CHECKED_OUT`
2. `ItemCheckout` record created, linked to the specific `AssetUnit`
3. On return: scan barcode → find `AssetUnit` → find open `ItemCheckout` → process return

### Integration with Inventory
- `SupplyItem.qty_on_hand` for serial-tracked items = count of `AssetUnit` records with status `AVAILABLE`
- Could be denormalized for performance, or computed since serial-tracked items are typically low volume

---

## 7. Domain C: Helpdesk / Ad-Hoc Requests

### Overview
During the event, people come to the helpdesk needing supplies immediately. The core interaction is: **scan badge → confirm department → pick items → issue**. For non-expendable items, this auto-creates a checkout record.

### Key Characteristics
- **Requesters:** Badge holders at the event (scan to identify)
- **Approval:** Helpdesk staff approve on the spot; high-value items may need manager sign-off
- **Fulfillment:** Immediate - item handed over at the desk
- **Tracking:** Every issue recorded for inventory accuracy and cost allocation

### Badge Scan Flow
```
┌──────────────┐     ┌──────────────────┐     ┌─────────────────┐     ┌──────────────┐
│ Scan badge   │────►│ Confirm identity │────►│ Select items    │────►│ Issue items   │
│ (or manual   │     │ + department     │     │ from catalog    │     │ Auto-checkout │
│  lookup)     │     │                  │     │ (search/browse) │     │ if non-expend │
└──────────────┘     └──────────────────┘     └─────────────────┘     └──────────────┘
                                                     │
                                                     ▼ (high-value)
                                              ┌─────────────────┐
                                              │ Manager sign-off│
                                              │ (on device)     │
                                              └─────────────────┘
```

### Proposed Model

#### `HelpdeskRequest`
```
HelpdeskRequest
├── id
├── event_cycle_id (FK → event_cycles)
├── requester_user_id (FK → users, nullable - from badge scan)
├── requester_name (String - display/fallback)
├── requester_department_id (FK → departments, nullable)
├── status (OPEN → FULFILLED / DENIED / CANCELLED)
├── priority (NORMAL, URGENT)
├── requires_manager_approval (Boolean)
├── manager_approved_by_user_id (FK → users, nullable)
├── manager_approved_at (DateTime, nullable)
├── notes
├── fulfilled_by_user_id (FK → users - helpdesk staff)
├── fulfilled_at (DateTime)
├── created_at
└── created_by_user_id (the helpdesk staff member who entered it)
```

#### `HelpdeskRequestLine`
```
HelpdeskRequestLine
├── id
├── helpdesk_request_id (FK → helpdesk_requests)
├── item_id (FK → supply_items, nullable - could be a non-catalog item)
├── asset_id (FK → asset_units, nullable - specific serial-tracked unit)
├── description (String - for non-catalog items or clarifying notes)
├── quantity
├── fulfilled_quantity
└── notes
```

### Why Not Use WorkItem?
The WorkItem/WorkLine system is designed for multi-stage approval with dispatch, routing, and review stages. Helpdesk requests need:
- No dispatch step
- No approval group routing
- Immediate resolution
- Badge-scan-driven identification
- Different UI (quick entry, not form-heavy)

Forcing this into WorkItem would mean skipping most of the workflow steps and adding special cases throughout. A separate lightweight model is cleaner.

### High-Value Item Approval
For items above a configurable value threshold or flagged as requiring sign-off:
1. Helpdesk staff enters the request normally
2. System flags it as `requires_manager_approval = True`
3. On-duty manager reviews on a tablet/screen and approves (digital signature or confirmation)
4. Item issued after approval

This keeps the helpdesk flow fast for 90% of requests while adding a guardrail for expensive items.

### Auto-Checkout for Non-Expendables
When a helpdesk request includes a non-expendable item:
1. `HelpdeskRequestLine` is fulfilled
2. System automatically creates an `ItemCheckout` record
3. For serial-tracked items: specific `AssetUnit` is linked and marked `CHECKED_OUT`
4. For quantity-tracked non-expendables: quantity recorded on checkout

The helpdesk staff member doesn't need to separately "check out" the item - it's part of the fulfillment flow.

---

## 8. Domain D: Return Tracking

### Overview
Non-expendable items (radios, laptops, tools) are loaned out for the event and must be returned. Returns are tracked **per-department** (not per-person). The critical constraint: **event ends Sunday 4pm, venues must be cleared by midnight** - the return process must be fast and friction-free.

### Which Items Need Return Tracking?
`SupplyItem.is_expendable = False` triggers return tracking. Expendables (pens, tape, paper) are consumed and not tracked post-event.

### Proposed Model

#### `ItemCheckout`
```
ItemCheckout
├── id
├── item_id (FK → supply_items)
├── asset_id (FK → asset_units, nullable - for serial-tracked items)
├── event_cycle_id (FK → event_cycles)
├── quantity_out
├── quantity_returned (default 0)
├── checked_out_to_department_id (FK → departments) -- primary tracking unit
├── checked_out_to_user_id (FK → users, nullable - who picked it up, informational)
├── source_type ("supply_order" | "helpdesk" | "manual")
├── source_id (nullable - FK to originating order/request)
├── checked_out_at
├── checked_out_by_user_id (who processed the checkout)
├── expected_return_at (DateTime - event end + buffer)
├── returned_at (DateTime, nullable - null means still out)
├── returned_to_user_id (who processed the return)
├── condition_on_return ("GOOD" | "DAMAGED" | "LOST", nullable)
├── return_notes
├── charge_to_department (Boolean, default False - flagged later for cost allocation)
└── created_at
```

### Teardown Return Flow (The Critical Path)

The 8-hour window from event end (4pm) to venue clear (midnight) drives the UX:

```
EVENT ENDS (4pm Sun)
     │
     ▼
┌─────────────────────────────┐
│ RETURN STATIONS              │  Multiple stations to avoid bottlenecks
│ Scan item barcode            │  (serial-tracked: instant lookup)
│   OR                         │  (quantity-tracked: find by dept + item)
│ Search by department/item    │
│                              │
│ Record condition             │  Quick tap: Good / Damaged / Lost
│ Process return               │  One button per item
└─────────────────────────────┘
     │
     ▼
┌─────────────────────────────┐
│ OUTSTANDING DASHBOARD        │  Real-time view of what's still out
│ Filter by department         │  Sorted by value (laptops first, then radios)
│ Highlight high-value items   │  Radio/message department leads
│ Send reminder notifications  │
└─────────────────────────────┘
     │
     ▼
VENUE CLEAR (midnight)
     │
     ▼
┌─────────────────────────────┐
│ POST-EVENT RECONCILIATION    │  Anything still out = flag for follow-up
│ Mark as LOST if unresolved   │  Department charged later (not real-time)
│ Generate charge report       │  Feeds into budget system for cost allocation
└─────────────────────────────┘
```

### Return Tracking Scope
- **Track returns for:** Non-expendable items only (`is_expendable = False`)
- **Track per:** Department (not individual person)
- **Serial-tracked items:** Scan barcode → instant return processing
- **Quantity-tracked non-expendables:** Match by department + item type
- **Charges:** Flagged for later department charging, not processed during event

### Outstanding Items Dashboard
During teardown, a real-time dashboard showing:
- All items still checked out, grouped by department
- Sorted by value (high-value first)
- Department contact info for follow-up
- Quick actions: send reminder, mark returned, mark lost

---

## 9. Fulfillment Workflow (Warehouse Operations)

### Overview
After supply orders are approved (FINALIZED), warehouse staff need to pick, pack, and stage items. This is the bridge between "approved request" and "items delivered." Most bulk fulfillment happens during load-in when the warehouse moves to the expo hall back area.

### Fulfillment States (per WorkLine)
```
FINALIZED ──► READY_TO_PICK ──► PICKED ──► STAGED ──► DELIVERED
                                   │
                                   ▼
                              BACKORDERED
                            (insufficient stock)
```

### Proposed Model

#### `FulfillmentRecord`
```
FulfillmentRecord
├── id
├── work_line_id (FK → work_lines, unique - one per line)
├── status (READY_TO_PICK | PICKED | STAGED | DELIVERED | BACKORDERED)
├── assigned_to_user_id (warehouse staff assigned to pick)
├── quantity_to_fulfill (from quantity_approved)
├── quantity_fulfilled (actual picked)
├── pick_location_zone (snapshot from item at pick time)
├── pick_bin_location (snapshot from item at pick time)
├── picked_at
├── staged_at
├── staged_location (String - e.g., "Loading Dock B", "Dept Staging Area 3")
├── delivered_at
├── delivery_notes
├── created_at
└── updated_at
```

### Warehouse Staff UX
- **Pick list view** - All items to pick, grouped by zone/bin for efficient warehouse walks
- **Pick confirmation** - Scan or confirm each item picked, record actual quantity
- **For serial-tracked items** - Pick list shows specific asset units to pull; scan each barcode
- **Staging** - Mark items as staged with location (e.g., "Loading Dock B")
- **Delivery confirmation** - Mark as delivered

### Auto-Checkout on Delivery
When a fulfilled line contains non-expendable items and is marked DELIVERED:
1. System auto-creates `ItemCheckout` record(s)
2. Checked out to the ordering department
3. For serial-tracked items: specific `AssetUnit` records marked `CHECKED_OUT`

---

## 10. Admin & Reporting

### Supply Admin Dashboard
- Orders by status (pipeline view)
- Fulfillment progress
- Stock alerts (items below par level - mainly high-value)
- Helpdesk activity summary
- Outstanding checkouts (critical during teardown)

### Reports
- **Usage by department** - What each dept consumed (for cost allocation/charging)
- **Item velocity** - Most/least requested items (helps procurement planning)
- **Return compliance** - Non-expendable return rates by department
- **Event comparison** - Supply usage across events
- **Inventory valuation** - Total value of warehouse stock
- **Lost/damaged report** - Items not returned or returned damaged, with department charges

### Cost Allocation
Supply usage feeds into department cost tracking:
- `unit_cost_cents` on items × quantity fulfilled = cost per line
- Aggregate by department for event cost allocation
- Lost/damaged charges added post-event
- This data is available for reporting but **not** charged in real-time during the event

---

## 11. Phasing / Implementation Order

### Phase 1: Supply Order Request & Approval
**Goal:** Departments can request supplies through the existing workflow engine.

- Wire up supply order creation (catalog browse, add lines)
- Enable `supports_supplementary = True` for SUPPLY work type
- Submit → Dispatch → Review → Finalize (reuse existing WorkItem lifecycle)
- Quantity-based approval (approve/adjust quantities instead of dollar amounts)
- Portfolio landing page for supply orders
- Multi-category orders with per-line approval group routing (already supported by CategoryRoutingStrategy)

**Leverages:** Existing WorkItem/WorkLine infrastructure, CategoryRoutingStrategy, line partials.

### Phase 2: Basic Inventory Management
**Goal:** Track stock levels and transactions.

- `InventoryTransaction` model and migration
- `qty_committed` / `qty_available` on SupplyItem
- Stock level updates on approval and fulfillment
- Admin UI for stock adjustments and receiving
- Phase-dependent stock visibility in catalog (no visibility pre-event, levels shown at-event)

### Phase 3: Serial / Asset Tracking
**Goal:** Track individual high-value units with barcodes.

- `AssetUnit` model and migration
- `requires_serial_tracking` flag on SupplyItem
- Barcode format flexibility (MAGFest-generated, vendor, manufacturer)
- Asset CRUD in admin UI
- Barcode scanning support (browser camera API or USB scanner input)

### Phase 4: Fulfillment Workflow
**Goal:** Warehouse staff can pick and deliver approved orders.

- `FulfillmentRecord` model
- Pick list generation (optimized by warehouse zone/bin)
- Pick → Stage → Deliver workflow
- Serial-tracked item picking (scan specific barcodes)
- Auto-checkout on delivery for non-expendables
- Backorder handling

### Phase 5: Helpdesk & Ad-Hoc
**Goal:** At-event walk-up supply requests with badge scan.

- `HelpdeskRequest` / `HelpdeskRequestLine` models
- Badge scan → identity confirmation → item selection → issue flow
- Auto-checkout for non-expendable items
- Manager sign-off flow for high-value items
- Helpdesk activity log with stock warnings

### Phase 6: Return Tracking
**Goal:** Track and recover non-expendable items during teardown.

- `ItemCheckout` model
- Auto-create checkouts from fulfillment and helpdesk flows
- Return processing UI (optimized for speed during 8-hour teardown window)
- Barcode scan returns for serial-tracked items
- Outstanding items dashboard (real-time during teardown)
- Post-event reconciliation and department charge flagging

### Phase 7: Reporting & Analytics
**Goal:** Operational visibility and cost allocation.

- Dashboard widgets
- Usage reports by department/event
- Return compliance reporting
- Cost allocation reports for department charging
- Event comparison analytics

### Future / Nice-to-Have
- **Vendor/procurement integration** - Purchase orders to replenish stock
- **Barcode label printing** - Generate and print MAGFest barcode labels
- **Mobile-optimized warehouse UI** - Phone/tablet picking interface

---

## 12. Data Model Summary

```
                    EXISTING (modify)                  NEW MODELS
                    ─────────────────                  ──────────

 ┌──────────────┐                        ┌──────────────────────┐
 │ SupplyItem   │◄───────────────────────│ InventoryTransaction │  (audit trail)
 │ + qty_commit │                        └──────────────────────┘
 │ + par_level  │
 │ + requires_  │◄───────────────────────┌──────────────────────┐
 │   serial_trk │                        │ AssetUnit            │  (individual units)
 └──────┬───────┘                        │ barcode, status,     │
        │                                │ condition, vendor    │
        │                                └──────────┬───────────┘
 ┌──────▼───────┐     ┌──────────────┐              │
 │ SupplyOrder  │─────│ WorkLine     │◄─────────────│
 │ LineDetail   │     │ (existing)   │     ┌────────▼───────────┐
 └──────────────┘     └──────┬───────┘     │ FulfillmentRecord  │  (pick/deliver)
                             │             └────────────────────┘
                             │
                      ┌──────▼───────┐
                      │ WorkItem     │     (existing, reused)
                      │ (existing)   │
                      └──────────────┘

 ┌──────────────────┐     ┌─────────────────────┐
 │ HelpdeskRequest  │─────│ HelpdeskRequestLine  │  (ad-hoc walk-ups)
 └──────────────────┘     └─────────────────────┘

 ┌──────────────────┐
 │ ItemCheckout     │──── links to AssetUnit for serial-tracked
 │ per-department   │     (return tracking)
 └──────────────────┘
```

---

## 13. Permissions & Roles

### New Roles Needed

| Role | Scope | How Implemented |
|------|-------|-----------------|
| **Supply Admin** | Per work type | `WORKTYPE_ADMIN` for SUPPLY (already supported) |
| **Warehouse Staff** | Global | New `WAREHOUSE_STAFF` role code in `UserRole` |
| **Helpdesk Staff** | Per event | New `HELPDESK_STAFF` role code in `UserRole` |

Warehouse Manager = `WORKTYPE_ADMIN` for SUPPLY (full admin access to supply system including fulfillment oversight, stock management, and reporting).

### Membership-Based Access
- Existing `DepartmentMembership` with `can_view`/`can_edit` for supply work type controls who can create supply orders
- No changes needed for the request side

### Helpdesk Permissions
- `HELPDESK_STAFF` can create and fulfill helpdesk requests
- Manager sign-off for high-value items requires `WORKTYPE_ADMIN` for SUPPLY or `SUPER_ADMIN`

---

## 14. Integration Points

### With Budget System
- Supply usage (unit_cost × quantity) feeds department cost reports
- Lost/damaged charges flagged post-event for later department billing
- Cost allocation is reporting-only, not real-time transactional

### With Notifications
- Existing notification system handles supply-specific events
- New notification types: order fulfilled, item backordered, return overdue, return reminder (teardown)

### With Audit Trail
- Existing `WorkLineAuditEvent` / `WorkItemAuditEvent` cover the order workflow
- `InventoryTransaction` is its own audit trail for stock changes
- New audit events for helpdesk requests, checkouts, and returns

---

## 15. Resolved Design Decisions

Decisions confirmed during initial speccing (2026-04-13):

| # | Question | Decision | Rationale |
|---|----------|----------|-----------|
| 1 | Supplementary requests? | **Yes** | At-event orders are the primary use case for supplementary |
| 2 | Stock visibility for requesters? | **Phase-dependent** | Pre-event: no (ordering to match). At-event: yes (finite stock) |
| 3 | Multi-category orders? | **Yes** | One order can have laptops + paper + pens, routes to different groups |
| 4 | Rejected order handling? | **Qty adjustment preferred** | Full rejection rare; usually adjust qty up or down |
| 5 | Serial/lot tracking? | **Yes, flexible barcodes** | Must handle MAGFest barcodes, vendor rental barcodes, manufacturer serials |
| 6 | Multi-location inventory? | **Single location** | Warehouse moves as a unit (warehouse → expo → helpdesk storage) |
| 7 | Vendor/procurement? | **Future/nice-to-have** | Not in initial phases |
| 8 | Reconciliation? | **High-value items only** | Laptops must be tracked; pens don't matter |
| 9 | Offline helpdesk? | **No** | Helpdesk is near network core; offline adds too much complexity with AWS hosting |
| 10 | Auto-checkout from helpdesk? | **Yes** | Badge scan → confirm dept → issue → auto-checkout for non-expendables |
| 11 | Helpdesk approval? | **Manager sign-off for high-value** | On-device confirmation; general staff judgment for normal items |
| 12 | Return deadline? | **Event end → midnight** | 8-hour teardown window; return UI must be fast |
| 13 | Non-return consequences? | **Department charged later** | Tracking + post-event charge, not real-time during event |
| 14 | Return tracking granularity? | **Per-department** | Rare to track per-person; use a department as proxy if needed |
