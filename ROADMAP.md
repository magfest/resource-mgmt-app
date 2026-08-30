# Roadmap

What is planned, what is in progress, and what shipped. Items move between
sections as priorities change. This is not a commitment list.

This file is the high-level view. Granular work moves to GitHub issues once it
enters active development; the repository's Issues tab is the open list. To pick
something up, start from **Up Next** or **Nice to Have** and open an issue or
pull request that names the roadmap item.

Last updated: 2026-08-29

---

## Up Next

Items the team is working on or planning for the near term.

### UX polish

- [ ] **Home page approver section**. Decide whether to keep the approval group
  buttons on the home page, move the pending count into the nav Review dropdown,
  or reduce the section to the count alone.
- [ ] **Collapsible reference info**. Make line status definitions and help text
  collapsible so review screens stay short.
- [ ] **Inline form validation**. Show each error next to the field that failed,
  not only as a flash message at the top of the page.
- [ ] **"How It Works" text updates**. Rewrite the step 2 and step 4 wording, the
  locked-request explanation, and the contact links. Deferred since March 2026
  and still waiting on final text from the directors.

### Workflow

- [ ] **Submission deadline policy**. Soft-block submissions after the event
  deadline with an admin override, and warn in the app inside the last 7 days.
  Deferred in March 2026 because the deadline was months away. Submission
  reminders shipped since then, but they run as a manual command per event and
  send one tier only, so enforcement is the part still missing.

---

## Planned Features

Scoped but not started, roughly in priority order.

| Feature | What it adds | Notes |
| --- | --- | --- |
| Interdepartmental spend | A spending-department field on budget lines and a cross-department report, for lines one department funds and another spends | Admin add-lines, its prerequisite, shipped in July 2026 |
| Full income accounts | Income line items that run through the approval pipeline, split expense and revenue displays, and a net budget figure | Replaces the estimate and notes on the Notes tab |
| Historical P&L import | Prior-year profit and loss data next to budget lines, with an admin import page, category mapping, and a comparison report | |
| Work type build-out | Warehouse, fulfillment, and returns for SUPPLY; dispatch and admin final for TECHOPS; a route package and templates for CONTRACT | TECHOPS is the next work type to get attention |

Current state of each work type lives in [Work Types](docs/work-types.md). Do
not infer it from this file.

---

## Nice to Have

Lower priority. None of these block anything.

- [ ] **Site map page**. A role-aware page listing accessible routes by category,
  similar to Ubersystem's `/accounts/sitemap`. Team-requested.
- [ ] **CSS refactoring**. Replace inline styles with classes, incrementally, as
  templates are touched for other reasons.
- [ ] **"Falling through the cracks" report**. Would surface requests that are
  stuck or forgotten. Needs requirements before it can be scoped.
- [ ] **AV packages on fixed costs**. Revisit when the AV work type is built.
- [ ] **Testing volunteers**. A process question, not code. The app has no way to
  recruit or track people who agree to test a release.

---

## Recently Completed

### August 2026

| Shipped | What it does |
| --- | --- |
| Board approval hold and release | A finalized budget stays held until the board approves the event topline. One admin page releases the whole event, stamps each item, and queues the department email. See [Workflow: Board release](docs/workflow.md#board-release) |
| Email outbox | Every email is written to a database queue in the same transaction as the change that owed it, and a scheduled job sends it. Admins can trace a message that did not arrive. See [Email outbox](docs/email-outbox.md) |
| Fixed-cost and hotel accounts in the admin line tools | Admins can add and edit fixed-cost and hotel lines after submission, override a price with a reason, and the override shows as a badge on the line |
| Hotel report corrections | Rejected hotel lines no longer count toward the report summary, and the duplicate queue links came off the work item action row |

### July 2026

| Shipped | What it does |
| --- | --- |
| Admin line tools | Budget admins can add a line to a request that is already in review, and change a line's expense account, without sending the request back to draft |
| Supply ordering, part one | Catalog, cart, ordering, and admin final for the SUPPLY work type. Requesters pick a pickup time instead of entering delivery details, and the catalog marks items already in the cart |
| Budget reports | Expense Account Lines, Warehouse Lines, and Reviewer Group Health Check, all with CSV export |
| Per-group subtotals | The budget detail view breaks totals down by review group, including the approved-amount column |
| Recommended, needs review | An approver can approve a line with comments and leave the binding decision to the budget admin, instead of choosing between approve and reject |
| Review group labels | Budget approval screens show which group each line was routed to |

### May 2026

| Shipped | What it does |
| --- | --- |
| Submission confirmation email | The requester gets an email when a request is submitted |
| Submission reminders | A command emails departments that have not submitted a budget for an event. It is run by hand per event; see [Scheduled Jobs](docs/README.md#scheduled-jobs-heroku-scheduler) |
| Budget extension flag | Admins grant or revoke a deadline extension on a single request. The request shows an extension badge in listings and the audit log records both actions |
| Recall to draft | A requester can pull back a request that is awaiting dispatch and keep editing it |
| Narrow-screen support | Tables, navigation, and forms stay usable on phone-width screens |

### April 2026

| Shipped | What it does |
| --- | --- |
| Shared workflow engine | Portfolios, work items, lines, and reviews moved out of budget-only code. Lifecycle flags let each work type opt into the dispatch and admin final stages separately |
| TECHOPS request entry and line review | Departments can enter TechOps requests and approvers can review the lines. Deployed to production so the team can run tests and give feedback, not for general use |
| Budget line audit logging | Line-level decisions, requester responses, and amount overrides are recorded as audit events |

### March 2026 (Launch)

| Shipped | What it does |
| --- | --- |
| Production launch | Budget system released to the MAGFest staff community |
| Pre-production hardening | Security fixes (XSS, open redirects), data integrity work (NULL-safe unique constraints, finalization guards), and N+1 query elimination on the dashboards |
| Meaningful request IDs | Human-readable IDs such as `SMF27-TECHOPS-BUD-1` replaced random codes. A May 2026 follow-up widened the column so long event and department codes fit |
| Income tracking | Departments record estimated income and notes on the Notes tab, with an Income Report for admins |
| Ubersystem-style navigation | A persistent top nav bar with role-gated dropdowns, replacing the hub-and-spoke pattern |
| Wording consistency pass | Volunteer-facing status labels ("Pending Review", "Changes Requested", "Start Reviewing") and consistent terminology throughout |
| Unsaved changes protection | A warning when switching draft tabs or navigating away with unsaved edits |
| Double-click protection | Form submissions are guarded against duplicate submits |
| Faster budget creation | Starting a first budget request skips the portfolio page |
| User display names | Raw UUIDs resolve to display names through the `\|user_display` filter |
| Dependency security | pip-audit in pre-commit and CI, Dependabot weekly pip version updates, unused `requests` package removed |
| Supplementary request UX | Reason field, sequential numbering, inline editing |
| Badges tab | A dedicated tab for badge counts on the draft screen |
| Admin-editable site content | Tab descriptions and help text are configurable from the admin UI |
| Reporting | Income report, plus request type filters on the department and ledger reports |
| System Admin dashboard | Stats tiles, event progress, and a request status breakdown |
| Budget Admin dashboard | Event progress with per-department submission tracking |
| Required dropdowns | Priority, price certainty, and type of expense are required on budget lines |
| Experimental priority level | An "Experimental / Stretch Goal" option for aspirational budget items |
| Docker support | A Dockerfile and a draft CI workflow for GHCR builds targeting Kubernetes |
