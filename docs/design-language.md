# MAGFest Budget App - Design Language Guide

This document records the UI patterns new templates follow. Written 2026-02-26
and checked against the templates in August 2026; patterns marked `UNVERIFIED`
below could not be settled either way and are kept rather than deleted.

There is no external stylesheet. `app/static/` holds images and no `.css` file,
so every rule sits in a `<style>` block inside a template. The shared block is
in `app/templates/layout/base.html`; eight other templates carry their own local
blocks, among them the three `quick_review.html` pages, `budget/work_item_edit.html`,
and `admin/security_logs/list.html`. Check the page's own block before assuming
a rule is in `base.html`. A class this document names that neither block
defines is inline-styled at each call site.

---

## Table of Contents

1. [Button Placement](#button-placement)
2. [Section & Card Headers](#section--card-headers)
3. [Pills & Badges](#pills--badges)
4. [Navigation](#navigation)
5. [Forms](#forms)
6. [Callouts & Alerts](#callouts--alerts)
7. [Text Hierarchy](#text-hierarchy)
8. [Color Tokens](#color-tokens)
9. [Spacing](#spacing)

---

## Button Placement

UNVERIFIED as of August 2026. These are conventions, not enforced anywhere, and
no mechanical check settles how closely the templates follow them. Kept because
they record the intent.

### Principle
**Top-right header area is for management/admin actions. Inline buttons are for editing specific content.**

### Patterns

#### Page Header Actions
Actions that manage the entity or apply to the whole page go in the top-right:
```html
<div style="display: flex; justify-content: space-between; align-items: flex-start;">
  <div>
    <h1>Page Title</h1>
    <div class="muted">Subtitle or metadata</div>
  </div>
  <div style="display: flex; gap: 8px; align-items: center;">
    <!-- Role badges first -->
    <span class="pill pill-info">Department Head</span>
    <!-- Management actions -->
    <a class="btn" href="...">Manage Members</a>
  </div>
</div>
```

#### Section Header Actions
Actions that add items to a section go in the section header:
```html
<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
  <h3 style="margin: 0;">Section Title</h3>
  <a class="btn btn-primary" href="...">+ Add Item</a>
</div>
```

#### Inline Edit Actions
Edit buttons for specific content sections go inline with that section:
```html
<section class="card">
  <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px;">
    <h4 style="margin: 0;">Department Info</h4>
    <a class="btn" href="...">Edit</a>
  </div>
  <!-- Content here -->
</section>
```

#### Form Button Order
Primary action first, cancel second, destructive/admin actions pushed right:
```html
<div class="btn-row" style="margin-top: 20px;">
  <button class="btn btn-primary" type="submit">Save Changes</button>
  <a class="btn" href="...">Cancel</a>
  <!-- Destructive actions pushed right -->
  <form method="post" action="..." style="margin-left: auto;">
    <button class="btn btn-danger" type="submit">Archive</button>
  </form>
</div>
```

---

## Section & Card Headers

### Standard Section Header
```html
<h3 style="margin: 0 0 12px 0;">Section Title</h3>
<div class="muted" style="margin-bottom: 16px;">
  Optional description of what this section contains.
</div>
```

### Section Header with Action
```html
<div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
  <h3 style="margin: 0;">Section Title</h3>
  <a class="btn btn-primary" href="...">+ Add Item</a>
</div>
```

### Card with Header
```html
<div class="card">
  <h4 style="margin: 0 0 12px 0;">Card Title</h4>
  <!-- Card content -->
</div>
```

### Card with Header and Action
```html
<div class="card">
  <div style="display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 12px;">
    <h4 style="margin: 0;">Card Title</h4>
    <a class="btn" href="...">Edit</a>
  </div>
  <!-- Card content -->
</div>
```

---

## Pills & Badges

### Base Classes
Use these semantic class names; `base.html` defines all of them.

| Class | Use Case | Colors |
|-------|----------|--------|
| `pill` | Default/neutral | Gray background |
| `pill-draft` | Draft status | Light gray |
| `pill-submitted` | Submitted/pending | Light blue |
| `pill-approved` | Approved, subtle; also FINALIZED | Light green |
| `pill-success` | Approved, vivid; line-level | Green |
| `pill-needs` | Needs attention | Light yellow/orange |
| `pill-warning` | Needs info, needs adjustment, paused, awaiting dispatch, under review, approved needs review, pending board approval | Yellow, dark text |
| `pill-rejected` | Rejected/error | Light red |
| `pill-info` | Informational badge | Blue |
| `pill-dh` | Department head badge | Amber |
| `pill-divhead` | Division head badge | Purple |
| `pill-admin` | Admin badge | Blue |
| `pill-sm` | Modifier, not a color: shrinks any pill above | - |
| `a.pill-link` | Modifier, not a color: makes any pill above clickable | Hover lift, no underline |

### Usage Examples
```html
<!-- Status pills -->
<span class="pill pill-draft">Draft</span>
<span class="pill pill-submitted">Under Review</span>
<span class="pill pill-approved">Approved</span>
<span class="pill pill-needs">Needs Info</span>
<span class="pill pill-rejected">Rejected</span>

<!-- Role badges -->
<span class="pill pill-info">Department Head</span>
<span class="pill" style="background: #f3e8ff; color: #7c3aed;">Division Head</span>

<!-- Permission indicators -->
<span class="pill" style="background: #e9f7ef; font-size: 0.7rem;">Can Edit</span>
<span class="pill" style="background: #dbeafe; font-size: 0.7rem;">View Only</span>
```

### Status pills go through the macro

`app/templates/macros/status_pill.html` holds the status-to-class mapping for
the whole app. Do not write an `{% if status == ... %}` chain in a template;
one edit to the macro should change every render.

```html
{% from "macros/status_pill.html" import render_status_pill %}
{{ render_status_pill(work_item.status) }}
{{ render_status_pill(line.status, label=friendly_status(line.status)) }}
```

An unknown status falls through to `pill-draft`, so a new work type can add a
status without breaking existing pages. Imported macros that call
`friendly_status()` need `with context` on the import.

---

## Navigation

### Top Navigation Bar
`app/templates/components/_top_nav.html` renders a persistent bar on every page
for a signed-in user. It replaced the older hub-and-spoke admin pages in March
2026. Menus appear by role: Review for approvers, Reports and Budget Admin for
budget admins, per-work-type admin menus, and Admin for super admins. Add a new
destination to that file rather than building a page-local menu.

### Back Links
Back links survive alongside the top bar and carry page-to-page context the bar
does not. Use at the top of pages, with left arrow:
```html
<div class="muted" style="margin-bottom: 12px;">
  <a href="{{ url_for('...') }}">&larr; Back to [Context]</a>
</div>
```

### Bottom Navigation
Use `.btn-row` for page-level navigation at bottom:
```html
<div class="btn-row" style="margin-top: 24px;">
  <a class="btn" href="{{ url_for('home.index') }}">Back to Home</a>
</div>
```

### Tabs
There is no shared tab component, and the `.tab` / `.tab-content` markup this
document once prescribed appears in no template. Four pages implement tabs
inline with `tab-btn` buttons and `tab-panel` divs, each styled and switched by
a nonced script in its own file: `home.html`, `budget/work_item_detail.html`,
`budget/work_item_edit.html`, and `admin_final/email_debug.html`. Four copies of
the same pattern is the argument for promoting it to a component.

```html
<div class="tab-row" style="display: flex; flex-wrap: wrap; margin-bottom: -1px;">
  <button type="button" class="tab-btn active" data-tab="queue" style="...">Queue</button>
  <button type="button" class="tab-btn" data-tab="log" style="...">Log</button>
</div>

<div class="tab-panel active" id="panel-queue">...</div>
<div class="tab-panel" id="panel-log" style="display: none;">...</div>
```

Copy that page if you need tabs, or promote it to a shared component first.

---

## Forms

### Field Structure
`base.html` defines `.form-label` (block, 600 weight, 6px bottom margin) and the
`.mt-4` and `.mb-16` spacing utilities. Prefer them over repeating the inline
style.

```html
<div class="mb-16">
  <label for="field_id" class="form-label">
    Field Label <span style="color: red;">*</span>
  </label>
  <input type="text" id="field_id" name="field_name" required style="width: 100%;">
  <div class="muted small mt-4">
    Helper text explaining the field.
  </div>
</div>
```

Older templates carry `style="font-size: 13px; font-weight: 600; display:
block; margin-bottom: 4px;"` on the label instead. Both render acceptably; new
work uses the class.

### Two-Column Grid
```html
<div style="display: grid; grid-template-columns: 1fr 1fr; gap: 16px;">
  <div><!-- Field 1 --></div>
  <div><!-- Field 2 --></div>
</div>
```

### Full-Width Field in Grid
```html
<div style="grid-column: 1 / -1;">
  <!-- Full width field -->
</div>
```

### Textarea Sizing
- **Description fields**: `rows="3"`
- **Notes/comments**: `rows="3"`
- **Short notes**: `rows="2"`

### Button Row
```html
<div class="btn-row" style="margin-top: 20px;">
  <button class="btn btn-primary" type="submit">Save Changes</button>
  <a class="btn" href="...">Cancel</a>
</div>
```

### Checkbox with Description
```html
<label style="display: flex; gap: 8px; align-items: flex-start; font-weight: normal;">
  <input type="checkbox" name="field" value="1" style="margin-top: 3px;">
  <div>
    <strong>Checkbox Label</strong>
    <div class="muted small">Description of what this checkbox does.</div>
  </div>
</label>
```

---

## Callouts & Alerts

### Classes
| Class | Use Case | Appearance |
|-------|----------|------------|
| `callout` | Base container | Default styling |
| `callout-info` | Information | Blue left border |
| `callout-success` | Success/approval | Green left border |
| `callout-warning` | Warning/attention | Yellow left border |
| `callout-danger` | Error/rejection | Red left border |
| `callout-action` | Action required | Yellow with emphasis |

### Structure
```html
<div class="callout callout-warning" style="margin-bottom: 16px;">
  <strong>Action Required</strong>
  <div class="muted" style="margin-top: 4px;">
    Description of what the user needs to do.
  </div>
  <!-- Optional action buttons -->
  <div style="margin-top: 12px;">
    <a class="btn btn-primary" href="...">Take Action</a>
  </div>
</div>
```

`base.html` now defines all five callout classes. The inline-style fallbacks
this document once listed are no longer needed; use the class.

---

## Text Hierarchy

### Headings
- `<h1>`: Page titles
- `<h2>`: Major sections
- `<h3>`: Subsections (margin: 0 0 12px 0)
- `<h4>`: Card titles (margin: 0 0 12px 0)

### Muted Text
- `.muted`: Secondary text, metadata, descriptions
- `.muted.small`: Helper text below inputs, fine print
- Always use `margin-top: 4px` for helper text below inputs
- Use `margin-top: 8px` for section descriptions

### Emphasis in Muted Text
```html
<div class="muted">
  <strong>Important term</strong> followed by explanation.
</div>
```

---

## Color Tokens

The `<style>` block in `app/templates/layout/base.html` holds every hex value.
Read it there rather than from a copy here; the copy that used to live in this
section had drifted from the stylesheet by August 2026. Apply the class and do
not hardcode a hex in a template.

Two traps worth knowing. `pill-needs` (#fff4e5, orange) and `pill-warning`
(#fef3c7, yellow) are different classes with similar intent, and the status
macro picks `pill-warning`. `pill-admin` and `pill-dh` are the same amber, so an
admin badge and a department head badge look alike.

### Action Colors
| Type | Class | Background |
|------|-------|------------|
| Primary | `btn-primary` | Blue |
| Secondary | `btn` | Gray/outline |
| Danger | `btn-danger` | Red |

---

## Spacing

`base.html` defines two spacing utilities, `.mt-4` and `.mb-16`. The rest of the
scale below is UNVERIFIED as of August 2026: it is applied inline, and no check
enforces it.

### Standard Values
- **4px**: Tight spacing (between label and input, pill margins)
- **8px**: Small gaps (between buttons, between inline elements)
- **12px**: Standard margin (after headings, between form sections)
- **16px**: Section padding (card padding, major spacing)
- **20px**: Form button row margin-top
- **24px**: Page section margins

### Common Patterns
```css
/* Heading to content */
margin: 0 0 12px 0;

/* Form field spacing */
margin-bottom: 16px;

/* Button row */
margin-top: 20px;

/* Section spacing */
margin-top: 24px;
margin-bottom: 24px;

/* Helper text */
margin-top: 4px;

/* Gap between inline elements */
gap: 8px;
```

---

## Component Checklist

When creating new templates, verify:

- [ ] Page title uses `<h1>`
- [ ] Back link at top with `&larr;`
- [ ] Section headers use `<h3>` with proper margins
- [ ] Action buttons placed according to placement rules
- [ ] Status pills go through `render_status_pill`; other pills use semantic class names
- [ ] Callouts use appropriate color classes
- [ ] Form fields have labels using `.form-label`
- [ ] Form fields have `.muted.small` helper text where needed
- [ ] Button rows follow primary/secondary/destructive order
- [ ] `.muted` used consistently for secondary text
- [ ] Spacing follows standard values

---

## Open Items

Checked August 2026. The wider backlog is [ROADMAP.md](../ROADMAP.md); the
items below are UI-only.

| Item | State |
| --- | --- |
| CSS classes for all pill variants | Done; `base.html` defines thirteen variants plus the base `.pill` they modify |
| CSS classes for callout color variants | Done; `base.html` defines five |
| Remove inline colors in favor of classes | Partly done. Role and permission badges still carry inline hexes |
| Reusable form field macros | Not done. `macros/` holds status, comments, checkout, audit, and badge macros, no form field macro |
| Tab component for reuse | Not done. Four inline implementations of the same pattern |
| Standardize button placement across forms | UNVERIFIED. No mechanical check settles it |
| Standardize textarea sizing | UNVERIFIED. `rows="3"` appears in 19 templates and `rows="2"` in 7, with no rule enforcing either |
| Breadcrumb component for deep pages | Not done. The top nav bar covers part of the need |
