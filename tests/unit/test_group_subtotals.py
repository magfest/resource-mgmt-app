"""Unit tests for compute_group_subtotals."""
from types import SimpleNamespace

from app.routes.work.helpers.computations import (
    compute_group_subtotals,
    GroupSubtotal,
)


def _line(group_id, unit_price_cents, quantity, status="PENDING", approved_cents=None):
    """Build a minimal WorkLine-like stub for the helper.

    The helper reads line.budget_detail.routed_approval_group_id,
    line.status, line.approved_amount_cents, and (via get_line_amount_cents)
    budget_detail.unit_price_cents / quantity.
    """
    detail = SimpleNamespace(
        routed_approval_group_id=group_id,
        unit_price_cents=unit_price_cents,
        quantity=quantity,
    )
    return SimpleNamespace(
        budget_detail=detail,
        contract_detail=None,
        supply_detail=None,
        techops_detail=None,
        status=status,
        approved_amount_cents=approved_cents,
    )


def test_buckets_by_group_with_requested_and_approved():
    lines = [
        _line(1, 200_00, 2, status="APPROVED", approved_cents=350_00),  # req 400.00, appr 350.00
        _line(1, 100_00, 1),                                            # req 100.00, appr 0
        _line(2, 50_00, 3, status="APPROVED", approved_cents=150_00),   # req 150.00, appr 150.00
    ]
    group_names = {1: "TECH", 2: "HOTEL"}

    result = compute_group_subtotals(lines, group_names)

    assert [g.group_name for g in result] == ["HOTEL", "TECH"]  # alpha order
    hotel = next(g for g in result if g.group_id == 2)
    tech = next(g for g in result if g.group_id == 1)
    assert tech.line_count == 2
    assert tech.requested_cents == 500_00
    assert tech.approved_cents == 350_00
    assert hotel.line_count == 1
    assert hotel.requested_cents == 150_00
    assert hotel.approved_cents == 150_00


def test_null_group_becomes_unassigned_and_sorts_last():
    lines = [
        _line(None, 100_00, 1),
        _line(1, 100_00, 1),
    ]
    result = compute_group_subtotals(lines, {1: "TECH"})

    assert [g.group_name for g in result] == ["TECH", "Unassigned"]
    unassigned = result[-1]
    assert unassigned.group_id is None
    assert unassigned.requested_cents == 100_00


def test_empty_lines_returns_empty_list():
    assert compute_group_subtotals([], {}) == []
