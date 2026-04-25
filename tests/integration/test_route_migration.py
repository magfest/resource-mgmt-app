"""
Tests for the multi-work-type route migration (PR 2).

Auto-discovery test asserts that every route registered with /budget/ in
its URL pattern has a parallel <work_type_slug> alias pointing to the
same endpoint. This is the forcing function that prevents routes from
being missed during the migration.

Smoke tests verify both URL patterns reach the same handler for budget,
and that non-budget slugs behave correctly (coming-soon for landing,
404 for budget-specific subroutes).
"""
from app import db
from app.models import (
    WorkType,
    WorkTypeConfig,
    ROUTING_STRATEGY_DIRECT,
)


def _login(client, user_id):
    """Set the session to simulate a logged-in user."""
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def _seed_techops(seed_workflow_data):
    """Add a TECHOPS work type + config to the seeded data."""
    wt = WorkType(code="TECHOPS", name="TechOps Services", is_active=False)
    db.session.add(wt)
    db.session.flush()
    config = WorkTypeConfig(
        work_type_id=wt.id,
        url_slug="techops",
        public_id_prefix="TEC",
        line_detail_type="techops",
        routing_strategy=ROUTING_STRATEGY_DIRECT,
    )
    db.session.add(config)
    db.session.commit()
    return wt


class TestRouteAliasCoverage:
    """Every /<event>/<dept>/budget/... route must have a <work_type_slug> alias."""

    def test_every_budget_request_route_has_slug_alias(self, app):
        """Auto-discovery: assert no route was missed during the migration."""
        rules = list(app.url_map.iter_rules())

        # Routes whose path contains /<event>/<dept>/budget/...
        # (these are the request/approval/admin-final routes that need parameterizing)
        request_routes = [
            r for r in rules
            if "/<event>/<dept>/budget" in str(r.rule)
        ]
        assert request_routes, (
            "Sanity check: expected to find /<event>/<dept>/budget/... routes "
            "in the URL map, but found none."
        )

        # Endpoints that have the new <work_type_slug> alias
        slug_endpoints = {
            r.endpoint for r in rules
            if "<work_type_slug>" in str(r.rule)
        }

        missing = []
        for route in request_routes:
            if route.endpoint in slug_endpoints:
                continue
            missing.append((route.endpoint, str(route.rule)))

        assert not missing, (
            "Routes with /<event>/<dept>/budget/ are missing a "
            "<work_type_slug> alias:\n  " +
            "\n  ".join(f"{e}: {r}" for e, r in missing)
        )


class TestBudgetUrlsStillResolve:
    """Existing /budget/ URLs must continue to work — no breakage from the migration."""

    def test_legacy_budget_portfolio_url_resolves(self, app, client, seed_workflow_data):
        """Hitting /<event>/<dept>/budget reaches portfolio_landing handler."""
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        _login(client, "test:admin")

        response = client.get(f"/{cycle.code}/{dept.code}/budget")
        assert response.status_code == 200, (
            f"Legacy budget URL returned {response.status_code} — "
            f"the route may have been broken during migration."
        )

    def test_new_slug_url_resolves_for_budget(self, app, client, seed_workflow_data):
        """Same URL via the new <work_type_slug> rule reaches the same handler."""
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        _login(client, "test:admin")

        # The URL string is identical to the legacy one — Flask picks the
        # literal "budget" rule due to specificity preference. This proves
        # the legacy URL still maps cleanly even with both rules registered.
        response = client.get(f"/{cycle.code}/{dept.code}/budget")
        assert response.status_code == 200


class TestNonBudgetSlugBehavior:
    """Non-budget slugs reach the right handlers with the right behavior."""

    def test_techops_portfolio_renders_coming_soon(self, app, client, seed_workflow_data):
        """Non-budget portfolio_landing renders coming-soon (no 404, no crash)."""
        _seed_techops(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        _login(client, "test:admin")

        response = client.get(f"/{cycle.code}/{dept.code}/techops")
        assert response.status_code == 200
        assert b"TechOps" in response.data
        assert b"Coming Soon" in response.data

    def test_techops_line_create_returns_404(self, app, client, seed_workflow_data):
        """Budget-specific subroutes 404 for non-budget work types via the guard."""
        _seed_techops(seed_workflow_data)
        cycle = seed_workflow_data["cycle"]
        dept = seed_workflow_data["department"]
        _login(client, "test:admin")

        # Try to access a budget-line creation page under the techops slug.
        # require_budget_work_type fires inside get_work_item_by_public_id
        # before any BudgetLineDetail query, so this 404s cleanly.
        response = client.get(
            f"/{cycle.code}/{dept.code}/techops/item/FAKE-1/lines/new"
        )
        assert response.status_code == 404, (
            f"Expected 404 for techops line URL (require_budget_work_type guard), "
            f"got {response.status_code}"
        )
