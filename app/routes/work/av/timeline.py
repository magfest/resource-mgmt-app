"""Status timeline derivation for AV requests.

Walks the various event sources (WorkItem audit, AVRequestPlan revisions,
WorkLineReview transitions, AVScopeIncorporatedRequest, AVAcknowledgment)
and produces a unified chronological list of TimelineEvent for display.

Engine status (DRAFT/SUBMITTED/etc.) and the AV-specific layers (scope
incorporation, acks) both contribute. Some labels are derived (e.g.,
"Incorporated into <Space> v<N>" — derived from the locked scope at that
moment).
"""
from __future__ import annotations

from collections import namedtuple

from app.models import ActivityEvent, WorkItem
from app.models.av import AVScopeIncorporatedRequest


TimelineEvent = namedtuple("TimelineEvent", ["timestamp", "label", "actor_user_id", "kind"])


def build_status_timeline(work_item: WorkItem) -> list[TimelineEvent]:
    """Return chronologically-sorted timeline events for an AV WorkItem.

    Sources:
    - WorkItem.created_at → "Created"
    - ActivityEvent rows (filter by work_item_id) → submission/recall/etc.
    - AVRequestPlan rows → "Logged · rev N"
    - WorkLineReview rows where status is one of NEEDS_INFO/NEEDS_ADJUSTMENT/REJECTED → kickback labels
    - AVScopeIncorporatedRequest rows → "Incorporated into <Space> v<N>"
    - AVAcknowledgment for the request's space's latest scope → ack label (only if relevant)
    """
    events: list[TimelineEvent] = []

    # 1. Created
    events.append(TimelineEvent(
        timestamp=work_item.created_at,
        label="Created",
        actor_user_id=work_item.created_by_user_id,
        kind="created",
    ))

    # 2. ActivityEvent rows for this work item (Submitted, Recalled, etc.)
    activity_events = (
        ActivityEvent.query
        .filter_by(work_item_id=work_item.id)
        .order_by(ActivityEvent.occurred_at)
        .all()
    )
    for ae in activity_events:
        if ae.event_type == "AV_REQUEST_SUBMITTED":
            label = "Submitted"
        elif ae.event_type == "AV_REQUEST_RECALLED":
            label = "Recalled to draft"
        elif ae.event_type == "AV_REQUEST_REJECTED":
            label = "Rejected by AV team"
        else:
            continue  # other activity events handled below or skipped

        events.append(TimelineEvent(
            timestamp=ae.occurred_at,
            label=label,
            actor_user_id=ae.actor_user_id,
            kind="activity",
        ))

    # 3. AV plan revisions
    plans = sorted(work_item.av_plans, key=lambda p: p.revision)
    for plan in plans:
        events.append(TimelineEvent(
            timestamp=plan.created_at,
            label=f"Logged · rev {plan.revision}",
            actor_user_id=plan.authored_by_user_id,
            kind="plan",
        ))

    # 4. Kickbacks (line reviews that aren't PENDING / APPROVED / LOGGED)
    if work_item.lines:
        line = work_item.lines[0]
        for review in line.reviews:
            label = None
            if review.status == "NEEDS_INFO":
                label = "AV requested more info"
            elif review.status == "NEEDS_ADJUSTMENT":
                label = "AV requested adjustment"
            elif review.status == "REJECTED":
                label = "AV rejected the request"
            if label:
                # Use the review's decided_at if present, fallback to created_at-ish
                ts = getattr(review, "decided_at", None) or getattr(review, "created_at", work_item.created_at)
                events.append(TimelineEvent(
                    timestamp=ts,
                    label=label,
                    actor_user_id=getattr(review, "decided_by_user_id", None),
                    kind="review",
                ))

    # 5. Scope incorporations
    for inc in work_item.av_scope_incorporations:
        scope = inc.scope
        space = scope.space
        events.append(TimelineEvent(
            timestamp=inc.incorporated_at,
            label=f"Incorporated into {space.name} v{scope.version}",
            actor_user_id=scope.locked_by_user_id,
            kind="incorporated",
        ))

    # 6. Acknowledgments for the request's space's scope (only relevant ones)
    # Skip for now in the basic implementation — acks belong to scopes, not requests.
    # Future: surface "you owe an ack" status as derived label when an open scope's
    # ack for the request's dept is PENDING.

    # Sort chronologically (defensive — same timestamp order is OK to keep insertion order)
    events.sort(key=lambda e: e.timestamp)
    return events
