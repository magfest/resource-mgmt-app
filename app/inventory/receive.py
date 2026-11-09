"""Receive owned assets: one scan pair makes an asset and queues its label.

Receiving records assets and never changes an item total or a stock count,
since only some of a group may be scanned. Nothing here commits.
"""
from __future__ import annotations

from app import db
from app.inventory import labels
from app.inventory.codes import normalize_code
from app.inventory.ledger import (
    Actor, InventoryError, add_unit, retire_received_unit,
)
from app.inventory.printing import (
    cancel_job, fields_for_unit, local_today, printer_for, printer_status,
    request_label, unit_number,
)
from app.inventory.resolver import KIND_CONTAINER, resolve
from app.inventory.sign_in import SIGN_IN_CARD, is_card_code
from app.inventory.zpl import can_print_code
from app.models import InvContainer, InvMovement, InvPrintJob, InvUnit, InvUnitIdentifier
from app.models.constants import (
    INV_ACTION_ADD, INV_CONDITION_RETIRED, INV_JOB_CLAIMED, INV_JOB_FAILED,
    INV_JOB_SENT, INV_IDENT_FESTOPS_LABEL,
    INV_IDENT_MAGFEST_TAG, INV_IDENT_SERIAL, INV_JOB_OWNED_UNIT,
    INV_JOB_QUEUED, INV_JOB_SOURCE_SCAN, INV_OWNERSHIP_OWNED,
)

# zpl._field cuts text at 64 characters, and a label must never print a cut serial.
MAX_SERIAL = 64
LABEL_SHAPED = "That is a label. Scan the asset tag, or tap Skip."
NOT_PRINTABLE = "This asset tag cannot be printed as a barcode."
SAME_CODE = "That is the same number as the asset tag. Scan the SERIAL."
NOTHING_TO_UNDO = "Only the last asset this scanner received can be undone."
LABEL_CANCELLED = "Label cancelled."
THROW_AWAY = "Throw that label away."


class AlreadyReceived(InventoryError):
    """An active unit already holds this tag, or this serial on the same item.

    `already` is the answer the scanner shows: the unit's number and id, and its item.
    """

    def __init__(self, unit: InvUnit):
        super().__init__("Already received")
        self.unit = unit
        self.already = {"code": unit_number(unit), "unit_id": unit.id,
                        "item_name": unit.item.item_name}


def container_for_code(raw: str | None) -> InvContainer:
    """Return the active container a scanned code names, or refuse."""
    resolution = resolve(raw)
    if resolution.kind != KIND_CONTAINER:
        raise InventoryError(f"{resolution.code or 'That'} is not somewhere to put things. "
                             "Scan where it's going.")
    if not resolution.container.is_active:
        raise InventoryError(f"{resolution.code} is not in use.")
    return resolution.container


def _active_holder(kind: str, normalized: str, item=None) -> InvUnit | None:
    query = (db.session.query(InvUnit)
             .join(InvUnitIdentifier, InvUnitIdentifier.unit_id == InvUnit.id)
             .filter(InvUnitIdentifier.kind == kind,
                     InvUnitIdentifier.normalized_value == normalized,
                     InvUnitIdentifier.retired_at.is_(None),
                     InvUnit.condition != INV_CONDITION_RETIRED))
    if item is not None:
        query = query.filter(InvUnit.item_id == item.id)
    return query.order_by(InvUnit.id).first()


def _clean(value) -> str | None:
    text = value.strip() if isinstance(value, str) else ""
    return text or None


def label_job(unit: InvUnit, actor: Actor, client_request_id, printed_date=None,
               fields=None) -> InvPrintJob:
    number = unit_number(unit)
    return request_label(
        INV_JOB_OWNED_UNIT, number, fields or fields_for_unit(unit), actor,
        INV_JOB_SOURCE_SCAN, printed_date=printed_date or local_today(), unit=unit,
        client_request_id=client_request_id)


def label_answer(unit: InvUnit, job: InvPrintJob) -> dict:
    status = printer_status(job.printer)
    return {"code": unit_number(unit), "unit_id": unit.id, "job_id": job.id,
            "station_seen": status["station_seen"], "waiting": status["waiting"]}


def check_tag(tag: str) -> None:
    """Refuse a tag that is a sign-in card, label-shaped, unprintable, or already held.

    Reads only. The check route and receive_unit share it so both give the same answer.
    """
    if is_card_code(tag):
        raise InventoryError(SIGN_IN_CARD)
    if labels.parse(tag) is not None:
        raise InventoryError(LABEL_SHAPED)
    if not can_print_code(tag):
        raise InventoryError(NOT_PRINTABLE)
    held = _active_holder(INV_IDENT_MAGFEST_TAG, normalize_code(tag))
    if held is not None:
        raise AlreadyReceived(held)


def receive_unit(item, actor: Actor, tag=None, serial=None, container=None,
                 client_request_id=None, queue_label: bool = True) -> dict:
    """Create an owned unit from a tag and a serial, either of which may be missing.

    With no tag the unit gets a generated U- number. The label is queued in the same
    transaction. The caller rolls back on a refusal.

    Args:
        queue_label: False skips the label and the print route check. The Check
            station passes False because its finish queues the label, and a
            asset retired by that finish must get none.
    """
    tag, serial = _clean(tag), _clean(serial)
    if not item.is_serialized:
        raise InventoryError(
            f"{item.item_name} is counted stock; scan the item and enter a quantity.")
    if not item.is_active:
        raise InventoryError(f"{item.item_name} is not in use.")
    if container is not None and not container.is_active:
        raise InventoryError(f"{container.label_code} is not in use.")
    if tag and serial and normalize_code(tag) == normalize_code(serial):
        raise InventoryError(SAME_CODE)
    if tag:
        check_tag(tag)
    # A typed field skips the scan shell, so a card can arrive here as a serial.
    if serial and is_card_code(serial):
        raise InventoryError(SIGN_IN_CARD)
    if serial and len(serial) > MAX_SERIAL:
        raise InventoryError(f"That serial is longer than {MAX_SERIAL} characters.")
    if queue_label:
        # Checked before anything is created, so a missing route leaves no unit behind.
        printer_for(INV_JOB_OWNED_UNIT)
    if serial:
        held = _active_holder(INV_IDENT_SERIAL, normalize_code(serial), item)
        if held is not None:
            raise AlreadyReceived(held)
    identifiers = []
    if tag:
        identifiers.append((INV_IDENT_MAGFEST_TAG, tag))
    else:
        identifiers.append((INV_IDENT_FESTOPS_LABEL, labels.allocate(labels.PREFIX_UNIT, 1)[0]))
    if serial:
        identifiers.append((INV_IDENT_SERIAL, serial))
    try:
        unit = add_unit(item, container, actor, identifiers)
    except InventoryError:
        # Two scanners can pass the checks above together; the unique index picks one.
        # The loser gets the same screen as a plain repeat.
        held = tag and _active_holder(INV_IDENT_MAGFEST_TAG, normalize_code(tag))
        held = held or (serial and _active_holder(INV_IDENT_SERIAL, normalize_code(serial), item))
        if held:
            raise AlreadyReceived(held) from None
        raise
    if not queue_label:
        return {"code": unit_number(unit), "unit_id": unit.id}
    return label_answer(unit, label_job(unit, actor, client_request_id))


def _last_received(actor: Actor) -> InvMovement | None:
    """The newest unit this actor created, in this shift when the actor has one."""
    query = db.session.query(InvMovement).filter(
        InvMovement.action == INV_ACTION_ADD, InvMovement.unit_id.isnot(None))
    if actor.shift_id is not None:
        query = query.filter(InvMovement.shift_id == actor.shift_id,
                             InvMovement.device_id == actor.device_id)
    else:
        query = query.filter(InvMovement.performed_by_user_id == actor.user_id,
                             InvMovement.shift_id.is_(None))
    return query.order_by(InvMovement.id.desc()).first()


def undo_receive(unit_id: int, actor: Actor) -> None:
    """Retire the unit this actor received last and cancel its label if still queued.

    The unit number is not reused. No movement, total or stock changes. A label
    already collected cannot be recalled; the caller tells the person to discard it.
    """
    last = _last_received(actor)
    if last is None or last.unit_id != unit_id:
        raise InventoryError(NOTHING_TO_UNDO)
    unit = (db.session.query(InvUnit).filter_by(id=unit_id)
            .with_for_update().populate_existing().one())
    if unit.condition == INV_CONDITION_RETIRED:
        raise InventoryError(NOTHING_TO_UNDO)
    jobs = (db.session.query(InvPrintJob).filter_by(unit_id=unit.id)
            .with_for_update().populate_existing().all())
    retire_received_unit(unit, actor)
    for job in jobs:
        if job.status == INV_JOB_QUEUED:
            cancel_job(job)


def undo_message(unit_id: int) -> str:
    """What to tell the person after an undo: the label was stopped, or it may exist.

    FAILED counts as printed; a claim that timed out may have reached the printer.
    """
    collected = (db.session.query(InvPrintJob.id)
                 .filter(InvPrintJob.unit_id == unit_id,
                         InvPrintJob.status.in_(
                             (INV_JOB_CLAIMED, INV_JOB_SENT, INV_JOB_FAILED)))
                 .first())
    return THROW_AWAY if collected is not None else LABEL_CANCELLED


def reprint_unit(unit_id, actor: Actor, client_request_id=None) -> dict:
    """Queue the unit's label again with the fields and date of its newest label."""
    unit = db.session.get(InvUnit, unit_id) if type(unit_id) is int else None
    if unit is None or unit.condition == INV_CONDITION_RETIRED:
        raise InventoryError("That asset is not in use, so it has no label to print.")
    if unit.ownership != INV_OWNERSHIP_OWNED:
        raise InventoryError("Only owned assets print from this station.")
    previous = (db.session.query(InvPrintJob).filter_by(unit_id=unit.id)
                .order_by(InvPrintJob.id.desc()).first())
    job = label_job(
        unit, actor, client_request_id,
        printed_date=previous.printed_date if previous else None,
        fields=dict(previous.fields) if previous else None)
    return label_answer(unit, job)
