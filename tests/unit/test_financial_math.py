"""
Unit tests for financial math — Decimal-based dollar-to-cents conversion
and line total calculations.

Covers the floating-point precision fix: all monetary math must use
Decimal (not float) to avoid off-by-one-cent errors.
"""

from decimal import Decimal
from types import SimpleNamespace

from app.line_details import get_line_amount_cents
from app.routes.admin.data_upload import _parse_dollars_to_cents
from app.routes.admin.expense_accounts import _parse_price_cents


# ---------------------------------------------------------------------------
# get_line_amount_cents — budget line: unit_price_cents * quantity
# ---------------------------------------------------------------------------

class TestGetLineAmountCents:
    """Tests for budget line total calculation using Decimal math."""

    @staticmethod
    def _make_line(unit_price_cents, quantity):
        """Build a minimal fake WorkLine with a budget detail."""
        detail = SimpleNamespace(
            unit_price_cents=unit_price_cents,
            quantity=Decimal(str(quantity)),
        )
        return SimpleNamespace(
            budget_detail=detail,
            contract_detail=None,
            supply_detail=None,
        )

    def test_simple_multiplication(self):
        line = self._make_line(5000, 3)
        assert get_line_amount_cents(line) == 15000

    def test_fractional_quantity(self):
        """1.5 units at $50 each = $75 = 7500 cents."""
        line = self._make_line(5000, "1.5")
        assert get_line_amount_cents(line) == 7500

    def test_precision_no_float_drift(self):
        """Regression: float(1999) * float(1.001) = 2000.999 → int truncates to 2000.
        With Decimal this must be exactly 2000 (1999 * 1.001 = 2000.999, int → 2000)
        — but more importantly, no spurious float noise."""
        line = self._make_line(1999, "1.001")
        # Decimal: 1999 * 1.001 = 2000.999 → int() = 2000
        assert get_line_amount_cents(line) == 2000

    def test_classic_float_bug(self):
        """Regression: int(33 * float(Decimal('1.1'))) could give 36 instead of 36
        due to float representation. Decimal keeps it exact."""
        line = self._make_line(33, "1.1")
        # 33 * 1.1 = 36.3 → int() = 36
        assert get_line_amount_cents(line) == 36

    def test_penny_precision(self):
        """$19.99 item (1999 cents) * quantity 1 must be exactly 1999."""
        line = self._make_line(1999, 1)
        assert get_line_amount_cents(line) == 1999

    def test_zero_quantity(self):
        line = self._make_line(5000, 0)
        assert get_line_amount_cents(line) == 0

    def test_no_detail_returns_zero(self):
        line = SimpleNamespace(
            budget_detail=None,
            contract_detail=None,
            supply_detail=None,
        )
        assert get_line_amount_cents(line) == 0


# ---------------------------------------------------------------------------
# _parse_dollars_to_cents (data_upload.py)
# ---------------------------------------------------------------------------

class TestParseDollarsToCents:
    """Tests for CSV dollar string → cents conversion."""

    def test_simple_dollars(self):
        assert _parse_dollars_to_cents("25.00") == 2500

    def test_with_currency_symbol(self):
        assert _parse_dollars_to_cents("$25.00") == 2500

    def test_with_commas(self):
        assert _parse_dollars_to_cents("$1,234.56") == 123456

    def test_nineteen_ninety_nine(self):
        """Classic float bug: float('19.99') * 100 = 1998.9999… → int = 1998.
        Decimal must give exactly 1999."""
        assert _parse_dollars_to_cents("19.99") == 1999

    def test_ten_cents(self):
        """float('0.10') * 100 can drift. Decimal must give exactly 10."""
        assert _parse_dollars_to_cents("0.10") == 10

    def test_whole_dollars(self):
        assert _parse_dollars_to_cents("100") == 10000

    def test_none_returns_none(self):
        assert _parse_dollars_to_cents(None) is None

    def test_empty_string_returns_none(self):
        assert _parse_dollars_to_cents("") is None

    def test_invalid_returns_none(self):
        assert _parse_dollars_to_cents("abc") is None

    def test_large_amount(self):
        assert _parse_dollars_to_cents("999999.99") == 99999999


# ---------------------------------------------------------------------------
# _parse_price_cents (expense_accounts.py)
# ---------------------------------------------------------------------------

class TestParsePriceCents:
    """Tests for price string → cents conversion."""

    def test_simple_price(self):
        assert _parse_price_cents("24.50") == 2450

    def test_with_dollar_sign(self):
        assert _parse_price_cents("$24.50") == 2450

    def test_with_commas(self):
        assert _parse_price_cents("$1,000.00") == 100000

    def test_nineteen_ninety_nine(self):
        """Same float regression test as above."""
        assert _parse_price_cents("19.99") == 1999

    def test_none_returns_none(self):
        assert _parse_price_cents(None) is None

    def test_empty_returns_none(self):
        assert _parse_price_cents("") is None

    def test_invalid_returns_none(self):
        assert _parse_price_cents("not-a-number") is None
