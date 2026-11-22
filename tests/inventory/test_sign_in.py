"""Card and PIN credentials for scanner sign-in."""
from datetime import datetime, timedelta

import pytest

from app import db
from app.inventory import sign_in
from app.inventory.sign_in import (
    can_sign_in, credentials_for, is_card_code, keypad_locked, new_card, new_pin,
    record_pin_failure, clear_pin, clear_pin_failures, user_for_card, user_for_pin,
)
from app.models import InvCredential, InvDevice, User


@pytest.fixture
def app_ctx(inv):
    return inv


@pytest.fixture
def device(inv):
    row = InvDevice(name="Scanner 1", token_hash="h" * 64, enrolled_by_user_id="test:admin")
    db.session.add(row)
    db.session.flush()
    return row


def test_new_pin_is_six_digits_and_signs_in(app_ctx):
    user = db.session.get(User, "test:operator")
    pin = new_pin(user)
    assert len(pin) == 6 and pin.isdigit()
    assert user_for_pin(pin).id == "test:operator"


def test_new_pin_voids_the_old_one(app_ctx):
    user = db.session.get(User, "test:operator")
    old = new_pin(user)
    new = new_pin(user)
    assert user_for_pin(old) is None and user_for_pin(new).id == user.id


def test_new_pin_retries_a_taken_pin(app_ctx, monkeypatch):
    a, b = db.session.get(User, "test:operator"), db.session.get(User, "test:invadmin")
    draws = iter(["111111", "111111", "222222"])
    monkeypatch.setattr(sign_in, "_draw_pin", lambda: next(draws))
    assert new_pin(a) == "111111" and new_pin(b) == "222222"


def test_pin_keeps_leading_zeros(app_ctx, monkeypatch):
    monkeypatch.setattr(sign_in, "_draw_pin", lambda: "012345")
    new_pin(db.session.get(User, "test:operator"))
    assert user_for_pin("012345").id == "test:operator"
    assert user_for_pin("12345") is None


def test_pin_is_not_stored_readable(app_ctx):
    pin = new_pin(db.session.get(User, "test:operator"))
    row = db.session.get(InvCredential, "test:operator")
    assert pin not in (row.pin_digest or "") and len(row.pin_digest) == 64


def test_clear_pin_voids_it(app_ctx):
    pin = new_pin(db.session.get(User, "test:operator"))
    clear_pin("test:operator")
    assert user_for_pin(pin) is None
    clear_pin("test:nobody")


def test_card_value_fits_a_label_and_is_recognised(app_ctx):
    code = new_card(db.session.get(User, "test:operator"))
    assert len(code) == 15 and code.startswith("OP-") and is_card_code(code.lower())
    assert user_for_card(f"  {code} ").id == "test:operator"


def test_new_card_voids_the_old_one(app_ctx):
    user = db.session.get(User, "test:operator")
    old = new_card(user)
    new_card(user)
    assert user_for_card(old) is None


def test_card_and_pin_coexist_and_credentials_for_batches(app_ctx):
    user = db.session.get(User, "test:operator")
    pin, card = new_pin(user), new_card(user)
    assert user_for_pin(pin).id == user_for_card(card).id == user.id
    found = credentials_for(["test:operator", "test:nobody"])
    assert set(found) == {"test:operator"}


def test_gear_codes_are_not_cards():
    assert not any(is_card_code(c) for c in ["U-00001", "C-0001", "FEST10001", "OP-1", "", None])


def test_can_sign_in(app_ctx):
    assert can_sign_in(db.session.get(User, "test:operator"))
    assert can_sign_in(db.session.get(User, "test:invadmin"))
    assert can_sign_in(db.session.get(User, "test:admin"))
    assert not can_sign_in(db.session.get(User, "test:nobody"))
    assert not can_sign_in(None)
    op = db.session.get(User, "test:operator")
    op.is_active = False
    assert not can_sign_in(op)


def test_fifth_wrong_pin_locks_the_keypad_for_five_minutes(app_ctx, device):
    t = datetime(2026, 12, 7, 9, 0)
    for _ in range(4):
        record_pin_failure(device, t)
    assert not keypad_locked(device, t)
    record_pin_failure(device, t)
    assert device.pin_failures == 0
    assert keypad_locked(device, t + timedelta(minutes=4, seconds=59))
    assert not keypad_locked(device, t + timedelta(minutes=5, seconds=1))


def test_clear_pin_failures_resets_the_count(app_ctx, device):
    record_pin_failure(device)
    clear_pin_failures(device)
    assert device.pin_failures == 0
