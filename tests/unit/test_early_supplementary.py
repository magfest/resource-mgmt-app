"""
Unit tests for the per-event ``allow_early_supplementary`` flag.

The flag relaxes the rule that a Primary Budget Request must be
FINALIZED before any Supplementary can be created. Used for events
like the FY corporate-budget cycle where a department splits its
budget across sibling requests.
"""
from __future__ import annotations

import pytest

from app import db
from app.models import (
    Department,
    DepartmentMembership,
    DepartmentMembershipWorkTypeAccess,
    EventCycle,
    User,
    WorkItem,
    WorkPortfolio,
    WorkType,
    WorkTypeConfig,
    REQUEST_KIND_PRIMARY,
    ROUTING_STRATEGY_DIRECT,
    WORK_ITEM_STATUS_DRAFT,
    WORK_ITEM_STATUS_FINALIZED,
)
from app.routes import UserContext
from app.routes.work.helpers.context import (
    PortfolioContext,
    build_portfolio_perms,
)


@pytest.fixture
def perms_fixture(app):
    """Seed the minimum org + portfolio + primary needed to exercise
    build_portfolio_perms for supplementary creation."""
    user = User(
        id="test:editor", email="editor@test.local",
        display_name="Editor", is_active=True,
    )
    cycle = EventCycle(
        code="TST2026", name="Test Event 2026",
        is_active=True, sort_order=1,
    )
    dept = Department(code="TESTDEPT", name="Test Department", is_active=True)
    db.session.add_all([user, cycle, dept])

    wt = WorkType(code="BUDGET", name="Budget", is_active=True)
    db.session.add(wt)
    db.session.flush()

    wtc = WorkTypeConfig(
        work_type_id=wt.id, url_slug="budget",
        public_id_prefix="BUD", line_detail_type="budget",
        routing_strategy=ROUTING_STRATEGY_DIRECT,
    )
    db.session.add(wtc)

    portfolio = WorkPortfolio(
        work_type_id=wt.id, event_cycle_id=cycle.id,
        department_id=dept.id, created_by_user_id=user.id,
    )
    db.session.add(portfolio)
    db.session.flush()

    membership = DepartmentMembership(
        user_id=user.id, department_id=dept.id, event_cycle_id=cycle.id,
    )
    db.session.add(membership)
    db.session.flush()

    db.session.add(DepartmentMembershipWorkTypeAccess(
        department_membership_id=membership.id, work_type_id=wt.id,
        can_view=True, can_edit=True,
    ))

    primary = WorkItem(
        portfolio_id=portfolio.id,
        request_kind=REQUEST_KIND_PRIMARY,
        status=WORK_ITEM_STATUS_DRAFT,
        public_id="TST2026-TESTDEPT-BUD-1",
        created_by_user_id=user.id,
    )
    db.session.add(primary)
    db.session.commit()

    user_ctx = UserContext(
        user_id=user.id,
        user=user,
        roles=(),
        is_super_admin=False,
        approval_group_ids=set(),
    )

    return {
        "cycle": cycle, "dept": dept, "wt": wt, "portfolio": portfolio,
        "membership": membership, "primary": primary, "user_ctx": user_ctx,
    }


def _make_ctx(d) -> PortfolioContext:
    return PortfolioContext(
        event_cycle=d["cycle"],
        department=d["dept"],
        portfolio=d["portfolio"],
        work_type=d["wt"],
        user_ctx=d["user_ctx"],
        membership=d["membership"],
        division_membership=None,
    )


class TestEarlySupplementaryFlag:
    def test_flag_defaults_false(self, perms_fixture):
        """Existing event cycles default to the strict rule."""
        assert perms_fixture["cycle"].allow_early_supplementary is False

    def test_strict_rule_blocks_supplementary_when_primary_is_draft(self, perms_fixture):
        """Without the flag, a DRAFT primary blocks supplementary creation."""
        ctx = _make_ctx(perms_fixture)
        perms = build_portfolio_perms(ctx)
        assert perms.can_create_supplementary is False

    def test_strict_rule_allows_supplementary_when_primary_is_finalized(self, perms_fixture):
        """Without the flag, a FINALIZED primary allows supplementary creation
        — baseline that we haven't broken the existing path."""
        perms_fixture["primary"].status = WORK_ITEM_STATUS_FINALIZED
        db.session.commit()

        ctx = _make_ctx(perms_fixture)
        perms = build_portfolio_perms(ctx)
        assert perms.can_create_supplementary is True

    def test_flag_allows_supplementary_when_primary_is_draft(self, perms_fixture):
        """With the flag set on the event, a DRAFT primary still allows
        supplementary creation."""
        perms_fixture["cycle"].allow_early_supplementary = True
        db.session.commit()

        ctx = _make_ctx(perms_fixture)
        perms = build_portfolio_perms(ctx)
        assert perms.can_create_supplementary is True

    def test_flag_still_requires_primary_to_exist(self, perms_fixture):
        """Even with the flag, a portfolio with no primary cannot host
        a supplementary — the data invariant 'supplementaries belong to
        a primary' is preserved."""
        perms_fixture["cycle"].allow_early_supplementary = True
        db.session.delete(perms_fixture["primary"])
        db.session.commit()

        ctx = _make_ctx(perms_fixture)
        perms = build_portfolio_perms(ctx)
        assert perms.can_create_supplementary is False
