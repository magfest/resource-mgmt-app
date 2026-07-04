# Backlog — open items

**Created:** 2026-07-03, extracted from `docs/archive/plan-outstanding-march2026.md`
(the March doc is archived; completed items live there with their details).
Not a commitment list — a "don't lose this" list. Big-picture sequencing lives in
the RMS architecture design (local spec, 2026-07-03).

## Small

- **QW-1: "How It Works" text updates** — DEFERRED, still waiting on director's final
  text (step 2/4 rewording, locked-request explanation, contact links).
- **MP-10: Home page approver section** — decide: keep, simplify to pending-count
  callout, or move pending count into the nav Review dropdown.
- **MP-6: Collapsible reference info** — collapsible line statuses/definitions in
  templates.

## Medium

- **MP-1: Deadline policy** — deferred in March because "deadline is months away."
  Partially overtaken: submission reminders shipped, and the email-system spec
  (2026-05-19) covers deadline reminder tiers. Revisit what's left (enforcement?
  lock-on-deadline?) once email-system lands.
- **D-2: Site map page** — role-aware `/sitemap` listing accessible pages by category.
  Team-requested, low priority.
- **MP-7: Request ID format** — LIKELY SUPERSEDED by the deterministic public-ID
  system (`SMF27-TECHOPS-BUD-1`). Verify nothing remains, then drop.

## Large (future sprints, priority order from March)

- **LF-1: Admin add lines** on non-draft requests (P2; prerequisite for LF-2).
- **LF-2: Interdepartmental spend** — `spending_department_id` on lines + report (P3).
- **LF-3: Income accounts** — EXPENSE|INCOME account type, net budget calc (P4).
- **LF-4: Historical P&L import** — prior-year comparison model + import + report (P5).

## Ongoing / discussion needed

- **D-1: CSS refactoring** — replace inline styles incrementally as templates are touched.
- **"Falling through the cracks" report** — needs requirements definition.
- **AV packages on fixed costs** — future enhancement, revisit with AV work type.
- **Testing volunteers** — process question, not code.