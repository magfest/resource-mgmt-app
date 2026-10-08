"""The shared comment form posts to its own work type and returns to its page.

Each test reads the form action from the rendered page rather than building
the URL itself, so a form that names the wrong route fails here.
"""
import re

from app.models import WorkItemComment
from tests.integration.test_supply_submit import (
    _login,
    _make_draft_order,
    _seed_supply,
)
from tests.integration.test_techops_room_first_display import (  # noqa: F401
    techops_reviewable_request,
)


def _post_comment_from_page(client, page_path, text):
    html = client.get(page_path).get_data(as_text=True)
    form = re.search(
        r'<form method="post" action="([^"]+/comment)">(.*?)</form>', html, re.S)
    assert form, f"no comment form on {page_path}"
    return_to = re.search(r'name="return_to" value="([^"]*)"', form.group(2))
    data = {"comment": text}
    if return_to:
        data["return_to"] = return_to.group(1)
    return client.post(form.group(1), data=data)


def _assert_saved_and_returned(response, work_item, page_path, text):
    assert response.status_code == 302
    assert response.headers["Location"].endswith(page_path)
    assert WorkItemComment.query.filter_by(
        work_item_id=work_item.id, body=text).count() == 1


def test_supply_order_comment(app, client, seed_workflow_data):
    wt = _seed_supply(seed_workflow_data)
    cycle = seed_workflow_data["cycle"]
    dept = seed_workflow_data["department"]
    work_item = _make_draft_order(wt, cycle, dept)
    page = f"/{cycle.code}/{dept.code}/supply/order/{work_item.public_id}"

    _login(client, "test:admin")
    response = _post_comment_from_page(client, page, "Pickup at the loading dock")

    _assert_saved_and_returned(response, work_item, page, "Pickup at the loading dock")


def test_techops_request_comment(app, client, techops_reviewable_request):
    work_item = techops_reviewable_request["work_item"]
    page = techops_reviewable_request["detail_url"]

    _login(client, "test:admin")
    response = _post_comment_from_page(client, page, "Room swap confirmed")

    _assert_saved_and_returned(response, work_item, page, "Room swap confirmed")


def test_budget_request_comment(app, client, seed_draft_work_item):
    work_item = seed_draft_work_item["work_item"]
    cycle = seed_draft_work_item["cycle"]
    dept = seed_draft_work_item["department"]
    page = f"/{cycle.code}/{dept.code}/budget/item/{work_item.public_id}"

    _login(client, "test:admin")
    response = _post_comment_from_page(client, page, "Quote attached")

    _assert_saved_and_returned(response, work_item, page, "Quote attached")
