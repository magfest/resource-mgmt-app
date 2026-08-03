"""Unit tests for the admin line tools' pricing rules."""
from app import db
from app.models import (
    ExpenseAccount,
    ExpenseAccountEventOverride,
    UI_GROUP_BADGES,
    UI_GROUP_HOTEL_SERVICES,
)


def _account(code, is_fixed, ui_group=None, price_cents=0):
    acct = ExpenseAccount(
        code=code, name=code.title(), is_active=True,
        is_fixed_cost=is_fixed, ui_display_group=ui_group,
        default_unit_price_cents=price_cents,
    )
    db.session.add(acct)
    db.session.commit()
    return acct


class TestClassifyAccount:
    def test_standard(self, app):
        from app.routes.admin_final.line_pricing import (
            classify_account, KIND_STANDARD,
        )
        acct = _account("STD", is_fixed=False)
        assert classify_account(acct, None) == KIND_STANDARD

    def test_hotel(self, app):
        from app.routes.admin_final.line_pricing import (
            classify_account, KIND_HOTEL,
        )
        acct = _account("HTL_DOUBLE_MAGPAID", True, UI_GROUP_HOTEL_SERVICES, 15900)
        assert classify_account(acct, None) == KIND_HOTEL

    def test_badge(self, app):
        from app.routes.admin_final.line_pricing import (
            classify_account, KIND_BADGE,
        )
        acct = _account("BADGE_STAFF", True, UI_GROUP_BADGES, 0)
        assert classify_account(acct, None) == KIND_BADGE

    def test_fixed_without_group(self, app):
        from app.routes.admin_final.line_pricing import (
            classify_account, KIND_FIXED,
        )
        acct = _account("ETH_DROP", True, None, 25000)
        assert classify_account(acct, None) == KIND_FIXED

    def test_event_override_changes_classification(self, app, seed_workflow_data):
        """An event override flips a standard account to hotel, but only for
        that event cycle; passing None still reads the base account."""
        from app.routes.admin_final.line_pricing import (
            classify_account, KIND_HOTEL, KIND_STANDARD,
        )
        cycle = seed_workflow_data["cycle"]
        acct = _account("HTL_SEASONAL", is_fixed=False)
        override = ExpenseAccountEventOverride(
            expense_account_id=acct.id, event_cycle_id=cycle.id,
            is_fixed_cost=True, ui_display_group=UI_GROUP_HOTEL_SERVICES,
        )
        db.session.add(override)
        db.session.commit()

        assert classify_account(acct, cycle.id) == KIND_HOTEL
        assert classify_account(acct, None) == KIND_STANDARD


class TestBuildAccountPricingMap:
    def test_carries_kind_and_default_price(self, app):
        from app.routes.admin_final.line_pricing import (
            build_account_pricing_map, KIND_HOTEL,
        )
        acct = _account("HTL_KING_CRASH", True, UI_GROUP_HOTEL_SERVICES, 15900)
        result = build_account_pricing_map([acct], None)
        entry = result[acct.id]
        assert entry["kind"] == KIND_HOTEL
        assert entry["default_unit_price_cents"] == 15900
        assert "default_frequency_id" in entry
        assert "warehouse_default" in entry


class TestStripRoomsPrefix:
    def test_removes_prefix(self, app):
        from app.routes.admin_final.line_pricing import strip_rooms_prefix
        assert strip_rooms_prefix("3 rooms: Guest suite") == "Guest suite"

    def test_leaves_plain_text_alone(self, app):
        from app.routes.admin_final.line_pricing import strip_rooms_prefix
        assert strip_rooms_prefix("Guest suite") == "Guest suite"

    def test_handles_none(self, app):
        from app.routes.admin_final.line_pricing import strip_rooms_prefix
        assert strip_rooms_prefix(None) == ""


class TestResolveLinePricing:
    def test_hotel_multiplies_rooms_by_nights_and_prefixes(self, app):
        from app.routes.admin_final.line_pricing import resolve_line_pricing
        acct = _account("HTL_DOUBLE_MAGPAID", True, UI_GROUP_HOTEL_SERVICES, 15900)
        values, errors = resolve_line_pricing(acct, None, {
            "rooms": "2", "nights": "3", "description": "Guest of honor",
        })
        assert errors == []
        assert values["quantity"] == 6
        assert values["description"] == "2 rooms: Guest of honor"
        assert values["unit_price_cents"] == 15900

    def test_prefix_application_is_idempotent(self, app):
        from app.routes.admin_final.line_pricing import resolve_line_pricing
        acct = _account("HTL_KING_MAGPAID", True, UI_GROUP_HOTEL_SERVICES, 15900)
        values, errors = resolve_line_pricing(acct, None, {
            "rooms": "3", "nights": "1", "description": "2 rooms: Guest of honor",
        })
        assert errors == []
        assert values["description"] == "3 rooms: Guest of honor"

    def test_single_room_gets_no_prefix(self, app):
        from app.routes.admin_final.line_pricing import resolve_line_pricing
        acct = _account("HTL_SINGLE_CRASH", True, UI_GROUP_HOTEL_SERVICES, 15900)
        values, errors = resolve_line_pricing(acct, None, {
            "rooms": "1", "nights": "4", "description": "Crash space",
        })
        assert errors == []
        assert values["description"] == "Crash space"
        assert values["quantity"] == 4

    def test_hotel_empty_description_gets_no_trailing_space(self, app):
        from app.routes.admin_final.line_pricing import resolve_line_pricing
        acct = _account("HTL_DOUBLE_HELD", True, UI_GROUP_HOTEL_SERVICES, 15900)
        values, errors = resolve_line_pricing(acct, None, {
            "rooms": "3", "nights": "2", "description": "",
        })
        assert values["description"] == "3 rooms:"

    def test_fixed_uses_default_when_override_absent(self, app):
        from app.routes.admin_final.line_pricing import resolve_line_pricing
        acct = _account("ETH_DROP", True, None, 25000)
        values, errors = resolve_line_pricing(acct, None, {
            "quantity": "2", "unit_price": "999.00", "description": "Drop",
        })
        assert errors == []
        assert values["unit_price_cents"] == 25000
        assert values["account_default_unit_price_cents"] == 25000

    def test_fixed_honors_override_when_present(self, app):
        from app.routes.admin_final.line_pricing import resolve_line_pricing
        acct = _account("ETH_DROP_2", True, None, 25000)
        values, errors = resolve_line_pricing(acct, None, {
            "quantity": "1", "unit_price": "310.50",
            "price_override": "on", "description": "Long run",
        })
        assert errors == []
        assert values["unit_price_cents"] == 31050
        assert values["account_default_unit_price_cents"] == 25000

    def test_standard_takes_form_price_and_stores_no_snapshot(self, app):
        from app.routes.admin_final.line_pricing import resolve_line_pricing
        acct = _account("STD_MISC", is_fixed=False)
        values, errors = resolve_line_pricing(acct, None, {
            "quantity": "3", "unit_price": "75.00", "description": "Supplies",
        })
        assert errors == []
        assert values["unit_price_cents"] == 7500
        assert values["account_default_unit_price_cents"] is None

    def test_hotel_rejects_zero_rooms(self, app):
        from app.routes.admin_final.line_pricing import resolve_line_pricing
        acct = _account("HTL_DOUBLE_CRASH", True, UI_GROUP_HOTEL_SERVICES, 15900)
        values, errors = resolve_line_pricing(acct, None, {
            "rooms": "0", "nights": "3", "description": "Bad",
        })
        assert any("Rooms" in e for e in errors)
        # Rooms falls back to 1 on the error path; pin the resulting quantity so
        # a caller cannot mistake this plausible-looking 3 for a valid booking.
        assert values["quantity"] == 3

    def test_negative_price_rejected(self, app):
        from app.routes.admin_final.line_pricing import resolve_line_pricing
        acct = _account("STD_NEG", is_fixed=False)
        values, errors = resolve_line_pricing(acct, None, {
            "quantity": "1", "unit_price": "-5.00", "description": "Bad",
        })
        assert any("negative" in e.lower() for e in errors)

    def test_badge_uses_default_price(self, app):
        from app.routes.admin_final.line_pricing import (
            resolve_line_pricing, KIND_BADGE,
        )
        acct = _account("BADGE_STAFF", True, UI_GROUP_BADGES, 0)
        values, errors = resolve_line_pricing(acct, None, {
            "quantity": "1", "description": "Staff badge",
        })
        assert errors == []
        assert values["kind"] == KIND_BADGE
        assert values["unit_price_cents"] == 0
        assert values["account_default_unit_price_cents"] == 0

    def test_price_override_requires_exact_literal_on(self, app):
        """Only "on" (an HTML checkbox's default submitted value) triggers the
        override. A future template rendering value="1" must not silently
        disable overrides without a visible test failure."""
        from app.routes.admin_final.line_pricing import resolve_line_pricing
        acct = _account("ETH_DROP_3", True, None, 25000)
        values, errors = resolve_line_pricing(acct, None, {
            "quantity": "1", "unit_price": "310.50",
            "price_override": "1", "description": "Long run",
        })
        assert errors == []
        assert values["unit_price_cents"] == 25000

    def test_description_crlf_normalized_to_lf(self, app):
        from app.routes.admin_final.line_pricing import resolve_line_pricing
        acct = _account("STD_CRLF", is_fixed=False)
        values, errors = resolve_line_pricing(acct, None, {
            "quantity": "1", "unit_price": "1.00",
            "description": "Line one\r\nLine two\r\nLine three",
        })
        assert "\r" not in values["description"]
        assert values["description"] == "Line one\nLine two\nLine three"

    def test_quantity_nan_is_a_validation_error_not_a_crash(self, app):
        from app.routes.admin_final.line_pricing import resolve_line_pricing
        acct = _account("STD_NAN_QTY", is_fixed=False)
        values, errors = resolve_line_pricing(acct, None, {
            "quantity": "nan", "unit_price": "1.00", "description": "Bad",
        })
        assert any("quantity" in e.lower() for e in errors)

    def test_quantity_infinity_is_a_validation_error_not_a_crash(self, app):
        from app.routes.admin_final.line_pricing import resolve_line_pricing
        acct = _account("STD_INF_QTY", is_fixed=False)
        values, errors = resolve_line_pricing(acct, None, {
            "quantity": "Infinity", "unit_price": "1.00", "description": "Bad",
        })
        assert any("quantity" in e.lower() for e in errors)

    def test_unit_price_nan_is_a_validation_error_not_a_crash(self, app):
        from app.routes.admin_final.line_pricing import resolve_line_pricing
        acct = _account("STD_NAN_PRICE", is_fixed=False)
        values, errors = resolve_line_pricing(acct, None, {
            "quantity": "1", "unit_price": "nan", "description": "Bad",
        })
        assert any("unit price" in e.lower() for e in errors)

    def test_unit_price_infinity_is_a_validation_error_not_a_crash(self, app):
        from app.routes.admin_final.line_pricing import resolve_line_pricing
        acct = _account("STD_INF_PRICE", is_fixed=False)
        values, errors = resolve_line_pricing(acct, None, {
            "quantity": "1", "unit_price": "Infinity", "description": "Bad",
        })
        assert any("unit price" in e.lower() for e in errors)
