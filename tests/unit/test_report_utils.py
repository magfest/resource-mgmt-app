"""
Unit tests for app/routes/admin_final/report_utils.py
"""
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from app.routes.admin_final.report_utils import (
    PipelineTotals,
    calculate_days_waiting,
    compute_line_amount_cents,
    compute_pipeline_summary,
)


class TestPipelineTotals:
    """Tests for the PipelineTotals dataclass."""

    @staticmethod
    def _totals(scale=1):
        return PipelineTotals(
            draft_cents=100 * scale,
            in_review_cents=1000 * scale,
            approved_requested_cents=4000 * scale,
            approved_cents=3000 * scale,
            rejected_cents=500 * scale,
        )

    def test_requested_excludes_draft(self):
        assert self._totals().requested_cents == 1000 + 4000 + 500

    def test_reduced_and_ceiling(self):
        totals = self._totals()
        assert totals.reduced_cents == 1000
        assert totals.ceiling_cents == 3000 + 1000

    def test_columns_reconcile_to_requested(self):
        t = self._totals()
        assert t.requested_cents == (
            t.in_review_cents + t.approved_cents + t.reduced_cents + t.rejected_cents
        )

    def test_add(self):
        result = self._totals().add(self._totals(scale=10))
        assert result == self._totals(scale=11)


class TestCalculateDaysWaiting:
    """Tests for the calculate_days_waiting function."""

    def test_calculate_days_waiting_returns_zero_for_none(self):
        """calculate_days_waiting should return 0 when submitted_at is None."""
        assert calculate_days_waiting(None) == 0

    def test_calculate_days_waiting_returns_positive_days(self):
        """calculate_days_waiting should return the number of days since submission."""
        # Mock datetime.utcnow() to return a fixed time
        fixed_now = datetime(2026, 3, 9, 12, 0, 0)
        submitted_at = datetime(2026, 3, 4, 12, 0, 0)  # 5 days ago

        with patch("app.routes.admin_final.report_utils.datetime") as mock_dt:
            mock_dt.utcnow.return_value = fixed_now

            result = calculate_days_waiting(submitted_at)

            assert result == 5


class TestComputePipelineSummary:
    """Tests for the compute_pipeline_summary function."""

    def test_compute_pipeline_summary(self):
        rows = [
            PipelineTotals(100, 200, 300, 250, 50),
            PipelineTotals(1000, 2000, 3000, 2500, 500),
        ]

        result = compute_pipeline_summary(rows)

        assert result == PipelineTotals(1100, 2200, 3300, 2750, 550)


def test_compute_line_amount_cents_basic():
    # 3 units @ $50.00 = $150.00
    assert compute_line_amount_cents(5000, Decimal("3")) == 15000


def test_compute_line_amount_cents_fractional_quantity_rounds_half_up():
    # 1.5 units @ $1.00 = $1.50 -> 150 cents
    assert compute_line_amount_cents(100, Decimal("1.5")) == 150
    # 1 unit @ 1 cent, qty 0.5 -> 0.5 cent rounds half-up to 1
    assert compute_line_amount_cents(1, Decimal("0.5")) == 1


def test_compute_line_amount_cents_handles_none_and_zero():
    assert compute_line_amount_cents(None, Decimal("3")) == 0
    assert compute_line_amount_cents(5000, None) == 0
    assert compute_line_amount_cents(0, Decimal("3")) == 0
