"""Engine-level read-model interpreting a line's two review-stage records.

`line.status` is a lossy summary of two stages (APPROVAL_GROUP recommendation +
ADMIN_FINAL decision) plus the requester kickback loop. This helper reads the
review records directly and answers "who must act next" without guessing from
`line.status`. Work-type-agnostic: handles work types without an admin stage.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from app import db
from app.models import (
    ApprovalGroup, WorkLine, WorkLineReview,
    REVIEW_STAGE_APPROVAL_GROUP, REVIEW_STAGE_ADMIN_FINAL,
    REVIEW_STATUS_PENDING, REVIEW_STATUS_APPROVED, REVIEW_STATUS_REJECTED,
    REVIEW_STATUS_NEEDS_INFO, REVIEW_STATUS_NEEDS_ADJUSTMENT,
    REVIEW_STATUS_APPROVED_NEEDS_REVIEW,
)

AWAITING_REVIEWER_GROUP = "REVIEWER_GROUP"
AWAITING_ADMIN = "ADMIN"
AWAITING_REQUESTER = "REQUESTER"
AWAITING_DONE = "DONE"

_KICKBACK = (REVIEW_STATUS_NEEDS_INFO, REVIEW_STATUS_NEEDS_ADJUSTMENT)
_AG_TERMINAL = (REVIEW_STATUS_APPROVED, REVIEW_STATUS_APPROVED_NEEDS_REVIEW, REVIEW_STATUS_REJECTED)
_ADMIN_TERMINAL = (REVIEW_STATUS_APPROVED, REVIEW_STATUS_REJECTED)


@dataclass(frozen=True)
class LineReviewState:
    ag: Optional[WorkLineReview]
    admin: Optional[WorkLineReview]
    has_admin_stage: bool
    awaiting: str
    kickback_review: Optional[WorkLineReview]


def _has_admin_stage(line: WorkLine) -> bool:
    portfolio = line.work_item.portfolio if line.work_item else None
    work_type = portfolio.work_type if portfolio else None
    config = work_type.config if work_type else None
    return bool(config and config.has_admin_final)


def is_group_final(work_item) -> bool:
    """Return whether the approval group's decision is final for this item.

    True when the work type has no admin-final stage; as of 2026-10-12 that
    is TechOps alone. A missing config counts as not group-final, so the
    checkout stays required rather than silently dropping away.
    """
    portfolio = work_item.portfolio if work_item else None
    work_type = portfolio.work_type if portfolio else None
    config = work_type.config if work_type else None
    return bool(config) and not config.has_admin_final


def get_line_review_state(line: WorkLine) -> LineReviewState:
    ag = WorkLineReview.query.filter_by(
        work_line_id=line.id, stage=REVIEW_STAGE_APPROVAL_GROUP).first()
    admin = WorkLineReview.query.filter_by(
        work_line_id=line.id, stage=REVIEW_STAGE_ADMIN_FINAL).first()
    has_admin_stage = _has_admin_stage(line)

    # The admin stage wins if both stages somehow bounced to the requester.
    kickback_review = None
    if admin is not None and admin.status in _KICKBACK:
        kickback_review = admin
    elif ag is not None and ag.status in _KICKBACK:
        kickback_review = ag

    ag_decided = ag is not None and ag.status in _AG_TERMINAL
    admin_terminal = admin is not None and admin.status in _ADMIN_TERMINAL

    if kickback_review is not None:
        awaiting = AWAITING_REQUESTER
    elif has_admin_stage and admin_terminal:
        awaiting = AWAITING_DONE
    elif not has_admin_stage and ag_decided:
        awaiting = AWAITING_DONE
    elif ag is None or ag.status == REVIEW_STATUS_PENDING:
        awaiting = AWAITING_REVIEWER_GROUP
    elif has_admin_stage:
        awaiting = AWAITING_ADMIN
    else:
        awaiting = AWAITING_DONE

    return LineReviewState(
        ag=ag, admin=admin, has_admin_stage=has_admin_stage,
        awaiting=awaiting, kickback_review=kickback_review,
    )


@dataclass(frozen=True)
class GroupProgress:
    """How far one approval group has got on one request."""
    group: ApprovalGroup
    total: int
    decided: int
    waiting_on_requester: int

    @property
    def pending(self) -> int:
        return self.total - self.decided - self.waiting_on_requester


def group_progress_for_items(work_item_ids) -> dict[int, list[GroupProgress]]:
    """Return per-group progress for each item, groups in sort order.

    Reads APPROVAL_GROUP review rows, not line.status, which summarises two
    stages lossily. An item with no review rows, such as a draft, is absent.
    """
    ids = list(work_item_ids)
    if not ids:
        return {}
    rows = (
        db.session.query(WorkLine.work_item_id, WorkLineReview.status, ApprovalGroup)
        .join(WorkLineReview, WorkLineReview.work_line_id == WorkLine.id)
        .join(ApprovalGroup, ApprovalGroup.id == WorkLineReview.approval_group_id)
        .filter(WorkLine.work_item_id.in_(ids))
        .filter(WorkLineReview.stage == REVIEW_STAGE_APPROVAL_GROUP)
        .all()
    )
    tallies: dict[int, dict[int, list]] = {}
    for item_id, status, group in rows:
        entry = tallies.setdefault(item_id, {}).setdefault(group.id, [group, 0, 0, 0])
        entry[1] += 1
        if status in _AG_TERMINAL:
            entry[2] += 1
        elif status in _KICKBACK:
            entry[3] += 1
    return {
        item_id: [
            GroupProgress(group=g, total=t, decided=d, waiting_on_requester=w)
            for g, t, d, w in sorted(
                by_group.values(), key=lambda e: (e[0].sort_order or 0, e[0].name),
            )
        ]
        for item_id, by_group in tallies.items()
    }
