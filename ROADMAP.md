# Roadmap

What's planned, what's in progress, and what's done. This is a living document — items move between sections as priorities shift.

Last updated: 2026-03-24

---

## Up Next

Items the team is actively working on or planning for the near term.

### UX Polish
- [ ] **Home page approver section** — Decide whether to keep the approval group buttons on the home page, move the pending count badge into the nav bar, or simplify to just the count
- [ ] **Collapsible reference info** — Make line status definitions and help text collapsible for cleaner screens
- [ ] **Inline form validation** — Show validation errors next to the field that failed, not just as a flash message at the top of the page
- [ ] **How It Works section updates** — Rewrite step descriptions per director feedback (waiting on final text)

### Workflow
- [ ] **Submission deadline policy** — Soft-block submissions after the event deadline with an admin override. Show a warning banner when the deadline is approaching (within 7 days)

### Request IDs
- [ ] **Friendlier request ID format** — More intuitive IDs (e.g., `BUD-SUP27-AUTO-1`) instead of the current format

---

## Planned Features

Larger features that are scoped but not yet started. Roughly in priority order.

### Admin Add Lines
Allow budget admins to add lines to requests that are already submitted or under review (not just drafts). This is a prerequisite for interdepartmental spend tracking.

### Interdepartmental Spend
Track when one department's budget line is actually spent by a different department. Adds a "spending department" field to budget lines and a new cross-department report. Depends on Admin Add Lines.

### Full Income Accounts
Expand the current income tracking (estimate + notes on the Notes tab) into full income line items that flow through the approval pipeline, with separate display sections for expenses vs. revenue and a net budget calculation.

### Historical P&L Import
Import prior-year profit & loss data so reviewers can see historical context when reviewing budget lines. Includes an admin import page with category mapping and a historical comparison report.

### Contracts & Supply Orders
The work type system is designed to support multiple request types beyond budgets. Contracts and supply orders are stubbed in the data model but don't have UI yet.

---

## Nice to Have

Lower priority items that would improve the app but aren't blocking anything.

- [ ] **Site map page** — A role-aware page listing all accessible routes, organized by category (similar to Uber's `/accounts/sitemap`)
- [ ] **CSS refactoring** — Replace inline styles with CSS classes (ongoing, do incrementally)
- [ ] **"Falling through cracks" report** — Needs requirements definition; would surface requests that are stuck or forgotten
- [ ] **AV packages on fixed costs** — Future enhancement for audio/visual equipment budgeting

---

## Security & Infrastructure

Items from the dependency supply chain review (2026-03-24). The basics are in place (pip-audit, Dependabot, CI scanning). These are the next steps.

### Short-Term
- [ ] **Branch protection on `master`** — Require status checks to pass and PRs before merging, so CI is enforceable not just advisory
- [ ] **Hash-pinned dependencies** — `pip-compile --generate-hashes` to verify package integrity against supply chain tampering
- [ ] **Keep prod/dev requirements in sync** — Always compile both files together to prevent version drift

### Medium-Term
- [ ] **SAST in CI** — Add Bandit or Semgrep to catch common Python/Flask vulnerabilities (SQL injection, hardcoded secrets, open redirects)
- [ ] **Evaluate psycopg2-binary vs source build** — With Docker, source builds are practical and avoid trusting PyPI's pre-built binaries
- [ ] **File upload hardening** — Max file size at the web server level, keep openpyxl updated for XML parsing vulnerabilities
- [ ] **Docker image scanning** — Add Trivy or Grype to CI to catch OS-level vulnerabilities in the container image

### Long-Term
- [ ] **Authlib maintainer risk** — Single-maintainer project handling all authentication. Monitor health, have a migration path ready
- [ ] **K8s secrets management** — External Secrets Operator backed by AWS Secrets Manager, credential rotation
- [ ] **CSP hardening** — Audit for remaining inline handlers, consider `strict-dynamic`, add `Permissions-Policy` headers
- [ ] **Dependency review process** — Lightweight checklist for adding new packages (maintainer count, CVE history, transitive deps)

---

## Recently Completed

### March 2026

- **Income tracking** — Departments can record estimated income and notes on the Notes tab, with an Income Report for admins
- **RAMS-style navigation** — Persistent top nav bar with role-gated dropdown menus, replacing the old hub-and-spoke pattern
- **Wording consistency pass** — Volunteer-friendly status labels ("Pending Review", "Changes Requested", "Start Reviewing"), consistent terminology throughout
- **Unsaved changes protection** — Warning when switching draft tabs or navigating away with unsaved edits
- **Double-click protection** — All form submissions protected against accidental duplicate submits
- **Faster budget creation flow** — Skip the portfolio page when starting a first budget request
- **User display names** — Resolved raw UUIDs to display names everywhere via `|user_display` template filter
- **Dependency security** — pip-audit in pre-commit and CI, Dependabot enabled, unused `requests` package removed
- **Supplementary request UX** — Reason field, sequential numbering, inline editing
- **Badges tab** — Dedicated tab for badge counts on draft screen
- **Admin-editable site content** — Tab descriptions and help text configurable from admin UI
- **Reporting** — Income report, request type filters on department and ledger reports
- **System Admin dashboard** — Stats tiles, event progress, request status breakdown
- **Budget Admin dashboard** — Event progress section with department submission tracking
- **Editable tab descriptions** — Admin UI for customizing tab help text and titles
- **Required dropdowns** — Priority, price certainty, and type of expense fields now required on budget lines
- **Experimental priority level** — "Experimental / Stretch Goal" option for aspirational budget items
- **Docker support** — Dockerfile and CI workflow (draft) for GHCR builds targeting K8s

---

## How This Relates to GitHub Issues

This roadmap is the high-level view. As items move into active development, they may be tracked as GitHub Issues for more granular progress. If you're looking to contribute, the **Up Next** and **Nice to Have** sections are good starting points — open an issue or PR and reference the roadmap item.
