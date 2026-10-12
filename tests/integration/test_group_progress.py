"""Each TechOps request shows how far each approval group has got."""
from app import db
from app.models import (
    ROLE_WORKTYPE_ADMIN, WorkLineReview, REVIEW_STATUS_NEEDS_INFO,
    WORK_ITEM_STATUS_DRAFT,
)
from app.routes.work.helpers.review_state import group_progress_for_items

from tests.integration.techops_review_helpers import (
    add_routed_line, add_user_with_role, grant_department_access, line_url, login,
)
from tests.integration.test_techops_review_affordances import (  # noqa: F401
    DETAIL_URL, techops_review,
)


def _mixed_request(client, techops_review):
    """GEN line 1 approved; NET line 2 pending; NET line 3 waiting on the requester."""
    item = techops_review["item"]
    add_routed_line(item, 2, "TECHOPS_NET")
    line3 = add_routed_line(item, 3, "TECHOPS_NET")
    review = WorkLineReview.query.filter_by(work_line_id=line3.id).one()
    review.status = REVIEW_STATUS_NEEDS_INFO
    db.session.commit()
    login(client, "test:reviewer")
    client.post(line_url(1, "approve"))
    return item


def test_counts_per_group(client, techops_review):
    item = _mixed_request(client, techops_review)

    progress = {p.group.code: p for p in group_progress_for_items([item.id])[item.id]}

    assert (progress["TECHOPS_GEN"].total, progress["TECHOPS_GEN"].decided) == (1, 1)
    net = progress["TECHOPS_NET"]
    assert (net.total, net.decided, net.waiting_on_requester, net.pending) == (2, 0, 1, 1)


def test_detail_and_all_requests_show_the_strip(client, techops_review):
    _mixed_request(client, techops_review)
    add_user_with_role("test:techopsadmin", ROLE_WORKTYPE_ADMIN)
    grant_department_access("test:author", techops_review["item"])

    login(client, "test:author")
    requester_html = client.get(DETAIL_URL).get_data(as_text=True)
    login(client, "test:techopsadmin")
    list_html = client.get("/admin/techops/requests/").get_data(as_text=True)

    assert "TechOps Generic · 1 of 1 decided" in requester_html
    assert "1 waiting on you" in requester_html
    assert "1 waiting on requester" in list_html


def test_draft_request_renders_without_a_strip(client, techops_review):
    techops_review["item"].status = WORK_ITEM_STATUS_DRAFT
    WorkLineReview.query.delete()
    db.session.commit()
    grant_department_access("test:author", techops_review["item"])
    login(client, "test:author")

    resp = client.get(DETAIL_URL)

    assert resp.status_code == 200
    assert "group-progress" not in resp.get_data(as_text=True)


def test_two_lines_with_the_same_status_both_count(client, techops_review):
    # Identical (item, status, group) rows must not collapse into one.
    item = techops_review["item"]
    add_routed_line(item, 2, "TECHOPS_GEN")
    login(client, "test:reviewer")
    client.post(line_url(1, "approve"))
    client.post(line_url(2, "approve"))

    progress = {p.group.code: p for p in group_progress_for_items([item.id])[item.id]}

    assert (progress["TECHOPS_GEN"].total, progress["TECHOPS_GEN"].decided) == (2, 2)
