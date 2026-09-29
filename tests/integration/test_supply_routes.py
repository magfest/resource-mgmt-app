"""
Tests for the SUPPLY work-type portfolio landing route (Task 7).

Mirrors the harness in tests/integration/test_route_migration.py: seeds an
active SUPPLY work type + config on top of seed_workflow_data, then hits
the portfolio landing URL directly.
"""
import re

from app import db
from app.models import (
    SupplyCategory,
    SupplyItem,
    SupplyOrderDetail,
    SupplyOrderLineDetail,
    WorkItem,
    WorkLine,
    WorkPortfolio,
    WorkType,
    WorkTypeConfig,
    REQUEST_KIND_PRIMARY,
    ROUTING_STRATEGY_CATEGORY,
    WORK_ITEM_STATUS_DRAFT,
    WORK_ITEM_STATUS_SUBMITTED,
    WORK_LINE_STATUS_NEEDS_ADJUSTMENT,
    WORK_LINE_STATUS_PENDING,
)


def _login(client, user_id):
    """Set the session to simulate a logged-in user."""
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def _seed_supply(seed_workflow_data):
    """Add an active SUPPLY work type + config to the seeded data.

    is_active=True here is TEST-SEED-ONLY (the app's bootstrap seed keeps
    SUPPLY inactive until Task 15 activation) so the portfolio landing
    route can be exercised end to end before the work type goes live.
    """
    wt = WorkType(code="SUPPLY", name="Supply Orders", is_active=True)
    db.session.add(wt)
    db.session.flush()
    config = WorkTypeConfig(
        work_type_id=wt.id,
        url_slug="supply",
        public_id_prefix="SUP",
        line_detail_type="supply",
        routing_strategy=ROUTING_STRATEGY_CATEGORY,
        supports_supplementary=False,
        supports_fixed_costs=False,
        uses_dispatch=False,
        has_admin_final=True,
        item_singular="Supply Order",
        item_plural="Supply Orders",
        line_singular="Item",
        line_plural="Items",
    )
    db.session.add(config)
    db.session.commit()
    return wt


class TestSupplyPortfolioLanding:
    """GET /<event>/<dept>/supply renders the real supply portfolio landing."""

    def test_supply_portfolio_landing_renders(self, app, client, seed_workflow_data):
        _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        _login(client, "test:admin")

        response = client.get(f"/{cycle.code}/{dept.code}/supply")

        assert response.status_code == 200
        assert b"Start new order" in response.data

    def test_supply_route_bypasses_coming_soon(self, app, client, seed_workflow_data):
        """With the SUPPLY entry removed from _COMING_SOON_DETAILS, the
        literal /supply route must answer with the real landing page, not
        the coming-soon placeholder copy."""
        _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        _login(client, "test:admin")

        response = client.get(f"/{cycle.code}/{dept.code}/supply")

        assert response.status_code == 200
        assert b"Coming Soon" not in response.data

    def test_start_new_order_still_shown_with_existing_order(
        self, app, client, seed_workflow_data
    ):
        """Supply is a repeat-ordering work type: every order is PRIMARY and
        departments may place unlimited orders. The 'Start new order' CTA
        must therefore remain visible after the first order exists (i.e. it
        is gated on can_edit, not the engine's single-PRIMARY gate)."""
        wt = _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]

        portfolio = WorkPortfolio(
            work_type_id=wt.id,
            event_cycle_id=cycle.id,
            department_id=dept.id,
            created_by_user_id="test:admin",
        )
        db.session.add(portfolio)
        db.session.flush()
        order = WorkItem(
            portfolio_id=portfolio.id,
            request_kind=REQUEST_KIND_PRIMARY,
            status=WORK_ITEM_STATUS_DRAFT,
            public_id="TST2026-TESTDEPT-SUP-1",
            created_by_user_id="test:admin",
        )
        db.session.add(order)
        db.session.commit()

        _login(client, "test:admin")
        response = client.get(f"/{cycle.code}/{dept.code}/supply")

        assert response.status_code == 200
        assert b"TST2026-TESTDEPT-SUP-1" in response.data
        assert b"Start new order" in response.data

    def test_order_cards_link_to_supply_order_detail(
        self, app, client, seed_workflow_data
    ):
        """Card title/View links must target the supply cab's own order
        detail route (/supply/order/<public_id>), NOT the generic
        work.work_item_detail endpoint — for supply there is no literal
        /supply/item/... route, so the generic URL falls through to the
        BUDGET detail handler/template (totals, budget edit flow)."""
        wt = _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]

        portfolio = WorkPortfolio(
            work_type_id=wt.id,
            event_cycle_id=cycle.id,
            department_id=dept.id,
            created_by_user_id="test:admin",
        )
        db.session.add(portfolio)
        db.session.flush()
        order = WorkItem(
            portfolio_id=portfolio.id,
            request_kind=REQUEST_KIND_PRIMARY,
            status=WORK_ITEM_STATUS_DRAFT,
            public_id="TST2026-TESTDEPT-SUP-1",
            created_by_user_id="test:admin",
        )
        db.session.add(order)
        db.session.commit()

        _login(client, "test:admin")
        response = client.get(f"/{cycle.code}/{dept.code}/supply")

        assert response.status_code == 200
        expected = (
            f"/{cycle.code}/{dept.code}/supply/order/TST2026-TESTDEPT-SUP-1"
        )
        assert expected.encode() in response.data
        assert b"/supply/item/" not in response.data


class TestSupplyOrderNew(object):
    """POST /<event>/<dept>/supply/order/new starts a draft order (the cart)."""

    def test_supply_order_new_creates_draft_and_redirects(
        self, app, client, seed_workflow_data
    ):
        """Creation is gated on require_portfolio_edit + can_edit (NOT
        perms.can_create_primary — supply allows unlimited independent
        PRIMARY orders). As of Task 9 the redirect target is
        work.supply_catalog, landing the requester straight in the catalog
        to start filling their cart."""
        _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        _login(client, "test:admin")

        response = client.post(f"/{cycle.code}/{dept.code}/supply/order/new")

        assert response.status_code == 302

        work_item = WorkItem.query.filter(
            WorkItem.public_id.like("%-SUP-1")
        ).first()
        assert work_item is not None
        assert work_item.status == WORK_ITEM_STATUS_DRAFT
        assert work_item.request_kind == REQUEST_KIND_PRIMARY

        order_detail = SupplyOrderDetail.query.filter_by(
            work_item_id=work_item.id
        ).first()
        assert order_detail is not None

        expected_url = (
            f"/{cycle.code}/{dept.code}/supply/order/{work_item.public_id}/catalog"
        )
        assert response.headers["Location"].endswith(expected_url)


class TestSupplyOrderDetail(object):
    """GET /<event>/<dept>/supply/order/<public_id> — the cart/order view."""

    def test_supply_order_detail_shows_empty_cart(
        self, app, client, seed_workflow_data
    ):
        wt = _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]

        portfolio = WorkPortfolio(
            work_type_id=wt.id,
            event_cycle_id=cycle.id,
            department_id=dept.id,
            created_by_user_id="test:admin",
        )
        db.session.add(portfolio)
        db.session.flush()
        work_item = WorkItem(
            portfolio_id=portfolio.id,
            request_kind=REQUEST_KIND_PRIMARY,
            status=WORK_ITEM_STATUS_DRAFT,
            public_id="TST2026-TESTDEPT-SUP-1",
            created_by_user_id="test:admin",
        )
        db.session.add(work_item)
        db.session.flush()
        db.session.add(SupplyOrderDetail(
            work_item_id=work_item.id,
            created_by_user_id="test:admin",
        ))
        db.session.commit()

        _login(client, "test:admin")
        response = client.get(
            f"/{cycle.code}/{dept.code}/supply/order/{work_item.public_id}"
        )

        assert response.status_code == 200
        assert work_item.public_id.encode() in response.data
        assert b"No items yet" in response.data

    def test_draft_order_detail_renders_edit_widgets(
        self, app, client, seed_workflow_data
    ):
        """A DRAFT order with can_edit renders per-row Save/Remove forms
        and a pickup-details form (Task 10 template widgets)."""
        wt = _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        work_item = _make_draft_order(wt, cycle, dept)
        category, popular_item, plain_item = _seed_catalog()
        _add_line(work_item, plain_item, quantity=2, notes="for tech booth")

        _login(client, "test:admin")
        response = client.get(
            f"/{cycle.code}/{dept.code}/supply/order/{work_item.public_id}"
        )

        assert response.status_code == 200
        assert b"Save" in response.data
        assert b"Remove" in response.data
        assert b'<select name="pickup_time">' in response.data
        assert b"Save pickup details" in response.data

    def test_submitted_order_detail_renders_read_only(
        self, app, client, seed_workflow_data
    ):
        """A non-DRAFT order renders the cart/pickup details read-only —
        no edit widgets."""
        wt = _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        work_item = _make_draft_order(wt, cycle, dept)
        category, popular_item, plain_item = _seed_catalog()
        _add_line(work_item, plain_item, quantity=2, notes="for tech booth")
        work_item.status = WORK_ITEM_STATUS_SUBMITTED
        db.session.commit()

        _login(client, "test:admin")
        response = client.get(
            f"/{cycle.code}/{dept.code}/supply/order/{work_item.public_id}"
        )

        assert response.status_code == 200
        assert b"Save pickup details" not in response.data
        assert b'<select name="pickup_time">' not in response.data

    def test_kicked_back_line_reopens_edit_form_on_submitted_order(
        self, app, client, seed_workflow_data
    ):
        """A SUBMITTED order with one kicked-back line (NEEDS_ADJUSTMENT +
        needs_requester_action) must render the qty/notes edit form for
        that line only — the normal submitted line stays read-only and no
        Remove button appears (delete is DRAFT-only)."""
        wt = _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        work_item = _make_draft_order(wt, cycle, dept)
        category, popular_item, plain_item = _seed_catalog()
        kicked = _add_line(
            work_item, plain_item, quantity=3, notes="original",
            line_number=1,
            status=WORK_LINE_STATUS_NEEDS_ADJUSTMENT, needs_requester_action=True,
        )
        normal = _add_line(work_item, popular_item, quantity=2, line_number=2)
        work_item.status = WORK_ITEM_STATUS_SUBMITTED
        db.session.commit()

        _login(client, "test:admin")
        response = client.get(
            f"/{cycle.code}/{dept.code}/supply/order/{work_item.public_id}"
        )

        assert response.status_code == 200
        # Edit form present for the kicked-back line only
        assert f"/lines/{kicked.line_number}/update".encode() in response.data
        assert f"/lines/{normal.line_number}/update".encode() not in response.data
        # Visual cue on the kicked-back row
        assert b"Needs adjustment" in response.data
        # Remove stays DRAFT-only — no delete forms anywhere on this page
        assert b"/delete" not in response.data


def _make_draft_order(wt, cycle, dept):
    """Create a portfolio + DRAFT supply order WorkItem (the cart)."""
    portfolio = WorkPortfolio(
        work_type_id=wt.id,
        event_cycle_id=cycle.id,
        department_id=dept.id,
        created_by_user_id="test:admin",
    )
    db.session.add(portfolio)
    db.session.flush()
    work_item = WorkItem(
        portfolio_id=portfolio.id,
        request_kind=REQUEST_KIND_PRIMARY,
        status=WORK_ITEM_STATUS_DRAFT,
        public_id="TST2026-TESTDEPT-SUP-1",
        created_by_user_id="test:admin",
    )
    db.session.add(work_item)
    db.session.flush()
    db.session.add(SupplyOrderDetail(
        work_item_id=work_item.id,
        created_by_user_id="test:admin",
    ))
    db.session.commit()
    return work_item


def _seed_catalog():
    """Seed one active category with a popular item and a plain item."""
    category = SupplyCategory(
        code="OFFICE", name="Office Supplies", is_active=True, sort_order=1,
    )
    db.session.add(category)
    db.session.flush()

    popular_item = SupplyItem(
        category_id=category.id,
        item_name="Gaffer Tape",
        unit="roll",
        is_active=True,
        is_popular=True,
    )
    plain_item = SupplyItem(
        category_id=category.id,
        item_name="Sharpie Markers",
        unit="each",
        is_active=True,
        is_popular=False,
    )
    db.session.add_all([popular_item, plain_item])
    db.session.commit()
    return category, popular_item, plain_item


class TestSupplyCatalog:
    """GET/POST /<event>/<dept>/supply/order/<public_id>/catalog and
    .../lines/add — browsing the item catalog and adding to the cart."""

    def test_catalog_shows_in_cart_badge_single_line(
        self, app, client, seed_workflow_data
    ):
        """An item already in the cart shows 'In order ×<qty>' on its row."""
        wt = _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        work_item = _make_draft_order(wt, cycle, dept)
        category, popular_item, plain_item = _seed_catalog()
        _add_line(work_item, plain_item, quantity=12, notes="to power things")

        _login(client, "test:admin")
        response = client.get(
            f"/{cycle.code}/{dept.code}/supply/order/{work_item.public_id}/catalog"
        )

        assert response.status_code == 200
        assert "In order ×12".encode("utf-8") in response.data

    def test_catalog_shows_lines_and_qty_for_multiple_lines(
        self, app, client, seed_workflow_data
    ):
        """Duplicate adds are separate lines; the badge sums them."""
        wt = _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        work_item = _make_draft_order(wt, cycle, dept)
        category, popular_item, plain_item = _seed_catalog()
        _add_line(work_item, plain_item, quantity=1, notes="tech booth", line_number=1)
        _add_line(work_item, plain_item, quantity=12, notes="registration", line_number=2)

        _login(client, "test:admin")
        response = client.get(
            f"/{cycle.code}/{dept.code}/supply/order/{work_item.public_id}/catalog"
        )

        assert response.status_code == 200
        assert b"2 lines" in response.data
        assert b"qty 13" in response.data

    def test_catalog_no_badge_when_item_not_in_cart(
        self, app, client, seed_workflow_data
    ):
        wt = _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        work_item = _make_draft_order(wt, cycle, dept)
        _seed_catalog()

        _login(client, "test:admin")
        response = client.get(
            f"/{cycle.code}/{dept.code}/supply/order/{work_item.public_id}/catalog"
        )

        assert response.status_code == 200
        assert b"In order" not in response.data

    def test_catalog_badge_renders_once_for_popular_item(
        self, app, client, seed_workflow_data
    ):
        """A popular item renders once, in its category, with its badge."""
        wt = _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        work_item = _make_draft_order(wt, cycle, dept)
        category, popular_item, plain_item = _seed_catalog()
        _add_line(work_item, popular_item, quantity=5, notes="for panels")

        _login(client, "test:admin")
        response = client.get(
            f"/{cycle.code}/{dept.code}/supply/order/{work_item.public_id}/catalog"
        )

        assert response.status_code == 200
        assert response.data.count("In order ×5".encode("utf-8")) == 1

    def test_catalog_renders_item_category_and_popular(
        self, app, client, seed_workflow_data
    ):
        wt = _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        work_item = _make_draft_order(wt, cycle, dept)
        category, popular_item, plain_item = _seed_catalog()

        _login(client, "test:admin")
        response = client.get(
            f"/{cycle.code}/{dept.code}/supply/order/{work_item.public_id}/catalog"
        )

        assert response.status_code == 200
        assert category.name.encode() in response.data
        assert plain_item.item_name.encode() in response.data
        assert popular_item.item_name.encode() in response.data
        assert b"<h2>Popular</h2>" not in response.data
        assert b"Commonly ordered" in response.data

    def test_add_item_creates_line_and_redirects(
        self, app, client, seed_workflow_data
    ):
        wt = _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        work_item = _make_draft_order(wt, cycle, dept)
        category, popular_item, plain_item = _seed_catalog()

        _login(client, "test:admin")
        response = client.post(
            f"/{cycle.code}/{dept.code}/supply/order/{work_item.public_id}/lines/add",
            data={
                "item_id": str(plain_item.id),
                "quantity": "3",
                "notes": "for tech booth",
            },
        )

        assert response.status_code == 302
        assert response.headers["Location"].endswith(f"#item-{plain_item.id}")

        details = SupplyOrderLineDetail.query.filter_by(item_id=plain_item.id).all()
        assert len(details) == 1
        assert details[0].quantity_requested == 3
        assert details[0].requester_notes == "for tech booth"

    def test_add_same_item_twice_creates_two_lines(
        self, app, client, seed_workflow_data
    ):
        """Duplicate adds of the same item are intentional — never merge or
        dedupe. Requesters distinguish duplicates via per-line notes."""
        wt = _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        work_item = _make_draft_order(wt, cycle, dept)
        category, popular_item, plain_item = _seed_catalog()

        _login(client, "test:admin")
        add_url = (
            f"/{cycle.code}/{dept.code}/supply/order/{work_item.public_id}/lines/add"
        )
        client.post(add_url, data={"item_id": str(plain_item.id), "quantity": "1"})
        client.post(add_url, data={"item_id": str(plain_item.id), "quantity": "2"})

        details = SupplyOrderLineDetail.query.filter_by(item_id=plain_item.id).all()
        assert len(details) == 2
        quantities = sorted(d.quantity_requested for d in details)
        assert quantities == [1, 2]


def _add_line(work_item, item, quantity=1, notes=None, line_number=None, status=WORK_LINE_STATUS_PENDING, needs_requester_action=False):
    """Add a WorkLine + SupplyOrderLineDetail directly to an order (bypasses
    the catalog add-to-cart route so tests can control status/flags)."""
    if line_number is None:
        line_number = 1 + max((l.line_number for l in work_item.lines), default=0)
    line = WorkLine(
        work_item_id=work_item.id,
        line_number=line_number,
        status=status,
        needs_requester_action=needs_requester_action,
    )
    db.session.add(line)
    db.session.flush()
    db.session.add(SupplyOrderLineDetail(
        work_line_id=line.id,
        item_id=item.id,
        quantity_requested=quantity,
        requester_notes=notes,
    ))
    db.session.commit()
    return line


class TestSupplyCatalogBrowse:
    """GET /<event>/<dept>/supply/catalog — standalone view-only browse
    (Task 16). No order needs to exist; add-to-cart forms are hidden."""

    def test_browse_shows_item_and_category_description_without_order(
        self, app, client, seed_workflow_data
    ):
        _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]

        category = SupplyCategory(
            code="OFFICE", name="Office Supplies", is_active=True, sort_order=1,
            description="Ask FestOps before ordering bulk paper here.",
        )
        db.session.add(category)
        db.session.flush()
        item = SupplyItem(
            category_id=category.id, item_name="Sharpie Markers", unit="each",
            is_active=True,
        )
        db.session.add(item)
        db.session.commit()

        _login(client, "test:admin")
        response = client.get(f"/{cycle.code}/{dept.code}/supply/catalog")

        assert response.status_code == 200
        assert item.item_name.encode() in response.data
        assert b"Ask FestOps before ordering bulk paper here." in response.data
        # No add-to-cart form anywhere on the page (can_add=False, work_item=None).
        assert b'name="item_id"' not in response.data
        assert b'name="quantity"' not in response.data


class TestSupplyCatalogGuidanceAndReturnBadge:
    """The in-order catalog surfaces the return policy and order_guidance
    hint (Task 16 comprehension-first revision)."""

    def test_catalog_shows_must_be_returned_and_order_guidance(
        self, app, client, seed_workflow_data
    ):
        wt = _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        work_item = _make_draft_order(wt, cycle, dept)

        category = SupplyCategory(code="SIGNAGE", name="Signage", is_active=True, sort_order=1)
        db.session.add(category)
        db.session.flush()
        returnable_item = SupplyItem(
            category_id=category.id, item_name="Easel Stand", unit="each",
            is_active=True, is_expendable=False,
        )
        guided_item = SupplyItem(
            category_id=category.id, item_name="Gaffer Tape", unit="roll",
            is_active=True, is_expendable=True,
            order_guidance="1 roll covers roughly one booth setup",
        )
        db.session.add_all([returnable_item, guided_item])
        db.session.commit()

        _login(client, "test:admin")
        response = client.get(
            f"/{cycle.code}/{dept.code}/supply/order/{work_item.public_id}/catalog"
        )

        assert response.status_code == 200
        assert b"Must be returned" in response.data
        assert b"1 roll covers roughly one booth setup" in response.data


class TestSupplyLineUpdate(object):
    """POST /<event>/<dept>/supply/order/<public_id>/lines/<line_number>/update"""

    def test_update_line_persists_qty_and_notes(self, app, client, seed_workflow_data):
        wt = _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        work_item = _make_draft_order(wt, cycle, dept)
        category, popular_item, plain_item = _seed_catalog()
        line = _add_line(work_item, plain_item, quantity=3, notes="original notes")

        _login(client, "test:admin")
        response = client.post(
            f"/{cycle.code}/{dept.code}/supply/order/{work_item.public_id}"
            f"/lines/{line.line_number}/update",
            data={"quantity": "5", "notes": "updated notes"},
        )

        assert response.status_code == 302
        detail = SupplyOrderLineDetail.query.filter_by(work_line_id=line.id).first()
        assert detail.quantity_requested == 5
        assert detail.requester_notes == "updated notes"

    def test_update_rejected_on_submitted_order(self, app, client, seed_workflow_data):
        """A plain SUBMITTED order (line not kicked back) must reject line
        edits — not DRAFT, and the line has no needs_requester_action
        exception to fall back on."""
        wt = _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        work_item = _make_draft_order(wt, cycle, dept)
        category, popular_item, plain_item = _seed_catalog()
        line = _add_line(work_item, plain_item, quantity=3, notes="original notes")
        work_item.status = WORK_ITEM_STATUS_SUBMITTED
        db.session.commit()

        _login(client, "test:admin")
        response = client.post(
            f"/{cycle.code}/{dept.code}/supply/order/{work_item.public_id}"
            f"/lines/{line.line_number}/update",
            data={"quantity": "5", "notes": "updated notes"},
        )

        assert response.status_code == 403

        detail = SupplyOrderLineDetail.query.filter_by(work_line_id=line.id).first()
        assert detail.quantity_requested == 3
        assert detail.requester_notes == "original notes"

    def test_update_allowed_on_kicked_back_line_even_when_not_draft(
        self, app, client, seed_workflow_data
    ):
        """A line flagged needs_requester_action + NEEDS_ADJUSTMENT is
        editable even though the parent order is SUBMITTED (kickback
        exception) — this is the one predicate the brief calls out
        explicitly as needing to mirror the engine's respond flow."""
        wt = _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        work_item = _make_draft_order(wt, cycle, dept)
        category, popular_item, plain_item = _seed_catalog()
        line = _add_line(
            work_item, plain_item, quantity=3, notes="original notes",
            status=WORK_LINE_STATUS_NEEDS_ADJUSTMENT, needs_requester_action=True,
        )
        work_item.status = WORK_ITEM_STATUS_SUBMITTED
        db.session.commit()

        _login(client, "test:admin")
        response = client.post(
            f"/{cycle.code}/{dept.code}/supply/order/{work_item.public_id}"
            f"/lines/{line.line_number}/update",
            data={"quantity": "5", "notes": "fixed per feedback"},
        )

        assert response.status_code == 302
        detail = SupplyOrderLineDetail.query.filter_by(work_line_id=line.id).first()
        assert detail.quantity_requested == 5
        assert detail.requester_notes == "fixed per feedback"


class TestSupplyLineDelete(object):
    """POST /<event>/<dept>/supply/order/<public_id>/lines/<line_number>/delete"""

    def test_delete_line_removes_it_without_renumbering(self, app, client, seed_workflow_data):
        wt = _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        work_item = _make_draft_order(wt, cycle, dept)
        category, popular_item, plain_item = _seed_catalog()
        line1 = _add_line(work_item, plain_item, quantity=1, line_number=1)
        line2 = _add_line(work_item, popular_item, quantity=2, line_number=2)

        _login(client, "test:admin")
        response = client.post(
            f"/{cycle.code}/{dept.code}/supply/order/{work_item.public_id}"
            f"/lines/{line1.line_number}/delete"
        )

        assert response.status_code == 302
        remaining = WorkLine.query.filter_by(work_item_id=work_item.id).all()
        assert len(remaining) == 1
        assert remaining[0].line_number == 2  # untouched — no renumbering
        assert SupplyOrderLineDetail.query.filter_by(work_line_id=line1.id).first() is None


class TestSupplyOrderDetailsSave(object):
    """POST /<event>/<dept>/supply/order/<public_id>/details"""

    def test_details_save_persists_pickup_time(self, app, client, seed_workflow_data):
        wt = _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        work_item = _make_draft_order(wt, cycle, dept)

        _login(client, "test:admin")
        response = client.post(
            f"/{cycle.code}/{dept.code}/supply/order/{work_item.public_id}/details",
            data={
                "pickup_time": "Tuesday Evening (after 6 PM)",
                "additional_notes": "",
            },
        )

        assert response.status_code == 302
        order_detail = SupplyOrderDetail.query.filter_by(work_item_id=work_item.id).first()
        assert order_detail.pickup_time == "Tuesday Evening (after 6 PM)"

    def test_details_save_rejects_unknown_pickup_time(self, app, client, seed_workflow_data):
        """Values outside PICKUP_TIME_OPTIONS are a form-tampering guard."""
        wt = _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        work_item = _make_draft_order(wt, cycle, dept)

        _login(client, "test:admin")
        response = client.post(
            f"/{cycle.code}/{dept.code}/supply/order/{work_item.public_id}/details",
            data={"pickup_time": "Whenever I feel like it", "additional_notes": ""},
        )

        assert response.status_code == 302
        order_detail = SupplyOrderDetail.query.filter_by(work_item_id=work_item.id).first()
        assert order_detail.pickup_time is None


class TestSupplyEndpointReferences:
    """The old work.supply_placeholder endpoint was deleted; everything
    that referenced it must point at work.supply_portfolio_landing."""

    def test_supply_placeholder_endpoint_is_gone(self, app):
        endpoints = {r.endpoint for r in app.url_map.iter_rules()}
        assert "work.supply_placeholder" not in endpoints
        assert "work.supply_portfolio_landing" in endpoints

    def test_department_home_renders_with_active_supply(
        self, app, client, seed_workflow_data
    ):
        """department_home.html builds a supply card URL via url_for on the
        supply endpoint — before the template repoint this raised BuildError
        (500) whenever SUPPLY was active. Rendering it end to end pins the
        fix."""
        _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        _login(client, "test:admin")

        response = client.get(f"/{cycle.code}/{dept.code}/")

        assert response.status_code == 200
        assert f"/{cycle.code}/{dept.code}/supply".encode() in response.data


class TestSupplyItemDetail:
    """GET /<event>/<dept>/supply/catalog/item/<item_id> — standalone item
    detail page (Task 17), replacing the rejected details popover."""

    def test_standalone_detail_shows_name_unit_return_policy_no_add_form(
        self, app, client, seed_workflow_data
    ):
        _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        category, popular_item, plain_item = _seed_catalog()

        _login(client, "test:admin")
        response = client.get(
            f"/{cycle.code}/{dept.code}/supply/catalog/item/{plain_item.id}"
        )

        assert response.status_code == 200
        assert plain_item.item_name.encode() in response.data
        assert b"each" in response.data
        assert b"Return any unused items after the event." in response.data
        # No add-to-cart form — no order context given.
        assert b'name="item_id"' not in response.data
        assert b'name="quantity"' not in response.data

    def test_detail_with_order_context_shows_add_form_and_can_add_line(
        self, app, client, seed_workflow_data
    ):
        wt = _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        work_item = _make_draft_order(wt, cycle, dept)
        category, popular_item, plain_item = _seed_catalog()

        _login(client, "test:admin")
        response = client.get(
            f"/{cycle.code}/{dept.code}/supply/catalog/item/{plain_item.id}"
            f"?order={work_item.public_id}"
        )

        assert response.status_code == 200
        assert b'name="item_id"' in response.data
        assert b'name="quantity"' in response.data

        post_response = client.post(
            f"/{cycle.code}/{dept.code}/supply/order/{work_item.public_id}/lines/add",
            data={
                "item_id": str(plain_item.id),
                "quantity": "2",
                "notes": "for tech booth",
            },
        )

        assert post_response.status_code == 302
        details = SupplyOrderLineDetail.query.filter_by(item_id=plain_item.id).all()
        assert len(details) == 1
        assert details[0].quantity_requested == 2

    def test_inactive_item_returns_404(self, app, client, seed_workflow_data):
        _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        category, popular_item, plain_item = _seed_catalog()
        plain_item.is_active = False
        db.session.commit()

        _login(client, "test:admin")
        response = client.get(
            f"/{cycle.code}/{dept.code}/supply/catalog/item/{plain_item.id}"
        )

        assert response.status_code == 404

    def test_catalog_page_has_no_popover_markup_and_links_to_detail(
        self, app, client, seed_workflow_data
    ):
        wt = _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        work_item = _make_draft_order(wt, cycle, dept)
        category, popular_item, plain_item = _seed_catalog()

        _login(client, "test:admin")
        response = client.get(
            f"/{cycle.code}/{dept.code}/supply/order/{work_item.public_id}/catalog"
        )

        assert response.status_code == 200
        assert b"popover" not in response.data
        expected_link = (
            f"/{cycle.code}/{dept.code}/supply/catalog/item/{plain_item.id}"
            f"?order={work_item.public_id}"
        ).encode()
        assert expected_link in response.data


class TestSupplyItemRouteRedirect:
    """GET /<event>/<dept>/supply/item/<public_id> must claim the literal
    URL Flask's matcher would otherwise route to BUDGET's generic
    /<work_type_slug>/item/... handler (approvals/_queue_table.html and
    approvals/dashboard.html build supply reviewer-queue links via
    url_for('work.work_item_detail', work_type_slug='supply', ...), which
    resolves to this exact URL string) and redirect to the real order
    detail route."""

    def test_supply_item_url_redirects_to_order_detail(
        self, app, client, seed_workflow_data
    ):
        wt = _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]

        portfolio = WorkPortfolio(
            work_type_id=wt.id,
            event_cycle_id=cycle.id,
            department_id=dept.id,
            created_by_user_id="test:admin",
        )
        db.session.add(portfolio)
        db.session.flush()
        order = WorkItem(
            portfolio_id=portfolio.id,
            request_kind=REQUEST_KIND_PRIMARY,
            status=WORK_ITEM_STATUS_DRAFT,
            public_id="TST2026-TESTDEPT-SUP-1",
            created_by_user_id="test:admin",
        )
        db.session.add(order)
        db.session.commit()

        _login(client, "test:admin")
        response = client.get(
            f"/{cycle.code}/{dept.code}/supply/item/TST2026-TESTDEPT-SUP-1"
        )

        assert response.status_code == 302
        assert response.headers["Location"].endswith(
            f"/{cycle.code}/{dept.code}/supply/order/TST2026-TESTDEPT-SUP-1"
        )


class TestSupplyCatalogSections:
    """One entry per item, grouped by category, with counts."""

    def _browse(self, client, seed_workflow_data, q=None):
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        _login(client, "test:admin")
        url = f"/{cycle.code}/{dept.code}/supply/catalog"
        return client.get(url, query_string={"q": q} if q else None)

    def test_each_item_renders_once_with_counts(self, app, client, seed_workflow_data):
        _seed_supply(seed_workflow_data)
        category, popular_item, plain_item = _seed_catalog()

        html = self._browse(client, seed_workflow_data).get_data(as_text=True)

        assert html.count(f'id="item-{popular_item.id}"') == 1
        assert html.count(f'id="item-{plain_item.id}"') == 1
        assert 'data-catalog-nav="OFFICE"' in html
        assert '<span class="catalog-count">2 items</span>' in html
        assert '<span class="catalog-total">2 items</span>' in html

    def test_inactive_category_items_are_hidden_and_uncounted(
        self, app, client, seed_workflow_data
    ):
        _seed_supply(seed_workflow_data)
        _seed_catalog()
        retired = SupplyCategory(code="OLD", name="Retired", is_active=False, sort_order=2)
        empty = SupplyCategory(code="EMPTY", name="Nothing Here", is_active=True, sort_order=3)
        db.session.add_all([retired, empty])
        db.session.flush()
        db.session.add(SupplyItem(category_id=retired.id, item_name="Fax Machine", unit="each", is_active=True))
        db.session.commit()

        html = self._browse(client, seed_workflow_data).get_data(as_text=True)

        assert "Fax Machine" not in html
        assert 'data-catalog-nav="OLD"' not in html
        assert 'data-catalog-nav="EMPTY"' not in html
        assert '<span class="catalog-total">2 items</span>' in html

    def test_search_counts_matches_only(self, app, client, seed_workflow_data):
        _seed_supply(seed_workflow_data)
        _seed_catalog()

        html = self._browse(client, seed_workflow_data, q="gaffer").get_data(as_text=True)

        assert "Gaffer Tape" in html
        assert "Sharpie Markers" not in html
        assert '<span class="catalog-count">1 item</span>' in html

    def test_search_with_no_match_shows_empty_state_and_no_nav(
        self, app, client, seed_workflow_data
    ):
        _seed_supply(seed_workflow_data)
        _seed_catalog()

        html = self._browse(client, seed_workflow_data, q="zzz").get_data(as_text=True)

        assert "No items match" in html
        assert "data-catalog-nav=" not in html
        # No empty menu area either: no nav element, and the list takes the full width.
        assert 'class="catalog-nav"' not in html
        assert 'catalog-layout--empty' in html

    def test_browse_rows_link_to_item_detail(self, app, client, seed_workflow_data):
        _seed_supply(seed_workflow_data)
        category, popular_item, plain_item = _seed_catalog()

        html = self._browse(client, seed_workflow_data).get_data(as_text=True)

        assert 'class="btn catalog-view"' in html
        assert f"/supply/catalog/item/{plain_item.id}" in html
        assert 'name="quantity"' not in html

    def test_submitted_order_rows_show_view_not_add_form(
        self, app, client, seed_workflow_data
    ):
        wt = _seed_supply(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        work_item = _make_draft_order(wt, cycle, dept)
        work_item.status = WORK_ITEM_STATUS_SUBMITTED
        db.session.commit()
        _seed_catalog()

        _login(client, "test:admin")
        html = client.get(
            f"/{cycle.code}/{dept.code}/supply/order/{work_item.public_id}/catalog"
        ).get_data(as_text=True)

        assert 'class="btn catalog-view"' in html
        assert 'name="quantity"' not in html

    def test_highlight_script_carries_the_nonce(self, app, client, seed_workflow_data):
        _seed_supply(seed_workflow_data)
        _seed_catalog()

        html = self._browse(client, seed_workflow_data).get_data(as_text=True)

        assert re.search(r'<script nonce="[^"]+">\s*// Catalog section highlight', html)


class TestSupplyItemDetailEditMode:
    """The item page edits the existing line instead of adding a second one."""

    def _item_page(self, client, seed_workflow_data, work_item, item):
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        _login(client, "test:admin")
        return client.get(
            f"/{cycle.code}/{dept.code}/supply/catalog/item/{item.id}"
            f"?order={work_item.public_id}"
        ).get_data(as_text=True)

    def test_item_on_one_line_prefills_and_posts_to_update(
        self, app, client, seed_workflow_data
    ):
        wt = _seed_supply(seed_workflow_data)
        work_item = _make_draft_order(wt, seed_workflow_data["cycle"], seed_workflow_data["department"])
        category, popular_item, plain_item = _seed_catalog()
        _add_line(work_item, plain_item, quantity=3, notes="for registration")

        html = self._item_page(client, seed_workflow_data, work_item, plain_item)

        assert f"/supply/order/{work_item.public_id}/lines/1/update" in html
        assert 'name="return_to" value="item"' in html
        assert 'value="3"' in html
        assert ">for registration</textarea>" in html
        assert "Save changes" in html

    def test_item_on_two_lines_links_to_order_page(
        self, app, client, seed_workflow_data
    ):
        wt = _seed_supply(seed_workflow_data)
        work_item = _make_draft_order(wt, seed_workflow_data["cycle"], seed_workflow_data["department"])
        category, popular_item, plain_item = _seed_catalog()
        _add_line(work_item, plain_item, quantity=1, notes="tech booth", line_number=1)
        _add_line(work_item, plain_item, quantity=2, notes="registration", line_number=2)

        html = self._item_page(client, seed_workflow_data, work_item, plain_item)

        assert "On your order as 2 lines" in html
        assert f'href="/{seed_workflow_data["cycle"].code}/{seed_workflow_data["department"].code}/supply/order/{work_item.public_id}"' in html
        assert 'name="quantity"' not in html

    def test_item_not_in_order_keeps_add_form(self, app, client, seed_workflow_data):
        wt = _seed_supply(seed_workflow_data)
        work_item = _make_draft_order(wt, seed_workflow_data["cycle"], seed_workflow_data["department"])
        category, popular_item, plain_item = _seed_catalog()

        html = self._item_page(client, seed_workflow_data, work_item, plain_item)

        assert f"/supply/order/{work_item.public_id}/lines/add" in html
        assert 'name="item_id"' in html
        assert "Add to order" in html
        assert "Save changes" not in html


class TestSupplyLineUpdateReturnTo:
    """supply_line_update sends the requester back where the edit started."""

    def _update(self, client, seed_workflow_data, work_item, data):
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        _login(client, "test:admin")
        return client.post(
            f"/{cycle.code}/{dept.code}/supply/order/{work_item.public_id}/lines/1/update",
            data=data,
        )

    def _setup(self, seed_workflow_data, notes_required=False):
        wt = _seed_supply(seed_workflow_data)
        work_item = _make_draft_order(wt, seed_workflow_data["cycle"], seed_workflow_data["department"])
        category, popular_item, plain_item = _seed_catalog()
        plain_item.notes_required = notes_required
        db.session.commit()
        _add_line(work_item, plain_item, quantity=1, notes="first")
        return work_item, plain_item

    def test_return_to_catalog_lands_on_the_row(self, app, client, seed_workflow_data):
        work_item, item = self._setup(seed_workflow_data)
        response = self._update(client, seed_workflow_data, work_item,
                                {"quantity": "4", "notes": "more", "return_to": "catalog"})
        assert response.status_code == 302
        assert response.headers["Location"].endswith(
            f"/supply/order/{work_item.public_id}/catalog#item-{item.id}")
        assert SupplyOrderLineDetail.query.one().quantity_requested == 4

    def test_return_to_item_lands_on_the_item_page(self, app, client, seed_workflow_data):
        work_item, item = self._setup(seed_workflow_data)
        response = self._update(client, seed_workflow_data, work_item,
                                {"quantity": "2", "notes": "", "return_to": "item"})
        assert response.headers["Location"].endswith(
            f"/supply/catalog/item/{item.id}?order={work_item.public_id}")

    def test_unknown_return_to_falls_back_to_order_page(self, app, client, seed_workflow_data):
        work_item, item = self._setup(seed_workflow_data)
        response = self._update(client, seed_workflow_data, work_item,
                                {"quantity": "2", "return_to": "https://example.com/"})
        location = response.headers["Location"]
        assert location.endswith(f"/supply/order/{work_item.public_id}")
        assert "example.com" not in location

    def test_validation_error_keeps_the_return_target(self, app, client, seed_workflow_data):
        work_item, item = self._setup(seed_workflow_data, notes_required=True)
        response = self._update(client, seed_workflow_data, work_item,
                                {"quantity": "5", "notes": "   ", "return_to": "catalog"})
        assert response.headers["Location"].endswith(f"#item-{item.id}")
        detail = SupplyOrderLineDetail.query.one()
        assert detail.quantity_requested == 1
        assert detail.requester_notes == "first"


def _row_html(html: str, item_id: int) -> str:
    """Return one catalog row's markup, stopping before the next row or the dialog."""
    start = html.index(f'id="item-{item_id}"')
    ends = [i for i in (html.find('class="card catalog-row"', start), html.find("<dialog", start)) if i != -1]
    return html[start:min(ends) if ends else len(html)]


class TestSupplyCatalogRowActions:
    """Rows carry one button; the dialog holds the only form."""

    def _order_catalog(self, client, seed_workflow_data, work_item):
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        _login(client, "test:admin")
        return client.get(
            f"/{cycle.code}/{dept.code}/supply/order/{work_item.public_id}/catalog"
        ).get_data(as_text=True)

    def _setup(self, seed_workflow_data):
        wt = _seed_supply(seed_workflow_data)
        work_item = _make_draft_order(wt, seed_workflow_data["cycle"], seed_workflow_data["department"])
        return work_item, _seed_catalog()

    def test_row_states_for_zero_one_and_two_lines(self, app, client, seed_workflow_data):
        work_item, (category, popular_item, plain_item) = self._setup(seed_workflow_data)
        third = SupplyItem(category_id=category.id, item_name="Zip Ties", unit="bag", is_active=True)
        db.session.add(third)
        db.session.commit()
        _add_line(work_item, popular_item, quantity=3, notes="stage", line_number=1)
        _add_line(work_item, third, quantity=1, notes="a", line_number=2)
        _add_line(work_item, third, quantity=1, notes="b", line_number=3)

        html = self._order_catalog(client, seed_workflow_data, work_item)

        none_row = _row_html(html, plain_item.id)
        assert "data-supply-open" in none_row and "Add to order" in none_row
        one_row = _row_html(html, popular_item.id)
        assert 'data-line-number="1"' in one_row and 'data-qty="3"' in one_row
        assert ">Edit</a>" in one_row
        two_row = _row_html(html, third.id)
        assert "Edit on order page" in two_row
        assert "data-supply-open" not in two_row

    def test_rows_have_no_inputs_and_one_dialog(self, app, client, seed_workflow_data):
        work_item, _ = self._setup(seed_workflow_data)
        html = self._order_catalog(client, seed_workflow_data, work_item)

        assert html.count("<dialog") == 1
        assert 'name="quantity"' not in html.split("<dialog")[0]

    def test_no_dialog_in_browse_or_submitted(self, app, client, seed_workflow_data):
        work_item, _ = self._setup(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        _login(client, "test:admin")
        browse = client.get(f"/{cycle.code}/{dept.code}/supply/catalog").get_data(as_text=True)
        work_item.status = WORK_ITEM_STATUS_SUBMITTED
        db.session.commit()
        submitted = self._order_catalog(client, seed_workflow_data, work_item)

        assert "<dialog" not in browse
        assert "<dialog" not in submitted

    def test_item_data_is_escaped_and_notes_keep_line_breaks(
        self, app, client, seed_workflow_data
    ):
        work_item, (category, popular_item, plain_item) = self._setup(seed_workflow_data)
        plain_item.item_name = 'Tape "Pro" <x>'
        db.session.commit()
        _add_line(work_item, plain_item, quantity=2, notes="line one\nline two")

        html = self._order_catalog(client, seed_workflow_data, work_item)

        assert 'data-name="Tape &#34;Pro&#34; &lt;x&gt;"' in html
        assert 'data-notes="line one\nline two"' in html

    def test_dialog_script_carries_the_nonce(self, app, client, seed_workflow_data):
        work_item, _ = self._setup(seed_workflow_data)
        html = self._order_catalog(client, seed_workflow_data, work_item)

        assert re.search(r'<script nonce="[^"]+">\s*// Supply add/edit dialog', html)
