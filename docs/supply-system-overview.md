# MAGFest Supply Order & Inventory System

**For team review — April 2026**

---

## What Is This?

A system for departments to request supplies, for the warehouse team to fulfill those requests, and for helpdesk to handle walk-up needs during the event. It also tracks non-expendable items (radios, laptops) so we get them back.

Think of it as the supply-side companion to the budget system — same approval philosophy, but instead of "how much money," it's "what stuff do you need."

---

## The Big Picture

There are two phases of supply ordering, plus helpdesk and returns:

```
┌─────────────────────────────────────────────────────────────────────────┐
│                                                                         │
│   WEEKS BEFORE EVENT          LOAD-IN         EVENT          TEARDOWN   │
│   ──────────────────          ───────         ─────          ────────   │
│                                                                         │
│   Departments submit     Warehouse team    Supplementary     Return     │
│   bulk orders            picks, packs,     orders come in    stations   │
│   ("we need 50 radios,   and stages        ("we forgot X")  open at    │
│    200 pens, 10 iPads")  totes for each                     4pm Sun    │
│                          department        Helpdesk handles             │
│   Approval groups                          walk-ups ("I     Must be    │
│   review quantities                        need an adapter  cleared    │
│                                            right now")      by midnight│
│                                                                         │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Workflow 1: Placing a Supply Order

**Who:** Department heads / staff with ordering access  
**When:** Weeks before the event (bulk) or during the event (supplementary)

### The Experience

> **Sarah** is the TechOps department head preparing for SMF 2027. She logs in, navigates to her department, and opens the Supply Orders section.
>
> She clicks **"New Supply Order"** and sees the supply catalog organized by category — Tech Equipment, Office Supplies, Safety, Signage, Event Supplies.
>
> She expands **Tech Equipment** and adds:
> - 50× Two-Way Radios
> - 10× Laptops
> - 25× USB-C Adapters
>
> She expands **Office Supplies** and adds:
> - 200× Pens
> - 50× Clipboards
>
> For the radios, the system flags that **notes are required** — she types "Need channels pre-programmed for TechOps frequencies."
>
> She doesn't see stock levels right now — it's pre-event, so procurement will source what's needed based on approved orders. She just orders what she needs.
>
> She clicks **Submit**. Her order gets a tracking ID: **SMF27-TECHOPS-SUP-1**.
>
> ---
>
> **Three weeks later, during the event**, Sarah realizes they need more adapters. She opens her department's supply page and creates a **supplementary order** (SMF27-TECHOPS-SUP-2). This time, she *can* see stock levels — the warehouse has 12 USB-C adapters available. She requests 8.

---

## Workflow 2: Reviewing & Approving Orders

**Who:** Approval group members, Supply Admin  
**When:** After orders are submitted

### The Experience

> **Mike** is on the Tech Equipment approval group. He gets a notification that TechOps submitted an order with tech items.
>
> He opens the approval queue and sees Sarah's lines:
> - 50× Two-Way Radios
> - 10× Laptops
> - 25× USB-C Adapters
>
> The radios and laptops look right. But 25 adapters seems high for a team of 15. He **adjusts the quantity to 15** and adds a note: "Adjusted to match team size + spares."
>
> He approves all three lines and moves on.
>
> Meanwhile, **Jamie** on the Office Supplies approval group reviews the pens and clipboards. She approves both as-is.
>
> ---
>
> **Later, the Supply Admin** does a final review across all lines. They confirm the quantities, note that the radio programming request will need to be coordinated with the vendor, and **finalize the order**.
>
> Sarah gets a notification: "Your supply order SMF27-TECHOPS-SUP-1 has been finalized."

**Key difference from budget:** Reviewers adjust *quantities*, not dollar amounts. Full rejection is rare — usually it's "you asked for 25, you're getting 15."

---

## Workflow 3: Picking & Packing (Warehouse)

**Who:** Warehouse staff  
**When:** During load-in, when the warehouse moves to the expo hall back area

### The Experience

> **Carlos** is on the warehouse team. It's load-in day. He opens the warehouse pick screen on his tablet and sees a list of orders ready for fulfillment.
>
> He selects the next department to pick: **TechOps (SMF27-TECHOPS-SUP-1)**. The system generates a **pick list** organized by warehouse zone and bin location, so he can walk the warehouse efficiently:
>
> ```
> PICK LIST — TechOps (SUP-1)                    Tote: LP-2027-0042
> ─────────────────────────────────────────────────────────────────
> Zone A, Bin A-12    Two-Way Radios          Pick: 50    □ Done
>                     ⚠ Serial tracked — scan each unit
> Zone A, Bin A-15    Laptops                 Pick: 10    □ Done
>                     ⚠ Serial tracked — scan each unit
> Zone B, Bin B-03    USB-C Adapters          Pick: 15    □ Done
> Zone C, Bin C-22    Pens (box of 50)        Pick: 4     □ Done
> Zone C, Bin C-08    Clipboards              Pick: 50    □ Done
> ```
>
> He grabs a **tote** and slaps on license plate **LP-2027-0042**. He scans the license plate to associate it with this order.
>
> For the radios and laptops, he scans each unit's barcode as he puts it in the tote. The system records exactly which radio (MAG-RAD-0047) and which laptop (SN:ABC123) went to TechOps.
>
> For pens and adapters, he just picks the quantity and checks them off. No barcode scanning needed for expendable items.
>
> When the tote is fully picked, he marks it **Staged** and notes the location: "Staging Area 3." The tote is ready for TechOps to pick up.

### Tote / License Plate Concept
- Each tote gets a **license plate** (a barcode label) that ties it to a specific order
- One order might need multiple totes (50 radios won't fit in one tote)
- Scanning the license plate pulls up everything about that tote: what's in it, who it's for, what's still missing
- During delivery, scanning the license plate confirms handoff

---

## Workflow 4: Order Pickup & Checkout

**Who:** Department representatives picking up their staged orders  
**When:** During load-in / early event

### The Experience

> **Sarah** gets a notification: "Your supply order is staged and ready for pickup at Staging Area 3."
>
> She (or someone from TechOps) goes to the staging area. A warehouse team member scans the **tote license plate LP-2027-0042**. The screen shows:
>
> ```
> ORDER PICKUP — SMF27-TECHOPS-SUP-1
> ─────────────────────────────────────────────
> Tote LP-2027-0042
>
> ✓ 50× Two-Way Radios        (serial tracked)
> ✓ 10× Laptops               (serial tracked)
> ✓ 15× USB-C Adapters
> ✓ 200× Pens
> ✓ 50× Clipboards
>
> ⚠ Non-expendable items checked out to: TECHOPS
>   Radios and laptops are due back Sunday by 4:00 PM.
>
> Picked up by: Sarah Chen, TechOps
>                                    [ Confirm Pickup ]
> ```
>
> Sarah confirms, and the order is marked **Delivered**. The 50 radios and 10 laptops now show as **checked out to TechOps** in the system, with a return deadline of Sunday at 4pm.
>
> The pens and clipboards? Those are expendable — nobody tracks whether they come back.

---

## Workflow 5: Helpdesk Walk-Up (Ad-Hoc Requests)

**Who:** Helpdesk staff, anyone at the event who needs something  
**When:** During the event

### The Experience

> It's Saturday afternoon. **Dave** from the Panels team shows up at the helpdesk: "Hey, I need a VGA adapter and a power strip for the panel room."
>
> **Reese**, the helpdesk staffer, starts a new request:
>
> 1. **Scans Dave's badge** — the system pulls up his name and department (Panels)
> 2. **Searches "VGA"** in the catalog — finds "VGA to HDMI Adapter," sees 8 in stock
> 3. Adds 1× VGA to HDMI Adapter
> 4. Searches "power strip" — finds it, 23 in stock
> 5. Adds 1× Power Strip
>
> Both items are expendable (non-returnable), so Reese just hits **Issue** and hands them over. Done in 30 seconds. Inventory is updated, the request is logged under the Panels department for cost tracking.
>
> ---
>
> **Later**, the Head of Logistics walks up: "I need a laptop for the logistics office."
>
> Reese scans their badge, searches "laptop," and adds 1× Laptop. The system flags this as **high-value** and pops up: "Manager approval required."
>
> Reese calls over **the on-duty manager**, who reviews on the screen and taps **Approve**. Reese scans the barcode on the specific laptop being issued (SN:DEF456). The system:
> - Records the laptop as checked out to the Logistics department
> - Sets the return deadline for Sunday 4pm
> - Marks asset SN:DEF456 as checked out
>
> The whole interaction takes about 90 seconds.

---

## Workflow 6: High-Speed Returns (Teardown)

**Who:** Return station staff, department representatives  
**When:** Sunday 4pm–midnight (the critical window)

### The Experience

> It's **4:15 PM Sunday**. The event just ended. Return stations are set up near the exits. The **Outstanding Items Dashboard** is up on a big screen showing what's still out:
>
> ```
> OUTSTANDING NON-EXPENDABLE ITEMS
> ─────────────────────────────────────────────
> TECHOPS          50 radios, 10 laptops       ██████████ HIGH
> LOGISTICS         2 laptops                  ████ MED
> SECURITY         30 radios                   ████████ HIGH
> PANELS            1 laptop                   ██ LOW
> HOTELS            5 radios                   ███ MED
> ─────────────────────────────────────────────
> Total outstanding: 83 radios, 13 laptops
> ```
>
> **A TechOps volunteer arrives** with a bin of radios. The return station staffer starts scanning:
>
> *beep* — MAG-RAD-0047 ✓ Returned (Good)  
> *beep* — MAG-RAD-0012 ✓ Returned (Good)  
> *beep* — MAG-RAD-0089 ✓ Returned (Good)  
> *beep* — MAG-RAD-0033 ⚠ Returned (Damaged — note: cracked antenna)  
>
> Each scan takes about 2 seconds. The dashboard updates in real time. After all 50 TechOps radios are scanned, the screen shows TechOps radios: 50/50 ✓.
>
> The laptops come back next. Same process — scan, confirm condition, done.
>
> ---
>
> **At 10 PM**, the dashboard shows 6 radios still outstanding — all from Security. The system already sent a reminder notification to the Security department head at 8 PM. A staff member radios them: "We need those last 6 radios returned to the station by the loading dock."
>
> ---
>
> **At midnight**, 2 radios are still missing. They get flagged as **Lost** in the system. After the event, the Supply Admin generates a charge report — those 2 radios get charged to the Security department's budget for replacement.

### Why Speed Matters
- The return UI is designed for **barcode scan → condition tap → next**
- No typing, no forms, no lookups — scan and go
- The dashboard is the command center: what's still out, sorted by value
- Notifications go out automatically as the deadline approaches
- Condition options are just three big buttons: **Good** / **Damaged** / **Lost**

---

## Item Types & What Gets Tracked

Not everything gets the same level of tracking:

| Type | Examples | Ordering | Inventory | Return? | Serial tracked? |
|------|----------|----------|-----------|---------|-----------------|
| **Expendable** | Pens, tape, paper, batteries | Quantity | Count only | No | No |
| **Non-expendable, low-value** | Clipboards, extension cords | Quantity | Count only | Yes, by quantity | No |
| **Non-expendable, high-value** | Radios, laptops, iPads, tools | Quantity | Individual unit barcodes | Yes, by barcode scan | Yes |
| **Rented from vendor** | Specialty radios, AV equipment | Quantity | Vendor barcodes | Yes, by barcode scan | Yes |

---

## Roles

| Role | What they do |
|------|-------------|
| **Department staff** | Browse catalog, place orders, pick up staged totes |
| **Approval group members** | Review and adjust quantities for their category |
| **Supply Admin** | Final review, dispatch, oversee everything |
| **Warehouse staff** | Pick, pack totes, stage for pickup, manage stock |
| **Helpdesk staff** | Handle walk-up requests, scan badges, issue items |
| **Return station staff** | Scan returns, assess condition, track outstanding items |

---

## What's Next

We plan to build this in phases:

1. **Order placement & approval** — Departments can submit supply orders and get them approved (same workflow engine as budget)
2. **Inventory tracking** — Stock levels, receiving, adjustments
3. **Serial/asset tracking** — Barcodes for high-value items (radios, laptops)
4. **Picking & totes** — Warehouse fulfillment with tote license plates
5. **Helpdesk** — Badge-scan walk-up requests
6. **Returns** — High-speed barcode return stations, teardown dashboard
7. **Reporting** — Usage by department, cost allocation, return compliance
