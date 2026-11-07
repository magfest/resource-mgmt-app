"""Stored answers for scanner writes, so a retried request writes once."""
from __future__ import annotations

import json
from typing import Callable

from sqlalchemy.exc import IntegrityError

from app import db
from app.inventory.ledger import InventoryError
from app.models import InvScanRequest

MAX_REQUEST_ID = 64
MISSING_ID = "This scan has no request id. Reload the page."


def _stored(client_request_id: str) -> dict | None:
    row = (db.session.query(InvScanRequest)
           .filter_by(client_request_id=client_request_id).first())
    return json.loads(row.response_json) if row else None


def run_once(client_request_id: str, action: Callable[[], dict]) -> dict:
    """Run `action` once per request id and commit its answer with its writes.

    A refusal rolls back and returns `{ok: False, message}` without storing, so a
    corrected retry runs again. A repeat of a stored id returns the first answer.
    """
    if not isinstance(client_request_id, str) or not client_request_id \
            or len(client_request_id) > MAX_REQUEST_ID:
        return {"ok": False, "message": MISSING_ID}
    previous = _stored(client_request_id)
    if previous is not None:
        return previous
    try:
        answer = action()
    except InventoryError as exc:
        db.session.rollback()
        return {"ok": False, "message": str(exc)}
    answer = {"ok": True, **answer}
    db.session.add(InvScanRequest(client_request_id=client_request_id,
                                  response_json=json.dumps(answer)))
    try:
        db.session.commit()
    except IntegrityError:
        # Two copies of one request raced; the first commit wins and this one's writes vanish.
        db.session.rollback()
        previous = _stored(client_request_id)
        if previous is None:
            raise
        return previous
    return answer
