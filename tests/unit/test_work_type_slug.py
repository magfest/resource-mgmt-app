"""
Tests for the work_type_slug property on WorkPortfolio and PortfolioContext.

These properties are the foundation of the Phase 1 multi-work-type refactor:
templates use them to build URLs that include the work type slug, so a single
url_for call can target any work type's routes.
"""
from app import db
from app.models import (
    WorkType,
    WorkTypeConfig,
    WorkPortfolio,
    ROUTING_STRATEGY_DIRECT,
)
from app.routes.work.helpers.context import PortfolioContext
from app.seeds.bootstrap import seed_work_types, seed_work_type_configs


class TestWorkPortfolioSlug:
    """WorkPortfolio.work_type_slug delegates to WorkTypeConfig.url_slug."""

    def test_returns_budget_slug_for_budget_portfolio(self, app, seed_workflow_data):
        portfolio = seed_workflow_data["portfolio"]
        assert portfolio.work_type_slug == "budget"

    def test_returns_techops_slug_for_techops_portfolio(self, app, seed_workflow_data):
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
        db.session.flush()

        portfolio = WorkPortfolio(
            work_type_id=wt.id,
            event_cycle_id=seed_workflow_data["cycle"].id,
            department_id=seed_workflow_data["department"].id,
            created_by_user_id=seed_workflow_data["admin"].id,
        )
        db.session.add(portfolio)
        db.session.commit()

        assert portfolio.work_type_slug == "techops"


class TestPortfolioContextSlug:
    """PortfolioContext.work_type_slug delegates to its work_type's config slug."""

    def test_returns_slug_from_work_type_config(self, app, seed_workflow_data):
        ctx = PortfolioContext(
            event_cycle=seed_workflow_data["cycle"],
            department=seed_workflow_data["department"],
            portfolio=seed_workflow_data["portfolio"],
            work_type=seed_workflow_data["work_type"],
            user_ctx=None,
            membership=None,
            division_membership=None,
        )
        assert ctx.work_type_slug == "budget"


class TestSeedConfigWorkTypeActivation:
    """Only BUDGET ships seeded-active. CONTRACT and SUPPLY have no requester
    UI yet and stay inactive to keep them out of pickers; TECHOPS ships
    inactive because it is still in beta and is enabled per-environment via
    the admin Work Types page (staging flips it on, production leaves it
    off). All five worktypes still get their config rows created so URL
    routing resolves cleanly even when the worktype is inactive."""

    def test_only_budget_seeded_active(self, app):
        work_types = seed_work_types()
        seed_work_type_configs(work_types)
        db.session.commit()

        budget = WorkType.query.filter_by(code="BUDGET").one()
        assert budget.is_active is True

        for code in ("CONTRACT", "SUPPLY", "TECHOPS", "AV"):
            wt = WorkType.query.filter_by(code=code).one()
            assert wt.is_active is False, f"{code} should ship seeded-inactive"

    def test_inactive_worktypes_still_have_configs(self, app):
        """Slugs and prefixes are still configured for inactive worktypes
        so existing URLs and public IDs resolve."""
        work_types = seed_work_types()
        seed_work_type_configs(work_types)
        db.session.commit()

        techops = WorkType.query.filter_by(code="TECHOPS").one()
        assert techops.config.url_slug == "techops"
        assert techops.config.public_id_prefix == "TEC"

        av = WorkType.query.filter_by(code="AV").one()
        assert av.config.url_slug == "av"
        assert av.config.public_id_prefix == "AV"
