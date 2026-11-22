"""Review entry points for Supply: dashboard links, order-page buttons, cross-group reading."""
import re

from app import db
from app.models import (
    ApprovalGroup, BudgetLineDetail, User, UserRole, WorkItem, WorkLine,
    REQUEST_KIND_PRIMARY, ROLE_APPROVER, ROLE_WORKTYPE_ADMIN,
    WORK_ITEM_STATUS_SUBMITTED, WORK_LINE_STATUS_PENDING,
)
from tests.integration.supply_helpers import (
    add_line, login, make_draft_order, seed_approval_group, seed_category,
    seed_item, seed_reviewer, seed_supply, set_pickup_details,
)


def _hrefs(html):
    return re.findall(r'href="([^"]+)"', html)


def first_href_containing(html, fragment):
    for href in _hrefs(html):
        if fragment in href:
            return href.replace("&amp;", "&")
    return None


def decision_buttons_for_line(html, line_number):
    """Return the data-action values inside one line's table row."""
    match = re.search(
        rf'<tr id="line-{line_number}".*?</tr>\s*(?=<tr id="line-|</tbody>)', html, re.S,
    )
    assert match, f"row for line {line_number} not found"
    return re.findall(r'data-action="([^"]+)"', match.group(0))


def _user(user_id):
    db.session.add(User(id=user_id, email=f"{user_id.split(':')[1]}@test.local",
                        display_name=user_id, is_active=True))
    db.session.commit()


def _two_group_order(client, data, both_lines=True):
    """Submit an order with a SUPPLY_GEN line and, optionally, a SUPPLY_TECH line."""
    wt = seed_supply(data)
    gen = seed_approval_group(wt, "SUPPLY_GEN")
    tech = seed_approval_group(wt, "SUPPLY_TECH")
    gen_item = seed_item(seed_category(gen, "OFFICE"), "Sharpie Markers")
    tech_item = seed_item(seed_category(tech, "TECHCAT"), "Gaffer Tape")
    order = make_draft_order(wt, data["cycle"], data["department"])
    add_line(order, gen_item, quantity=2)
    if both_lines:
        add_line(order, tech_item, quantity=1)
    set_pickup_details(order)
    login(client, "test:admin")
    cycle, dept = data["cycle"], data["department"]
    resp = client.post(f"/{cycle.code}/{dept.code}/supply/order/{order.public_id}/submit")
    assert resp.status_code == 302
    db.session.refresh(order)
    assert order.status == WORK_ITEM_STATUS_SUBMITTED
    return wt, gen, tech, order


def _order_url(data, order):
    return f"/{data['cycle'].code}/{data['department'].code}/supply/order/{order.public_id}"


def test_reviewer_reaches_a_decision_by_following_links(client, seed_workflow_data):
    wt, gen, tech, order = _two_group_order(client, seed_workflow_data)
    seed_reviewer(seed_workflow_data, gen)
    login(client, "test:reviewer")
    dash = client.get(f"/approvals/{gen.code}").data.decode()
    href = first_href_containing(dash, "/quick-review")
    assert href is not None
    page = client.get(href)
    assert page.status_code == 200 and b"Sharpie Markers" in page.data
    assert b"Approve" in page.data


def test_order_page_shows_quick_review_and_line_review_links_to_reviewer(client, seed_workflow_data):
    wt, gen, tech, order = _two_group_order(client, seed_workflow_data)
    seed_reviewer(seed_workflow_data, gen)
    login(client, "test:reviewer")
    body = client.get(_order_url(seed_workflow_data, order)).data.decode()
    assert "/quick-review" in body and "/line/1/review" in body


def test_order_page_hides_review_links_from_department_member(client, seed_workflow_data):
    from app.models import DepartmentMembership, DepartmentMembershipWorkTypeAccess
    wt, gen, tech, order = _two_group_order(client, seed_workflow_data)
    _user("test:member")
    membership = DepartmentMembership(
        user_id="test:member", department_id=seed_workflow_data["department"].id,
        event_cycle_id=seed_workflow_data["cycle"].id,
    )
    db.session.add(membership)
    db.session.flush()
    db.session.add(DepartmentMembershipWorkTypeAccess(
        department_membership_id=membership.id, work_type_id=wt.id,
        can_view=True, can_edit=True,
    ))
    db.session.commit()
    login(client, "test:member")
    resp = client.get(_order_url(seed_workflow_data, order))
    assert resp.status_code == 200
    body = resp.data.decode()
    assert "Sharpie Markers" in body
    assert "/quick-review" not in body and "/review" not in body


def test_tech_reviewer_reads_supplyops_only_order(client, seed_workflow_data):
    wt, gen, tech, order = _two_group_order(client, seed_workflow_data, both_lines=False)
    _user("test:techrev")
    db.session.add(UserRole(user_id="test:techrev", role_code=ROLE_APPROVER,
                            approval_group_id=tech.id))
    db.session.commit()
    login(client, "test:techrev")
    assert client.get(_order_url(seed_workflow_data, order)).status_code == 200


def test_tech_reviewer_sees_other_group_line_without_decision_buttons_in_quick_review(
    client, seed_workflow_data,
):
    wt, gen, tech, order = _two_group_order(client, seed_workflow_data)
    _user("test:techrev")
    db.session.add(UserRole(user_id="test:techrev", role_code=ROLE_APPROVER,
                            approval_group_id=tech.id))
    db.session.commit()
    login(client, "test:techrev")
    order_page = client.get(_order_url(seed_workflow_data, order)).data.decode()
    href = first_href_containing(order_page, "/quick-review")
    assert href is not None
    body = client.get(href).data.decode()
    assert "Sharpie Markers" in body
    assert 'data-line-num="1"' in body and decision_buttons_for_line(body, 1) == []


def test_supply_admin_outside_groups_sees_every_line_in_quick_review(client, seed_workflow_data):
    wt, gen, tech, order = _two_group_order(client, seed_workflow_data)
    _user("test:supadmin")
    db.session.add(UserRole(user_id="test:supadmin", role_code=ROLE_WORKTYPE_ADMIN,
                            work_type_id=wt.id))
    db.session.commit()
    login(client, "test:supadmin")
    order_page = client.get(_order_url(seed_workflow_data, order)).data.decode()
    href = first_href_containing(order_page, "/quick-review")
    assert href is not None
    body = client.get(href).data.decode()
    assert "Sharpie Markers" in body and "Gaffer Tape" in body


def test_budget_approver_still_cannot_read_other_group_request(client, seed_workflow_data):
    data = seed_workflow_data
    other = ApprovalGroup(work_type_id=data["work_type"].id, code="OTHER",
                          name="Other", is_active=True)
    db.session.add(other)
    db.session.flush()
    item = WorkItem(portfolio_id=data["portfolio"].id, request_kind=REQUEST_KIND_PRIMARY,
                    status=WORK_ITEM_STATUS_SUBMITTED, public_id="TST2026-TESTDEPT-BUD-1",
                    created_by_user_id="test:admin")
    db.session.add(item)
    db.session.flush()
    line = WorkLine(work_item_id=item.id, line_number=1, status=WORK_LINE_STATUS_PENDING)
    db.session.add(line)
    db.session.flush()
    db.session.add(BudgetLineDetail(
        work_line_id=line.id, expense_account_id=data["expense_account"].id,
        spend_type_id=data["spend_type"].id, quantity=1, unit_price_cents=100,
        routed_approval_group_id=other.id,
    ))
    db.session.add(UserRole(user_id="test:reviewer", role_code=ROLE_APPROVER,
                            approval_group_id=data["approval_group"].id))
    db.session.commit()
    login(client, "test:reviewer")
    resp = client.get(f"/TST2026/TESTDEPT/budget/item/{item.public_id}")
    assert resp.status_code == 403
