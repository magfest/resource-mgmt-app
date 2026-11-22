"""Tests for the shared Supply line-state helpers."""
from datetime import datetime
from types import SimpleNamespace

from app import db
from app.models import SupplyOrderLineDetail, WORK_ITEM_STATUS_FINALIZED
from app.routes.work.supply.line_state import (
    is_line_removed,
    is_order_closed,
    reviewer_quantity_label,
    supply_display_status,
)
from tests.integration.supply_helpers import (
    add_line, make_draft_order, seed_approval_group, seed_category, seed_item, seed_supply,
)


def _order(seed_workflow_data):
    wt = seed_supply(seed_workflow_data)
    group = seed_approval_group(wt)
    item = seed_item(seed_category(approval_group=group))
    order = make_draft_order(wt, seed_workflow_data["cycle"], seed_workflow_data["department"])
    add_line(order, item, quantity=10)
    return order


def test_is_line_removed_reads_removed_at():
    assert is_line_removed(None) is False
    assert is_line_removed(SupplyOrderLineDetail(removed_at=None)) is False
    assert is_line_removed(SupplyOrderLineDetail(removed_at=datetime(2026, 1, 1))) is True


def test_supply_display_status_closed_overrides_finalized(seed_workflow_data):
    order = _order(seed_workflow_data)
    order.status = WORK_ITEM_STATUS_FINALIZED
    assert supply_display_status(order) == "FINALIZED"
    order.supply_order_detail.closed_at = datetime(2026, 1, 1)
    db.session.commit()
    assert is_order_closed(order) is True
    assert supply_display_status(order) == "CLOSED"


def test_supply_display_status_falls_back_to_effective_status(seed_workflow_data):
    order = _order(seed_workflow_data)
    order.status = "SUBMITTED"
    db.session.commit()
    assert is_order_closed(order) is False
    assert supply_display_status(order) == "UNDER_REVIEW"


def test_reviewer_quantity_label():
    approved = SimpleNamespace(status="APPROVED")
    pending = SimpleNamespace(status="PENDING")
    reviewed = SupplyOrderLineDetail(quantity_requested=10, quantity_reviewed=6)
    unreviewed = SupplyOrderLineDetail(quantity_requested=10, quantity_reviewed=None)
    assert reviewer_quantity_label(reviewed, approved) == "Approved 6 of 10"
    assert reviewer_quantity_label(unreviewed, pending) is None
    assert reviewer_quantity_label(reviewed, pending) is None
