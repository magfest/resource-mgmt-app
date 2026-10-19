"""Return-to-draft controls on the detail page, and wording that matches them."""
from datetime import date

import pytest

from app import db
from app.models import (
    WorkLineReview,
    REVIEW_STATUS_APPROVED,
    ROLE_WORKTYPE_ADMIN,
    WORK_ITEM_STATUS_DRAFT,
)
from tests.integration.techops_review_helpers import (
    add_user_with_role,
    grant_department_access,
    login,
    set_review_status,
)
from tests.integration.test_techops_review_affordances import (  # noqa: F401
    DETAIL_URL,
    techops_review,
)

SUBMIT_FLASH = ("TechOps request submitted. You can return it to draft "
                "until TechOps starts reviewing it.")


@pytest.fixture
def ui_setup(techops_review):
    grant_department_access("test:author", techops_review["item"])
    add_user_with_role("test:admin", ROLE_WORKTYPE_ADMIN)
    return techops_review


def _make_draft(item):
    # A real draft has no review rows; submit creates them.
    WorkLineReview.query.delete()
    item.status = WORK_ITEM_STATUS_DRAFT
    db.session.commit()


def _page(client, user_id, url=DETAIL_URL):
    login(client, user_id)
    response = client.get(url)
    assert response.status_code == 200
    return response.get_data(as_text=True)


def test_detail_shows_return_button_to_editor(client, ui_setup):
    body = _page(client, "test:author")
    assert "Return to draft" in body
    assert "Send back to department" not in body


def test_detail_shows_send_back_to_admin(client, ui_setup):
    body = _page(client, "test:admin")
    assert "Send back to department" in body
    assert "Reason (the department sees this)" in body
    assert "Return to draft" not in body


def test_detail_hides_both_after_a_decision_for_editor(client, ui_setup):
    set_review_status(ui_setup["line"], REVIEW_STATUS_APPROVED)
    body = _page(client, "test:author")
    assert "Return to draft" not in body
    assert "Send back to department" not in body


def test_form_intro_promises_only_the_review_window(client, ui_setup):
    _make_draft(ui_setup["item"])
    body = _page(client, "test:author", f"{DETAIL_URL}/edit")
    assert "until TechOps starts" in body
    assert "couple of weeks" not in body


def test_submit_flash_names_the_return_window(client, ui_setup):
    _make_draft(ui_setup["item"])
    login(client, "test:author")
    client.post(f"{DETAIL_URL}/submit")
    with client.session_transaction() as sess:
        assert [m for _, m in sess.get("_flashes", [])] == [SUBMIT_FLASH]


DIVIDER = "border-left: 1px solid #a7f3d0"


def test_divider_only_follows_an_action_button(client, ui_setup):
    item = ui_setup["item"]
    login(client, "test:author")
    assert DIVIDER in client.get(DETAIL_URL).get_data(as_text=True)

    # Past the deadline a department editor has no action, only Back.
    item.portfolio.event_cycle.techops_submission_deadline = date(2000, 1, 1)
    db.session.commit()
    assert DIVIDER not in client.get(DETAIL_URL).get_data(as_text=True)
