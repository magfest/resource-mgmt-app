"""The event's TechOps deadline ends a department's own return to draft."""
from datetime import date, datetime, timedelta

from app import db
from app.models import (
    EventCycle,
    ROLE_SUPER_ADMIN,
    User,
    UserRole,
    WORK_ITEM_STATUS_DRAFT,
    WORK_ITEM_STATUS_SUBMITTED,
)
from app.routes.work.techops.send_back import (
    pull_back_deadline_passed,
    submitted_message,
)
from app.services.email_windows import utc_to_eastern_date
from tests.integration.techops_review_helpers import login
from tests.integration.test_techops_review_affordances import DETAIL_URL
from tests.integration.test_techops_send_back_routes import (  # noqa: F401
    PULL_BACK_URL,
    SEND_BACK_URL,
    send_back_setup,
    techops_review,
)

DEADLINE_PASSED = (
    "The TechOps deadline has passed. Ask for changes in "
    "#super-techops-requests."
)


def _eastern_today():
    return utc_to_eastern_date(datetime.utcnow())


def _set_deadline(item, deadline):
    item.portfolio.event_cycle.techops_submission_deadline = deadline
    db.session.commit()


def test_deadline_day_is_open_until_midnight_eastern():
    cycle = EventCycle(techops_submission_deadline=date(2026, 11, 14))
    # 23:30 Eastern on Nov 14 is 04:30 UTC on Nov 15.
    assert not pull_back_deadline_passed(cycle, now=datetime(2026, 11, 15, 4, 30))
    assert pull_back_deadline_passed(cycle, now=datetime(2026, 11, 15, 5, 1))


def test_no_deadline_never_passes():
    assert not pull_back_deadline_passed(EventCycle(), now=datetime(2099, 1, 1))


def test_submitted_message_names_the_date():
    cycle = EventCycle(techops_submission_deadline=date(2026, 11, 14))
    assert submitted_message(cycle, now=datetime(2026, 11, 1)) == (
        "TechOps request submitted. You can return it to draft until Nov 14, "
        "as long as TechOps has not started reviewing it.")
    assert submitted_message(cycle, now=datetime(2026, 11, 20)) == (
        "TechOps request submitted. " + DEADLINE_PASSED)
    assert submitted_message(EventCycle(), now=datetime(2026, 11, 1)) == (
        "TechOps request submitted. You can return it to draft until "
        "TechOps starts reviewing it.")


def test_pull_back_allowed_on_the_deadline_day(client, send_back_setup):
    item = send_back_setup["item"]
    _set_deadline(item, _eastern_today())
    login(client, "test:author")
    html = client.get(DETAIL_URL).get_data(as_text=True)
    assert "Return to draft" in html
    assert "You can return this to draft until" in html
    client.post(PULL_BACK_URL)
    db.session.refresh(item)
    assert item.status == WORK_ITEM_STATUS_DRAFT


def test_pull_back_refused_after_the_deadline(client, send_back_setup):
    item = send_back_setup["item"]
    _set_deadline(item, _eastern_today() - timedelta(days=1))
    login(client, "test:author")
    html = client.get(DETAIL_URL).get_data(as_text=True)
    assert "Return to draft" not in html
    assert DEADLINE_PASSED in html
    client.post(PULL_BACK_URL)
    db.session.refresh(item)
    assert item.status == WORK_ITEM_STATUS_SUBMITTED


def test_admin_can_send_back_after_the_deadline(client, send_back_setup):
    item = send_back_setup["item"]
    _set_deadline(item, _eastern_today() - timedelta(days=1))
    login(client, "test:admin")
    client.post(SEND_BACK_URL, data={"reason": "Rooms moved"})
    db.session.refresh(item)
    assert item.status == WORK_ITEM_STATUS_DRAFT


def test_event_cycle_form_saves_the_techops_deadline(app, client):
    db.session.add(User(id="test:cycleadmin", email="cycleadmin@test.local",
                        display_name="Cycle Admin", is_active=True))
    db.session.add(UserRole(user_id="test:cycleadmin", role_code=ROLE_SUPER_ADMIN))
    cycle = EventCycle(code="SMF2028", name="Super MAGFest 2028", is_active=True)
    db.session.add(cycle)
    db.session.commit()
    login(client, "test:cycleadmin")

    form = client.get(f"/admin/config/event-cycles/{cycle.id}").get_data(as_text=True)
    assert "Budget Submission Deadline" in form
    assert "TechOps Request Deadline" in form

    client.post(f"/admin/config/event-cycles/{cycle.id}", data={
        "code": "SMF2028", "name": "Super MAGFest 2028", "is_active": "1",
        "techops_submission_deadline": "2027-11-14",
    })
    db.session.refresh(cycle)
    assert cycle.techops_submission_deadline == date(2027, 11, 14)

