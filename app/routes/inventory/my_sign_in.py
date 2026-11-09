"""Let a signed-in operator get a scanner PIN and print a sign-in card."""
from flask import flash, make_response, redirect, url_for

from app import db
from app.inventory.ledger import Actor, InventoryError
from app.inventory.printing import local_day
from app.inventory.sign_in import credentials_for, issue_card, new_pin
from app.models import User
from app.routes import get_user_ctx
from app.security_audit import CATEGORY_ADMIN, EVENT_USER_MODIFY, log_security_event

from . import inventory_bp
from .helpers import render_inventory_page, require_inventory_operator


def log_sign_in_event(target: User, change: str) -> None:
    """Record a sign-in credential change; `change` is pin_issued, pin_reset or card_printed.

    The details hold no PIN, card value or email.
    """
    log_security_event(EVENT_USER_MODIFY, category=CATEGORY_ADMIN, details={
        "source": "inventory_sign_in",
        "target_user_id": target.id,
        "change": change,
    })


def _me() -> User:
    return db.session.get(User, get_user_ctx().user_id)


def _status(user: User) -> dict:
    row = credentials_for([user.id]).get(user.id)
    pin_at = row.pin_set_at if row else None
    card_at = row.card_printed_at if row else None
    return {
        "pin_day": local_day(pin_at) if pin_at else None,
        "card_day": local_day(card_at) if card_at else None,
    }


@inventory_bp.get("/me/sign-in")
@require_inventory_operator
def my_sign_in():
    return render_inventory_page("inventory/my_sign_in.html", pin=None, **_status(_me()))


@inventory_bp.post("/me/sign-in/pin")
@require_inventory_operator
def my_new_pin():
    user = _me()
    had_pin = _status(user)["pin_day"] is not None
    pin = new_pin(user)
    log_sign_in_event(user, "pin_reset" if had_pin else "pin_issued")
    db.session.commit()
    # The PIN is stored only as a digest, so this response is the one chance to show it.
    response = make_response(render_inventory_page(
        "inventory/my_sign_in.html", pin=pin, **_status(user)))
    response.headers["Cache-Control"] = "no-store"
    return response


@inventory_bp.post("/me/sign-in/card")
@require_inventory_operator
def my_new_card():
    user = _me()
    try:
        job = issue_card(user, Actor(user_id=user.id))
    except InventoryError as exc:
        db.session.rollback()
        flash(str(exc), "error")
        return redirect(url_for("inventory.my_sign_in"))
    log_sign_in_event(user, "card_printed")
    db.session.commit()
    flash(f"Your card is printing on {job.printer.name}.", "success")
    return redirect(url_for("inventory.my_sign_in"))
