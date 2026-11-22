"""FestOps label codes: C- containers, U- units, I- item types.

Codes are sequential per prefix and never reused. Labels are printed in
batches ahead of time and bound on first scan, so a printed code with no row
behind it is unused. A code above the counter was never printed; refusing it
stops a mistyped or invented code from creating a container.
"""
from __future__ import annotations

import re

from sqlalchemy.exc import IntegrityError

from app import db
from app.inventory.codes import normalize_code
from app.inventory.ledger import savepoint
from app.models import InvLabelSequence

PREFIX_CONTAINER = "C"
PREFIX_UNIT = "U"
PREFIX_ITEM = "I"
WIDTHS = {PREFIX_CONTAINER: 4, PREFIX_UNIT: 5, PREFIX_ITEM: 4}
MAX_BATCH = 500

_CODE = re.compile(r"^([CUI])-(\d{1,9})$", re.ASCII)


def format_code(prefix: str, number: int) -> str:
    return f"{prefix}-{number:0{WIDTHS[prefix]}d}"


def parse(code: str | None) -> tuple[str, int] | None:
    match = _CODE.match(normalize_code(code))
    if not match:
        return None
    return match.group(1), int(match.group(2))


def _locked_sequence(prefix: str) -> InvLabelSequence:
    # populate_existing: a counter already in the session keeps its old next_value
    # unless the locked read overwrites it, which would hand out a repeated code.
    query = (db.session.query(InvLabelSequence).filter_by(prefix=prefix)
             .with_for_update().populate_existing())
    row = query.first()
    if row is not None:
        return row
    try:
        with savepoint():
            row = InvLabelSequence(prefix=prefix, next_value=1)
            db.session.add(row)
    except IntegrityError:
        row = query.one()
    return row


def allocate(prefix: str, count: int) -> list[str]:
    """Reserve `count` new codes. The caller commits."""
    if prefix not in WIDTHS:
        raise ValueError(f"Unknown label prefix {prefix!r}")
    if not 1 <= count <= MAX_BATCH:
        raise ValueError(f"Batch size must be 1 to {MAX_BATCH}")
    sequence = _locked_sequence(prefix)
    start = sequence.next_value
    sequence.next_value = start + count
    db.session.flush()
    return [format_code(prefix, n) for n in range(start, start + count)]


def was_issued(code: str | None) -> bool:
    parsed = parse(code)
    if parsed is None:
        return False
    prefix, number = parsed
    # populate_existing: a sequence row already in the session would otherwise
    # report a stale next_value and refuse a label another scanner just issued.
    row = (db.session.query(InvLabelSequence).filter_by(prefix=prefix)
           .populate_existing().first())
    return row is not None and 1 <= number < row.next_value
