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

    def test_pipeline_totals_total_cents(self):
        """total_cents should sum all active stages (excluding rejected)."""
        totals = PipelineTotals(
            draft_cents=1000,
            submitted_cents=2000,
            reviewer_recommended_cents=3000,
            final_approved_cents=4000,
            rejected_cents=500,
        )

        assert totals.total_cents == 10000  # 1000 + 2000 + 3000 + 4000

    def test_pipeline_totals_total_with_rejected(self):
        """total_with_rejected_cents should include rejected amounts."""
        totals = PipelineTotals(
            draft_cents=1000,
            submitted_cents=2000,
            reviewer_recommended_cents=3000,
            final_approved_cents=4000,
            rejected_cents=500,
        )

        assert totals.total_with_rejected_cents == 10500  # 10000 + 500

    def test_pipeline_totals_add(self):
        """add should combine two PipelineTotals instances."""
        totals1 = PipelineTotals(
            draft_cents=100,
            submitted_cents=200,
            reviewer_recommended_cents=300,
            final_approved_cents=400,
            rejected_cents=50,
        )
        totals2 = PipelineTotals(
            draft_cents=10,
            submitted_cents=20,
            reviewer_recommended_cents=30,
            final_approved_cents=40,
            rejected_cents=5,
        )

        result = totals1.add(totals2)

        assert result.draft_cents == 110
        assert result.submitted_cents == 220
        assert result.reviewer_recommended_cents == 330
        assert result.final_approved_cents == 440
        assert result.rejected_cents == 55


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
        """compute_pipeline_summary should sum all row attributes into totals."""
        # Create mock rows with pipeline attributes
        class MockRow:
            def __init__(self, draft, submitted, ag_approved, final_approved, rejected):
                self.draft_cents = draft
                self.submitted_cents = submitted
                self.reviewer_recommended_cents = ag_approved
                self.final_approved_cents = final_approved
                self.rejected_cents = rejected

        rows = [
            MockRow(100, 200, 300, 400, 50),
            MockRow(1000, 2000, 3000, 4000, 500),
        ]

        result = compute_pipeline_summary(rows)

        assert result.draft_cents == 1100
        assert result.submitted_cents == 2200
        assert result.reviewer_recommended_cents == 3300
        assert result.final_approved_cents == 4400
        assert result.rejected_cents == 550
        assert result.total_cents == 11000


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
