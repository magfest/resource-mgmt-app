# Email outbox

Every email this app sends goes through a database queue. Application code
writes a row; a scheduled command sends it. This document covers what the rows
mean, what the statuses claim, and how to answer "why did this email not
arrive".

## What the outbox is

The `email_outbox` table is not a cache and not a send buffer. It is the record
that a notification was owed, written in the same transaction as the change
that owed it. A submitted request and the notification about it commit together
or not at all, so a failed commit cannot leave a department told about a
submission that did not happen.

`app/services/email_enqueue.py` writes the rows. `app/services/email_drainer.py`
sends them. Rows the notification layer writes carry a `dedup_key` under a
unique constraint, so a double-clicked submit button inserts one row, not two.

`app/services/email.py` is transport only. `send_via_ses()` reads no config,
touches no database, and never raises; a raising transport would abort the
drainer's batch on one bad recipient. Dedup, pacing, retry, and the
`EMAIL_ENABLED` kill switch all sit above it.

The drainer is the only path that sends *queued* mail, not the only caller of
SES. Two admin test-send routes call `send_via_ses()` directly and bypass the
outbox: the template test send in `app/routes/admin/email_templates.py` and the
test email on the email health page. Each writes its own `notification_logs`
row, since the transport writes none.

## Tables

| Table | Holds | Retention |
| --- | --- | --- |
| `email_outbox` | Mutable queue work state: claims, attempts, next dispatch time | 90 days after a row reaches a terminal status |
| `notification_logs` | Append-only record of what was attempted and how it ended | 4 years |
| `email_message_bodies` | The rendered subject, text, and HTML handed to SES | 24 months |
| `email_suppression` | Addresses the drainer refuses to send to | Until removed by an admin |

## Outbox statuses

| Status | Meaning | Next state |
| --- | --- | --- |
| `QUEUED` | Due at `dispatch_at`, waiting for a drain run | `SENDING` |
| `SENDING` | Claimed by a run in flight | Any terminal status, or back to `QUEUED` |
| `RENDER_BLOCKED` | The template failed to render; the row is parked | `SENDING` on the next retry |
| `SENT` | SES accepted the message. See below | Terminal |
| `SUPPRESSED` | Recipient is on the suppression list, or `EMAIL_ENABLED` is false | Terminal |
| `CANCELLED` | The request, department, or template the row referred to is gone | Terminal |
| `FAILED` | Out of attempts, or SES rejected it permanently | Terminal |

The constants are in `app/models/constants.py`. The status is `RENDER_BLOCKED`;
there is no `BLOCKED`.

A run that dies mid-send leaves rows in `SENDING`. The next run reaps any claim
older than 30 minutes and returns the row to the queue.

## The two retry ladders

Transport failures and render failures retry on separate schedules because they
are waiting on different things. A network or SES failure resolves on its own;
a broken template waits for a person to fix it.

| Failure | Delay | Gives up after |
| --- | --- | --- |
| Transport (SES or network) | Doubles from 20 minutes: 20, 40, 80, 160, 320, 640 | 7 attempts, about 21 hours |
| Render (template raised or returned nothing) | Flat 60 minutes | 7 days |

A render failure never increments `attempt_count`. Fix the template in
Admin, and the next drain run renders and sends the parked rows with their full
delivery budget intact. Both ladders are configurable through
`EMAIL_MAX_ATTEMPTS`, `EMAIL_RENDER_RETRY_MINUTES`, and
`EMAIL_RENDER_MAX_AGE_DAYS`.

## What `SENT` means

`SENT` means SES accepted the message and returned a message id. It does not
mean the message was delivered. This system cannot distinguish a delivered
message from one that bounced, one the recipient's provider filtered to spam,
and one that was accepted and then dropped. The admin pages label the status
"Accepted by SES" for that reason.

The `delivery_status`, `delivery_updated_at`, and `delivery_detail` columns on
`notification_logs` are always NULL. They exist for a future SES
event-notification handler; NULL means "not known", never "not delivered".

So a `SENT` row plus a recipient who never saw the email is not a contradiction
and not a bug in this app. The next step is the SES console, which holds the
bounce and complaint data this app does not receive. Whoever administers the
MAGFest AWS account has that access.

## Scheduler entries

Both commands run under Heroku Scheduler. **Adding and maintaining these
entries is a manual step in the Heroku dashboard. Neither is reproducible from
this repo, and a rebuilt app has neither until someone adds them.**

| Command | Frequency | Does |
| --- | --- | --- |
| `flask drain-email-outbox` | Every 10 minutes | Sends due rows, then prunes terminal outbox rows past 90 days |
| `flask prune-email-audit` | Daily | Deletes expired message bodies and notification log rows |

Both echo their counts. A Scheduler log line showing `sent=0` on a queue with
depth is a signal to open the health page.

To send outside the schedule, run `heroku run flask drain-email-outbox`. There
is no drain button in the UI; a throttled multi-minute loop does not fit
Heroku's 30-second router window.

## Answering "why did this email not arrive"

The queue health panel at `/admin/email/` answers "is email moving". It does
not answer "where is mine". Use the message lookup on that page, which searches
`notification_logs` by recipient address or by work item public ID such as
`SMF27-TECHOPS-BUD-1`.

| What the lookup shows | What it means | Do this |
| --- | --- | --- |
| No row at all | Nothing was ever queued. The bug is in the enqueue path or the audience logic, not in delivery | Check the notify call for that event |
| A `QUEUED` outbox row, dispatch time in the past | The drainer is not running or is failing | Check the Scheduler entry and the health page |
| `RENDER_BLOCKED` | A template is broken; the row is parked and will retry | Fix the template in Admin, then wait for the next run |
| `SUPPRESSED` | The address is on the suppression list, or `EMAIL_ENABLED` is false in that environment | Check both |
| `CANCELLED` | The request it referred to was deleted before the send | This is the answer; no send was possible |
| `FAILED` | Read `error_message` on the log row for the SES error | Fix the cause, then re-enqueue |
| `SENT` | SES accepted it. This app knows nothing further | Take the provider message id to the SES console |

A `FAILED` or `CANCELLED` row releases its dedup key, so the same notification
can be enqueued again. `SENT` and `SUPPRESSED` rows keep theirs.

## The body archive

`email_message_bodies` stores what was actually rendered, including for
suppressed recipients, so "what did that email say" has an answer that does not
depend on the recipient still having it. Bodies are visible to SUPER_ADMIN
only, and they render inside a sandboxed iframe because stored HTML is
attacker-influenced text.

Bodies expire at 24 months against the log's four years. A log row older than
that survives with no body, which is expected: the record of the send outlives
the copy of the message. Both windows are configurable through
`EMAIL_BODY_RETENTION_MONTHS` and `EMAIL_LOG_RETENTION_DAYS`, and the prune
treats a month as 30 days.
