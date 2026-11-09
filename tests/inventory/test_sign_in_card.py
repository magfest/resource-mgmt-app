"""Printing the sign-in card through the label queue."""
from datetime import date

import pytest

from app import db
from app.inventory.ledger import Actor, InventoryError
from app.inventory.printing import cancel_job, render_job, report_job
from app.inventory.shifts import current_shift, start_shift
from app.inventory.sign_in import issue_card, new_card, user_for_card
from app.inventory.zpl import render_card
from app.models import InvDevice, InvPrintJob, User
from app.models.constants import INV_JOB_CLAIMED, INV_JOB_SIGNIN_CARD
from tests.inventory.helpers import login


@pytest.fixture
def app_ctx(inv):
    return inv


@pytest.fixture
def device(inv):
    row = InvDevice(name="Scanner 1", token_hash="h" * 64, enrolled_by_user_id="test:admin")
    db.session.add(row)
    db.session.flush()
    return row


def test_card_label_shows_name_and_words_not_the_value(app_ctx):
    zpl = render_card("Jamie Lee", "OP-ABCDEFGHJKMN", date(2026, 12, 7), 203, 2.0, 3.0)
    assert "Jamie Lee" in zpl and "FestOps inventory sign-in" in zpl
    assert "Printed 2026-12-07" in zpl
    assert zpl.count("OP-ABCDEFGHJKMN") == 1          # inside the barcode field only
    assert "^BC" in zpl and "^FDOP-ABCDEFGHJKMN^FS" in zpl


def test_issue_card_queues_a_job_and_ends_shifts(app_ctx, card_route, device):
    start_shift(device, "test:operator")
    job = issue_card(db.session.get(User, "test:operator"), Actor(user_id="test:invadmin"))
    assert job.job_type == "SIGNIN_CARD" and job.fields == {"name": "Operator"}
    assert current_shift(device) is None
    assert user_for_card(job.code).id == "test:operator"


def test_card_refused_without_a_name(app_ctx, card_route):
    user = db.session.get(User, "test:operator")
    user.display_name = "  "
    with pytest.raises(InventoryError, match="no name to print"):
        issue_card(user, Actor(user_id="test:invadmin"))


def test_no_printer_keeps_the_old_card(app_ctx):
    user = db.session.get(User, "test:operator")
    old = new_card(user)
    with pytest.raises(InventoryError):
        issue_card(user, Actor(user_id="test:invadmin"))
    assert user_for_card(old).id == user.id


def test_card_job_renders_from_the_stored_value(app_ctx, card_route):
    job = issue_card(db.session.get(User, "test:operator"), Actor(user_id="test:invadmin"))
    zpl = render_job(job)
    assert f"^FD{job.code}^FS" in zpl and "Operator" in zpl


def test_card_value_is_cleared_once_printed_or_cancelled(app_ctx, card_route):
    actor = Actor(user_id="test:invadmin")
    sent = issue_card(db.session.get(User, "test:operator"), actor)
    sent.status = INV_JOB_CLAIMED
    report_job(sent, ok=True)
    assert sent.code == ""
    waiting = issue_card(db.session.get(User, "test:invadmin"), actor)
    cancel_job(waiting)
    assert waiting.code == ""


def test_queue_page_shows_the_person_not_the_value(client, card_route):
    job = issue_card(db.session.get(User, "test:operator"), Actor(user_id="test:invadmin"))
    code = job.code
    db.session.commit()
    login(client, "test:invadmin")
    page = client.get("/inv/admin/print-queue").get_data(as_text=True)
    assert "Sign-in card for Operator" in page and code not in page


def test_card_job_cannot_be_reprinted(client, card_route):
    job = issue_card(db.session.get(User, "test:operator"), Actor(user_id="test:invadmin"))
    job.status = INV_JOB_CLAIMED
    report_job(job, ok=False, error="jam")
    db.session.commit()
    login(client, "test:invadmin")
    response = client.post(f"/inv/admin/print-queue/{job.id}/reprint", follow_redirects=True)
    assert "Print a new card from the Operators page." in response.get_data(as_text=True)
    assert db.session.query(InvPrintJob).count() == 1
