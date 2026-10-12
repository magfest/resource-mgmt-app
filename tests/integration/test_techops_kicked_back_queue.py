"""The review dashboard's kicked-back panel renders room-first TechOps lines.

The panel's TechOps branch read `location` and `usage` for every service,
which room-first lines mostly leave empty, and it had no space column. It
now uses the same macros as quick review.
"""
from app import db
from app.models import (
    WorkLineReview, REVIEW_STAGE_APPROVAL_GROUP, REVIEW_STATUS_NEEDS_INFO,
    WORK_LINE_STATUS_NEEDS_INFO,
)

from tests.integration.test_techops_room_first_display import (  # noqa: F401
    techops_reviewable_request,
)


def _login(client, user_id):
    with client.session_transaction() as sess:
        sess["active_user_id"] = user_id


def _kick_back_every_line(work_item):
    """Route each line to its service's default group and mark it NEEDS_INFO."""
    for line in work_item.lines:
        detail = line.techops_detail
        group_id = detail.service_type.default_approval_group_id
        detail.routed_approval_group_id = group_id
        line.status = WORK_LINE_STATUS_NEEDS_INFO
        line.needs_requester_action = True
        db.session.add(WorkLineReview(
            work_line_id=line.id, stage=REVIEW_STAGE_APPROVAL_GROUP,
            approval_group_id=group_id, status=REVIEW_STATUS_NEEDS_INFO,
            note="Which outlet?", created_by_user_id="test:admin",
        ))
    db.session.commit()


def _kicked_back_panel(client, group_code):
    html = client.get(f"/approvals/{group_code}").get_data(as_text=True)
    start = html.index("Kicked Back (Awaiting Requester)")
    return html[start:html.index("Recently Reviewed", start)]


def test_kicked_back_line_shows_its_composed_space_name(app, client, techops_reviewable_request):
    _kick_back_every_line(techops_reviewable_request["work_item"])
    _login(client, "test:admin")

    panel = _kicked_back_panel(client, "TECHOPS_NET")

    assert "RiverView 1/2/3" in panel


def test_kicked_back_line_in_an_unheld_space_carries_the_warning(
        app, client, techops_reviewable_request):
    _kick_back_every_line(techops_reviewable_request["work_item"])
    _login(client, "test:admin")

    panel = _kicked_back_panel(client, "TECHOPS_NET")

    assert "Expo Hall Z" in panel
    assert "Not assigned to this department" in panel


def test_kicked_back_desk_phone_names_its_parent_line(app, client, techops_reviewable_request):
    _kick_back_every_line(techops_reviewable_request["work_item"])
    _login(client, "test:admin")

    panel = _kicked_back_panel(client, "TECHOPS_NET")

    parent = techops_reviewable_request["phone_number_line_number"]
    assert f"Rings the number on line {parent}." in panel
