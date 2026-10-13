"""Queries behind the TechOps admin home's first two cards.

Card A reuses group_progress_for_items, so its counts match the progress
strip on each request. Card B reads the event layout: a department that
holds a room is expected to file. The third card reads worklist.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app import db
from app.models import (
    Department,
    SpaceAssignment,
    WorkItem,
    WorkItemAuditEvent,
    WorkPortfolio,
    WorkType,
    WORK_ITEM_STATUS_DRAFT,
    WORK_ITEM_STATUS_FINALIZED,
    WORK_ITEM_STATUS_SUBMITTED,
)
from app.routes.work.helpers.event_enablement import get_enabled_department_ids_for_event
from app.routes.work.helpers.review_state import group_progress_for_items


@dataclass
class TeamQueue:
    group: object
    pending: int = 0
    waiting_on_requester: int = 0
    oldest_pending_since: datetime | None = None


@dataclass
class MissingDepartment:
    department: Department
    draft: WorkItem | None = None
    last_edited: datetime | None = None


def _techops_items(event_cycle):
    return (
        WorkItem.query
        .join(WorkPortfolio, WorkPortfolio.id == WorkItem.portfolio_id)
        .join(WorkType, WorkType.id == WorkPortfolio.work_type_id)
        .filter(
            WorkType.code == "TECHOPS",
            WorkPortfolio.event_cycle_id == event_cycle.id,
            WorkItem.is_archived.is_(False),
        )
    )


def team_queues(event_cycle, groups) -> list[TeamQueue]:
    """Pending and kicked-back line counts per group, with the oldest wait.

    The wait runs from the request's submitted_at, so a reopened line reads
    older than its current wait.
    """
    items = _techops_items(event_cycle).filter(
        WorkItem.status.in_((WORK_ITEM_STATUS_SUBMITTED, WORK_ITEM_STATUS_FINALIZED))).all()
    submitted = {item.id: item.submitted_at for item in items}
    queues = {group.id: TeamQueue(group=group) for group in groups}
    for item_id, progress in group_progress_for_items(submitted).items():
        for entry in progress:
            queue = queues.get(entry.group.id)
            if queue is None:
                continue
            queue.pending += entry.pending
            queue.waiting_on_requester += entry.waiting_on_requester
            since = submitted[item_id]
            if entry.pending and since and (
                    queue.oldest_pending_since is None or since < queue.oldest_pending_since):
                queue.oldest_pending_since = since
    return [queues[group.id] for group in groups]


def unfinished_request_count(event_cycle) -> int:
    return _techops_items(event_cycle).filter(
        WorkItem.status == WORK_ITEM_STATUS_SUBMITTED).count()


def _last_edited(updated_by_item_id: dict[int, datetime]) -> dict[int, datetime]:
    """Latest audit event per draft, else the item's own updated_at.

    A TechOps draft save writes the detail, spaces and lines but no
    work_items column, so WorkItem.updated_at stays at creation.
    """
    if not updated_by_item_id:
        return {}
    latest = dict(
        db.session.query(WorkItemAuditEvent.work_item_id,
                         db.func.max(WorkItemAuditEvent.created_at))
        .filter(WorkItemAuditEvent.work_item_id.in_(updated_by_item_id))
        .group_by(WorkItemAuditEvent.work_item_id)
        .all()
    )
    return {item_id: max(filter(None, (updated, latest.get(item_id))))
            for item_id, updated in updated_by_item_id.items()}


def departments_not_submitted(event_cycle, all_departments=False):
    """Departments expected to file that have not: (no request, draft only).

    Expected means enabled for the event and holding a room in its layout,
    or every enabled department when all_departments is set. Any request
    past DRAFT counts as filed, including one that only says "no services".
    """
    expected = get_enabled_department_ids_for_event(event_cycle.id)
    if not all_departments:
        holders = {
            dept_id for (dept_id,) in db.session.query(SpaceAssignment.department_id)
            .filter(SpaceAssignment.event_cycle_id == event_cycle.id).distinct()
        }
        expected &= holders
    if not expected:
        return [], []

    items = _techops_items(event_cycle).all()
    edited = _last_edited({item.id: item.updated_at for item in items
                           if item.status == WORK_ITEM_STATUS_DRAFT})
    latest_draft: dict[int, WorkItem] = {}
    filed: set[int] = set()
    for item in items:
        dept_id = item.portfolio.department_id
        if item.status != WORK_ITEM_STATUS_DRAFT:
            filed.add(dept_id)
        elif dept_id not in latest_draft or edited[item.id] > edited[latest_draft[dept_id].id]:
            latest_draft[dept_id] = item

    no_request, draft_only = [], []
    for dept in (Department.query.filter(Department.id.in_(expected))
                 .order_by(Department.name.asc()).all()):
        if dept.id in filed:
            continue
        if dept.id in latest_draft:
            draft = latest_draft[dept.id]
            draft_only.append(MissingDepartment(dept, draft, edited[draft.id]))
        else:
            no_request.append(MissingDepartment(dept))
    return no_request, draft_only
