"""Copy a once-a-year requester depends on, pinned so it cannot drift back.

A department head fills this in annually with no memory of last time. The
questions were well built and the page was silent on the four things that
frame them: what TechOps covers, what happens after Submit, that "not sure"
is allowed, and whether clicking Save finished anything. These assertions
exist because that copy is the difference between an accurate request and a
plausible guess, and copy has no other guard.
"""
from __future__ import annotations

import re

import pytest

from tests.integration.test_techops_room_first_form import (  # noqa: F401
    _login, techops_portfolio,
)


def _body(client, portfolio):
    _login(client, "test:admin")
    return client.get(portfolio["new_request_url"]).get_data(as_text=True)


def test_the_form_says_what_techops_covers_and_what_it_does_not(
        app, client, techops_portfolio):
    """Someone unsure whether a thing is on the menu does not order it, so an
    unsure requester answers "Nothing needed" for a room that needs a phone."""
    body = _body(client, techops_portfolio)
    assert "network, phones, radios" in body
    for elsewhere in ("A/V request form", "Supply ordering system",
                      "hotel request form"):
        assert elsewhere in body, elsewhere
    # No links: the other forms move every year and live on the department
    # head checklist instead.
    assert "department head checklist" in body


def test_the_form_says_what_happens_after_submit(app, client, techops_portfolio):
    body = _body(client, techops_portfolio)
    assert "#super-techops-requests" in body
    assert "email" in body.lower()


def test_the_form_says_not_to_guess(app, client, techops_portfolio):
    """The system records a blank as a question for the phone team to chase.
    Until this copy existed, nothing told the requester that was allowed."""
    body = _body(client, techops_portfolio)
    assert "do not guess" in body.lower()


def test_no_space_codes_reach_the_requester(app, client, techops_portfolio):
    """TechOps never uses room codes and a requester has never seen one."""
    body = _body(client, techops_portfolio)
    assert not re.search(r"\(\s*[A-Z]{3}-[PS](-\w+)?\s*\)", body)
    assert "EXPOE" not in body


def test_wifi_is_framed_as_staff_without_closing_the_door_on_attendees(
        app, client, techops_portfolio):
    body = _body(client, techops_portfolio)
    assert "WiFi coverage / access" in body
    assert "Staff and event ops" in body
    # The old example invited "it is for attendees" as a throwaway answer.
    assert "attendees on network" not in body


def test_the_order_preview_does_not_call_them_order_lines(
        app, client, techops_portfolio):
    body = _body(client, techops_portfolio)
    assert "order line" not in body.lower()
    assert "TechOps item" in body


def test_saving_one_room_says_the_request_is_not_submitted(
        app, client, techops_portfolio):
    """Three save-shaped buttons and no finish line. Someone who saves their
    last room has every reason to think they are done."""
    _login(client, "test:admin")
    room = techops_portfolio["room"]
    response = client.post(techops_portfolio["new_request_url"], data={
        "primary_contact_name": "Ada",
        "primary_contact_email": "ada@magfest.org",
        "space_ids": str(room.id),
        f"space_{room.id}_answer": "NEEDS",
        f"space_{room.id}_NETWORK_needed": "NO",
        f"space_{room.id}_wifi_requested": "1",
        "save_space_id": str(room.id),
    }, follow_redirects=True)
    body = response.get_data(as_text=True)
    assert "not submitted yet" in body.lower()
